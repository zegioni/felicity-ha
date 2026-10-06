# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Numbers derived from one day of minute history: the day's totals, the grid import split by tariff zone, and how
long the battery would carry the home.

Rows are realtime history records with "_local" (an aware local datetime) added; powers are kW (history API units).
The inverter's own day counters (eGridInToday, ePvToday; 0.1 kWh steps, 0 at midnight) are exact, while minute power
samples miss about 10 %, so power is integrated only where no counter exists. eLoadTotal is not used: the cloud
interleaves two different series in it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .tariff import DYNAMIC_ZONE, Pricer

GAP = timedelta(minutes=10)      # power integration does not bridge longer gaps in history
RUNTIME_WINDOW = timedelta(minutes=30)
RESET = 1.0                      # a day counter falling by more than this (kWh) has restarted


def num(r: dict, k: str) -> float:
    try:
        return float(r.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


def has(rows: list[dict], key: str) -> bool:
    return any(r.get(key) not in (None, "") for r in rows)


def counter_steps(rows: list[dict], key: str) -> list[float]:
    """Increase of a day counter at each row. Small dips are noise (ignored until the counter passes them again);
    a large drop is a restart, counted from its new value."""
    last, out = 0.0, []
    for r in rows:
        if r.get(key) in (None, ""):
            out.append(0.0)
            continue
        v = num(r, key)
        if v < last - RESET:
            last = 0.0
        out.append(max(v - last, 0.0))
        last = max(last, v)
    return out


def _intervals(rows: list[dict]):
    """(row, hours since the previous row or 0 across a gap, time since the previous row or None)."""
    prev = None
    for r in rows:
        dt = r["_local"] - prev if prev is not None else None
        yield r, (dt.total_seconds() / 3600 if dt is not None and timedelta(0) < dt <= GAP else 0.0), dt
        prev = r["_local"]


def day_totals(rows: list[dict]) -> dict:
    """kWh of the day: grid import/export, solar, battery charge/discharge, backup output and load behind the meter."""
    t = dict.fromkeys(("grid_import", "grid_export", "solar", "battery_charge", "battery_discharge", "home", "home_meter"), 0.0)
    for r, h, _ in _intervals(rows):
        grid, bat = num(r, "acTtlInpower"), num(r, "emsPower")  # grid + = buying, battery + = charging
        t["grid_import"] += max(grid, 0) * h
        t["grid_export"] += max(-grid, 0) * h
        t["solar"] += num(r, "pvTotalPower") * h
        t["battery_charge"] += max(bat, 0) * h
        t["battery_discharge"] += max(-bat, 0) * h
        t["home"] += num(r, "acTotalOutActPower") * h
        t["home_meter"] += num(r, "meterPower") * h
    for key, counter in (("grid_import", "eGridInToday"), ("solar", "ePvToday")):
        if has(rows, counter):
            t[key] = sum(counter_steps(rows, counter))
    return {k: round(v, 3) for k, v in t.items()}


def zone_split(rows: list[dict], pricer: Pricer, zones: list[dict], export_price: float, currency: str) -> dict:
    """Grid import, home use and export of a day by tariff zone, with their money.

    zones: the zones to list (the day's current tariff); time priced by an earlier tariff version that had other
    zones adds them as extra rows. Each minute is priced at its own moment; a counter step that spans a gap in
    history is spread evenly over the missing minutes."""
    acc: dict[str, dict] = {}

    def entry(z: dict) -> dict:
        if z["id"] not in acc:
            acc[z["id"]] = {"id": z["id"], "name": z["name"], "color": z["color"], "price": z.get("price"),
                            "grid_kwh": 0.0, "grid_cost": 0.0, "home_kwh": 0.0, "home_cost": 0.0, "export_kwh": 0.0}
        return acc[z["id"]]

    def book(when: datetime, what: str, kwh: float) -> None:
        z, price = pricer.at(when)
        if z is None or not kwh:
            return
        e = entry(z)
        e[f"{what}_kwh"] += kwh
        if what != "export" and price is not None:  # an unknown price still counts the energy
            e[f"{what}_cost"] += kwh * price

    for z in zones or [DYNAMIC_ZONE]:
        entry(z)
    steps = counter_steps(rows, "eGridInToday") if has(rows, "eGridInToday") else None
    for i, (r, h, dt) in enumerate(_intervals(rows)):
        when, grid = r["_local"], num(r, "acTtlInpower")
        if steps is None:
            book(when, "grid", max(grid, 0) * h)
        elif steps[i] and dt is not None and dt > GAP:
            n = int(dt.total_seconds() // 60)
            for k in range(1, n + 1):
                book(when - dt + timedelta(minutes=k), "grid", steps[i] / n)
        else:
            book(when, "grid", steps[i])
        book(when, "home", max(num(r, "acTotalOutActPower") + num(r, "meterPower"), 0) * h)
        book(when, "export", max(-grid, 0) * h)

    out = []
    for z in acc.values():
        if z["price"] is None:  # dynamic price: the average actually paid
            z["price"] = round(z["grid_cost"] / z["grid_kwh"], 4) if z["grid_kwh"] > 0.01 else None
        out.append({k: (round(v, 3) if isinstance(v, float) else v) for k, v in z.items()})
    grid_cost, home_cost = sum(z["grid_cost"] for z in out), sum(z["home_cost"] for z in out)
    export_kwh = sum(z["export_kwh"] for z in out)
    earned = export_kwh * export_price
    return {"zones": out, "grid_kwh": round(sum(z["grid_kwh"] for z in out), 3), "grid_cost": round(grid_cost, 2),
            "home_kwh": round(sum(z["home_kwh"] for z in out), 3), "home_cost": round(home_cost, 2),
            "export_kwh": round(export_kwh, 3), "export_earned": round(earned, 2),
            # what the home's use would have cost bought straight from the grid, minus what was actually paid
            "saved": round(home_cost - grid_cost + earned, 2), "currency": currency}


def battery_runtime(rows: list[dict], soc: float | None, reserve: float, capacity_kwh: float,
                    live_load_w: float | None) -> dict:
    """Hours until the battery reaches the reserve if the home kept using what it used over the last half hour
    (solar and grid ignored: "how long would the battery carry the house")."""
    loads = []
    if rows:
        since = rows[-1]["_local"] - RUNTIME_WINDOW
        loads = [(num(r, "acTotalOutActPower") + num(r, "meterPower")) * 1000 for r in rows if r["_local"] >= since]
    avg_w = sum(loads) / len(loads) if loads else live_load_w
    out = {"reserve": reserve, "capacity_kwh": round(capacity_kwh, 2), "avg_load_w": round(avg_w) if avg_w is not None else None,
           "window_min": int(RUNTIME_WINDOW.total_seconds() // 60), "hours": None, "energy_left_kwh": None}
    if soc is None or not capacity_kwh:
        return out
    left = max(0.0, (soc - reserve) / 100 * capacity_kwh)
    out["energy_left_kwh"] = round(left, 2)
    if avg_w and avg_w > 30:
        out["hours"] = round(left * 1000 / avg_w, 2)
    return out
