# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Power outage schedules of the DTEK grid companies (Ukraine), for an address.

DTEK has no public API: its "shutdowns" page carries the address book, the week's plan per queue (черга) and the
announced schedule for today and tomorrow, and asks /ua/ajax for the houses of a street. The page sits behind a bot
filter (Incapsula) that sometimes answers with a challenge instead: then the last data is kept and retried later."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_OUTAGES = "outages"  # option: {"region", "city", "street", "house"}
REGIONS = {  # key -> (name, site)
    "oem": ("Odesa region (DTEK Odesa Grids)", "https://www.dtek-oem.com.ua"),
    "dnem": ("Dnipropetrovsk region (DTEK Dnipro Grids)", "https://www.dtek-dnem.com.ua"),
    "kem": ("Kyiv (DTEK Kyiv Electric Networks)", "https://www.dtek-kem.com.ua"),
    "krem": ("Kyiv region (DTEK Kyiv Regional Grids)", "https://www.dtek-krem.com.ua"),
}
KYIV = ZoneInfo("Europe/Kyiv")  # the schedules are in Ukrainian time, whatever the home's time zone
CHECK_EVERY = 15 * 60       # seconds: "has the schedule changed?" (a small request)
PAGE_EVERY = 6 * 3600       # seconds: the whole page again (about 1 MB: session, plan, address book)
HOUSE_EVERY = 24 * 3600     # seconds: the queue of the house
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.6",
}
# an hour's status -> its two half hours: "on" (power), "off" (outage), "maybe" (outage possible)
_HALVES = {"yes": ("on", "on"), "no": ("off", "off"), "maybe": ("maybe", "maybe"), "first": ("off", "on"),
           "second": ("on", "off"), "mfirst": ("maybe", "on"), "msecond": ("on", "maybe")}


class OutageError(Exception):
    """The site did not answer as expected."""


class OutageBlocked(OutageError):
    """The site's bot filter answered instead of the page."""


def _js_object(html: str, name: str):
    m = re.search(re.escape(name) + r"\s*=\s*(\{.*?\})\s*(?:DisconSchedule|</script>)", html, re.S)
    return json.loads(m.group(1)) if m else None


class DtekClient:
    """One DTEK site, with its own cookies (the page's session and CSRF token are needed for the AJAX calls)."""

    def __init__(self, hass: HomeAssistant, region: str) -> None:
        if region not in REGIONS:
            raise OutageError(f"unknown region {region}")
        self.site = REGIONS[region][1]
        self._session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
        self._csrf: str | None = None

    async def page_retrying(self, tries: int = 3) -> dict:
        """page(), tried again a few seconds later when the bot filter answers (it lets most second tries through)."""
        for n in range(tries):
            try:
                return await self.page()
            except OutageBlocked:
                if n == tries - 1:
                    raise
                await asyncio.sleep(4 * (n + 1))
        raise OutageBlocked(self.site)

    async def page(self) -> dict:
        """{"streets": {city: [street]}, "preset": week plan, "fact": announced days} from the shutdowns page."""
        try:
            async with self._session.get(f"{self.site}/ua/shutdowns", headers=_HEADERS,
                                         timeout=aiohttp.ClientTimeout(total=60)) as resp:
                html = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise OutageError(f"{self.site}: {err!r}") from err
        streets = _js_object(html, "DisconSchedule.streets")
        if streets is None:
            raise OutageBlocked(f"{self.site}: the bot filter answered instead of the schedule page")
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        self._csrf = m.group(1) if m else None
        return {"streets": streets, "preset": _js_object(html, "DisconSchedule.preset") or {},
                "fact": _js_object(html, "DisconSchedule.fact") or {}}

    async def _ajax(self, form: dict, unchanged_ok: bool = False) -> dict:
        """One AJAX call; when the bot filter answers, once more with a fresh session from the page."""
        try:
            return await self._ajax_once(form, unchanged_ok)
        except OutageBlocked:
            await self.page()
            return await self._ajax_once(form, unchanged_ok)

    async def _ajax_once(self, form: dict, unchanged_ok: bool) -> dict:
        if not self._csrf:
            await self.page()
        headers = {**_HEADERS, "Accept": "application/json, text/javascript, */*; q=0.01",
                   "X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": self._csrf or "",
                   "Referer": f"{self.site}/ua/shutdowns"}
        try:
            async with self._session.post(f"{self.site}/ua/ajax", data=form, headers=headers,
                                          timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise OutageError(f"{self.site}: {err!r}") from err
        try:
            answer = json.loads(text)
        except ValueError:
            self._csrf = None  # a challenge or an expired session: the next call starts from the page again
            raise OutageBlocked(f"{self.site}: no JSON from /ua/ajax") from None
        if unchanged_ok and isinstance(answer, dict) and answer.get("result") is False:
            return {}  # checkDisconUpdate: nothing new
        if not isinstance(answer, dict) or not answer.get("result"):
            raise OutageError(f"{self.site}: {str(answer)[:200]}")
        return answer

    async def houses(self, city: str, street: str) -> dict[str, dict]:
        """{house: {"sub_type_reason": [queue], "type", "start_date", "end_date", ...}} of a street."""
        answer = await self._ajax({"method": "getHomeNum", "data[0][name]": "city", "data[0][value]": city,
                                   "data[1][name]": "street", "data[1][value]": street})
        return answer.get("data") or {}

    async def check(self, update: str) -> dict:
        """{"fact", "preset"} when the schedule changed since `update`, else {}."""
        answer = await self._ajax({"method": "checkDisconUpdate", "update": update}, unchanged_ok=True)
        return {k: answer[k] for k in ("fact", "preset") if answer.get(k)}


def queues_of(house: dict | None) -> list[str]:
    return [q for q in (house or {}).get("sub_type_reason") or [] if q]


def _day_slots(hours: dict) -> list[str]:
    """48 half hours of a day from {"1": status, … "24": status} (hour 1 = 00:00–01:00)."""
    out = []
    for h in range(1, 25):
        out += _HALVES.get(str(hours.get(str(h), "")), ("unknown", "unknown"))
    return out


def schedule(preset: dict, fact: dict, queue: str | None, now: datetime) -> dict:
    """Today and tomorrow for a queue as DTEK announced them ("fact"), and what follows from that right now. Without
    an announced schedule there is power by the schedule: the week's plan (where outages are always "possible") is
    kept apart, for display only once outages are announced."""
    now = now.astimezone(KYIV)
    days, plan = [], {}
    for wd, hours in ((preset.get("data") or {}).get(queue) or {}).items():
        plan[wd] = _day_slots(hours)
    for offset in (0, 1):
        day = (now + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        announced = ((fact.get("data") or {}).get(str(int(day.timestamp()))) or {}).get(queue)
        days.append({"date": day.date().isoformat(), "source": "fact" if announced else None,
                     "slots": _day_slots(announced) if announced else []})
    flat = [(datetime.fromisoformat(d["date"]).replace(tzinfo=KYIV) + timedelta(minutes=30 * i), s, d["source"])
            for d in days for i, s in enumerate(d["slots"])]
    cur = next((x for x in flat if x[0] <= now < x[0] + timedelta(minutes=30)), None)

    def run(states: set[str]) -> tuple[datetime | None, datetime | None]:
        """The next (or current) stretch of these states, within today and tomorrow."""
        start = end = None
        for t, s, _ in flat:
            if t + timedelta(minutes=30) <= now:
                continue
            if s in states and start is None:
                start = t
            elif s not in states and start is not None:
                end = t
                break
        if start is not None and end is None:
            end = flat[-1][0] + timedelta(minutes=30)
        return start, end

    off = run({"off"})
    maybe = run({"maybe"})
    announced = any(d["source"] for d in days)
    return {"queue": queue, "announced": announced,
            "now": cur[1] if cur else "on" if queue else None, "now_source": cur[2] if cur else None,
            "next_off": [t.isoformat() if t else None for t in off],
            "next_maybe": [t.isoformat() if t else None for t in maybe],
            "days": days, "plan": plan, "updated": fact.get("update") or preset.get("updateFact"),
            "labels": preset.get("time_type") or {}, "queue_names": preset.get("sch_names") or {}}


class OutageCoordinator(DataUpdateCoordinator[dict]):
    """The address's schedule: a small "changed?" request every 15 minutes, the page every 6 hours, the house's queue
    daily. The last good answer is stored, so a blocked site or a restart still shows the schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: dict) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} outages",
                         update_interval=timedelta(seconds=CHECK_EVERY))
        self.address = address
        self.region_name = REGIONS.get(address.get("region"), ("?",))[0]
        self.client = DtekClient(hass, address["region"])
        self._store = Store(hass, 1, f"{DOMAIN}.outages.{entry.entry_id}")
        self._saved: dict = {}  # {"preset", "fact", "house", "page_at", "house_at"}
        self.blocked = False
        self.error: str | None = None

    async def async_load(self) -> None:
        saved = await self._store.async_load() or {}
        if saved.get("address") == self.address:
            self._saved = saved

    async def _async_update_data(self) -> dict:
        s, now = self._saved, time.time()
        try:
            if not s.get("preset") or now - s.get("page_at", 0) > PAGE_EVERY:
                page = await self.client.page_retrying(2)
                s.update(preset=page["preset"], fact=page["fact"], page_at=now)
            elif changed := await self.client.check((s.get("fact") or {}).get("update") or ""):
                s.update(changed)
            if "house" not in s or now - s.get("house_at", 0) > HOUSE_EVERY:
                houses = await self.client.houses(self.address["city"], self.address["street"])
                if self.address["house"] not in houses:
                    raise OutageError(f"house {self.address['house']} is not in DTEK's list for this street")
                s.update(house=houses[self.address["house"]], house_at=now)
            self.blocked, self.error = False, None
            s["checked_at"] = now  # the last time the site answered (DTEK's own "update" is when it changed the schedule)
            await self._store.async_save({**s, "address": self.address})
        except OutageError as err:
            self.blocked, self.error = isinstance(err, OutageBlocked), str(err)
            _LOGGER.debug("outages: %s", err)
            if not s.get("preset"):
                raise UpdateFailed(str(err)) from err
        return dict(s)

    def view(self) -> dict:
        """The schedule as of now (computed on every read: the current half hour moves on between updates)."""
        s = self.data or {}
        house = s.get("house") or {}
        q = queues_of(house)
        out = schedule(s.get("preset") or {}, s.get("fact") or {}, q[0] if q else None, datetime.now(KYIV))
        checked = s.get("checked_at")
        out.update(queues=q, address=self.address, region=self.region_name, blocked=self.blocked, error=self.error,
                   checked=datetime.fromtimestamp(checked, KYIV).isoformat() if checked else None,
                   site=self.client.site,
                   outage={k: house.get(k) for k in ("type", "sub_type", "start_date", "end_date") if house.get(k)})
        return out


# ---- entities ----

STATES = ["on", "off", "maybe"]


class _OutageEntity(CoordinatorEntity[OutageCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: OutageCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_outages_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_outages")},
                                            name="Power outages", manufacturer="DTEK", model=coordinator.region_name,
                                            configuration_url=f"{coordinator.client.site}/ua/shutdowns")

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data)


class OutageQueueSensor(_OutageEntity):
    """The queue (черга) of the address, e.g. 4.2; the address and the data's state in the attributes."""

    _attr_icon = "mdi:home-lightning-bolt-outline"

    def __init__(self, coordinator: OutageCoordinator) -> None:
        super().__init__(coordinator, "queue", "Outage queue")

    @property
    def native_value(self):
        q = self.coordinator.view()["queues"]
        return ", ".join(x.replace("GPV", "") for x in q) if q else None

    @property
    def extra_state_attributes(self):
        v = self.coordinator.view()
        return {"felicity": "outages", "address": v["address"], "region": v["region"], "updated": v["updated"],
                "site_blocked": v["blocked"], "error": v["error"], "current_outage": v["outage"] or None}


class OutageNowSensor(_OutageEntity):
    """Power by the schedule right now: on / off / maybe (an outage is possible)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATES
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator: OutageCoordinator) -> None:
        super().__init__(coordinator, "now", "Power by schedule")

    @property
    def native_value(self):
        now = self.coordinator.view()["now"]
        return now if now in STATES else None

    @property
    def extra_state_attributes(self):
        v = self.coordinator.view()
        return {"source": v["now_source"], "today": v["days"][0], "tomorrow": v["days"][1]}


class OutageTimeSensor(_OutageEntity):
    """Start or end of the next outage (announced, "off") or of the next possible one ("maybe")."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: OutageCoordinator, which: str, edge: int, name: str, icon: str) -> None:
        super().__init__(coordinator, f"{which}_{'start' if edge == 0 else 'end'}", name)
        self._which, self._edge = which, edge
        self._attr_icon = icon

    @property
    def native_value(self):
        t = self.coordinator.view()[self._which][self._edge]
        return datetime.fromisoformat(t) if t else None


def outage_entities(coordinator: OutageCoordinator) -> list[SensorEntity]:
    return [OutageQueueSensor(coordinator), OutageNowSensor(coordinator),
            OutageTimeSensor(coordinator, "next_off", 0, "Next outage", "mdi:power-plug-off-outline"),
            OutageTimeSensor(coordinator, "next_off", 1, "Next outage end", "mdi:power-plug-outline"),
            OutageTimeSensor(coordinator, "next_maybe", 0, "Next possible outage", "mdi:help-circle-outline"),
            OutageTimeSensor(coordinator, "next_maybe", 1, "Next possible outage end", "mdi:help-circle-outline")]
