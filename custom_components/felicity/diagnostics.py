# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, "deviceSn", "sn", "plantId", "plantName", "wifiSn", "bluetoothSn"}


async def async_get_config_entry_diagnostics(hass, entry):
    return {"entry": async_redact_data(entry.data, TO_REDACT),
            "devices": async_redact_data(list(entry.runtime_data.data.values()), TO_REDACT)}
