# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .controls import FelicitySettingEntity, is_toggle, writable_keys


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(FelicitySettingSelect(coordinator, sn, key, s)
                       for sn in coordinator.inverters for key, s in writable_keys(coordinator, sn)
                       if s.options and not is_toggle(s))


class FelicitySettingSelect(FelicitySettingEntity, SelectEntity):
    def __init__(self, coordinator, sn, key, s) -> None:
        super().__init__(coordinator, sn, key, s)
        self._attr_options = list(s.options.values())

    @property
    def current_option(self):
        return self._spec.options.get(str(self.raw))

    async def async_select_option(self, option: str) -> None:
        await self._write(next(k for k, v in self._spec.options.items() if v == option))
