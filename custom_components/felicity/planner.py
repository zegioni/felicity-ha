# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""How the battery would go through a day under a set of time-of-use rules: the base for recommending rules.
Replayed over 11 real days (real load and outages, the rules that were set) it follows the battery level within
1.4 % on average and the grid import within 2.4 %.

Behaviour of an IVAM in time-of-use mode, as observed on a real system:
- a rule with grid charging on charges the battery from the grid at the rule's power until its SOC; the home runs
  on the grid meanwhile;
- a rule without grid charging lets the battery carry the home down to the rule's SOC, at most at the rule's power;
  the rest, and everything once the SOC is reached, comes from the grid;
- solar covers the home first, its surplus charges the battery;
- in an outage the battery carries the home alone, down to the shutdown level;
- the inverter takes a few tens of watts from the grid on its own (zero-export margin, own use).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    start: float  # hours, 0–24
    stop: float
    soc: float    # %
    power: float  # W: charging power with grid charging, else the battery's discharge limit
    grid_charge: bool

    def covers(self, h: float) -> bool:
        return self.start <= h < self.stop if self.start < self.stop else h >= self.start or h < self.stop


@dataclass(frozen=True)
class System:
    capacity_kwh: float
    shutdown_soc: float = 10.0
    max_discharge_w: float = 6000.0
    charge_eff: float = 0.95                # battery energy stored per grid kWh put in
    idle_grid_w: float = 40.0               # what the inverter takes from the grid on its own


def _hours(t: str) -> float:
    hh, mm = str(t).split(":")[:2]
    return 24.0 if (hh, mm) == ("23", "59") else int(hh) + int(mm) / 60


def rules_from_settings(raw: dict) -> list[Rule]:
    """ecoRuleN settings (as the inverter reports them) -> rules. IVAM-style rules carry a grid-charging flag;
    IVGM / T-REX ones a mode (1 charge, 2 discharge, 0 off)."""
    out = []
    for key in sorted((k for k in raw if k.startswith("ecoRule") and k[7:].isdigit()), key=lambda k: int(k[7:])):
        r = raw[key]
        if not isinstance(r, dict) or not r.get("startTime") or not r.get("stopTime"):
            continue
        try:
            if "ruleMode" in r:
                if (rule := ivgm_rule(r)) is not None:
                    out.append(rule)
                continue
            out.append(Rule(_hours(r["startTime"]), _hours(r["stopTime"]), float(r.get("soc") or 0),
                            float(r.get("power") or 0), str(r.get("gridChangingEnable")) == "1"))
        except (TypeError, ValueError):
            continue
    return out


def simulate(soc: float, steps: list[tuple[float, float, float, bool, float]], rules, system: System) -> list[dict]:
    """steps: (hour of day, home W, solar W, grid available, length in hours); rules: a list of Rule, or a function
    of the hour giving one (rules changed during the period). Returns per step the SOC at its end, the battery power
    (+ charging, W) and the grid power (W)."""
    kwh_per_pct = system.capacity_kwh / 100
    out = []
    for h, load, pv, grid_up, step_h in steps:
        rule = next((r for r in (rules(h) if callable(rules) else rules) if r.covers(h)), None)
        net = load - pv  # what the home still needs after solar (negative: surplus)
        lo, hi = 0.0, 100.0  # where this step may take the battery
        if not grid_up:  # outage: the battery alone, down to the shutdown level
            batt, lo = -net, system.shutdown_soc
        elif rule and rule.grid_charge and soc < rule.soc:
            batt, hi = min(rule.power, system.max_discharge_w), rule.soc
        elif net < 0:
            batt = -net  # solar surplus charges the battery
        elif rule and not rule.grid_charge and soc > rule.soc:
            batt, lo = -min(net, rule.power, system.max_discharge_w), rule.soc
        else:
            batt = 0.0
        # stop at the level within the step (a long step would overshoot it)
        de = batt * step_h / 1000 * (system.charge_eff if batt > 0 else 1)
        de = min(de, max(0.0, (hi - soc) * kwh_per_pct)) if de > 0 else max(de, min(0.0, (lo - soc) * kwh_per_pct))
        batt = de * 1000 / step_h / (system.charge_eff if de > 0 else 1) if step_h else 0.0
        grid = max(0.0, net + batt) + system.idle_grid_w if grid_up else 0.0
        soc = min(100.0, max(0.0, soc + de / kwh_per_pct))
        out.append({"h": h, "soc": soc, "batt": batt, "grid": grid})
    return out


# ---- recommending rules ----

OUTAGE_MARGIN = 1.15  # the home may use more than usual during an outage: plan for 15 % more


def ivgm_rule(r: dict) -> Rule | None:
    """IVGM / T-REX rules carry a mode instead of the grid-charging flag (1 charge, 2 discharge; 0 off)."""
    mode = str(r.get("ruleMode"))
    if mode not in ("1", "2"):
        return None
    return Rule(_hours(r["startTime"]), _hours(r["stopTime"]), float(r.get("soc") or 0), float(r.get("power") or 0),
                mode == "1")


def _hm(slot: int) -> str:
    return "23:59" if slot >= 48 else f"{slot // 2:02d}:{30 * (slot % 2):02d}"


def _compress(actions: list[tuple]) -> list[tuple[int, int, tuple]]:
    """48 half-hour actions -> [(from slot, to slot, action)] with equal neighbours merged."""
    out = []
    for i, a in enumerate(actions):
        if out and out[-1][2] == a:
            out[-1] = (out[-1][0], i + 1, a)
        else:
            out.append((i, i + 1, a))
    return out


def _rules(actions: list[tuple], charge_w: float, power_max: float) -> list[Rule]:
    return [Rule(a / 2, b / 2, act[1], charge_w if act[0] == "charge" else power_max, act[0] == "charge")
            for a, b, act in _compress(actions)]


def evaluate_rules(start_soc: float, steps: list[tuple], prices: list[float | None], rules: list[Rule], system: System,
                   reserve: float, outages: list[tuple[int, int]], cheap: float) -> dict:
    """One set of rules over the coming steps: grid cost, the lowest battery level, kWh missing in announced
    outages (planned with OUTAGE_MARGIN more use), and a score (cost, the energy left at the end valued at the
    cheapest price, a heavy penalty for an outage not carried)."""
    kwh_per_pct = system.capacity_kwh / 100
    sim = simulate(start_soc, [(h, w * OUTAGE_MARGIN if not up else w, pv, up, dh) for h, w, pv, up, dh in steps],
                   rules, system)
    cost = sum(o["grid"] * s[4] / 1000 * (p or 0) for o, s, p in zip(sim, steps, prices))
    short = 0.0
    for a, b in outages:
        low = min((o["soc"] for o in sim[a:b]), default=100)
        short += max(0.0, reserve - low) * kwh_per_pct
    end = sim[-1]["soc"] if sim else start_soc
    value = cost - (end - start_soc) * kwh_per_pct * cheap / system.charge_eff
    return {"cost": round(cost, 2), "net": round(value, 2), "short_kwh": round(short, 2), "end_soc": round(end, 1),
            "min_soc": round(min((o["soc"] for o in sim), default=start_soc), 1), "score": value + short * 1000,
            "soc": [round(o["soc"], 1) for o in sim]}


def plan(*, start_soc: float, steps: list[tuple], prices: list[float | None], day_prices: list[float | None],
         current: list[Rule], system: System, reserve: float, charge_w: float, power_max: float, max_rules: int,
         outages: list[tuple[int, int]] | None = None) -> dict:
    """The cheapest daily rules for the coming steps (from now on: (hour, home W, solar W, grid up, hours)) that
    still carry every announced outage, compared with the current rules.

    prices: the price at each step; day_prices: the price of each half hour of a day (to lay the rules on the tariff
    zones); outages: (first, last+1) step indices of announced outages (their grid-up flag is False already)."""
    outages = outages or []
    kwh_per_pct = system.capacity_kwh / 100
    known = [p for p in day_prices if p is not None]
    if not known:
        return {"error": "no tariff prices"}
    cheap = min(known)
    levels = sorted({p for p in known if p != cheap})  # the zones the battery should carry, cheapest first

    def evaluate(rules: list[Rule]) -> dict:
        return evaluate_rules(start_soc, steps, prices, rules, system, reserve, outages, cheap)

    # one decision per tariff level: the cheapest zone charges to `target`, the others hold their floor;
    # a hold floor runs from the end of the charge window before an outage until the outage starts
    slot_level = [None if p is None else p for p in day_prices]
    hold_slots: set[int] = set()
    for a, _ in outages:
        h = steps[a][0]
        s = int(h * 2) % 48
        i = (s - 1) % 48
        while slot_level[i] != cheap and i != s:
            hold_slots.add(i)
            i = (i - 1) % 48

    def actions(p: dict) -> list[tuple]:
        out = []
        for i, price in enumerate(slot_level):
            if price == cheap:
                out.append(("charge", p["target"]))
            elif i in hold_slots and p.get("hold"):
                out.append(("floor", max(p["hold"], p["floors"].get(price, reserve))))
            else:
                out.append(("floor", p["floors"].get(price, reserve)))
        return out

    grid = list(range(int(reserve), 101, 5))
    starts = [{"target": 95, "floors": {lv: reserve for lv in levels}, "hold": None}]
    if outages:  # covering an outage needs a full charge and a hold together: start from there and make it cheaper
        need = reserve + max(sum(s[1] * s[4] for s in steps[a:b]) / 1000 * OUTAGE_MARGIN for a, b in outages) / kwh_per_pct
        starts.append({"target": 100, "floors": {lv: reserve for lv in levels},
                       "hold": min(100, 5 * -(-int(need) // 5)) if hold_slots else None})
    scored = [(evaluate(_rules(actions(p), charge_w, power_max)), p) for p in starts]
    best, best_p = min(scored, key=lambda x: x[0]["score"])
    for _ in range(3):  # coordinate descent: one decision at a time, a few rounds
        for name in ["target", *[("floor", lv) for lv in levels], *(["hold"] if hold_slots else [])]:
            for v in grid if name != "hold" else [None, *grid]:
                p = {"target": best_p["target"], "floors": dict(best_p["floors"]), "hold": best_p["hold"]}
                if name == "target":
                    p["target"] = max(v, reserve + 5)
                elif name == "hold":
                    p["hold"] = v
                else:
                    p["floors"][name[1]] = v
                rules = _rules(actions(p), charge_w, power_max)
                if len(rules) > max_rules:
                    continue
                r = evaluate(rules)
                if r["score"] < best["score"] - 1e-6:
                    best, best_p = r, p
    # tidy up: floors that make no difference go back to the reserve (fewer, plainer rules)
    for lv in levels:
        if best_p["floors"].get(lv, reserve) != reserve:
            p = {**best_p, "floors": {**best_p["floors"], lv: reserve}}
            r = evaluate(_rules(actions(p), charge_w, power_max))
            if r["score"] <= best["score"] + 0.05:
                best, best_p = r, p
    rules = _rules(actions(best_p), charge_w, power_max)
    now = evaluate(current)
    keep = now["score"] <= best["score"] + 0.5  # not worth changing for less than about half a unit of money
    # saving: grid cost over the period, with the energy left in the battery at its end valued at the cheapest price
    return {"current": now, "best": now if keep else best, "keep": keep,
            "saving": 0.0 if keep else round(now["net"] - best["net"], 2), "covered": (now if keep else best)["short_kwh"] == 0,
            "rules": [] if keep else [{"startTime": _hm(int(r.start * 2)), "stopTime": _hm(int(r.stop * 2)), "soc": int(r.soc),
                       "power": int(r.power), "gridChangingEnable": "1" if r.grid_charge else "0"} for r in rules],
            "outage_kwh": [round(sum(s[1] * s[4] for s in steps[a:b]) / 1000 * OUTAGE_MARGIN, 2) for a, b in outages]}
