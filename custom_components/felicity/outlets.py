# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Optional "Matter outlets": three virtual devices (Home / Battery / Grid), each a switch + power + energy sensor.

Meant to be exposed through a Matter bridge (e.g. matterbridge) as smart plugs with energy monitoring: while the switch
is on the sensors mirror the inverter, while it is off they report 0. The switch never controls the inverter.
matterbridge picks sensors by state_class + device_class and merges them into one plug by their entity ids, so the
energy sensors stay total_increasing and the ids stay switch.felicity_<id> / sensor.felicity_<id>_power/_energy.
(Switching an outlet off and on is therefore counted as a meter reset in HA's own statistics of that sensor.)"""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# id -> (name, power from the live record, energy keys of today's record (kWh, summed))
OUTLETS = {
    "home": ("Home", lambda live: _f(live.get("acTotalOutActPower")) + _f(live.get("meterPower")), ("offGridEnergy", "gridTiedEnergy")),
    "battery": ("Battery", lambda live: max(0.0, -_f(live.get("emsPower"))), ("batDisEnergy",)),
    "grid": ("Grid", lambda live: max(0.0, _f(live.get("acTtlInpower"))), ("gridInput",)),
}


def outlet_entities(coordinator, sn: str) -> tuple[list, list]:
    switches, sensors = [], []
    for oid in OUTLETS:
        sw = FelicityOutletSwitch(sn, oid)
        switches.append(sw)
        sensors += [FelicityOutletPower(coordinator, sn, oid, sw), FelicityOutletEnergy(coordinator, sn, oid, sw)]
    return switches, sensors


def _device(sn: str, oid: str) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, f"{sn}_outlet_{oid}")}, name=f"Felicity {OUTLETS[oid][0]}",
                      manufacturer="Felicity Solar", model="Matter outlet", via_device=(DOMAIN, sn))


class FelicityOutletSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:power-socket-eu"

    def __init__(self, sn: str, oid: str) -> None:
        self._attr_unique_id = f"{sn}_outlet_{oid}"
        self._attr_device_info = _device(sn, oid)
        self._attr_is_on = True
        self._listeners: list = []

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state != "off"
            for cb in self._listeners:  # the sensors may have been written before the state was restored
                cb()

    def _changed(self) -> None:
        self.async_write_ha_state()
        for cb in self._listeners:
            cb()

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self._changed()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self._changed()


class _OutletSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, sn: str, oid: str, switch: FelicityOutletSwitch) -> None:
        super().__init__(coordinator)
        self._sn, self._oid, self._switch = sn, oid, switch
        self._attr_device_info = _device(sn, oid)
        switch._listeners.append(self._on_switch)

    def _on_switch(self) -> None:
        if self.hass:
            self.async_write_ha_state()

    @property
    def _data(self) -> dict:
        return self.coordinator.data[self._sn]


class FelicityOutletPower(_OutletSensor):
    _attr_name = "Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator, sn, oid, switch) -> None:
        super().__init__(coordinator, sn, oid, switch)
        self._attr_unique_id = f"{sn}_outlet_{oid}_power"

    @property
    def native_value(self):
        if not self._switch.is_on:
            return 0
        return round(OUTLETS[self._oid][1](self._data.get("live") or {}))


class FelicityOutletEnergy(_OutletSensor):
    _attr_name = "Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, sn, oid, switch) -> None:
        super().__init__(coordinator, sn, oid, switch)
        self._attr_unique_id = f"{sn}_outlet_{oid}_energy"

    @property
    def native_value(self):
        if not self._switch.is_on:
            return 0
        energy = self._data["energy"]
        values = [energy.get(k) for k in OUTLETS[self._oid][2] if energy.get(k) not in (None, "")]
        return round(sum(_f(v) for v in values), 2) if values else None  # no record yet: unknown, not 0
