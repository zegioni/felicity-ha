# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Polling of the Felicity cloud (live data, daily energy, settings), setting writes, and the daily cost pipeline."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import FelicityApiError, FelicityAuthError, FelicityClient, history_time, powers_in_kw, scale_powers
from .const import (CONF_OUTLETS, DOMAIN, ENERGY_INTERVAL_SECONDS, EVENT_TYPE, EVENTS_INTERVAL_SECONDS, SCAN_INTERVAL_SECONDS,
                     SETTINGS_INTERVAL_SECONDS, UPLOAD_SECONDS)
from .codes import explain
from .daystats import add_up, battery_runtime, day_totals, hourly_load, hourly_profile, zone_split
from .outages import KYIV as KYIV_TZ
from .planner import System, evaluate_rules, plan, rules_from_settings
from . import profiles as prof
from .settings import ECO_RULES, is_risky, validate, validate_rule
from .tariff import Pricer, day_bands, versions, zone_at

_LOGGER = logging.getLogger(__name__)

HISTORY_DAYS_CACHED = 8              # one day of minute history is about 4 MB
FINAL_AFTER = timedelta(minutes=15)  # the cloud uploads a day's last minutes a little after midnight
METERS = ("solar", "grid_export", "battery_charge", "battery_discharge")
DAYS_VERSION = 7                     # bump when a day summary changes, so kept days are computed again
BACKFILL_PAUSE = 10                  # seconds between two past days fetched in the background
BACKFILL_EMPTY_STOP = 31             # past days without data in a row: the start of the history is reached
BACKFILL_MAX_DAYS = 92               # about three months back
BACKFILL_EVERY = 6 * 3600            # seconds; fills in days a cloud hiccup skipped
REVISIT_AFTER = timedelta(hours=12)  # a day kept sooner after its end is computed once more (late uploads)


def event_row(e: dict, model: str | None = None) -> dict:
    """One cloud event as shown to the user, with the manual's meaning and advice where known."""
    kind = "fault" if e.get("warringType") == "F" else "alarm"
    return {"time": e.get("dataTimeStr"), "type": kind, "code": e.get("warnCode"), "name": e.get("warringName"),
            **explain(model, kind, e.get("warnCode"))}


def _event_key(row: dict) -> tuple:
    return row["code"], row["type"], row["time"]


def today() -> str:
    return dt_util.now().strftime("%Y-%m-%d")


def day_start(day: str) -> datetime:
    return dt_util.start_of_local_day(datetime.strptime(day, "%Y-%m-%d"))


def day_end(day: str) -> datetime:
    return day_start((datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"))


class FelicityCoordinator(DataUpdateCoordinator[dict]):
    """data = {sn: {"device", "live", "energy", "settings"}}. Costs, Energy-dashboard meters and the battery
    runtime come from the day's minute history, recomputed every 5 minutes in the background."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: FelicityClient, tariff: dict) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS))
        self.client = client
        self.tariff = tariff
        self.outlets_on = bool(entry.options.get(CONF_OUTLETS))
        self.outlet_switches: list = []
        self.outlet_sensors: list = []
        self.devices: list[dict] = []
        self.settings: dict[str, dict] = {}        # sn -> current setting values
        self.setting_params: dict[str, dict] = {}  # sn -> {fieldName: definition}
        self.unlocked: set[str] = set()            # inverters whose risky settings are unlocked for a few minutes
        self.costs: dict[str, dict] = {}           # sn -> today's split of grid import by tariff zone
        self.meters: dict[str, dict] = {}          # sn -> today's kWh for the Energy dashboard
        self._energy: dict[str, dict] = {}
        self.events: dict[str, list] = {}          # sn -> alarms and faults of the last 7 days, newest first
        self._event_seen: dict[str, set] = {}      # sn -> keys of the events already announced (last 8 days)
        self._event_months: dict[tuple, tuple] = {}  # (sn, YYYY-MM) -> (final, fetched at, rows)
        self._event_months_lock = asyncio.Lock()
        self._live_kw: dict[str, bool] = {}        # sn -> the live record's powers come in kW (kept once seen)
        self._settings_lock = asyncio.Lock()
        self._profiles_store = Store(hass, 1, f"{DOMAIN}.profiles.{entry.entry_id}")
        self._profiles: dict[str, list[dict]] | None = None  # sn -> [{"name", "rules"}], loaded on first use
        self._history_kw: dict[str, bool] = {}     # sn:day -> False when that day's history powers come in W (scaled)
        self._history: OrderedDict[str, tuple[float, bool, list]] = OrderedDict()
        self._recent: dict[str, list] = {}         # sn -> today's rows, for the battery runtime
        # sn -> [day, {key: value}]: today's totals never go down (stored, so restarts keep it)
        self._floor: dict[str, list] = {}
        self._floor_store = Store(hass, 1, f"{DOMAIN}.floor.{entry.entry_id}")
        # sn -> {day: split by tariff zone}: finished days, kept for the month views (stored once a day is final)
        self.days: dict[str, dict] = {}
        self._days_store = Store(hass, 1, f"{DOMAIN}.days.{entry.entry_id}")
        self.month: dict[str, dict] = {}           # sn -> this month so far (kept days + today)
        self.backfilling = False
        self.outages = None  # OutageCoordinator when an address for DTEK outage schedules is set
        self._backfill_at = 0.0
        self._prev: dict[str, dict] = {}           # sn -> yesterday's last figures, until yesterday is kept
        self._fetching: dict[str, asyncio.Task] = {}
        self._last: dict[str, float] = {}
        self._costs_lock = asyncio.Lock()
        self._costs_task: asyncio.Task | None = None

    @property
    def inverters(self) -> list[str]:
        """Serials of the inverters (devices with a control firmware); battery packs have none."""
        return [d["deviceSn"] for d in self.devices if d.get("masterVersion")]

    def _due(self, what: str, every: int) -> bool:
        if time.time() - self._last.get(what, 0) < every:
            return False
        self._last[what] = time.time()
        return True

    async def _async_update_data(self) -> dict:
        try:
            if not self.devices:
                self.devices = await self.client.devices()
            sns = [d["deviceSn"] for d in self.devices]
            live = {r.get("deviceSn"): r for r in await self.client.latest(sns)} if sns else {}
            for sn, r in live.items():  # live powers are kept in W
                if (kw := powers_in_kw(r)) is not None:
                    self._live_kw[sn] = kw
                if self._live_kw.get(sn):
                    scale_powers(r, 1000)
            if self._due("energy", ENERGY_INTERVAL_SECONDS):
                for sn in sns:
                    try:
                        self._energy[sn] = await self.client.energy_today(sn, today())
                    except FelicityApiError as err:
                        _LOGGER.debug("energy for %s: %s", sn, err)
            # not in the first refresh: it would hold up the set-up (the first poll after it only takes stock)
            if self.data is not None and self._due("events", EVENTS_INTERVAL_SECONDS):
                for sn in self.inverters:
                    try:
                        self._new_events(sn, [event_row(e, self._model(sn)) for e in await self.client.events(sn)])
                    except FelicityAuthError:
                        raise
                    except FelicityApiError as err:
                        _LOGGER.debug("events for %s: %s", sn, err)
            if self._due("settings", SETTINGS_INTERVAL_SECONDS):
                # setting entities are created from the first read: without it, retry the set-up instead
                await self._refresh_settings(strict=self.data is None)
        except FelicityAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FelicityApiError as err:
            if self.data is None:
                self._last.pop("settings", None)
            self.update_interval = timedelta(seconds=30)  # the cloud failed: not every 10 s
            raise UpdateFailed(str(err)) from err
        # (not in the first refresh: the entities, which publish the result, do not exist yet)
        if self.data is not None and self._due("costs", ENERGY_INTERVAL_SECONDS) and not (self._costs_task and not self._costs_task.done()):
            self._costs_task = self.config_entry.async_create_background_task(
                self.hass, self.refresh_costs(), "felicity costs")
        # timed from the end of this update (HA counts the interval from here), on the inverters' uploads: battery
        # packs may upload less often, and their record comes with the same request anyway
        self.update_interval = timedelta(seconds=self._next_poll([live[sn] for sn in self.inverters if sn in live]))
        return {d["deviceSn"]: {"device": d, "settings": self.settings.get(d["deviceSn"], {}),
                                "live": live.get(d["deviceSn"], {}), "energy": self._energy.get(d["deviceSn"], {}),
                                "events": self.events.get(d["deviceSn"], [])}
                for d in self.devices}

    @staticmethod
    def _next_poll(records: list[dict]) -> float:
        """Seconds to the next poll: just after the next upload is due (a record about every UPLOAD_SECONDS), every
        SCAN_INTERVAL_SECONDS while a due upload is late, once a minute for a device that has gone quiet."""
        now, waits = dt_util.utcnow(), []
        for r in records:
            if (t := history_time(r.get("createTime"))) is None:
                continue
            due = (t - now).total_seconds() + UPLOAD_SECONDS + 3
            waits.append(due if due > 0 else SCAN_INTERVAL_SECONDS if due > -300 else UPLOAD_SECONDS)
        return min(max(min(waits, default=SCAN_INTERVAL_SECONDS), 10), UPLOAD_SECONDS)

    async def settings_now(self) -> float:
        """Re-read the inverters' settings for a viewer of the panel unless they were read in the last 30 seconds;
        returns when they were last read in full (an inverter that failed is retried at the next request)."""
        async with self._settings_lock:  # several viewers at once: one read
            if time.time() - self._last.get("settings", 0) > 30:
                if await self._refresh_settings():
                    self._last["settings"] = time.time()
                self.async_update_listeners()
        return self._last.get("settings", 0)

    def _model(self, sn: str) -> str | None:
        return next((d.get("deviceModel") for d in self.devices if d["deviceSn"] == sn), None)

    def _new_events(self, sn: str, rows: list[dict]) -> None:
        """Keep the events and fire EVENT_TYPE once for each new one. Those found by the first poll are only taken
        stock of; announced keys are remembered for 8 days, so a reply that briefly comes back empty does not
        announce the whole week again."""
        first = sn not in self._event_seen
        seen = self._event_seen.setdefault(sn, set())
        for row in reversed(rows):  # oldest first
            if (key := _event_key(row)) not in seen:
                seen.add(key)
                if not first:
                    self.hass.bus.async_fire(EVENT_TYPE, {"sn": sn, **row})
        cutoff = (dt_util.now() - timedelta(days=8)).strftime("%Y-%m-%d")
        self._event_seen[sn] = {k for k in seen if (k[2] or "") >= cutoff}
        self.events[sn] = rows

    async def events_month(self, sn: str, month: str) -> list[dict]:
        """Alarms and faults of a calendar month (YYYY-MM), newest first. The cloud answers at most 7 days per query;
        a month is fetched once when it ended more than a day ago (late uploads), otherwise again after EVENTS_INTERVAL_SECONDS."""
        async with self._event_months_lock:  # the panel and a widget may ask for the same month at once
            return await self._events_month(sn, month)

    async def _events_month(self, sn: str, month: str) -> list[dict]:
        hit = self._event_months.get((sn, month))
        if hit and (hit[0] or time.time() - hit[1] < EVENTS_INTERVAL_SECONDS):
            return hit[2]
        first = date.fromisoformat(f"{month}-01")
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        spans, d = [], first
        while d <= last:
            spans.append((d, min(d + timedelta(days=6), last)))
            d = spans[-1][1] + timedelta(days=1)
        parts = await asyncio.gather(*(self.client.events(sn, f"{a} 00:00:00", f"{b} 23:59:59") for a, b in spans))
        rows = list({_event_key(r): r for p in parts for r in (event_row(e, self._model(sn)) for e in p)}.values())
        rows.sort(key=lambda r: r["time"] or "", reverse=True)
        final = month < (dt_util.now() - timedelta(days=1)).strftime("%Y-%m")
        self._event_months[(sn, month)] = (final, time.time(), rows)
        return rows

    # ---- settings ----

    async def _refresh_settings(self, strict: bool = False) -> bool:
        """Read every inverter's settings; False when one of them failed (strict: raise instead)."""
        ok = True
        for d in self.devices:
            sn = d["deviceSn"]
            if not d.get("masterVersion"):
                continue
            try:
                if sn not in self.setting_params:
                    basic = await self.client.basic(sn)
                    params = await self.client.settings_params(str(basic.get("type")), str(basic.get("subType")))
                    self.setting_params[sn] = {p["fieldName"]: p for p in params if p.get("fieldName")}
                    d["deviceModel"] = basic.get("deviceModel")
                self.settings[sn] = await self.client.settings(sn)
            except FelicityApiError as err:
                if strict:
                    raise
                ok = False
                _LOGGER.debug("settings for %s: %s", sn, err)
            if self.data and sn in self.data:
                self.data[sn]["settings"] = self.settings.get(sn, {})
        return ok

    async def write_setting(self, sn: str, key: str, value, confirmed: bool = False) -> dict:
        """Validate and send one setting, then re-read the settings a few times so the UI shows the new value.

        Risky settings need confirmed=True (the card's second click) or the device's unlock switch."""
        params = self.setting_params.get(sn) or {}
        if key in ECO_RULES:
            v = validate_rule(key, value, (self.settings.get(sn) or {}).get(key), params)
        else:
            v = validate(key, value, params)
        if is_risky(key) and not (confirmed or sn in self.unlocked):
            raise ValueError("risky setting: turn on 'Unlock risky settings' first")
        _LOGGER.info("Felicity %s: set %s = %s", sn, key, v)
        res = await self.client.set_settings(sn, {key: v})
        for delay in (8, 30, 90):
            self.config_entry.async_create_background_task(self.hass, self._reread(delay), f"felicity reread {key}")
        return {"sent": {key: v}, "command": res}

    async def _reread(self, delay: int) -> None:
        await asyncio.sleep(delay)
        await self._refresh_settings()
        self.async_update_listeners()

    def capacity_kwh(self, sn: str) -> float:
        """Battery capacity from the inverter's 'Battery capacity' setting (Ah at 51.2 V nominal)."""
        try:
            return float((self.settings.get(sn) or {}).get("batteryCapacity")) * 51.2 / 1000
        except (TypeError, ValueError):
            return 0.0

    def reserve(self, sn: str) -> tuple[float, str]:
        """Battery reserve for the runtime estimate: set by hand, else the inverter's own low-battery level."""
        if self.tariff.get("battery_reserve") is not None:
            return float(self.tariff["battery_reserve"]), "set in the tariff settings"
        values = self.settings.get(sn) or {}
        for key, label in (("batteryOffGridDischargeDepthSoc", "inverter: off-grid discharge depth"),
                           ("batteryLowVoltageAlarmSoc", "inverter: Low Batt")):
            try:
                return float(values[key]), label
            except (KeyError, TypeError, ValueError):
                continue
        return 20.0, "default"

    # ---- minute history and prices ----

    async def history(self, sn: str, day: str, cache: bool = True) -> list[dict]:
        """Minute history of a local day, each row with "_local" (aware local time). A few days are kept; a day is
        refetched every 2 minutes until it is final (FINAL_AFTER past its end). Callers asking for the same day
        at once share one download; cache=False (bulk look-ups) leaves the kept days alone."""
        key = f"{sn}:{day}"
        hit = self._history.get(key)
        if hit and (hit[1] or time.time() - hit[0] < 120):
            self._history.move_to_end(key)
            return hit[2]
        # the fetch that makes the day final takes it whole, so late or out-of-order uploads are in
        have = hit[2] if hit and dt_util.now() <= day_end(day) + FINAL_AFTER else None
        if key not in self._fetching:
            self._fetching[key] = self.hass.async_create_task(self._download(sn, day, have))
        try:
            rows = await asyncio.shield(self._fetching[key])
        finally:
            self._fetching.pop(key, None)
        if cache:
            self._keep(key, day, rows, whole=not have)
        return rows

    async def _download(self, sn: str, day: str, have: list[dict] | None = None) -> list[dict]:
        """The day's rows. With rows already kept, only the newest page (300 minutes) is fetched and appended when it
        overlaps them; otherwise the whole day is fetched."""
        if have:
            page = (await self.client.history_page(sn, day)).get("dataList") or []
            times = sorted(r.get("deviceDataTime") or "" for r in page)
            newest = have[-1].get("deviceDataTime") or ""
            if times and times[0] <= newest <= times[-1]:  # the page spans the newest kept record: nothing was missed
                kept = {r.get("deviceDataTime") for r in have}
                fresh = [r for r in page if r.get("deviceDataTime") not in kept]  # late arrivals inside the page too
                rows = have + self._prepare(f"{sn}:{day}", fresh, vote=False)
                return sorted(rows, key=lambda r: r.get("deviceDataTime") or "") if fresh else rows
        return self._prepare(f"{sn}:{day}", await self.client.history(sn, day))

    def _prepare(self, key: str, records: list[dict], vote: bool = True) -> list[dict]:
        """Rows with "_local", powers in kW. A whole day votes on its unit (no votes: kept as is); vote=False (rows
        appended to that day) uses the unit found for it."""
        rows = []
        for r in records:
            utc = history_time(r.get("deviceDataTime"))
            if utc is not None:
                r["_local"] = dt_util.as_local(utc)
                rows.append(r)
        if vote:
            votes = [v for v in map(powers_in_kw, rows) if v is not None]
            self._history_kw[key] = not votes or votes.count(False) <= len(votes) / 2
        if not self._history_kw.get(key, True):  # history powers are kept in kW
            for r in rows:
                scale_powers(r, 0.001)
        return rows

    def _keep(self, key: str, day: str, rows: list[dict], whole: bool = True) -> None:
        final = whole and bool(rows) and dt_util.now() > day_end(day) + FINAL_AFTER  # an append never makes it final
        self._history[key] = (time.time(), final, rows)
        self._history.move_to_end(key)
        while len(self._history) > HISTORY_DAYS_CACHED:
            self._history.popitem(last=False)
        return rows

    def tariff_versions(self) -> list[tuple[datetime | None, dict]]:
        return versions(self.config_entry.options.get("tariff_history") or [], self.tariff, dt_util.get_default_time_zone())

    async def _price_points(self, tariff_versions: list, start: datetime) -> dict[str, list[tuple[datetime, float]]]:
        """Recorded prices of the dynamic tariffs' sensors during a day (with the one in force at its start)."""
        entities = {t["price_entity"] for _, t in tariff_versions if t.get("mode") == "entity" and t.get("price_entity")}
        if not entities:
            return {}
        from homeassistant.components.recorder import get_instance, history  # recorder is only an after_dependency
        out = {}
        for entity in entities:
            try:
                states = await get_instance(self.hass).async_add_executor_job(
                    lambda e=entity: history.state_changes_during_period(self.hass, start, start + timedelta(days=1), e,
                                                                         include_start_time_state=True))
            except Exception as err:  # noqa: BLE001 - no recorder / not recorded: that day has no prices
                _LOGGER.debug("price history of %s: %s", entity, err)
                states = {}
            scale = self._price_scale(entity)
            out[entity] = []
            for st in states.get(entity, []):
                try:
                    out[entity].append((max(st.last_changed, start), float(st.state) * scale))
                except (TypeError, ValueError):
                    pass
        return out

    def _price_scale(self, entity: str) -> float:
        """Price entities may be per MWh or per Wh; costs are computed per kWh."""
        st = self.hass.states.get(entity)
        unit = str((st.attributes.get("unit_of_measurement") if st else "") or "")
        return 0.001 if unit.endswith("/MWh") else 1000.0 if unit.endswith("/Wh") else 1.0

    def price_now(self) -> float | None:
        if self.tariff.get("mode") == "entity":
            entity = self.tariff.get("price_entity") or ""
            st = self.hass.states.get(entity)
            try:
                return float(st.state) * self._price_scale(entity) if st else None
            except (TypeError, ValueError):
                return None
        z = zone_at(self.tariff, dt_util.now())
        return z["price"] if z else None

    async def breakdown(self, sn: str, day: str, cache: bool = True, rows: list | None = None) -> tuple[dict, list[dict]]:
        """(the day's grid import / home use / export by tariff zone, its history rows). Every minute is priced
        with the tariff version in force at that minute; zones are those of the version in force at the end."""
        if rows is None:
            rows = await self.history(sn, day, cache=cache)
        start = day_start(day)
        tariff_versions = self.tariff_versions()
        pricer = Pricer(tariff_versions, await self._price_points(tariff_versions, start),
                        fallback=self.price_now() if day == today() else None)
        first, last = pricer.tariff_at(start), pricer.tariff_at(start + timedelta(hours=23, minutes=59))
        b = zone_split(rows, pricer, last["zones"] if last.get("mode") == "schedule" else [],
                       float(last.get("export_price") or 0), last.get("currency", "UAH"))
        since = next((s for s, t in reversed(tariff_versions) if t is last), None)
        b["bands"] = day_bands(last, start.date(), dt_util.get_default_time_zone())
        b["tariff"] = {"zones": last.get("zones"), "currency": last.get("currency"), "same": last == self.tariff,
                       "changed": first is not last, "from": since.isoformat() if since else None}
        return b, rows

    # ---- today's costs, meters and battery runtime ----

    async def async_load_floor(self) -> None:
        self._floor = await self._floor_store.async_load() or {}
        self.days = await self._days_store.async_load() or {}

    # ---- finished days and months ----

    @staticmethod
    def _summary(b: dict) -> dict:
        keys = ("grid_kwh", "grid_cost", "home_kwh", "home_cost", "export_kwh", "export_earned", "solar_kwh", "saved", "battery", "currency")
        # energy bought without a known price (a dynamic price older than the recorder keeps): cost unknown, not 0
        unpriced = any(z["grid_kwh"] > 0.01 and not z["grid_cost"] for z in b["zones"])
        return {"v": DAYS_VERSION, "zones": b["zones"], **{k: b.get(k) for k in keys}, "unpriced": unpriced}

    def _kept(self, sn: str, day: str) -> dict | None:
        """A kept day; one kept soon after its end is computed once more after REVISIT_AFTER (late uploads)."""
        kept = (self.days.get(sn) or {}).get(day)
        if not kept or kept.get("v") != DAYS_VERSION:
            return None
        revisit = day_end(day) + REVISIT_AFTER
        at = dt_util.parse_datetime(kept.get("at") or "")
        return None if (at is None or at < revisit) and dt_util.now() > revisit else kept

    async def day_summary(self, sn: str, day: str, cache: bool = True) -> dict | None:
        """A past day's split by tariff zone, kept once the day is final; None for a day without data."""
        if kept := self._kept(sn, day):
            return None if kept.get("empty") else kept
        rows = await self.history(sn, day, cache=cache)
        summary = self._summary((await self.breakdown(sn, day, rows=rows))[0]) if rows else {"v": DAYS_VERSION, "empty": True}
        if rows:
            summary["hourly_load"] = hourly_load(rows)  # for the battery runtime estimate
        if dt_util.now() > day_end(day) + FINAL_AFTER:
            summary["at"] = dt_util.now().isoformat()
            self.days.setdefault(sn, {})[day] = summary
            self._days_store.async_delay_save(lambda: self.days, 30)
        return None if summary.get("empty") else summary

    async def backfill_days(self) -> None:
        """Keep every finished day back to the first one with data; fetched one by one, gently, in the background."""
        self.backfilling, self._backfill_at = True, time.time()
        try:
            for sn in self.inverters:
                empty, day = 0, dt_util.now().date() - timedelta(days=1)
                for _ in range(BACKFILL_MAX_DAYS):
                    key = day.strftime("%Y-%m-%d")
                    fetch = self._kept(sn, key) is None
                    summary = None
                    for attempt in range(3):
                        try:
                            summary = await self.day_summary(sn, key, cache=False)
                            break
                        except Exception as err:  # noqa: BLE001 - a cloud hiccup: try again, then skip the day
                            _LOGGER.debug("Felicity: backfill of %s for %s failed (%s): %s", key, sn, attempt + 1, err)
                            await asyncio.sleep(60)
                    else:
                        day -= timedelta(days=1)  # skipped; the next run (BACKFILL_EVERY) tries it again
                        continue
                    empty = empty + 1 if summary is None else 0
                    if empty >= BACKFILL_EMPTY_STOP:
                        break
                    day -= timedelta(days=1)
                    if fetch:
                        await asyncio.sleep(BACKFILL_PAUSE)
                self.month[sn] = self.month_summary(sn, today()[:7])
        finally:
            self.backfilling = False
        self.async_update_listeners()

    def month_summary(self, sn: str, month: str) -> dict:
        """A calendar month (YYYY-MM) by tariff zone: its kept days, plus today while it is the current month.
        The savings ("sav") cover finished days only: during a day, grid energy stored in the battery counts as a loss."""
        kept = sorted((d, s) for d, s in (self.days.get(sn) or {}).items()
                      if d.startswith(month) and s.get("v") == DAYS_VERSION and not s.get("empty")
                      and (s["grid_kwh"] > 0.01 or s["home_kwh"] > 0.01))  # records, but no energy: the inverter was off
        done = [s for _, s in kept]
        prev = self._prev.get(sn)  # yesterday until it is kept (the first minutes after midnight)
        if prev and prev["date"].startswith(month) and not (self.days.get(sn) or {}).get(prev["date"]):
            done.append(prev)
        b = self.costs.get(sn)
        out = add_up(done + ([b] if b and b.get("date") == today() and today().startswith(month) else []))
        sav = add_up(done)
        out["sav"] = {k: sav[k] for k in ("home_kwh", "home_cost", "grid_cost", "export_earned", "saved", "battery", "days")}
        out["unpriced"] = sum(1 for s in done if s.get("unpriced"))
        first = datetime.strptime(f"{month}-01", "%Y-%m-%d").date()
        last = min((first.replace(day=28) + timedelta(days=4)).replace(day=1), dt_util.now().date())
        known = {d for d, s in (self.days.get(sn) or {}).items() if d.startswith(month) and s.get("v") == DAYS_VERSION}
        out["missing"] = [d.strftime("%Y-%m-%d") for d in (first + timedelta(days=i) for i in range((last - first).days))
                          if d.strftime("%Y-%m-%d") not in known]
        out["per_day"] = [{"date": d, "grid_kwh": s["grid_kwh"], "grid_cost": s["grid_cost"], "saved": s["saved"],
                           "battery_saved": (s.get("battery") or {}).get("saved"),
                           "zones": {z["id"]: z["grid_kwh"] for z in s["zones"]}} for d, s in kept]
        out["month"], out["currency"] = month, self.tariff.get("currency", "UAH")
        return out

    async def refresh_costs(self) -> None:
        async with self._costs_lock:  # a refresh started with an old tariff finishes before one with the new
            for sn in self.inverters:
                await self._refresh_costs(sn)
        self.async_update_listeners()

    async def _refresh_costs(self, sn: str) -> None:
        day = today()
        old = self.costs.get(sn)
        if old and old.get("date") != day:
            self._prev[sn] = old
        try:
            b, rows = await self.breakdown(sn, day)
            totals = day_totals(rows)
        except Exception as err:  # noqa: BLE001 - costs are extras; never break the live data
            _LOGGER.warning("Felicity: could not compute today's costs for %s: %s", sn, err)
            return
        if not rows:  # nothing for today yet (or a cloud hiccup): keep what was published
            return
        # a daily total that goes down is counted as a meter reset by the Energy dashboard
        d, floor = self._floor.get(sn, (day, {}))
        floor = dict(floor) if d == day else {}

        def keep(key: str, value: float) -> float:
            floor[key] = max(round(value, 3), floor.get(key, 0.0))
            return floor[key]
        for z in b["zones"]:
            z["grid_kwh"], z["grid_cost"] = keep(z["id"], z["grid_kwh"]), keep(f"{z['id']}:cost", z["grid_cost"])
        b["grid_kwh"] = round(sum(z["grid_kwh"] for z in b["zones"]), 3)
        b["grid_cost"] = round(sum(z["grid_cost"] for z in b["zones"]), 2)
        self._floor[sn] = [day, floor]
        self._floor_store.async_delay_save(lambda: self._floor, 10)
        self.costs[sn] = {**b, "date": day}
        self.meters[sn] = {"date": day, **{k: keep(f"meter:{k}", totals[k]) for k in METERS}}
        self._recent[sn] = rows
        yesterday = (dt_util.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if self._kept(sn, yesterday) is None and not self.backfilling:  # keep the day that just ended
            try:
                await self.day_summary(sn, yesterday)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Felicity: could not keep %s for %s: %s", yesterday, sn, err)
        if not self.backfilling and time.time() - self._backfill_at > BACKFILL_EVERY:
            self.config_entry.async_create_background_task(self.hass, self.backfill_days(), "felicity backfill")
        self.month[sn] = self.month_summary(sn, day[:7])

    def runtime_now(self, sn: str) -> dict:
        """Battery runtime with the latest SOC and the home's usual use by hour (the last 7 kept days and today)."""
        live = ((self.data or {}).get(sn) or {}).get("live", {})

        def num(k):
            try:
                return float(live.get(k))
            except (TypeError, ValueError):
                return None
        load = (num("acTotalOutActPower") or 0) + (num("meterPower") or 0)
        reserve, source = self.reserve(sn)
        past = []
        for i in range(7, 0, -1):
            kept = self._kept(sn, (dt_util.now() - timedelta(days=i)).strftime("%Y-%m-%d"))
            if kept and kept.get("hourly_load"):
                past.append(kept["hourly_load"])
        return {**battery_runtime(self._recent.get(sn, []), num("emsSoc"), reserve, self.capacity_kwh(sn),
                                  load if live else None, past=past or None), "reserve_source": source}

    def _plan_inputs(self, sn: str) -> dict:
        """What the planner needs from now to the end of tomorrow: the battery level, the home's usual use and solar by
        hour, prices, announced outages, the inverter's limits and its current rules; {"error"} when not ready."""
        live = ((self.data or {}).get(sn) or {}).get("live", {})
        values, params = self.settings.get(sn) or {}, self.setting_params.get(sn) or {}
        try:
            soc = float(live.get("emsSoc"))
        except (TypeError, ValueError):
            return {"error": "no battery level yet"}
        current = rules_from_settings(values)
        if not current:
            return {"error": "this inverter has no time-of-use rules set"}
        past = []
        for i in range(7, 0, -1):
            kept = self._kept(sn, (dt_util.now() - timedelta(days=i)).strftime("%Y-%m-%d"))
            if kept and kept.get("hourly_load"):
                past.append(kept["hourly_load"])
        profile = hourly_profile(past + ([self._recent[sn]] if self._recent.get(sn) else []))
        if not profile:
            return {"error": "needs a day or two of history to know your usual use"}
        pv = [[0.0, 0] for _ in range(24)]  # usual solar by hour, from the days already downloaded
        for key, (_, _, rows) in list(self._history.items()):
            if key.startswith(f"{sn}:"):
                for r in rows:
                    try:
                        pv[r["_local"].hour][0] += float(r.get("pvTotalPower") or 0) * 1000
                        pv[r["_local"].hour][1] += 1
                    except (TypeError, ValueError):
                        pass
        pv_w = [w / n if n else 0.0 for w, n in pv]

        def num(key, default):
            try:
                return float(values.get(key))
            except (TypeError, ValueError):
                return default
        m = re.search(r"~\s*(\d+)", str((params.get("ecoRule1.power") or {}).get("rang") or ""))
        power_max = float(m.group(1)) if m else 6000.0
        system = System(capacity_kwh=self.capacity_kwh(sn), shutdown_soc=num("batteryShutdownSoc", 10.0),
                        max_discharge_w=min(power_max, num("batteryMaxDischargeCurrent", 120.0) * 51.2))
        if system.capacity_kwh <= 0:
            return {"error": "battery capacity unknown"}
        off = []  # announced outages, as local times
        if self.outages and self.outages.data:
            view = self.outages.view()
            for d in view["days"]:
                if d["source"] != "fact":
                    continue
                start = datetime.fromisoformat(d["date"]).replace(tzinfo=KYIV_TZ)
                for i, s in enumerate(d["slots"]):
                    if s == "off":
                        off.append(dt_util.as_local(start + timedelta(minutes=30 * i)))
        now = dt_util.now()
        t = now.replace(minute=30 * (now.minute // 30), second=0, microsecond=0)
        end = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        flat = self.price_now()

        def price(when):
            z = zone_at(self.tariff, when)
            return z["price"] if z else flat
        steps, prices, outages = [], [], []
        while t < end:
            down = any(o <= t < o + timedelta(minutes=30) for o in off)
            if down and (not outages or outages[-1][1] != len(steps)):
                outages.append([len(steps), len(steps) + 1])
            elif down:
                outages[-1][1] += 1
            steps.append((t.hour + t.minute / 60, profile[t.hour], pv_w[t.hour], not down, 0.5))
            prices.append(price(t))
            t += timedelta(minutes=30)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_prices = [price(tomorrow + timedelta(minutes=30 * i)) for i in range(48)]
        return {"soc": soc, "steps": steps, "prices": prices, "day_prices": day_prices, "current": current,
                "system": system, "reserve": self.reserve(sn)[0], "power_max": power_max, "now": now,
                "charge_w": next((r.power for r in current if r.grid_charge and r.power), 2000.0),
                "max_rules": sum(1 for k in values if k.startswith("ecoRule") and k[7:].isdigit()) or len(current),
                "outages": [tuple(o) for o in outages]}

    async def plan_now(self, sn: str) -> dict:
        """Recommended time-of-use rules from now to the end of tomorrow (planner.plan) for this inverter: the home's
        usual use by hour, its usual solar, the tariff and the announced outages; with the current rules beside it."""
        i = self._plan_inputs(sn)
        if "error" in i:
            return i
        soc, steps, system, reserve, power_max, outages, now = (i[k] for k in (
            "soc", "steps", "system", "reserve", "power_max", "outages", "now"))
        result = await self.hass.async_add_executor_job(lambda: plan(
            start_soc=soc, steps=steps, prices=i["prices"], day_prices=i["day_prices"], current=i["current"],
            system=system, reserve=reserve, charge_w=i["charge_w"], power_max=power_max, max_rules=i["max_rules"],
            outages=outages))
        if "error" in result:
            return result
        if prof.modeful(self.settings.get(sn) or {}):  # IVGM / T-REX: a rule mode instead of the grid-charging flag
            for r in result["rules"]:
                r["ruleMode"] = "1" if r.pop("gridChangingEnable") == "1" else "2"
        result.update(soc=soc, reserve=reserve, currency=self.tariff.get("currency"), capacity_kwh=system.capacity_kwh, power_max=power_max,
                      start=steps[0][0] if steps else None, outages=[
                          {"start": (now.replace(minute=30 * (now.minute // 30), second=0, microsecond=0)
                                     + timedelta(minutes=30 * a)).isoformat(),
                           "end": (now.replace(minute=30 * (now.minute // 30), second=0, microsecond=0)
                                   + timedelta(minutes=30 * b)).isoformat()} for a, b in outages])
        result["current"].pop("soc", None)
        result["best"].pop("soc", None)
        return result

    # ---- schedule profiles ----

    async def _profile_list(self, sn: str) -> list[dict]:
        if self._profiles is None:
            self._profiles = await self._profiles_store.async_load() or {}
        return self._profiles.setdefault(sn, [])

    async def profiles_view(self, sn: str) -> dict:
        """The saved schedules, which one the inverter runs now, and what each costs up to the end of tomorrow."""
        current = self.settings.get(sn) or {}
        now_rules = prof.from_inverter(current)
        items = [dict(p) for p in await self._profile_list(sn)]
        i = self._plan_inputs(sn)
        if "error" not in i:
            cheap = min((p for p in i["day_prices"] if p is not None), default=0)

            def estimate(rules):
                r = evaluate_rules(i["soc"], i["steps"], i["prices"],
                                   rules_from_settings(dict(zip(prof.ECO_RULES, rules))), i["system"], i["reserve"],
                                   i["outages"], cheap)
                return {k: r[k] for k in ("cost", "min_soc", "short_kwh")}
            *estimates, now_estimate = await self.hass.async_add_executor_job(
                lambda: [estimate(r) for r in [*(p["rules"] for p in items), now_rules]])
            for p, e in zip(items, estimates):
                p["estimate"] = e
        else:
            now_estimate = None
        for p in items:
            p["active"] = prof.same(p["rules"], now_rules)
        return {"sn": sn, "segments": prof.segment_count(current), "modeful": prof.modeful(current),
                "current": {"rules": now_rules, "estimate": now_estimate}, "profiles": items,
                "currency": self.tariff.get("currency"), "error": i.get("error")}

    async def save_profile(self, sn: str, name: str, rules: list[dict], old_name: str | None = None) -> None:
        name = (name or "").strip()[:40]
        if not name:
            raise ValueError("give the schedule a name")
        prof.check(rules, self.settings.get(sn) or {})
        items = await self._profile_list(sn)
        items[:] = [p for p in items if p["name"] not in (name, old_name)]
        items.append({"name": name, "rules": [{f: r.get(f) for f in prof.FIELDS if r.get(f) is not None} for r in rules]})
        await self._profiles_store.async_save(self._profiles)

    async def delete_profile(self, sn: str, name: str) -> None:
        items = await self._profile_list(sn)
        items[:] = [p for p in items if p["name"] != name]
        await self._profiles_store.async_save(self._profiles)

    async def apply_profile(self, sn: str, rules: list[dict]) -> dict:
        """Send a whole schedule in one command (as the Felicity app's Send Command), checked against the inverter's
        own limits first; the settings are read again afterwards."""
        current = self.settings.get(sn) or {}
        prof.check(rules, current)
        params = self.setting_params.get(sn) or {}
        content = {k: validate_rule(k, r, current.get(k), params) for k, r in zip(prof.ECO_RULES, rules)}
        _LOGGER.info("Felicity %s: schedule %s", sn, content)
        res = await self.client.set_settings(sn, content)
        for delay in (8, 30, 90):
            self.config_entry.async_create_background_task(self.hass, self._reread(delay), "felicity reread schedule")
        return {"sent": content, "command": res}

    async def set_tariff(self, tariff: dict) -> None:
        """Use a newly saved tariff right away (called by the options update listener)."""
        self.tariff = tariff
        await self.refresh_costs()
