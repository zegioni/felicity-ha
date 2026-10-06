# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""Account set-up (and re-authentication), and the options: Matter outlets, electricity tariff."""
from __future__ import annotations

import re

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import FelicityApiError, FelicityAuthError, FelicityClient
from .const import CONF_OUTLET_LABEL, CONF_OUTLETS, DEFAULT_OUTLET_LABEL, DOMAIN
from .tariff import DEFAULT, PRESETS, remember, validate_tariff

CONF_ACCEPT = "accept_risk"
SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str,
                     vol.Required(CONF_ACCEPT, default=False): bool})


class FelicityConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FelicityOptionsFlow()

    async def _check(self, username: str, password: str) -> str | None:
        """None when the account logs in, else the form error."""
        client = FelicityClient(async_get_clientsession(self.hass), username, password)
        try:
            await client.login()
            await client.devices()
        except FelicityAuthError:
            return "invalid_auth"
        except FelicityApiError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None and not user_input.get(CONF_ACCEPT):
            errors["base"] = "accept_risk"
        elif user_input is not None:
            user_input = {k: v for k, v in user_input.items() if k != CONF_ACCEPT}
            if not (error := await self._check(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])):
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Felicity ({user_input[CONF_USERNAME]})", data=user_input)
            errors["base"] = error
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            if not (error := await self._check(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])):
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]})
            errors["base"] = error
        return self.async_show_form(step_id="reauth_confirm", errors=errors,
                                    data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
                                    description_placeholders={"username": entry.data[CONF_USERNAME]})


DAYS_TEXT = {"1234567": "", "12345": "Mon-Fri", "67": "Sat-Sun", "6": "Sat", "7": "Sun"}


def ranges_to_text(ranges: list) -> str:
    return ", ".join(f"{r[0]}-{r[1]}" + (f" {DAYS_TEXT.get(r[2], r[2])}" if len(r) > 2 else "") for r in ranges)


def text_to_ranges(text: str) -> list:
    """'07:00-11:00, 20:00-22:00 Mon-Fri' -> [["07:00","11:00"], ["20:00","22:00","12345"]]"""
    back = {v.lower(): k for k, v in DAYS_TEXT.items() if v}
    out = []
    for part in filter(None, (p.strip() for p in str(text or "").split(","))):
        m = re.fullmatch(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})\s*(.*)", part)
        if not m:
            raise ValueError(f"'{part}': use HH:MM-HH:MM")
        days = m.group(3).strip().lower().replace("–", "-").replace(" ", "")
        if days and days not in back and not re.fullmatch(r"[1-7]+", days):
            raise ValueError(f"'{part}': days can be Mon-Fri, Sat-Sun, Sat, Sun or weekday numbers 1-7")
        out.append([m.group(1), m.group(2)] + ([back.get(days, days)] if days else []))
    return out


class FelicityOptionsFlow(OptionsFlow):
    """General options and the electricity tariff (the Solar panel's Tariff tab edits the same tariff, with more comfort)."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["general", "tariff"])

    async def async_step_general(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        return self.async_show_form(step_id="general", data_schema=vol.Schema({
            vol.Optional(CONF_OUTLETS, default=self.config_entry.options.get(CONF_OUTLETS, False)): bool,
            vol.Optional(CONF_OUTLET_LABEL, default=self.config_entry.options.get(CONF_OUTLET_LABEL, DEFAULT_OUTLET_LABEL)): str}))

    def _save(self, t: dict) -> dict:
        return remember(self.config_entry.options, t, dt_util.now())

    def _current(self) -> dict:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        return self.config_entry.options.get("tariff") or (coordinator.tariff if coordinator else DEFAULT)

    async def async_step_tariff(self, user_input=None):
        cur = self._current()
        errors = {}
        if user_input is not None:
            preset = user_input.get("preset", "keep")
            raw = dict(cur)
            if preset != "keep":
                raw.update({k: v for k, v in PRESETS[preset].items() if k != "name"})
            else:  # own zones: as many as asked for; new ones start empty and are named on the next page
                n = user_input.get("zone_count") or len(raw.get("zones") or []) or 1
                zones = [dict(z) for z in (raw.get("zones") or [])][:n]
                colors = ["#ff6b6b", "#ffb547", "#5aa9ff", "#3ddc84", "#c08bff", "#2ec4b6", "#ff7eb6", "#ffe066"]
                while len(zones) < n:
                    zones.append({"name": f"Zone {len(zones) + 1}", "price": 0, "color": colors[len(zones)], "ranges": []})
                raw["zones"] = zones
            if preset == "keep" or "currency" not in PRESETS[preset]:
                raw["currency"] = user_input["currency"]
            raw.update(battery_reserve=user_input.get("battery_reserve"),
                       export_price=user_input.get("export_price") or 0, price_entity=user_input.get("price_entity") or "")
            if preset == "dynamic":
                raw["mode"] = "entity"
                try:
                    t = validate_tariff(raw)
                except ValueError as err:
                    errors["base"] = "tariff_invalid"
                    self._err = str(err)
                else:
                    return self.async_create_entry(data=self._save(t))
            else:
                raw["mode"] = "schedule"
                self._draft = raw
                return await self.async_step_zones()
        presets = [selector.SelectOptionDict(value="keep", label="My own zones (names, prices and hours on the next page)")] + [
            selector.SelectOptionDict(value=k, label=v["name"]) for k, v in PRESETS.items()]
        return self.async_show_form(step_id="tariff", errors=errors, description_placeholders={"error": getattr(self, "_err", "")},
            data_schema=vol.Schema({
                vol.Required("preset", default="dynamic" if cur.get("mode") == "entity" else "keep"): selector.SelectSelector(selector.SelectSelectorConfig(options=presets, mode=selector.SelectSelectorMode.DROPDOWN)),
                vol.Required("zone_count", default=max(1, len(cur.get("zones") or []))): vol.All(vol.Coerce(int), vol.Range(min=1, max=8)),
                vol.Required("currency", default=cur.get("currency") or self.hass.config.currency or "UAH"): str,
                vol.Optional("price_entity", description={"suggested_value": cur.get("price_entity") or None}): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number", "number"])),
                vol.Required("export_price", default=cur.get("export_price", 0)): vol.Coerce(float),
                vol.Optional("battery_reserve", description={"suggested_value": cur.get("battery_reserve")}): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            }))

    async def async_step_zones(self, user_input=None):
        draft = self._draft
        cur_name = draft.get("currency", "UAH")
        errors, err_text = {}, ""
        if user_input is not None:
            zones = []
            try:
                for i, z in enumerate(draft["zones"]):
                    zones.append({**z, "name": user_input[f"z{i}_name"], "price": user_input[f"z{i}_price"],
                                  "ranges": text_to_ranges(user_input.get(f"z{i}_hours", ""))})
                t = validate_tariff({**draft, "zones": zones})
            except ValueError as err:
                errors["base"], err_text = "tariff_invalid", str(err)
            else:
                return self.async_create_entry(data=self._save(t))
        schema = {}
        for i, z in enumerate(draft["zones"]):
            schema[vol.Required(f"z{i}_name", default=z["name"])] = str
            schema[vol.Required(f"z{i}_price", default=z["price"])] = vol.Coerce(float)
            schema[vol.Optional(f"z{i}_hours", default=ranges_to_text(z["ranges"]))] = str
        return self.async_show_form(step_id="zones", data_schema=vol.Schema(schema), errors=errors,
                                    description_placeholders={"currency": cur_name, "error": err_text})
