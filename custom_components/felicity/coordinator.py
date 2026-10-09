# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Polling of the Felicity cloud (live data, daily energy, settings), setting writes, and the daily cost pipeline."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import FelicityApiError, FelicityAuthError, FelicityClient, history_time, powers_in_kw, scale_powers
from .const import CONF_OUTLETS, DOMAIN, ENERGY_INTERVAL_SECONDS, SCAN_INTERVAL_SECONDS, SETTINGS_INTERVAL_SECONDS
from .daystats import add_up, battery_runtime, day_totals, zone_split
from .settings import ECO_RULES, is_risky, validate, validate_rule
from .tariff import Pricer, day_bands, versions, zone_at

_LOGGER = logging.getLogger(__name__)

HISTORY_DAYS_CACHED = 8              # one day of minute history is about 4 MB
FINAL_AFTER = timedelta(minutes=15)  # the cloud uploads a day's last minutes a little after midnight
METERS = ("solar", "grid_export", "battery_charge", "battery_discharge")
DAYS_VERSION = 6                     # bump when a day summary changes, so kept days are computed again
BACKFILL_PAUSE = 10                  # seconds between two past days fetched in the background
BACKFILL_EMPTY_STOP = 31             # past days without data in a row: the start of the history is reached
BACKFILL_MAX_DAYS = 92               # about three months back
BACKFILL_EVERY = 6 * 3600            # seconds; fills in days a cloud hiccup skipped
REVISIT_AFTER = timedelta(hours=12)  # a day kept sooner after its end is computed once more (late uploads)


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
        self._live_kw: dict[str, bool] = {}        # sn -> the live record's powers come in kW (kept once seen)
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
            if self._due("settings", SETTINGS_INTERVAL_SECONDS):
                # setting entities are created from the first read: without it, retry the set-up instead
                await self._refresh_settings(strict=self.data is None)
        except FelicityAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FelicityApiError as err:
            if self.data is None:
                self._last.pop("settings", None)
            raise UpdateFailed(str(err)) from err
        # (not in the first refresh: the entities, which publish the result, do not exist yet)
        if self.data is not None and self._due("costs", ENERGY_INTERVAL_SECONDS) and not (self._costs_task and not self._costs_task.done()):
            self._costs_task = self.config_entry.async_create_background_task(
                self.hass, self.refresh_costs(), "felicity costs")
        return {d["deviceSn"]: {"device": d, "settings": self.settings.get(d["deviceSn"], {}),
                                "live": live.get(d["deviceSn"], {}), "energy": self._energy.get(d["deviceSn"], {})}
                for d in self.devices}

    # ---- settings ----

    async def _refresh_settings(self, strict: bool = False) -> None:
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
                _LOGGER.debug("settings for %s: %s", sn, err)
            if self.data and sn in self.data:
                self.data[sn]["settings"] = self.settings.get(sn, {})

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
        if key not in self._fetching:
            self._fetching[key] = self.hass.async_create_task(self._download(sn, day))
        try:
            rows = await asyncio.shield(self._fetching[key])
        finally:
            self._fetching.pop(key, None)
        if cache:
            self._keep(key, day, rows)
        return rows

    async def _download(self, sn: str, day: str) -> list[dict]:
        rows = []
        for r in await self.client.history(sn, day):
            utc = history_time(r.get("deviceDataTime"))
            if utc is not None:
                r["_local"] = dt_util.as_local(utc)
                rows.append(r)
        votes = [v for v in map(powers_in_kw, rows) if v is not None]
        if votes and votes.count(False) > len(votes) / 2:  # history powers are kept in kW
            for r in rows:
                scale_powers(r, 0.001)
        return rows

    def _keep(self, key: str, day: str, rows: list[dict]) -> None:
        self._history[key] = (time.time(), bool(rows) and dt_util.now() > day_end(day) + FINAL_AFTER, rows)
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
        """Battery runtime with the latest SOC and the last half hour's average home use."""
        live = ((self.data or {}).get(sn) or {}).get("live", {})

        def num(k):
            try:
                return float(live.get(k))
            except (TypeError, ValueError):
                return None
        load = (num("acTotalOutActPower") or 0) + (num("meterPower") or 0)
        reserve, source = self.reserve(sn)
        return {**battery_runtime(self._recent.get(sn, []), num("emsSoc"), reserve, self.capacity_kwh(sn),
                                  load if live else None), "reserve_source": source}

    async def set_tariff(self, tariff: dict) -> None:
        """Use a newly saved tariff right away (called by the options update listener)."""
        self.tariff = tariff
        await self.refresh_costs()
