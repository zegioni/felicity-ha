# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Electricity tariffs: time-of-use zones (Ukraine 1/2/3-zone, Spain 2.0TD, Economy 7, any custom schedule)
or a dynamic price taken from another Home Assistant entity (Nord Pool, ENTSO-E, Tibber, Octopus ...).

Stored in the config entry options under "tariff":
{
  "currency": "UAH",
  "mode": "schedule" | "entity",
  "price_entity": "sensor.nordpool_kwh_...",          # mode "entity"
  "zones": [{"id": "peak", "name": "Peak", "price": 6.48, "color": "#ff6b6b",
             "ranges": [["07:00", "11:00"], ["20:00", "22:00", "12345"]]}, ...],   # optional 3rd item: ISO weekdays
  "default_zone": "half",                              # zone for times not covered by any range
  "export_price": 0.0,                                 # paid for energy sold to the grid
  "battery_reserve": null                              # % the runtime estimate counts down to; null = the inverter's own setting
}
Every saved version is kept in options["tariff_history"] = [{"from": <ISO time saved, "" = before versions were
kept>, "tariff": {...}}, ...]: any moment is priced with the version in force then, so a change made mid-day applies
from that moment and past days keep their own prices.
"""
from __future__ import annotations

import math
import re
from bisect import bisect_right
from datetime import date as Date, datetime, timedelta, tzinfo

ALL_DAYS = "1234567"
DAY_SETS = {"1234567": "Every day", "12345": "Mon–Fri", "67": "Sat–Sun", "6": "Saturday", "7": "Sunday"}

# Ukrainian household price since 2024-06: 4.32 UAH/kWh; multi-zone coefficients 0.5 / 1 (2 zones), 0.4 / 1 / 1.5 (3 zones)
PRESETS = {
    "ua1": {"name": "Ukraine · single rate", "currency": "UAH", "mode": "schedule", "default_zone": "flat",
            "zones": [{"id": "flat", "name": "Single rate", "price": 4.32, "color": "#5aa9ff", "ranges": []}]},
    "ua2": {"name": "Ukraine · day / night", "currency": "UAH", "mode": "schedule", "default_zone": "day",
            "zones": [{"id": "day", "name": "Day", "price": 4.32, "color": "#ffb547", "ranges": [["07:00", "23:00"]]},
                      {"id": "night", "name": "Night", "price": 2.16, "color": "#5aa9ff", "ranges": [["23:00", "07:00"]]}]},
    "ua3": {"name": "Ukraine · peak / half-peak / night", "currency": "UAH", "mode": "schedule", "default_zone": "half",
            "zones": [{"id": "peak", "name": "Peak", "price": 6.48, "color": "#ff6b6b", "ranges": [["07:00", "11:00"], ["20:00", "22:00"]]},
                      {"id": "half", "name": "Half-peak", "price": 4.32, "color": "#ffb547", "ranges": [["11:00", "20:00"], ["22:00", "23:00"]]},
                      {"id": "night", "name": "Night", "price": 1.728, "color": "#5aa9ff", "ranges": [["23:00", "07:00"]]}]},
    "es": {"name": "Spain · 2.0TD (peak / flat / valley)", "currency": "EUR", "mode": "schedule", "default_zone": "valley",
           "zones": [{"id": "peak", "name": "Peak", "price": 0.25, "color": "#ff6b6b",
                      "ranges": [["10:00", "14:00", "12345"], ["18:00", "22:00", "12345"]]},
                     {"id": "flat", "name": "Flat", "price": 0.17, "color": "#ffb547",
                      "ranges": [["08:00", "10:00", "12345"], ["14:00", "18:00", "12345"], ["22:00", "00:00", "12345"]]},
                     {"id": "valley", "name": "Valley", "price": 0.10, "color": "#5aa9ff", "ranges": []}]},
    "e7": {"name": "UK · Economy 7", "currency": "GBP", "mode": "schedule", "default_zone": "day",
           "zones": [{"id": "day", "name": "Day", "price": 0.30, "color": "#ffb547", "ranges": []},
                     {"id": "night", "name": "Night", "price": 0.15, "color": "#5aa9ff", "ranges": [["00:30", "07:30"]]}]},
    "flat": {"name": "Single rate (any country)", "mode": "schedule", "default_zone": "flat",
             "zones": [{"id": "flat", "name": "Single rate", "price": 0.25, "color": "#5aa9ff", "ranges": []}]},
    "dynamic": {"name": "Dynamic price from an entity (Nord Pool, ENTSO-E, Tibber…)", "mode": "entity", "zones": []},
}
DEFAULT = {"currency": "UAH", "mode": "schedule", "price_entity": "", "export_price": 0.0, "battery_reserve": None,
           "zones": PRESETS["ua2"]["zones"], "default_zone": "day"}


def default_for(currency: str | None) -> dict:
    """Until the user sets a tariff: Ukrainian day/night in UAH, otherwise a single rate in the home's currency."""
    if not currency or currency == "UAH":
        return dict(DEFAULT)
    return {**DEFAULT, "currency": currency, "zones": PRESETS["flat"]["zones"], "default_zone": "flat"}


DYNAMIC_ZONE = {"id": "dynamic", "name": "Dynamic price", "color": "#5aa9ff"}


def minutes(hhmm: str) -> int:
    h, m = str(hhmm).strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m):
        raise ValueError(f"bad time {hhmm!r}, use HH:MM")
    return h * 60 + m


def _hhmm(m: int) -> str:
    return "24:00" if m == 1440 else f"{m // 60:02d}:{m % 60:02d}"


def validate_tariff(t: dict) -> dict:
    """Normalise a tariff coming from the UI; raises ValueError with a message fit for the user."""
    if not isinstance(t, dict):
        raise ValueError("the tariff must be an object")
    try:
        export_price = float(t.get("export_price") or 0)
        reserve = None if t.get("battery_reserve") in (None, "") else int(float(t.get("battery_reserve")))
    except (TypeError, ValueError, OverflowError) as err:
        raise ValueError("export price and battery reserve must be numbers") from err
    if not math.isfinite(export_price) or export_price < 0:
        raise ValueError("the export price must be a positive number")
    if reserve is not None and not 0 <= reserve <= 100:
        raise ValueError("battery reserve must be 0–100 %")
    out = {"currency": (str(t.get("currency") or "UAH").strip().upper()[:4] or "UAH"),
           "mode": t.get("mode") if t.get("mode") in ("schedule", "entity") else "schedule",
           "price_entity": str(t.get("price_entity") or "").strip(), "export_price": round(export_price, 4),
           "battery_reserve": reserve}
    if out["mode"] == "entity" and not out["price_entity"].startswith(("sensor.", "input_number.", "number.")):
        raise ValueError("choose the sensor that provides the current price")
    zones, ids, names = [], set(), set()
    for z in t.get("zones") or [] if out["mode"] == "schedule" else []:
        if not isinstance(z, dict):
            raise ValueError("every zone must be an object")
        name = str(z.get("name") or "").strip()[:32]
        if not name:
            raise ValueError("every zone needs a name")
        if name.lower() in names:
            raise ValueError(f"two zones are called {name!r}")
        names.add(name.lower())
        zid = "".join(c if c.isascii() and c.isalnum() else "_" for c in str(z.get("id") or name).strip().lower()).strip("_")
        zid = zid[:24] or f"zone{len(zones) + 1}"
        while zid in ids:
            zid += "_2"
        ids.add(zid)
        try:
            price = float(z.get("price"))
        except (TypeError, ValueError) as err:
            raise ValueError(f"{name}: price must be a number") from err
        if not (math.isfinite(price) and 0 <= price < 1000):
            raise ValueError(f"{name}: price looks wrong ({price})")
        ranges = []
        for r in z.get("ranges") or []:
            if not isinstance(r, (list, tuple)) or len(r) < 2:
                raise ValueError(f"{name}: every range needs a start and an end")
            try:
                sa, sb = minutes(r[0]), minutes(r[1])
            except ValueError as err:
                raise ValueError(f"{name}: {err}") from err
            if sa == sb:
                raise ValueError(f"{name}: {r[0]}–{r[1]} is empty; leave the zone without hours to make it the default")
            days = str(r[2]) if len(r) > 2 and r[2] else ALL_DAYS
            if not re.fullmatch(r"[1-7]+", days):
                raise ValueError(f"{name}: weekdays are digits 1 (Monday) to 7 (Sunday)")
            days = "".join(sorted(set(days)))
            ranges.append([_hhmm(sa), _hhmm(sb)] + ([days] if days != ALL_DAYS else []))
        color = str(z.get("color") or "#5aa9ff").strip()
        zones.append({"id": zid, "name": name, "price": round(price, 4),
                      "color": color if re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}", color) else "#5aa9ff",
                      "ranges": ranges})
    if out["mode"] == "schedule" and not zones:
        raise ValueError("add at least one tariff zone")
    if len(zones) > 8:
        raise ValueError("at most 8 zones")
    open_zones = [z["id"] for z in zones if not z["ranges"]]
    if len(open_zones) > 1:
        raise ValueError("only one zone can be left without hours (it covers all remaining time)")
    out["zones"] = zones
    # the zone without hours covers the rest; otherwise the one marked, else the first
    want = t.get("default_zone") if isinstance(t.get("default_zone"), str) else None
    out["default_zone"] = open_zones[0] if open_zones else want if want in ids else (zones[0]["id"] if zones else "")
    return out


def parse_from(value: str, tz: tzinfo) -> datetime | None:
    """A version's start: "" = before any saved version, a date = that local midnight (older format), or ISO time."""
    if not value:
        return None
    if len(value) == 10:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=tz)
    return datetime.fromisoformat(value)


def remember(options: dict, tariff: dict, now: datetime) -> dict:
    """Options with the new tariff appended to tariff_history (the tariff used until now is kept as the first
    version when versions were not kept yet)."""
    history = list(options.get("tariff_history") or [])
    if not history and options.get("tariff"):
        history.append({"from": "", "tariff": options["tariff"]})
    if not history or history[-1]["tariff"] != tariff:  # saving without changes keeps the version
        history.append({"from": now.isoformat(timespec="seconds"), "tariff": tariff})
    return {**options, "tariff": tariff, "tariff_history": history[-100:]}


def versions(history: list[dict], current: dict, tz: tzinfo) -> list[tuple[datetime | None, dict]]:
    """[(start or None, tariff), ...] oldest first; just the current tariff when no history is kept."""
    out = sorted(((parse_from(h["from"], tz), h["tariff"]) for h in history or []),
                 key=lambda v: v[0] or datetime.min.replace(tzinfo=tz))
    return out or [(None, current)]


def zone_at(t: dict, when: datetime) -> dict | None:
    """The zone active at a local time. Ranges may cross midnight (the weekday of the start counts);
    later zones win on overlap; uncovered time falls to the default zone."""
    if not t or t.get("mode") != "schedule":
        return None
    m = when.hour * 60 + when.minute
    wd, yd = str(when.isoweekday()), str((when - timedelta(days=1)).isoweekday())
    hit = None
    for z in t.get("zones") or []:
        for r in z.get("ranges") or []:
            s, e = minutes(r[0]) % 1440, minutes(r[1]) % 1440
            days = r[2] if len(r) > 2 else ALL_DAYS
            if s < e:
                inside = s <= m < e and wd in days
            else:  # crosses midnight: evening part belongs to today, morning part to the range that started yesterday
                inside = (m >= s and wd in days) or (m < e and yd in days)
            if inside:
                hit = z
    if hit is None:
        hit = next((z for z in t.get("zones") or [] if z["id"] == t.get("default_zone")), None)
    return hit


def next_change(t: dict, now: datetime) -> tuple[datetime, dict] | None:
    """When the zone changes next and to which zone (up to a week ahead). Zones can only change at range edges."""
    cur = zone_at(t, now)
    if cur is None:
        return None
    edges = sorted({minutes(x) % 1440 for z in t.get("zones") or [] for r in z.get("ranges") or [] for x in r[:2]})
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for day in range(8):
        for m in edges:
            probe = midnight + timedelta(days=day, minutes=m)
            if probe > now:
                z = zone_at(t, probe)
                if z and z["id"] != cur["id"]:
                    return probe, z
    return None


def day_bands(t: dict, day: Date, tz: tzinfo) -> list[list]:
    """[[from_hour, to_hour, zone_id], ...] covering a local day, for timelines and chart backgrounds."""
    if not t or t.get("mode") != "schedule":
        return []
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    edges = sorted({0, 1440, *(minutes(x) % 1440 for z in t["zones"] for r in z["ranges"] for x in r[:2])})
    out: list[list] = []
    for a, b in zip(edges, edges[1:]):
        z = zone_at(t, start + timedelta(minutes=a))
        zid = z["id"] if z else None
        if out and out[-1][2] == zid:
            out[-1][1] = b / 60
        else:
            out.append([a / 60, b / 60, zid])
    return out


class Pricer:
    """Zone and price at any moment: the tariff version in force then, and for a dynamic tariff the recorded
    price of its sensor at that moment ({entity: [(time, price), ...]})."""

    def __init__(self, tariff_versions: list[tuple[datetime | None, dict]],
                 points: dict[str, list[tuple[datetime, float]]] | None = None, fallback: float | None = None) -> None:
        self.versions = tariff_versions
        self.points = {e: sorted(p) for e, p in (points or {}).items()}
        self._times = {e: [t for t, _ in p] for e, p in self.points.items()}
        self.fallback = fallback  # a dynamic price with nothing recorded (today: the price now)

    def tariff_at(self, when: datetime) -> dict:
        t = self.versions[0][1]  # before the first known version: the earliest one
        for start, v in self.versions:
            if start is None or start <= when:
                t = v
        return t

    def at(self, when: datetime) -> tuple[dict | None, float | None]:
        t = self.tariff_at(when)
        if t.get("mode") == "entity":
            pts = self.points.get(t.get("price_entity"))
            if not pts:
                return DYNAMIC_ZONE, self.fallback
            i = bisect_right(self._times[t["price_entity"]], when) - 1
            return DYNAMIC_ZONE, pts[max(i, 0)][1]
        z = zone_at(t, when)
        return z, (z["price"] if z else None)
