# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""HTTP API of the dashboard card (/api/felicity/*). Reads need a signed-in user, writes an administrator."""
from __future__ import annotations

import asyncio
import json
import re
import logging
import time
from datetime import datetime, timedelta

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .api import FelicityApiError
from .const import DOMAIN
from .coordinator import FINAL_AFTER, FelicityCoordinator, day_start, today
from .cost import zone_signature
from .daystats import day_totals
from .energy import energy_status, sync_energy
from .tariff import DAY_SETS, PRESETS, next_change, remember, validate_tariff, zone_at

_LOGGER = logging.getLogger(__name__)

# columns of /history (the card reads them by position)
SERIES = ("pvTotalPower", "pvPower", "pv2Power", "acTtlInpower", "emsPower", "acTotalOutActPower", "emsSoc", "pvVolt", "pv2Volt", "acRInVolt")
# columns of /series: the cloud's daily / monthly totals, and how day_totals() stands in for them
ENERGY_KEYS = {"gridInput": "grid_import", "feedOutput": "grid_export", "generateEnergy": "solar", "batCharEnergy": "battery_charge",
               "batDisEnergy": "battery_discharge", "offGridEnergy": "home", "gridTiedEnergy": "home_meter"}


def views(hass: HomeAssistant) -> list[HomeAssistantView]:
    return [HistoryView(hass), FieldHistoryView(hass), SeriesView(hass), SettingView(hass), TariffView(hass), PeriodView(hass)]


class BadRequest(Exception):
    """Answered with 400 and the message."""


def pick_inverter(hass: HomeAssistant, sn: str | None) -> tuple[FelicityCoordinator, str]:
    """(coordinator, serial) of the requested inverter, or of the first inverter of any account."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator: FelicityCoordinator = entry.runtime_data
        for s in coordinator.inverters:
            if not sn or s == sn:
                return coordinator, s
    raise web.HTTPNotFound(text=json.dumps({"error": "no inverter"}), content_type="application/json")


def day_param(request: web.Request) -> str:
    day = request.query.get("date") or today()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise BadRequest("date must be YYYY-MM-DD") from None
    return min(day, today())  # a browser ahead of the home's time zone asks for "tomorrow"


async def json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise BadRequest("the body must be JSON") from None
    if not isinstance(body, dict) or not body.get("sn"):
        raise BadRequest("the body must be an object with the inverter's sn")
    return body


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows(rows: list[dict], fields) -> list[list]:
    """[[epoch ms, field values...]] with powers converted from the history's kW to W."""
    def val(r, f):
        v = _num(r.get(f))
        is_power = "power" in f.lower() or f == "ctPower"
        return None if v is None else (round(v * 1000) if is_power else v)
    return [[int(r["_local"].timestamp() * 1000), *(val(r, f) for f in fields)] for r in rows]


class _View(HomeAssistantView):
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def _run(self, coro) -> web.Response:
        try:
            return self.json(await coro)
        except BadRequest as err:
            return self.json({"error": str(err)}, 400)
        except FelicityApiError as err:
            return self.json({"error": str(err)}, 502)


class HistoryView(_View):
    """GET /api/felicity/history?sn=&date= -> the day charts' columns (SERIES) per minute."""

    url = "/api/felicity/history"
    name = "api:felicity:history"

    async def get(self, request: web.Request) -> web.Response:
        async def answer():
            coordinator, sn = pick_inverter(self.hass, request.query.get("sn"))
            day = day_param(request)
            return {"date": day, "keys": ["t", *SERIES], "rows": _rows(await coordinator.history(sn, day), SERIES)}
        return await self._run(answer())


class FieldHistoryView(_View):
    """GET /api/felicity/hist?sn=&date=&fields=a,b,c -> rows [t, a, b, c] of the fields the inverter reports."""

    url = "/api/felicity/hist"
    name = "api:felicity:hist"

    async def get(self, request: web.Request) -> web.Response:
        async def answer():
            coordinator, sn = pick_inverter(self.hass, request.query.get("sn"))
            day = day_param(request)
            fields = [f for f in (request.query.get("fields") or "").split(",") if f][:24]
            rows = await coordinator.history(sn, day)
            present = [f for f in fields if any(r.get(f) not in (None, "") for r in rows)]
            return {"date": day, "fields": present, "rows": _rows(rows, present)}
        return await self._run(answer())


class SeriesView(_View):
    """GET /api/felicity/series?sn=&unit=day&count=60 (or unit=month&count=12) -> one row of energy totals per
    day / month, oldest first. A period is kept once it is over; the current one for 5 minutes."""

    url = "/api/felicity/series"
    name = "api:felicity:series"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        self._cache: dict[str, tuple[float, bool, list]] = {}  # key -> (fetched, final, row); a few hundred small rows
        self._sem = asyncio.Semaphore(2)

    async def _one(self, coordinator: FelicityCoordinator, sn: str, unit: str, date: str) -> list:
        key = f"{sn}:{unit}:{date}"
        hit = self._cache.get(key)
        if hit and (hit[1] or time.time() - hit[0] < 300):
            return hit[2]
        label = date[:7] if unit == "month" else date
        start = datetime.strptime(date, "%Y-%m-%d")
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) if unit == "month" else start + timedelta(days=1)
        final = dt_util.now() > day_start(end.strftime("%Y-%m-%d")) + FINAL_AFTER
        try:
            async with self._sem:
                rows = await coordinator.client.energy(sn, unit, f"{date} 00:00:00")
            r = rows[0] if rows else {}
            row = [label, *(round(_num(r.get(k)) or 0.0, 2) for k in ENERGY_KEYS)]
            if unit == "day" and not any(row[1:]):
                # the cloud's daily totals are sometimes missing although the minute history exists
                async with self._sem:
                    totals = day_totals(await coordinator.history(sn, date, cache=False))
                row = [label, *(round(totals[k], 2) for k in ENERGY_KEYS.values())]
        except FelicityApiError as err:
            _LOGGER.debug("energy %s %s: %s", unit, date, err)
            return [label, *([None] * len(ENERGY_KEYS))]  # not cached
        self._cache[key] = (time.time(), final, row)
        return row

    async def get(self, request: web.Request) -> web.Response:
        async def answer():
            coordinator, sn = pick_inverter(self.hass, request.query.get("sn"))
            unit = request.query.get("unit", "day")
            if unit not in ("day", "month"):
                raise BadRequest("unit is day or month")
            try:
                count = max(1, min(int(request.query.get("count", "60")), 400 if unit == "day" else 60))
            except ValueError:
                raise BadRequest("count must be a number") from None
            now = dt_util.now()
            if unit == "month":
                y, m, dates = now.year, now.month, []
                for _ in range(count):
                    dates.append(f"{y:04d}-{m:02d}-01")
                    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
            else:
                dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(count)]
            rows = await asyncio.gather(*(self._one(coordinator, sn, unit, d) for d in reversed(dates)))
            return {"unit": unit, "keys": ["t", *ENERGY_KEYS], "rows": rows}
        return await self._run(answer())


class SettingView(_View):
    """POST /api/felicity/setting {sn, key, value, confirm} -> send one inverter setting (administrators only)."""

    url = "/api/felicity/setting"
    name = "api:felicity:setting"

    async def post(self, request: web.Request) -> web.Response:
        if not request["hass_user"].is_admin:
            return self.json({"error": "only administrators can change settings"}, 403)

        async def answer():
            body = await json_body(request)
            coordinator, sn = pick_inverter(self.hass, body["sn"])
            try:
                return await coordinator.write_setting(sn, str(body.get("key")), body.get("value"),
                                                       confirmed=bool(body.get("confirm")))
            except ValueError as err:
                raise BadRequest(str(err)) from None
        return await self._run(answer())


class TariffView(_View):
    """GET /api/felicity/tariff?sn=&date= -> the tariff, price and zone now, the day's zones, cost split and battery
    runtime, the entities for the Energy dashboard. POST {sn, tariff} saves a tariff, {sn, energy: "setup"} sets the
    Energy dashboard up (administrators only)."""

    url = "/api/felicity/tariff"
    name = "api:felicity:tariff"

    def _entities(self, coordinator: FelicityCoordinator, sn: str) -> dict:
        ents: dict = {}
        for e in er.async_entries_for_config_entry(er.async_get(self.hass), coordinator.config_entry.entry_id):
            uid = e.unique_id
            if uid.endswith(("_tariff_price", "_tariff_zone")):
                ents[uid.rsplit("_", 1)[1]] = e.entity_id
            elif uid.startswith(f"{sn}_tariff_import_"):
                ents.setdefault("imports", {})[uid.removeprefix(f"{sn}_tariff_import_")] = e.entity_id
            elif uid.startswith(f"{sn}_tariff_"):
                ents[uid.removeprefix(f"{sn}_tariff_")] = e.entity_id
        return ents

    async def get(self, request: web.Request) -> web.Response:
        async def answer():
            coordinator, sn = pick_inverter(self.hass, request.query.get("sn"))
            day = day_param(request)
            t, now = coordinator.tariff, dt_util.now()
            b = coordinator.costs.get(sn) if day == today() else None
            if not b or b["date"] != day:
                try:
                    b, _ = await coordinator.breakdown(sn, day)
                except FelicityApiError as err:
                    b = {"error": str(err), "bands": [], "tariff": None}
            z = zone_at(t, now)
            current = {"zone": z and {k: z[k] for k in ("id", "name", "color")}, "price": coordinator.price_now()}
            if z and (nc := next_change(t, now)):
                current.update(next_at=nc[0].isoformat(), next_zone={k: nc[1][k] for k in ("id", "name", "color", "price")})
            day_split = {k: v for k, v in b.items() if k not in ("bands", "tariff")}
            out = {"sn": sn, "date": day, "tariff": t, "now": current, "bands": b.get("bands", []),
                   "day_tariff": b.get("tariff"), "day": day_split, "runtime": coordinator.runtime_now(sn),
                   "admin": request["hass_user"].is_admin, "presets": PRESETS, "day_sets": DAY_SETS,
                   "ha_currency": self.hass.config.currency}
            if day == today():
                out.update(entities=self._entities(coordinator, sn), energy=await energy_status(self.hass, coordinator))
            return out
        return await self._run(answer())

    async def post(self, request: web.Request) -> web.Response:
        if not request["hass_user"].is_admin:
            return self.json({"error": "only administrators can change the tariff"}, 403)

        async def answer():
            body = await json_body(request)
            coordinator, _ = pick_inverter(self.hass, body["sn"])
            entry = coordinator.config_entry
            if body.get("energy") == "setup":  # the card's button, offered while the Energy dashboard is empty
                status = await sync_energy(self.hass, coordinator, create=True)
                if status == "managed":
                    self.hass.config_entries.async_update_entry(entry, options={**entry.options, "energy_setup_done": True})
                return {"ok": True, "energy": status}
            try:
                tariff = validate_tariff(body.get("tariff"))
            except (ValueError, TypeError) as err:
                raise BadRequest(str(err)) from None
            # saving is the one way a tariff is applied: the options update listener takes it from here
            reload = zone_signature(tariff) != zone_signature(coordinator.tariff)
            self.hass.config_entries.async_update_entry(entry, options=remember(entry.options, tariff, dt_util.now()))
            return {"ok": True, "reload": reload, "tariff": tariff}
        return await self._run(answer())


class PeriodView(_View):
    """GET /api/felicity/period?sn=&month=YYYY-MM -> a calendar month by tariff zone (grid import, money, the battery,
    savings) and its days; today counts while it is the current month. Days not yet fetched are listed in "missing"."""

    url = "/api/felicity/period"
    name = "api:felicity:period"

    async def get(self, request: web.Request) -> web.Response:
        async def answer():
            coordinator, sn = pick_inverter(self.hass, request.query.get("sn"))
            month = request.query.get("month") or today()[:7]
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                raise BadRequest("month must be YYYY-MM")
            return {**coordinator.month_summary(sn, month), "sn": sn, "backfilling": coordinator.backfilling}
        return await self._run(answer())
