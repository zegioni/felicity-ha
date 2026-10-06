# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .controls import FelicitySettingEntity, is_toggle, writable_keys

UNLOCK_SECONDS = 300


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    entities = []
    for sn in coordinator.inverters:
        entities.append(FelicityRiskyUnlock(coordinator, sn))
        entities += [FelicitySettingSwitch(coordinator, sn, key, s) for key, s in writable_keys(coordinator, sn)
                     if s.options and is_toggle(s)]
    async_add_entities(entities + coordinator.outlet_switches)


class FelicitySettingSwitch(FelicitySettingEntity, SwitchEntity):
    def __init__(self, coordinator, sn, key, s) -> None:
        super().__init__(coordinator, sn, key, s)
        self._on = next(k for k, v in s.options.items() if v.lower() == "enable")
        self._off = next(k for k, v in s.options.items() if v.lower() == "disable")

    @property
    def is_on(self):
        r = self.raw
        return None if r in (None, "") else str(r) == self._on

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(self._on)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(self._off)


class FelicityRiskyUnlock(SwitchEntity):
    """While on (max 5 minutes), this inverter's risky settings can be changed from the device page."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:lock-open-alert"
    _attr_name = "Unlock risky settings (5 min)"
    _attr_should_poll = False

    def __init__(self, coordinator, sn) -> None:
        self.coordinator, self._sn = coordinator, sn
        self._attr_unique_id = f"{sn}_unlock_risky"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})
        self._cancel = None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(lambda: self._relock(None))

    @property
    def is_on(self) -> bool:
        return self._sn in self.coordinator.unlocked

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.unlocked.add(self._sn)
        if self._cancel:
            self._cancel()
        self._cancel = async_call_later(self.hass, UNLOCK_SECONDS, self._relock)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._relock(None)
        self.async_write_ha_state()

    @callback
    def _relock(self, _now) -> None:
        self.coordinator.unlocked.discard(self._sn)
        if self._cancel:
            self._cancel()
            self._cancel = None
        if _now is not None:  # timer fired
            self.async_write_ha_state()
