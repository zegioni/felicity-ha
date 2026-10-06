# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Shared base for the writable setting entities (number / select / switch on the device page)."""
from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import FelicityApiError
from .const import DOMAIN
from .coordinator import FelicityCoordinator
from .settings import Spec, is_risky, name_of, spec


def writable_keys(coordinator: FelicityCoordinator, sn: str):
    """(key, Spec) for every setting of an inverter that may be written."""
    params = coordinator.setting_params.get(sn) or {}
    for key in coordinator.settings.get(sn) or {}:
        if s := spec(key, params):
            yield key, s


def is_toggle(s: Spec) -> bool:
    return sorted(v.lower() for v in s.options.values()) == ["disable", "enable"]


class FelicitySettingEntity(CoordinatorEntity[FelicityCoordinator]):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FelicityCoordinator, sn: str, key: str, s: Spec) -> None:
        super().__init__(coordinator)
        self._sn, self._key, self._spec = sn, key, s
        self._attr_unique_id = f"{sn}_control_{key}"
        label = name_of(key, coordinator.setting_params.get(sn) or {})
        self._attr_name = f"{label} ⚠" if is_risky(key) else label
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    @property
    def raw(self):
        return (self.coordinator.settings.get(self._sn) or {}).get(self._key)

    async def _write(self, value) -> None:
        try:
            await self.coordinator.write_setting(self._sn, self._key, value)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        except FelicityApiError as err:
            raise HomeAssistantError(f"Inverter rejected the command: {err}") from err
