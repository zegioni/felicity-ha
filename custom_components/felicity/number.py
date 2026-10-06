# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode

from .controls import FelicitySettingEntity, writable_keys


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(FelicitySettingNumber(coordinator, sn, key, s)
                       for sn in coordinator.inverters for key, s in writable_keys(coordinator, sn) if not s.options)


class FelicitySettingNumber(FelicitySettingEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, sn, key, s) -> None:
        super().__init__(coordinator, sn, key, s)
        self._attr_native_min_value, self._attr_native_max_value = s.low, s.high
        self._attr_native_step = s.step
        self._attr_native_unit_of_measurement = s.unit

    @property
    def native_value(self):
        try:
            return float(self.raw)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self._write(value)
