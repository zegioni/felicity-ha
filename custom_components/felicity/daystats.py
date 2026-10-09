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
RUNTIME_WINDOW = timedelta(hours=24)
RESET = 1.0                      # a day counter falling by more than this (kWh) has restarted
CHARGE_LOSS = 1.25               # at most this much grid energy is paid per kWh charged (the inverter's losses)


def num(r: dict, k: str) -> float:
    try:
        return float(r.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


def has(rows: list[dict], key: str) -> bool:
    return any(r.get(key) not in (None, "") for r in rows)


def counter_steps(rows: list[dict], key: str) -> list[float]:
    """Increase of a day counter at each row. Small dips are noise (ignored until the counter passes them again), a
    lone 0 after the counter has grown is a dropped reading, and any other large drop is a restart, counted from its
    new value."""
    last, out = 0.0, []
    for r in rows:
        if r.get(key) in (None, ""):
            out.append(0.0)
            continue
        v = num(r, key)
        if v == 0 and last > RESET:  # a dropped reading (the cloud sometimes sends 0 between real values)
            out.append(0.0)
            continue
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


def power_scale(rows: list[dict]) -> float:
    """The day's grid-import counter divided by its power integral. The inverter's power readings run a few % high
    against its own counter, so home and battery energy are scaled by this: with no battery and no solar, the home's
    use then equals what was bought. 1 without a counter."""
    if not has(rows, "eGridInToday"):
        return 1.0
    power = sum(max(num(r, "acTtlInpower"), 0) * h for r, h, _ in _intervals(rows))
    return sum(counter_steps(rows, "eGridInToday")) / power if power > 0.1 else 1.0


def day_totals(rows: list[dict]) -> dict:
    """kWh of the day: grid import/export, solar, battery charge/discharge, backup output and load behind the meter.
    The same figures as zone_split (counters where they exist, power scaled to the grid counter elsewhere)."""
    t = dict.fromkeys(("grid_import", "grid_export", "solar", "battery_charge", "battery_discharge", "home", "home_meter"), 0.0)
    scale = power_scale(rows)
    for r, h, _ in _intervals(rows):
        grid, bat = num(r, "acTtlInpower"), num(r, "emsPower")  # grid + = buying, battery + = charging
        t["grid_import"] += max(grid, 0) * h
        t["grid_export"] += max(-grid, 0) * h
        t["solar"] += num(r, "pvTotalPower") * h
        t["battery_charge"] += max(bat, 0) * h * scale
        t["battery_discharge"] += max(-bat, 0) * h * scale
        t["home"] += max(num(r, "acTotalOutActPower"), 0) * h * scale
        t["home_meter"] += max(num(r, "meterPower"), 0) * h * scale
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
                            "grid_kwh": 0.0, "grid_cost": 0.0, "home_kwh": 0.0, "home_cost": 0.0, "export_kwh": 0.0,
                            # battery: charged (its grid-bought part priced) and discharged (valued at the zone price)
                            "bat_in_kwh": 0.0, "bat_in_cost": 0.0, "bat_out_kwh": 0.0, "bat_out_cost": 0.0}
        return acc[z["id"]]

    def book(when: datetime, what: str, kwh: float, priced: float | None = None) -> None:
        """priced: the part of kwh that is paid for (default all of it)."""
        z, price = pricer.at(when)
        if z is None or not kwh:
            return
        e = entry(z)
        e[f"{what}_kwh"] += kwh
        if what != "export" and price is not None:  # an unknown price still counts the energy
            e[f"{what}_cost"] += (kwh if priced is None else priced) * price

    for z in zones or [DYNAMIC_ZONE]:
        entry(z)
    steps = counter_steps(rows, "eGridInToday") if has(rows, "eGridInToday") else None
    # home and battery energy scaled to the grid counter (see power_scale); the counter, in 0.1 kWh steps, is too
    # coarse to tell which minute's import went where, so the battery's share uses the scaled power
    scale = power_scale(rows)
    for i, (r, h, dt) in enumerate(_intervals(rows)):
        when, grid = r["_local"], num(r, "acTtlInpower")
        bought = max(grid, 0) * h * scale  # this interval's grid import (kWh)
        if steps is None:
            book(when, "grid", max(grid, 0) * h)
        elif steps[i] and dt is not None and dt > GAP:
            n = int(dt.total_seconds() // 60)
            for k in range(1, n + 1):
                book(when - dt + timedelta(minutes=k), "grid", steps[i] / n)
        else:
            book(when, "grid", steps[i])
        home = (max(num(r, "acTotalOutActPower"), 0) + max(num(r, "meterPower"), 0)) * h * scale
        book(when, "home", home)
        book(when, "export", max(-grid, 0) * h)
        bat = num(r, "emsPower")  # + charging, - discharging
        charge = max(bat, 0) * h * scale
        # paid for charging: what was bought beyond the home's use, so the inverter's losses count (capped); solar is free
        book(when, "bat_in", charge, priced=min(max(bought - home, 0), charge * CHARGE_LOSS))
        book(when, "bat_out", max(-bat, 0) * h * scale)

    out = []
    for z in acc.values():
        if z["price"] is None:  # dynamic price: the average actually paid
            z["price"] = round(z["grid_cost"] / z["grid_kwh"], 4) if z["grid_kwh"] > 0.01 else None
        out.append({k: (round(v, 3) if isinstance(v, float) else v) for k, v in z.items()})
    grid_cost, home_cost = sum(z["grid_cost"] for z in out), sum(z["home_cost"] for z in out)
    export_kwh = sum(z["export_kwh"] for z in out)
    earned = export_kwh * export_price
    bat_in_cost, bat_out_value = sum(z["bat_in_cost"] for z in out), sum(z["bat_out_cost"] for z in out)
    totals = day_totals(rows)
    return {"zones": out, "grid_kwh": round(sum(z["grid_kwh"] for z in out), 3), "grid_cost": round(grid_cost, 2),
            "home_kwh": round(sum(z["home_kwh"] for z in out), 3), "home_cost": round(home_cost, 2),
            "export_kwh": round(export_kwh, 3), "export_earned": round(earned, 2), "solar_kwh": totals["solar"],
            # what the home's use would have cost bought straight from the grid, minus what was actually paid
            "saved": round(home_cost - grid_cost + earned, 2),
            # the battery: what its discharge would have cost from the grid, minus what charging it from the grid cost
            "battery": {"in_kwh": round(sum(z["bat_in_kwh"] for z in out), 3), "in_cost": round(bat_in_cost, 2),
                        "out_kwh": round(sum(z["bat_out_kwh"] for z in out), 3), "out_value": round(bat_out_value, 2),
                        "saved": round(bat_out_value - bat_in_cost, 2)},
            "currency": currency}


def battery_runtime(rows: list[dict], soc: float | None, reserve: float, capacity_kwh: float,
                    live_load_w: float | None, past: list[list[dict]] | None = None) -> dict:
    """Hours until the battery reaches the reserve if the home keeps its usual use ("how long would the battery carry
    the house"; solar and grid ignored). With earlier days (past: their rows or kept hourly loads), each coming hour
    uses that hour's average load over those days and today (about 4x closer to reality than a flat average);
    without them, the average over the last 24 hours."""
    loads = []
    if rows:
        since = rows[-1]["_local"] - RUNTIME_WINDOW
        loads = [(num(r, "acTotalOutActPower") + num(r, "meterPower")) * 1000 for r in rows if r["_local"] >= since]
    # the window reaches into yesterday: its hours after the window start, from yesterday's hourly load
    w_sum, n = sum(loads), len(loads)
    if past and rows:
        y = past[-1] if past[-1] and isinstance(past[-1][0], list) else hourly_load(past[-1])
        for h in range(since.hour + 1, 24):
            w_sum, n = w_sum + y[h][0], n + y[h][1]
    avg_w = w_sum / n if n else live_load_w
    out = {"reserve": reserve, "capacity_kwh": round(capacity_kwh, 2), "avg_load_w": round(avg_w) if avg_w is not None else None,
           "window_min": int(RUNTIME_WINDOW.total_seconds() // 60), "hours": None, "energy_left_kwh": None}
    if soc is None or not capacity_kwh:
        return out
    left = max(0.0, (soc - reserve) / 100 * capacity_kwh)
    out["energy_left_kwh"] = round(left, 2)
    profile = hourly_profile((past or []) + [rows]) if past else None
    if profile and rows:
        out["hours"] = hours_by_profile(left, profile, rows[-1]["_local"])
        out["basis"], out["profile_days"] = "profile", len(past)
    elif avg_w and avg_w > 30:
        out["hours"] = round(left * 1000 / avg_w, 2)
    return out


def hourly_load(rows: list[dict]) -> list[list[float]]:
    """[W summed, samples] of the home's load for each hour of a day (kept with each finished day, ~100 bytes)."""
    acc = [[0.0, 0] for _ in range(24)]
    for r in rows:
        a = acc[r["_local"].hour]
        a[0] += (num(r, "acTotalOutActPower") + num(r, "meterPower")) * 1000
        a[1] += 1
    return [[round(w, 1), n] for w, n in acc]


def hourly_profile(days: list) -> dict[int, float] | None:
    """The home's average load (W) for each hour of the day over the given days (each a day's rows, or its kept
    hourly_load); None while some hour has no data yet."""
    tot = [[0.0, 0] for _ in range(24)]
    for day in days:
        for h, (w, n) in enumerate(day if day and isinstance(day[0], list) else hourly_load(day)):
            tot[h][0] += w
            tot[h][1] += n
    if any(n == 0 for _, n in tot):
        return None
    return {h: w / n for h, (w, n) in enumerate(tot)}


def hours_by_profile(left_kwh: float, profile: dict[int, float], now: datetime) -> float | None:
    """Hours until the energy left is used if each coming hour uses its usual (profile) load."""
    t, used, horizon = now, 0.0, now + timedelta(days=7)
    while t < horizon:
        nxt = (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        w = max(profile[t.hour], 1.0)
        e = w / 1000 * (nxt - t).total_seconds() / 3600
        if used + e >= left_kwh:
            return round(((t - now).total_seconds() + (left_kwh - used) / w * 1000 * 3600) / 3600, 2)
        used, t = used + e, nxt
    return None


def add_up(days: list[dict]) -> dict:
    """Sum day splits (zone_split results) into one period: each zone's kWh and money, the totals and the battery.
    A zone's price is the period's average (it may have changed during the period)."""
    zones: dict[str, dict] = {}
    t = dict.fromkeys(("grid_kwh", "grid_cost", "home_kwh", "home_cost", "export_kwh", "export_earned", "solar_kwh", "saved"), 0.0)
    bat = dict.fromkeys(("in_kwh", "in_cost", "out_kwh", "out_value", "saved"), 0.0)
    for d in days:
        for k in t:
            t[k] += d.get(k) or 0.0
        for k in bat:
            bat[k] += (d.get("battery") or {}).get(k) or 0.0
        for z in d.get("zones") or []:
            e = zones.setdefault(z["id"], {"id": z["id"], **dict.fromkeys(
                ("grid_kwh", "grid_cost", "home_kwh", "home_cost", "bat_in_kwh", "bat_in_cost", "bat_out_kwh", "bat_out_cost"), 0.0)})
            e.update(name=z["name"], color=z["color"])
            for k in list(e):
                if k.endswith(("_kwh", "_cost")):
                    e[k] += z.get(k) or 0.0
    for z in zones.values():
        z["price"] = round(z["grid_cost"] / z["grid_kwh"], 4) if z["grid_kwh"] > 0.01 else None
    places = lambda k: 3 if k.endswith("kwh") else 4 if k == "price" else 2  # noqa: E731
    rnd = lambda d: {k: (round(v, places(k)) if isinstance(v, float) else v) for k, v in d.items()}  # noqa: E731
    return {**rnd(t), "zones": [rnd(z) for z in zones.values()], "battery": rnd(bat), "days": len(days)}
