# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Home Assistant Energy dashboard set up from the tariff: one grid connection per tariff zone with its price,
export, solar, battery with SOC, and live power flows.

The sources this integration writes are named "Felicity …" and remembered in the entry's options (OWN: their type,
energy statistic and name) when written. They are created when a tariff is first saved (only if the dashboard is empty) and
rewritten on later saves; only the remembered ones are ever replaced, so sources the user set up by hand (also on
the sensors this integration makes for the dashboard) and other accounts' sources are never touched."""
from __future__ import annotations

import logging

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .tariff import DYNAMIC_ZONE

_LOGGER = logging.getLogger(__name__)
MARK = "Felicity"
OWN = "energy_sources"  # entry option: [[type, energy statistic, name], ...] of the sources this entry wrote


def _entities(hass, entry_id: str) -> tuple[dict[str, str], set[str]]:
    """(unique_id -> entity_id of this entry, entity ids of the sensors it makes for the Energy dashboard)."""
    mine, made = {}, set()
    for e in er.async_get(hass).entities.values():
        if e.platform == DOMAIN and e.config_entry_id == entry_id:
            mine[e.unique_id] = e.entity_id
            if "_tariff_import_" in e.unique_id or "_tariff_meter_" in e.unique_id:
                made.add(e.entity_id)
    return mine, made


def _key(src: dict) -> tuple:
    return src.get("type"), src.get("stat_energy_from"), src.get("name")


def _written(entry, sources: list[dict], made: set[str]) -> set[tuple]:
    """The sources this entry wrote. Set-ups from before OWN was kept: the ones it named "Felicity …" on a sensor it
    made for the dashboard (Home Assistant's Energy settings do not name sources), taken over once."""
    if OWN in entry.options:
        return {tuple(k) for k in entry.options[OWN]}
    if not entry.options.get("energy_setup_done"):
        return set()
    return {_key(s) for s in sources if str(s.get("name", "")).startswith(MARK) and s.get("stat_energy_from") in made}


def _used(sources: list[dict]) -> set[str]:
    """Statistics these sources use (Home Assistant takes each one once)."""
    out = set()
    for s in sources:
        out |= {s.get(k) for k in ("stat_energy_from", "stat_energy_to", "stat_rate")}
        out |= set((s.get("power_config") or {}).values())
    return out - {None}


def _sources(coordinator, ids: dict[str, str], used: set[str]) -> list[dict]:
    """Our sources, leaving out the statistics the user's own sources already use."""
    t = coordinator.tariff
    zones = t["zones"] if t.get("mode") == "schedule" else [DYNAMIC_ZONE]
    out = []
    for sn in coordinator.inverters:
        def e(key: str, sn=sn) -> str | None:
            eid = ids.get(f"{sn}_{key}")
            return None if eid in used else eid
        first = True
        for z in zones:
            src = {"type": "grid", "name": f"{MARK} grid · {z['name']}", "stat_energy_from": e(f"tariff_import_{z['id']}"),
                   "stat_energy_to": None, "stat_cost": None, "entity_energy_price": None, "number_energy_price": z.get("price"),
                   "stat_compensation": None, "entity_energy_price_export": None, "number_energy_price_export": None,
                   "cost_adjustment_day": 0.0}
            if t.get("mode") == "entity":
                src["entity_energy_price"] = ids.get(f"{coordinator.config_entry.entry_id}_tariff_price")
            if not src["stat_energy_from"]:
                continue
            if first:  # export and live grid power belong to one connection
                first = False
                if e("tariff_meter_grid_export"):
                    src.update(stat_energy_to=e("tariff_meter_grid_export"), number_energy_price_export=float(t.get("export_price") or 0))
                if e("acTtlInpower"):
                    src["power_config"] = {"stat_rate": e("acTtlInpower")}  # + = buying
            out.append(src)
        if e("tariff_meter_solar"):
            out.append({"type": "solar", "name": f"{MARK} solar", "stat_energy_from": e("tariff_meter_solar"),
                        "config_entry_solar_forecast": None, **({"stat_rate": e("pvTotalPower")} if e("pvTotalPower") else {})})
        if e("tariff_meter_battery_charge") and e("tariff_meter_battery_discharge"):
            bat = {"type": "battery", "name": f"{MARK} battery", "stat_energy_from": e("tariff_meter_battery_discharge"),
                   "stat_energy_to": e("tariff_meter_battery_charge")}
            if e("emsPower"):
                bat["power_config"] = {"stat_rate_inverted": e("emsPower")}  # Felicity: + = charging
            if e("emsSoc"):
                bat["stat_soc"] = e("emsSoc")
            out.append(bat)
    return out


async def _prefs(hass):
    from homeassistant.components.energy.data import async_get_manager
    manager = await async_get_manager(hass)
    return manager, list((manager.data or {}).get("energy_sources") or [])


async def energy_status(hass, coordinator) -> str:
    """'managed' (has our sources), 'empty', or 'own' (set up by hand: left alone)."""
    _, sources = await _prefs(hass)
    _, made = _entities(hass, coordinator.config_entry.entry_id)
    written = _written(coordinator.config_entry, sources, made)
    if any(_key(s) in written for s in sources):
        return "managed"
    return "own" if sources else "empty"


async def sync_energy(hass, coordinator, create: bool) -> str:
    """Rewrite our sources (when there are some, or the dashboard is empty and create is set); returns the status."""
    from homeassistant.components.energy.data import ENERGY_SOURCE_SCHEMA
    manager, sources = await _prefs(hass)
    entry = coordinator.config_entry
    ids, made = _entities(hass, entry.entry_id)
    written = _written(entry, sources, made)
    kept = [s for s in sources if _key(s) not in written]
    status = "managed" if len(kept) < len(sources) else "own" if sources else "empty"
    if status == "own" or (status == "empty" and not create):
        return status
    ours = _sources(coordinator, ids, _used(kept))  # the user's own come first
    try:
        validated = ENERGY_SOURCE_SCHEMA(kept + ours)
    except Exception as err:  # noqa: BLE001 - never leave the dashboard half-written
        _LOGGER.warning("Felicity: Energy dashboard not updated: %s", err)
        return status
    if validated != sources:
        await manager.async_update({"energy_sources": validated})
        _LOGGER.info("Felicity: Energy dashboard %s", "set up" if status == "empty" else "updated")
    options = {**entry.options, OWN: [list(_key(s)) for s in ours], "energy_setup_done": True}
    if options != entry.options:  # one update: each one runs the options listener (and this sync) again
        hass.config_entries.async_update_entry(entry, options=options)
    return "managed"


async def after_tariff_saved(hass, coordinator) -> None:
    """The first saved tariff sets the Energy dashboard up (if it is empty); later saves keep our part in sync."""
    entry = coordinator.config_entry
    done = bool(entry.options.get("energy_setup_done"))
    try:
        await sync_energy(hass, coordinator, create=not done)
    except Exception as err:  # noqa: BLE001 - the tariff itself is saved either way
        _LOGGER.warning("Felicity: Energy dashboard sync failed: %s", err)
