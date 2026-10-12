# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""An event entity per device that fires on each new alarm or fault: a ready trigger for the user's own automations."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, EVENT_TYPE


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(FelicityAlarmEvent(sn) for sn in entry.runtime_data.inverters)


class FelicityAlarmEvent(EventEntity):
    _attr_has_entity_name = True
    _attr_name = "Alarm or fault"
    _attr_icon = "mdi:alert-circle-outline"
    _attr_event_types = ["alarm", "fault"]

    def __init__(self, sn: str) -> None:
        self._sn = sn
        self._attr_unique_id = f"{sn}_alarm_event"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    async def async_added_to_hass(self) -> None:
        @callback
        def fired(event) -> None:
            d = event.data
            if d.get("sn") == self._sn:
                self._trigger_event(d["type"], {k: v for k, v in d.items() if k not in ("sn", "type")})
                self.async_write_ha_state()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, fired))
