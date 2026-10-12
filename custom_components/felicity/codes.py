# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""What an alarm (W) or fault (F) code means and what to do, from the IVAM user manual (rev. 1.0, 2025-08,
section 7). The cloud sends the same numbers as warnCode. Other models have their own tables: only IVAM is mapped."""
from __future__ import annotations

_RESTART = "Restart the unit; if it comes back, contact Felicity."
_LOAD = "Check that the load is within the rated power. " + _RESTART
_PV_V = "Check the string voltage, reduce the number of PV modules in series. " + _RESTART
_PARALLEL = "Check the parallel cables. " + _RESTART

ALARMS = {
    "21": ("BMS communication alarm", "Battery and inverter do not talk in lithium mode: check the BMS cable and the battery."),
    "22": ("Low battery", "Battery voltage or SOC is too low: the battery should be charged."),
    "33": ("Grid abnormal", "Check the voltage, frequency and phase sequence at the grid input."),
    "37": ("Grid phase loss", "A grid phase is missing: check the wiring."),
    "38": ("Grid reversed", "Three-phase parallel system: fix the grid wiring to clear it."),
    "48": ("Backup overload", "The load on the backup output is too high: reduce it."),
    "57": ("Generator abnormal", "Check the voltage, frequency and phase sequence at the generator input."),
    "61": ("Generator phase loss", "A generator phase is missing: check the wiring."),
    "62": ("Generator reversed", "Three-phase parallel system: fix the generator wiring to clear it."),
    "67": ("Generator overload", "Check that the backup load is within the generator's rating."),
    "83": ("Heatsink over-temperature derating", "The heatsink is too hot: the inverter reduces power. Check ventilation."),
    "86": ("Fan failed", "Check that the fan works."),
}
FAULTS = {
    "1": ("PV1 over voltage", _PV_V), "2": ("PV2 over voltage", _PV_V),
    "5": ("PV1 over current", "PV1 current is too high: check the string. " + _RESTART),
    "6": ("PV2 over current", "PV2 current is too high: check the string. " + _RESTART),
    "9": ("PV1 reverse connection", "Check the PV1 wiring. " + _RESTART),
    "10": ("PV2 reverse connection", "Check the PV2 wiring. " + _RESTART),
    "18": ("PV1 short circuit", "Check the PV1 wiring."), "19": ("PV2 short circuit", "Check the PV2 wiring."),
    "25": ("Battery over voltage", "Check the battery voltage and that the battery cables are tight. " + _RESTART),
    "26": ("Battery over voltage (hardware)", "Check the battery voltage and that the battery cables are tight. " + _RESTART),
    "27": ("Battery over current", _RESTART), "28": ("Battery over current (hardware)", _RESTART),
    "30": ("LLC over current", _RESTART), "31": ("LLC over current (hardware)", _RESTART),
    "32": ("BMS communication fault", "Check the BMS cable. " + _RESTART),
    "37": ("LLC soft start failure", _RESTART),
    "41": ("DC bus over voltage", _PV_V), "42": ("DC bus over voltage (hardware)", _PV_V),
    "43": ("DC bus low voltage", _RESTART),
    "49": ("Inverter over current", _LOAD), "50": ("Inverter over current (hardware)", _LOAD),
    "51": ("Inverter soft start failure", _RESTART),
    "52": ("Inverter DC voltage component", _RESTART), "53": ("Inverter DC current component", _RESTART),
    "54": ("Inverter over voltage", _RESTART), "55": ("Inverter low voltage", _LOAD),
    "56": ("Inverter output short circuit", "Check the backup output wiring. " + _RESTART),
    "57": ("Grid overload", _LOAD), "58": ("Backup overload", _LOAD),
    "65": ("Heatsink over temperature", "Check the room temperature and ventilation; switch off for 15 minutes and restart."),
    "66": ("Ambient over temperature", "Check the room temperature and ventilation; switch off for 15 minutes and restart."),
    "67": ("DSP communication fault", _RESTART), "68": ("DSP and MCU communication fault", _RESTART),
    "69": ("EEPROM fault", _RESTART), "70": ("Leakage current sensor fault", _RESTART),
    "71": ("Leakage current fault", _RESTART), "72": ("Grid relay fault", _RESTART),
    "73": ("Grid relay short", _RESTART), "74": ("Inverter relay fault", _RESTART), "76": ("Generator relay fault", _RESTART),
    "77": ("PV insulation fault", "PV insulation resistance is too low: check the PV connections and the PE (earth) cable."),
    "78": ("Grid connected to the backup port", "Check the backup wiring. " + _RESTART),
    "79": ("Generator port wrongly connected", "In smart-load mode a generator or grid is connected to the GEN port: check the wiring. " + _RESTART),
    "81": ("Auxiliary power fault", _RESTART), "82": ("Temperature sensor (NTC) open", _RESTART),
    "86": ("System parameters changed", "Grid or battery settings changed: the system restarts by itself."),
    "88": ("Parallel: other model", "Check whether another inverter is in fault. " + _RESTART),
    "89": ("Parallel CAN communication", _PARALLEL), "90": ("Parallel master lost", _PARALLEL),
    "91": ("Parallel zero-sync signal lost", _PARALLEL),
    "92": ("Parallel firmware mismatch", "All inverters need the same firmware version."),
    "93": ("Parallel settings mismatch", "Check the parallel cables and that the firmware versions match."),
    "94": ("Parallel ID conflict", "Each inverter needs its own CAN ID and one master per phase."),
    "95": ("Parallel PWM sync fault", _PARALLEL),
    "96": ("Parallel system wiring", "Check the grid wiring and phase sequence. " + _RESTART),
}


def explain(model: str | None, kind: str, code) -> dict:
    """{"label": "W57 Generator abnormal", "advice": …} from the manual, or {} when the model or code is not known."""
    if not str(model or "").upper().startswith("IVAM"):
        return {}
    table, letter = (FAULTS, "F") if kind == "fault" else (ALARMS, "W")
    hit = table.get(str(code).lstrip("0") or "0")
    return {"label": f"{letter}{code} {hit[0]}", "advice": hit[1]} if hit else {}
