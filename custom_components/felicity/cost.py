# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Tariff entities: current price and zone (for the Energy dashboard and automations), today's grid import and
cost per tariff zone, money saved, the daily Energy-dashboard meters, and how long the battery would carry the home."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfTime
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import FelicityCoordinator, today
from .tariff import DYNAMIC_ZONE, next_change, zone_at


def zone_signature(t: dict) -> tuple:
    """Entities depend on these; any other tariff change is applied without reloading."""
    return (t.get("mode"), t.get("price_entity") if t.get("mode") == "entity" else "", tuple(z["id"] for z in t.get("zones") or []))


def cost_entities(hass, entry, coordinator: FelicityCoordinator) -> list[SensorEntity]:
    zones = coordinator.tariff["zones"] if coordinator.tariff.get("mode") == "schedule" else [DYNAMIC_ZONE]
    out: list[SensorEntity] = [TariffPriceSensor(coordinator), TariffZoneSensor(coordinator)]
    keep = set()
    for sn in coordinator.inverters:
        for z in zones:
            out += [ZoneImportSensor(coordinator, sn, z["id"]), MonthZoneImportSensor(coordinator, sn, z["id"])]
            keep |= {f"{sn}_tariff_import_{z['id']}", f"{sn}_tariff_month_import_{z['id']}"}
        out += [GridCostSensor(coordinator, sn), SavedSensor(coordinator, sn), BatteryRuntimeSensor(coordinator, sn)]
        out += [MonthMoneySensor(coordinator, sn, key, name, icon, path) for key, name, icon, path in MONTH_MONEY]
        out += [MeterSensor(coordinator, sn, key, name, icon) for key, name, icon in METERS]
    reg = er.async_get(hass)  # zones removed from the tariff: drop their sensors
    for e in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ("_tariff_import_" in e.unique_id or "_tariff_month_import_" in e.unique_id) and e.unique_id not in keep:
            reg.async_remove(e.entity_id)
    return out


class _TariffEntity(CoordinatorEntity[FelicityCoordinator], SensorEntity):
    """Price / zone of the account's tariff: no inverter data needed, so always available."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FelicityCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_tariff_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{entry_id}_tariff")}, name="Electricity tariff",
                                            manufacturer="Felicity Solar integration", model="Tariff")

    @property
    def available(self) -> bool:
        return True

    @property
    def _t(self) -> dict:
        return self.coordinator.tariff

    def _zone(self) -> dict | None:
        return zone_at(self._t, dt_util.now()) if self._t.get("mode") == "schedule" else DYNAMIC_ZONE

    @property
    def extra_state_attributes(self):
        z = self._zone() or {}
        attrs = {"felicity": "tariff", "currency": self._t.get("currency"), "mode": self._t.get("mode"),
                 "zone_id": z.get("id"), "zone": z.get("name"), "color": z.get("color"), "price": self.coordinator.price_now()}
        if self._t.get("mode") == "entity":
            attrs["price_entity"] = self._t.get("price_entity")
        elif nc := next_change(self._t, dt_util.now()):
            attrs.update(next_change=nc[0].isoformat(), next_zone=nc[1]["name"], next_price=nc[1]["price"])
        return attrs


class TariffPriceSensor(_TariffEntity):
    """Price of grid energy right now; pick it in Energy > Grid consumption > 'Use an entity with current price'."""

    _attr_name = "Current price"
    _attr_icon = "mdi:cash-clock"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "price")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._t.get("mode") == "entity":  # follow the dynamic price at once, not at the next poll
            @callback
            def changed(_event):
                self.async_write_ha_state()
            self.async_on_remove(async_track_state_change_event(self.hass, [self._t["price_entity"]], changed))

    @property
    def native_unit_of_measurement(self):
        return f"{self._t.get('currency', 'UAH')}/{UnitOfEnergy.KILO_WATT_HOUR}"

    @property
    def native_value(self):
        p = self.coordinator.price_now()
        return None if p is None else round(p, 4)


class TariffZoneSensor(_TariffEntity):
    _attr_name = "Zone"
    _attr_icon = "mdi:clock-time-eight-outline"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "zone")

    @property
    def options(self):
        return [z["name"] for z in self._t["zones"]] if self._t.get("mode") == "schedule" else [DYNAMIC_ZONE["name"]]

    @property
    def native_value(self):
        z = self._zone()
        return z["name"] if z else None


class _InverterSensor(CoordinatorEntity[FelicityCoordinator], SensorEntity):
    """A number of one inverter from today's history (available once it has been computed for today)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FelicityCoordinator, sn: str, key: str) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}_tariff_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    @property
    def _b(self) -> dict | None:
        b = self.coordinator.costs.get(self._sn)
        return b if b and b["date"] == today() else None

    @property
    def available(self) -> bool:
        return super().available and self._b is not None


class _DailyTotal(_InverterSensor):
    """A kWh total of today; it never goes down within the day (the coordinator keeps a stored floor)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2


class ZoneImportSensor(_DailyTotal):
    """Grid energy bought today in one tariff zone; add each one in Energy > Grid consumption with its zone price."""

    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator, sn, zone_id: str) -> None:
        super().__init__(coordinator, sn, f"import_{zone_id}")
        self._zid = zone_id

    @property
    def name(self):
        zone = next((z for z in self.coordinator.tariff.get("zones") or [DYNAMIC_ZONE] if z["id"] == self._zid), DYNAMIC_ZONE)
        return f"Grid import today ({zone['name']})"

    @property
    def _z(self) -> dict | None:
        return next((z for z in (self._b or {}).get("zones", []) if z["id"] == self._zid), None)

    @property
    def native_value(self):
        z = self._z
        return z["grid_kwh"] if z else None

    @property
    def extra_state_attributes(self):
        z = self._z or {}
        return {"felicity": "tariff_import", "sn": self._sn, "zone_id": self._zid, "price": z.get("price"),
                "cost": z.get("grid_cost"), "currency": self.coordinator.tariff.get("currency")}


METERS = (("solar", "Energy: solar today", "mdi:solar-power"),
          ("battery_charge", "Energy: battery charged today", "mdi:battery-arrow-up"),
          ("battery_discharge", "Energy: battery discharged today", "mdi:battery-arrow-down"),
          ("grid_export", "Energy: sold to grid today", "mdi:transmission-tower-export"))


class MeterSensor(_DailyTotal):
    """Daily kWh for the Energy dashboard (the cloud's own daily totals sometimes drop to 0, which the dashboard
    would count as a meter reset)."""

    def __init__(self, coordinator, sn, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator, sn, f"meter_{key}")
        self._key = key
        self._attr_name, self._attr_icon = name, icon

    @property
    def _m(self) -> dict | None:
        m = self.coordinator.meters.get(self._sn)
        return m if m and m["date"] == today() else None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._m is not None

    @property
    def native_value(self):
        return self._m[self._key] if self._m else None

    @property
    def extra_state_attributes(self):
        return {"felicity": "meter", "sn": self._sn}


class _Money(_InverterSensor):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    @property
    def native_unit_of_measurement(self):
        return self.coordinator.tariff.get("currency", "UAH")

    @property
    def last_reset(self):
        return dt_util.start_of_local_day()


class GridCostSensor(_Money):
    """What today's grid energy cost; usable in Energy > Grid consumption > 'Use an entity tracking the total costs'."""

    _attr_name = "Grid cost today"
    _attr_icon = "mdi:cash-minus"

    def __init__(self, coordinator, sn) -> None:
        super().__init__(coordinator, sn, "grid_cost")

    @property
    def native_value(self):
        return self._b["grid_cost"] if self._b else None

    @property
    def extra_state_attributes(self):
        b = self._b or {}
        return {"felicity": "tariff_cost", "sn": self._sn, "grid_kwh": b.get("grid_kwh"),
                "zones": {z["name"]: {"kwh": z["grid_kwh"], "cost": z["grid_cost"], "price": z["price"]} for z in b.get("zones", [])}}


class SavedSensor(_Money):
    """What the home's use today would have cost from the grid, minus what was paid (plus export earnings)."""

    _attr_name = "Saved today"
    _attr_icon = "mdi:piggy-bank-outline"

    def __init__(self, coordinator, sn) -> None:
        super().__init__(coordinator, sn, "saved")

    @property
    def native_value(self):
        return self._b["saved"] if self._b else None

    @property
    def extra_state_attributes(self):
        b = self._b or {}
        return {"felicity": "tariff_saved", "sn": self._sn, "home_kwh": b.get("home_kwh"), "home_cost": b.get("home_cost"),
                "grid_cost": b.get("grid_cost"), "export_kwh": b.get("export_kwh"), "export_earned": b.get("export_earned")}


class BatteryRuntimeSensor(_InverterSensor):
    """Hours the battery would carry the home down to the reserve at the last half hour's average use."""

    _attr_name = "Battery runtime"
    _attr_icon = "mdi:battery-clock-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, sn) -> None:
        super().__init__(coordinator, sn, "battery_runtime")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self):
        return self.coordinator.runtime_now(self._sn)["hours"]

    @property
    def extra_state_attributes(self):
        r = self.coordinator.runtime_now(self._sn)
        attrs = {"felicity": "battery_runtime", "sn": self._sn, **r}
        if r["hours"] is not None:
            attrs["until"] = (dt_util.now() + timedelta(hours=r["hours"])).isoformat(timespec="minutes")
        return attrs


class _MonthSensor(CoordinatorEntity[FelicityCoordinator], SensorEntity):
    """A number of this calendar month so far: the kept days plus today (available once today is computed)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FelicityCoordinator, sn: str, key: str) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}_tariff_month_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, sn)})

    @property
    def _m(self) -> dict | None:
        m = self.coordinator.month.get(self._sn)
        return m if m and m.get("month") == today()[:7] else None

    @property
    def available(self) -> bool:
        return super().available and self._m is not None

    @property
    def last_reset(self):
        return dt_util.start_of_local_day().replace(day=1)


class MonthZoneImportSensor(_MonthSensor):
    """Grid energy bought this month in one tariff zone (compare with the bill)."""

    _attr_icon = "mdi:calendar-month"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, sn, zone_id: str) -> None:
        super().__init__(coordinator, sn, f"import_{zone_id}")
        self._zid = zone_id

    @property
    def name(self):
        zone = next((z for z in self.coordinator.tariff.get("zones") or [DYNAMIC_ZONE] if z["id"] == self._zid), DYNAMIC_ZONE)
        return f"Grid import this month ({zone['name']})"

    @property
    def _z(self) -> dict | None:
        return next((z for z in (self._m or {}).get("zones", []) if z["id"] == self._zid), None)

    @property
    def native_value(self):
        return self._z["grid_kwh"] if self._z else (0.0 if self._m else None)

    @property
    def extra_state_attributes(self):
        z, m = self._z or {}, self._m or {}
        return {"felicity": "tariff_month_import", "sn": self._sn, "zone_id": self._zid, "cost": z.get("grid_cost"),
                "average_price": z.get("price"), "days": m.get("days"), "currency": m.get("currency")}


# (key, name, icon, path into the month summary)
# grid cost includes today; savings cover finished days only (during a day, energy stored in the battery is a loss)
MONTH_MONEY = (("grid_cost", "Grid cost this month", "mdi:cash-minus", ("grid_cost",)),
               ("saved", "Saved this month", "mdi:piggy-bank-outline", ("sav", "saved")),
               ("battery_saved", "Battery saved this month", "mdi:battery-heart-variant", ("sav", "battery", "saved")))


class MonthMoneySensor(_MonthSensor):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, sn, key: str, name: str, icon: str, path: tuple) -> None:
        super().__init__(coordinator, sn, key)
        self._attr_name, self._attr_icon, self._path = name, icon, path

    @property
    def native_unit_of_measurement(self):
        return self.coordinator.tariff.get("currency", "UAH")

    @property
    def native_value(self):
        v = self._m
        for k in self._path:
            v = (v or {}).get(k)
        return v

    @property
    def extra_state_attributes(self):
        m = self._m or {}
        attrs = {"felicity": f"tariff_month_{self._path[-1]}", "sn": self._sn, "month": m.get("month"), "days": m.get("days")}
        if self._path[0] == "sav":
            attrs["finished_days"] = (m.get("sav") or {}).get("days")
        if "battery" in self._path:
            attrs.update({k: ((m.get("sav") or {}).get("battery") or {}).get(k) for k in ("in_kwh", "in_cost", "out_kwh", "out_value")})
        return attrs
