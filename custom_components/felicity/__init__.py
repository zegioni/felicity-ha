# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Felicity Solar inverters and batteries through the Felicity cloud (Open API), with the "Solar" sidebar panel,
dashboard cards, settings control, electricity tariffs and the Energy dashboard set-up."""
from __future__ import annotations

import hashlib
import logging
import pathlib

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FelicityClient
from .const import CONF_OUTLET_LABEL, CONF_OUTLETS, DEFAULT_OUTLET_LABEL, DOMAIN
from .coordinator import FelicityCoordinator
from .cost import zone_signature
from .energy import after_tariff_saved
from .outlets import label_outlets, outlet_entities
from .tariff import default_for
from .views import views

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PANEL_PATH = "felicity-solar"
STATIC_URL = "/felicity_static"
CARD = pathlib.Path(__file__).parent / "frontend" / "felicity-flow-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Once per HA start, for all accounts: the HTTP API and the cards' files."""
    for view in views(hass):
        hass.http.register_view(view)
    await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(CARD.parent), cache_headers=False)])
    return True


async def _register_frontend(hass: HomeAssistant) -> None:
    """The cards on every dashboard and the "Solar" panel (again after the last account was removed and re-added)."""
    if hass.data.get(DOMAIN, {}).get("frontend"):
        return
    hass.data.setdefault(DOMAIN, {})["frontend"] = True
    version = await hass.async_add_executor_job(lambda: hashlib.md5(CARD.read_bytes()).hexdigest()[:8])
    url = f"{STATIC_URL}/{CARD.name}?v={version}"
    await _register_lovelace_resource(hass, url)
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL_PATH, webcomponent_name="felicity-panel", sidebar_title="Solar",
        sidebar_icon="mdi:solar-power-variant", module_url=url, require_admin=False, config={})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = FelicityClient(async_get_clientsession(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = FelicityCoordinator(hass, entry, client, entry.options.get("tariff") or default_for(hass.config.currency))
    await coordinator.async_load_floor()
    await coordinator.async_config_entry_first_refresh()
    await _register_frontend(hass)
    if coordinator.outlets_on:
        for sn in coordinator.inverters:
            switches, sensors = outlet_entities(coordinator, sn)
            coordinator.outlet_switches += switches
            coordinator.outlet_sensors += sensors
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # past days by tariff zone, for the month views (fetched gently once; later only the day that just ended)
    entry.async_create_background_task(hass, coordinator.backfill_days(), "felicity backfill")
    if coordinator.outlets_on:
        label_outlets(hass, coordinator.inverters, entry.options.get(CONF_OUTLET_LABEL, DEFAULT_OUTLET_LABEL))
    if entry.options.get("tariff_history"):  # a tariff was saved: keep the Energy dashboard in step
        entry.async_create_background_task(hass, after_tariff_saved(hass, coordinator), "felicity energy sync")
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A saved tariff is applied here (from the card and from the options flow alike): live when only prices, hours
    or the currency changed; with a reload when zones, the price sensor or the Matter outlets changed."""
    coordinator: FelicityCoordinator = entry.runtime_data
    tariff = entry.options.get("tariff") or coordinator.tariff
    if bool(entry.options.get(CONF_OUTLETS)) != coordinator.outlets_on or zone_signature(tariff) != zone_signature(coordinator.tariff):
        await hass.config_entries.async_reload(entry.entry_id)
        return
    if coordinator.outlets_on:
        label_outlets(hass, coordinator.inverters, entry.options.get(CONF_OUTLET_LABEL, DEFAULT_OUTLET_LABEL))
    if tariff != coordinator.tariff:
        await coordinator.set_tariff(tariff)
    if entry.options.get("tariff_history"):  # also when a tariff was saved unchanged (e.g. the defaults, first time)
        await after_tariff_saved(hass, coordinator)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The last account removed: take the panel and the cards' resource away too."""
    if [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]:
        return
    frontend.async_remove_panel(hass, PANEL_PATH)
    hass.data.setdefault(DOMAIN, {})["frontend"] = False
    resources = hass.data["lovelace"].resources
    for item in list(resources.async_items()):
        if str(item.get("url", "")).startswith(f"{STATIC_URL}/"):
            await resources.async_delete_item(item["id"])


async def _register_lovelace_resource(hass: HomeAssistant, url: str) -> None:
    """Make the cards available on every dashboard: a Lovelace module resource kept at the current file version.
    (An early extra JS module is not enough: the cards must be defined after the frontend has started.)"""
    try:
        resources = hass.data["lovelace"].resources
        await resources.async_get_info()  # loads the storage collection
        mine = [r for r in resources.async_items() if str(r.get("url", "")).startswith(f"{STATIC_URL}/")]
        if not mine:
            await resources.async_create_item({"res_type": "module", "url": url})
        elif mine[0].get("url") != url:
            await resources.async_update_item(mine[0]["id"], {"res_type": "module", "url": url})
    except Exception as err:  # noqa: BLE001 - YAML-mode dashboards have no resource collection to write
        _LOGGER.info("Felicity: no Lovelace resource (%s); loading the cards as extra JS instead", err)
        frontend.add_extra_js_url(hass, url)
