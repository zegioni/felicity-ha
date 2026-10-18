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
    """ecoRuleN settings (as the inverter reports them) -> rules."""
    out = []
    for key in sorted((k for k in raw if k.startswith("ecoRule")), key=lambda k: int(k[7:])):
        r = raw[key]
        if not isinstance(r, dict) or not r.get("startTime") or not r.get("stopTime"):
            continue
        try:
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
        if not grid_up:  # outage: the battery alone
            batt = -net if soc > system.shutdown_soc or net < 0 else 0.0
            grid = 0.0
        else:
            if rule and rule.grid_charge and soc < rule.soc:
                batt = min(rule.power, system.max_discharge_w)
            elif net < 0:
                batt = -net  # solar surplus charges the battery
            elif rule and not rule.grid_charge and soc > rule.soc:
                batt = -min(net, rule.power, system.max_discharge_w)
            else:
                batt = 0.0
            grid = max(0.0, net + batt) + system.idle_grid_w
        if batt > 0 and soc >= 100:
            batt = 0.0
            grid = max(0.0, net) + (system.idle_grid_w if grid_up else 0)
        de = batt * step_h / 1000 * (system.charge_eff if batt > 0 else 1)
        soc = min(100.0, max(0.0, soc + de / kwh_per_pct))
        out.append({"h": h, "soc": soc, "batt": batt, "grid": grid})
    return out
