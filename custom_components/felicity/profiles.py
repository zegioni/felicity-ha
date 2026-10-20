# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Saved time-of-use schedules ("profiles") of an inverter, to switch between, e.g. one for a stable grid and one that
keeps the battery full for outages.

A profile holds the schedule as the Felicity app edits it: on inverters with grid / generator charging flags (IVAM and
alike) SEGMENTS periods back to back over the day (the first from 00:00, the last to 23:59, each starting where the
one before ends); on inverters with a rule mode (IVGM, T-REX) as many rules as the inverter has, each on or off."""
from __future__ import annotations

from .settings import ECO_RULES

SEGMENTS = 6  # the Felicity app shows six periods on IVAM-style inverters
FIELDS = ("startTime", "stopTime", "soc", "power", "gridChangingEnable", "genChangingEnable", "ruleMode", "voltage")


def modeful(current: dict) -> bool:
    return any(isinstance(current.get(k), dict) and "ruleMode" in current[k] for k in ECO_RULES)


def segment_count(current: dict) -> int:
    if modeful(current):
        return sum(1 for k in ECO_RULES if k in current)
    return SEGMENTS


def from_inverter(current: dict) -> list[dict]:
    """The inverter's schedule as a profile's rules (its first segment_count rules)."""
    return [{f: v for f, v in (current.get(k) or {}).items() if f in FIELDS}
            for k in ECO_RULES[:segment_count(current)]]


def check(rules: list[dict], current: dict) -> None:
    """ValueError unless the rules make a complete schedule for this inverter."""
    n = segment_count(current)
    if len(rules) != n:
        raise ValueError(f"a schedule has {n} periods on this inverter")
    if modeful(current):
        return
    if rules[0].get("startTime") != "00:00" or rules[-1].get("stopTime") != "23:59":
        raise ValueError("the periods must run from 00:00 to 23:59")
    for i, (a, b) in enumerate(zip(rules, rules[1:]), 1):
        if a.get("stopTime") != b.get("startTime"):
            raise ValueError(f"period {i + 1} must start where period {i} ends ({a.get('stopTime')})")


def same(a: list[dict], b: list[dict]) -> bool:
    """Whether two schedules do the same (numbers compared as numbers)."""
    def norm(r: dict) -> tuple:
        def num(f):
            try:
                return round(float(r.get(f)), 1)
            except (TypeError, ValueError):
                return r.get(f)
        return (r.get("startTime"), r.get("stopTime"), num("soc"), num("power"), str(r.get("gridChangingEnable", "")),
                str(r.get("genChangingEnable", "")), str(r.get("ruleMode", "")))
    return len(a) == len(b) and all(norm(x) == norm(y) for x, y in zip(a, b))
