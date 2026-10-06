# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Sensors: live values and the cloud's daily energy of every device, and every inverter setting (read only).

Each value sensor carries {sn, field: "<source>.<api key>"} and each setting sensor {key, sn, group, card, ...}:
the dashboard card finds its data through these attributes, without any configuration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import (PERCENTAGE, EntityCategory, UnitOfApparentPower, UnitOfElectricCurrent,
                                 UnitOfElectricPotential, UnitOfEnergy, UnitOfFrequency, UnitOfPower, UnitOfTemperature)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import history_time
from .const import DOMAIN
from .coordinator import FelicityCoordinator
from .cost import cost_entities
from .settings import ECO_RULES, SKIP, display, eco_rule_summary, is_risky, name_of, placement, spec, unit_of

P, E, V, A, T = SensorDeviceClass.POWER, SensorDeviceClass.ENERGY, SensorDeviceClass.VOLTAGE, SensorDeviceClass.CURRENT, SensorDeviceClass.TEMPERATURE
W, KWH, VOLT, AMP, C = UnitOfPower.WATT, UnitOfEnergy.KILO_WATT_HOUR, UnitOfElectricPotential.VOLT, UnitOfElectricCurrent.AMPERE, UnitOfTemperature.CELSIUS
M, TI = SensorStateClass.MEASUREMENT, SensorStateClass.TOTAL_INCREASING


@dataclass(frozen=True, kw_only=True)
class FelicitySensorDescription(SensorEntityDescription):
    source: str  # "live" (devicesDataHistory latest) or "energy" (deviceDataEnergy, today)


def _s(source, key, name, dc=None, unit=None, sc=M, cat=None):
    return FelicitySensorDescription(key=key, name=name, source=source, device_class=dc,
                                     native_unit_of_measurement=unit, state_class=sc, entity_category=cat)


# when the cloud received the live record; the card's "Updated … ago" counts from it
LAST_DATA = _s("live", "createTime", "Last data", SensorDeviceClass.TIMESTAMP, sc=None, cat=EntityCategory.DIAGNOSTIC)


SENSORS = (
    _s("live", "pvTotalPower", "PV power", P, W),
    _s("live", "pvPower", "PV1 power", P, W),
    _s("live", "pvVolt", "PV1 voltage", V, VOLT),
    _s("live", "pv2Power", "PV2 power", P, W),
    _s("live", "pv2Volt", "PV2 voltage", V, VOLT),
    _s("live", "pvInCurr", "PV1 current", A, AMP),
    _s("live", "pv2InCurr", "PV2 current", A, AMP),
    _s("live", "acTtlInpower", "Grid power", P, W),
    _s("live", "acRInPower", "Grid L1 power", P, W),
    _s("live", "acRInVolt", "Grid voltage", V, VOLT),
    _s("live", "acRInCurr", "Grid current", A, AMP),
    _s("live", "acRInFreq", "Grid frequency", SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ),
    # three-phase models (IVGM 15K/20K, T-REX)
    _s("live", "acSInPower", "Grid L2 power", P, W), _s("live", "acTInPower", "Grid L3 power", P, W),
    _s("live", "acSInVolt", "Grid L2 voltage", V, VOLT),
    _s("live", "acTInVolt", "Grid L3 voltage", V, VOLT),
    _s("live", "acSOutPower", "Backup load L2 power", P, W), _s("live", "acTOutPower", "Backup load L3 power", P, W),
    _s("live", "pv3Power", "PV3 power", P, W), _s("live", "pv3Volt", "PV3 voltage", V, VOLT),
    # T-REX: up to four PV inputs, a second battery, per-phase generator and output values
    _s("live", "pv3InCurr", "PV3 current", A, AMP),
    _s("live", "pv4Power", "PV4 power", P, W), _s("live", "pv4Volt", "PV4 voltage", V, VOLT), _s("live", "pv4InCurr", "PV4 current", A, AMP),
    _s("live", "acSInCurr", "Grid L2 current", A, AMP), _s("live", "acTInCurr", "Grid L3 current", A, AMP),
    _s("live", "acSInFreq", "Grid L2 frequency", SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ),
    _s("live", "acTInFreq", "Grid L3 frequency", SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ),
    _s("live", "acROutPower", "Backup load L1 power", P, W),
    _s("live", "acROutCurr", "Backup load current", A, AMP),
    _s("live", "acSOutCurr", "Backup load L2 current", A, AMP), _s("live", "acTOutCurr", "Backup load L3 current", A, AMP),
    _s("live", "acSOutVolt", "Backup load L2 voltage", V, VOLT), _s("live", "acTOutVolt", "Backup load L3 voltage", V, VOLT),
    _s("live", "acROutFreq", "Backup load frequency", SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ),
    _s("live", "emsSoc2", "Battery 2 SOC", SensorDeviceClass.BATTERY, PERCENTAGE),
    _s("live", "emsPower2", "Battery 2 power", P, W),
    _s("live", "emsVoltage2", "Battery 2 voltage", V, VOLT),
    _s("live", "emsCurrent2", "Battery 2 current", A, AMP),
    _s("live", "genPower", "Generator L1 power", P, W), _s("live", "genPower2", "Generator L2 power", P, W),
    _s("live", "genPower3", "Generator L3 power", P, W),
    _s("live", "genVoltage2", "Generator L2 voltage", V, VOLT), _s("live", "genVoltage3", "Generator L3 voltage", V, VOLT),
    _s("live", "genCurrent", "Generator current", A, AMP), _s("live", "genCurrent2", "Generator L2 current", A, AMP),
    _s("live", "genCurrent3", "Generator L3 current", A, AMP),
    _s("live", "genFrequency", "Generator frequency", SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ),
    _s("live", "emsSoc", "Battery SOC", SensorDeviceClass.BATTERY, PERCENTAGE),
    _s("live", "emsPower", "Battery power", P, W),
    _s("live", "emsVoltage", "Battery voltage", V, VOLT),
    _s("live", "emsCurrent", "Battery current", A, AMP),
    _s("live", "acTotalOutActPower", "Backup load power", P, W),
    _s("live", "acTotalOutAppaPower", "Backup load apparent power", SensorDeviceClass.APPARENT_POWER, UnitOfApparentPower.VOLT_AMPERE),
    _s("live", "acROutVolt", "Backup load voltage", V, VOLT),
    _s("live", "meterPower", "Home load power", P, W),  # IVGM: load behind the grid meter
    _s("live", "totalConsumPower", "Total consumption power", P, W),
    _s("live", "ctPower", "CT power", P, W),
    _s("live", "genTotalPower", "Generator power", P, W),
    _s("live", "genVoltage", "Generator voltage", V, VOLT),
    _s("live", "smartTotalPower", "Smart load power live", P, W),
    _s("live", "smartLoadVolt", "Smart load voltage", V, VOLT),
    _s("live", "microInvTotalPower", "Micro inverter power", P, W),
    _s("live", "devTempMax", "Inverter temperature", T, C),
    _s("live", "devTempMin", "Inverter min temperature", T, C),
    _s("live", "tempMax", "Battery temperature", T, C),
    _s("live", "workMode", "Work mode", sc=None),
    _s("live", "ebatCharTotal", "Battery charged total", E, KWH, TI),
    _s("live", "ebatDischarTotal", "Battery discharged total", E, KWH, TI),
    LAST_DATA,
    *(_s("energy", key, name, E, KWH, TI) for key, name in (
        ("generateEnergy", "PV energy today"), ("gridInput", "Grid import today"), ("feedOutput", "Grid export today"),
        ("batCharEnergy", "Battery charge today"), ("batDisEnergy", "Battery discharge today"),
        ("offGridEnergy", "Backup load energy today"), ("gridTiedEnergy", "Home load energy today"))),
)

# Battery pack (device list entry with bp*Version instead of masterVersion).
BATTERY_SENSORS = (
    _s("live", "battSoc", "SOC", SensorDeviceClass.BATTERY, PERCENTAGE),
    _s("live", "bmsPower", "Power", P, W),
    _s("live", "battVolt", "Voltage", V, VOLT),
    _s("live", "battCurr", "Current", A, AMP),
    _s("live", "tempMax", "Max temperature", T, C),
    _s("live", "tempMin", "Min temperature", T, C),
    _s("live", "bmsState", "BMS state", sc=None),
    LAST_DATA,
)


# workMode codes from the API documentation (T-REX / IVGM)
WORK_MODES = {"0": "Power on", "1": "Standby", "2": "Bypass", "3": "Off-grid", "4": "Fault", "5": "Line",
              "6": "PV charge", "7": "Generator", "8": "Off"}


def _is_battery(device: dict) -> bool:
    return not device.get("masterVersion") and bool(device.get("bpMasterVersion"))


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: FelicityCoordinator = entry.runtime_data

    def reported(v: dict, d: FelicitySensorDescription) -> bool:
        # only what the device actually reports (an empty record, e.g. offline at start-up, creates everything)
        rec = v.get(d.source) or {}
        return not rec or rec.get(d.key) not in (None, "")
    entities: list[SensorEntity] = [FelicitySensor(coordinator, sn, d) for sn, v in coordinator.data.items()
                                    for d in (BATTERY_SENSORS if _is_battery(v["device"]) else SENSORS) if reported(v, d)]
    for sn in coordinator.inverters:
        entities += [FelicitySettingSensor(coordinator, sn, k) for k, x in (coordinator.settings.get(sn) or {}).items()
                     if k not in SKIP and (x or k not in ECO_RULES)]
    async_add_entities(entities + coordinator.outlet_sensors + cost_entities(hass, entry, coordinator))


class FelicitySensor(CoordinatorEntity[FelicityCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: FelicitySensorDescription

    def __init__(self, coordinator: FelicityCoordinator, sn: str, description: FelicitySensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._sn = sn
        self._attr_unique_id = f"{sn}_{description.key}"
        self._attr_extra_state_attributes = {"sn": sn, "field": f"{description.source}.{description.key}"}
        dev = coordinator.data[sn]["device"]
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)}, manufacturer="Felicity Solar",
                                            model=dev.get("deviceModel") or dev.get("deviceType"),
                                            name=f"Felicity {'battery' if _is_battery(dev) else 'inverter'} {sn[-4:]}",
                                            serial_number=sn, sw_version=dev.get("masterVersion") or dev.get("bpMasterVersion"))

    @property
    def native_value(self):
        d = self.entity_description
        v = self.coordinator.data[self._sn][d.source].get(d.key)
        if v in (None, ""):
            return None
        if d.device_class == SensorDeviceClass.TIMESTAMP:
            return history_time(v)
        if d.key == "workMode":
            return WORK_MODES.get(str(v).split(".")[0], v)
        if d.state_class is None:
            return v
        try:
            return float(v)
        except (TypeError, ValueError):
            return None


class FelicitySettingSensor(CoordinatorEntity[FelicityCoordinator], SensorEntity):
    """One inverter setting, read only. Writable ones are hidden by default: their number / select / switch
    control shows the same value."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cog-outline"

    def __init__(self, coordinator: FelicityCoordinator, sn: str, key: str) -> None:
        super().__init__(coordinator)
        self._sn, self._key = sn, key
        self._attr_unique_id = f"{sn}_setting_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})
        params = coordinator.setting_params.get(sn) or {}
        self._spec = spec(key, params)
        if key in ECO_RULES:
            self._attr_name = f"Time of use rule {key[7:]}"
            self._attr_icon = "mdi:clock-outline"
        else:
            self._attr_name = name_of(key, params)
            self._attr_native_unit_of_measurement = unit_of(key, params)
            self._attr_entity_registry_visible_default = self._spec is None

    @property
    def _value(self):
        return (self.coordinator.settings.get(self._sn) or {}).get(self._key)

    @property
    def native_value(self):
        v = self._value
        if v in (None, "", {}):
            return None
        if self._key in ECO_RULES:
            return eco_rule_summary(v)
        out = display(self._key, v, self.coordinator.setting_params.get(self._sn) or {})
        if self._attr_native_unit_of_measurement:
            try:
                return float(out)
            except (TypeError, ValueError):
                return None
        return out

    @property
    def extra_state_attributes(self):
        tab, card, _, order = placement(self._key)
        attrs = {"key": self._key, "sn": self._sn, "group": tab, "card": card, "order": order,
                 "writable": self._spec is not None, "risky": is_risky(self._key)}
        if self._spec and self._spec.options:
            attrs["options"] = self._spec.options
        elif self._spec:
            attrs["range"] = [f"{self._spec.low:g}", f"{self._spec.high:g}"]
            attrs["step"] = self._spec.step
        v = self._value
        if isinstance(v, dict):
            attrs.update(v)
        else:
            attrs["raw"] = v
        return attrs
