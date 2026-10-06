# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
DOMAIN = "felicity"
API = "https://open-api.felicitysolar.com"
# RSA public key published in the Felicity Open API docs (login page); the password must be encrypted with it.
PUBLIC_KEY = "MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAK0GDivaRzIKeTmQnAxAYh2LChuHWDp0yHZ0zIvm+Eoi7J+rx7phqR7EtkBDO3HWqAXVkNDeeQaU32P5w1Q4FVUCAwEAAQ=="
SCAN_INTERVAL_SECONDS = 15
ENERGY_INTERVAL_SECONDS = 300
SETTINGS_INTERVAL_SECONDS = 600
CONF_OUTLETS = "matter_outlets"  # option: create the virtual Matter outlets (Home / Battery / Grid)
CONF_OUTLET_LABEL = "matter_label"  # option: label put on the outlet devices, for bridges that pick devices by label
DEFAULT_OUTLET_LABEL = "matterbridge"  # what matterbridge-hass filters on by default
