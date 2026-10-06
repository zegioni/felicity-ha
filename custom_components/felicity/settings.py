# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
"""The inverter's remote-control settings: where they appear (laid out like the Felicity app), how they are named,
and what may be written. Ranges, options and units come from the inverter's own definitions
(/openApi/cmd/deviceSetting/params: {fieldName: {"rang", "unit", "definition"}})."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

ECO_RULES = [f"ecoRule{i}" for i in range(1, 11)]  # time-of-use rules: dict values with their own sub-definitions
SKIP = {"deviceSn"}

# Never written from Home Assistant: irreversible, turns the inverter off, or no safe encoding is known
# (touEffectiveWeek is reported as a list of day names but defined as a bitmask).
WRITE_BLOCKED = {"factoryReset", "remoteOnOffEnable", "remoteShutDown", "remoteOutputOnOffControl", "clearEnergyStorageLog",
                 "clearEventLog", "clearARCFault", "outputNPERelayClosure", "calibrateBATCurrent1Offset",
                 "calibrateBATCurrent2Offset", "touEffectiveWeek", "deviceTimeSetting", "afciModule", "rsdModule"}
_BLOCKED_PREFIX = ("calibrate",)  # factory calibration factors (T-REX phase*Calib, IVEM calibrate*)

# Changing these can stop charging/output, harm the battery or break grid compliance.
# The card asks for a second click (Confirm); device-page entities need the "Unlock risky settings" switch.
_PROTECTION = ("gfci", "isoDetection", "antiIslanding", "neutralGround", "isoCheck", "leakageCurrent", "solarArcFault",
               "activeIslanding", "signalIsland", "bmsComm")
# grid-code curves, trip values and ride-through (IVGM, T-REX): set by the installer for the local grid code
_GRIDCODE = ("overVoltage", "underVoltage", "overFrequency", "underFrequency", "pfPower", "puCurve", "quCurve", "fpCurve",
             "fpUnder", "fpOver", "fpRestore", "fpRecovery", "fpCharge", "lowVoltage", "highVoltage", "onGrid",
             "gridOver", "gridUnder", "gridNormalConnection", "gridReconnection", "grid10Min", "hvrt", "lvrt", "ppf", "pq",
             "uq", "upf", "upDerating", "fg", "fpResponse", "fpExit", "rocof", "ofMidpoint", "ufMidpoint",
             "upperFrequencyReconnection", "lowerFrequencyReconnection", "upperVoltageReconnection", "exitOverF",
             "stopF", "minCos", "quMinCos", "qtFilter", "reactivePower", "constantReactivePower", "powerFactorGiven",
             "runningActivePower", "runningReactivePower", "turnOnRamp", "turnOffRamp", "zeroCurrent", "hardSoftLimit",
             "g100", "drm", "dispatchActivePower")
_PHASE_CALIB = re.compile(r"^phase[A-Z]\w*Calib$")
RISKY = {
    "batteryMode", "lithiumProtocol", "batteryCapacity", "batteryChargedVoltage", "batteryFloatingChargedVoltage",
    "equalizationV", "equalizationDays", "equalizationHours", "batteryShutdownVoltage", "batteryRestartOutputVoltage",
    "batteryLowVoltageAlarmVoltage", "battResistance", "temperatureCoefficientOfBattery", "bmsCommunicationFailureEnabled",
    "gridStandardCode", "acOutputRatedVoltage", "acOutputRatedFrequency", "gridOverVoltageVoltage2", "gridUnderVoltageVoltage2",
    "gridImmediatelyOff", "peakInputCurrentPowerGrid", "inputPowerLimit", "operatedMode", "invOffGrid", "atsEnable",
    "dspParallelEnable", "parallelPhase", "masterSlaverSetting", "machineId", "ctRatio", "meterSelection", "setMeterPhase",
    "exmeterForCT", "smartPortModeEnable", "genConnectGridEnable", "genForceRunningEnable", "mpptMultiPoint",
    "faultClearanceAndRestart", "batteryShutdownSoc", "batteryMaxChargedCurrent", "batteryMaxDischargeCurrent",
    # IVGM
    "batteryModel", "batteryModules", "batteryOnGridDischargeDepthSoc", "batteryOffGridDischargeDepthSoc",
    "batteryOffGridRecoveryDepthSoc", "noBmsOnGridBatteryCutOffVoltage", "noBmsOffGridBatteryCutOffVoltage",
    "noBmsOffGridBatteryRestartVoltage", "zeroExportFunction", "gridPowerUnbalanceEnable", "gridWaveformDetectionMode",
    "powerFactor", "fixQPencent", "pvParallelSetting", "parallelEnable", "bmsHeartbeat", "autoTestEnable",
    "commOffLineEnable", "deratingByVoltageEnable", "additionalEcoEnable",
    # IVEM / IVPM / T-REX
    "outputVoltage", "outputMode", "frequency", "batCutOffVoltage", "overLoadReset",
    "batChargedVoltage", "batFloatVoltage", "chargingLowBatVoltage", "dischargingHighBatVoltage", "batteryRestartAcOutput",
    "batteryEqualization", "batteryEqualizedTime", "batteryEqualizedTimeout", "equalizationActivatedImmediately",
    "equalizationInterval", "enableBatteryTemperatureCompensation", "disableFloatCharge", "prohibitFloatChargingEnable",
    "forbitLimitBatCurr", "maxChargedCurrent", "maxGridChargedCurrent", "maxChargingCurrent", "maxDischargeCurrentBattery",
    "maxSolarChargeCurrent", "batteryParallelEnable", "batAllInOne", "dischargeCutOffSOC", "mainsInputMode",
    "offGridVoltageCompensation", "atsFunction", "singlePhaseA", "iTSystem", "asymmetricPhaseFeedingEnable",
    "acCoupleToGridOrLoad", "acCoupleFrequencyHigh", "maxAcLimit", "gridPhaseType", "gridConnectionWaitTime",
    "zeroExportModeSelection", "gridMeter2", "electricMeterBaudRate", "ctSelfAdaptive", "forCTPhaseSequenceDetection",
    "bmsCommunicationFailureShutdownEnable", "bmsCommunicationProtocolSelection", "smartPort",
}


def is_risky(key: str) -> bool:
    return key in RISKY or key.startswith(_GRIDCODE + _PROTECTION + ("genOver", "genUnder"))


def is_blocked(key: str) -> bool:
    return key in WRITE_BLOCKED or key.startswith(_BLOCKED_PREFIX) or bool(_PHASE_CALIB.match(key))


# Definitions that are wrong in the API (label/options of another field): (range, unit) used instead.
RANGE_OVERRIDE = {"batteryMaxDischargeCurrent": ("0~135", "A")}
# Names for keys the Felicity app does not show (the rest come from APP_LAYOUT or the API's definition).
_NAMES = {"smartLoadOpenBatterySoc": "Smart load end discharge SOC", "smartLoadCloseBatterySoc": "Smart load start discharge SOC",
          "smartloadOutputPower": "Smart load power"}
UNITS = {"AH": "Ah", "HZ": "Hz", "HOURS": "h", "DAYS": "d", "MS": "ms", "MOHMS": "mΩ", "SEC": "s"}

# Layout copied from the Felicity app (device page -> settings tabs). (tab, card, [(key, label)])
APP_LAYOUT = [
    ("Mode settings", "Mode settings", [("applicationMode", "Application Mode")]),
    ("Basic Setup", "Basic Setup", [("buzzerEnable", "Beep"), ("lcdBacklightEnable", "Auto Dim"), ("remoteOnOffEnable", "Remote Control")]),
    ("Batt Setting", "Battery Setting 1", [("batteryMode", "Battery Mode"), ("batteryCapacity", "Capacity"),
                                          ("batteryMaxChargedCurrent", "Max Charge"), ("batteryMaxDischargeCurrent", "Max Discharge")]),
    # the API's labels of genChargeEnable / gridChargeEnable are swapped; these are the app's
    ("Batt Setting", "Battery Setting 2", [("genChargeEnable", "Grid Charge"), ("gridStartSignal", "Grid Signal"),
                                          ("gridChargeCurrent", "Grid Charge Current"), ("gridAutoStartChargeSoc", "Grid Start Charge"),
                                          ("gridAutoExitChargeSoc", "Grid End Charge"), ("gridChargeEnable", "GEN Charge"),
                                          ("genStartSignalEnable", "GEN Signal"), ("genForceRunningEnable", "GEN Force"),
                                          ("genChargeCurrent", "Gen Charge Current"), ("genAutoStartChargeSoc", "GEN Start Charge"),
                                          ("genAutoExitChargeSoc", "GEN End Charge")]),
    ("Batt Setting", "Battery Setting 3", [("batteryShutdownSoc", "Shut Down"), ("batteryLowVoltageAlarmSoc", "Low Batt"),
                                          ("batteryRestartOutputSoc", "Restart")]),
    ("Grid Setting", "Grid Settings / Standard", [("gridStandardCode", "Grid Mode"), ("acOutputRatedVoltage", "AC Output Rated Voltage"),
                                                 ("acOutputRatedFrequency", "Inv Output Frequency"),
                                                 ("peakInputCurrentPowerGrid", "Input Current Limit"), ("inputPowerLimit", "Input Power Limit")]),
    ("Gen Setting", "GEN Port Use", [("smartPortModeEnable", "Port Type"), ("genConnectGridEnable", "Gen Connect to Grid"),
                                     ("genRatePower", "GEN Rated Power")]),
    ("Work Mode Setting", "Work Mode Setting 1", [("operatedMode", "Work Mode Setting"), ("zeroExportHysteresisPower", "Zero-Export Power"),
                                                  ("maxSellingPower", "Max-Sell Power"), ("gridPeakShavingPower", "Grid Peak Shaving Power"),
                                                  ("zeroExportToLoadEnable", "PV Sell Enable"), ("energyPriority", "Output Priority"),
                                                  ("maxPvInputPower", "Max Solar Power"), ("gridPeakShavingEnable", "Grid Peak Shaving")]),
    ("Work Mode Setting", "Work Mode Setting 2", [("timeOfUseEnable", "Time Of Use"), ("touEffectiveWeek", "Week Of Use")]
     + [(k, f"Rule {k[7:]}") for k in ECO_RULES]),
    ("Work Mode Setting", "Work Mode Setting 3", [("invOffGrid", "SBU")]),
    ("Profession Setting", "Profession Setting 1", [("bmsCommunicationFailureEnabled", "BMS_Err_Stop"), ("atsEnable", "ATS Enable"),
                                                    ("lowNoiseMode", "Low Noise Mode"), ("lowPowerMode", "Low Power Mode"),
                                                    ("powerDisplayTypeSetting", "Power/Current Display Type")]),
    ("Profession Setting", "Profession Setting 2", [("mpptMultiPoint", "MPPT Multi Point"), ("gridOverVoltageVoltage2", "Grid Upper Limit"),
                                                    ("gridUnderVoltageVoltage2", "Grid Lower Limit")]),
    ("Profession Setting", "Profession Setting 3", [("ctRatio", "CT Ratio"), ("meterSelection", "Meter Select"),
                                                    ("setMeterPhase", "ExMeter Phase")]),
    ("Parallel Setup", "Parallel Setup", [("dspParallelEnable", "Parallel"), ("masterSlaverSetting", "Master/Slave"),
                                          ("parallelPhase", "Parallel Phase"), ("machineId", "Parallel ID")]),
]
_APP = {k: (tab, card, label, i) for tab, card, items in APP_LAYOUT for i, (k, label) in enumerate(items)}

# Keys the app does not show (other models, IVGM): (tab, card, predicate), first match wins, else "Basic Setup / System".
_TOPICS = [
    ("Work Mode Setting", "Time of use (more)", lambda k: k == "additionalEcoEnable"),
    ("Grid Code", "Grid code curves & trip values", lambda k: k.startswith(_GRIDCODE)),
    ("Profession Setting", "Protection", lambda k: k.startswith(_PROTECTION) or k in ("bmsHeartbeat", "autoTestEnable", "commOffLineEnable")),
    ("Profession Setting", "PV", lambda k: k in ("pvParallelSetting", "fixQPencent", "powerFactor")),
    ("Batt Setting", "More charge settings", lambda k: k.startswith("gridAuto")),
    ("Gen Setting", "Smart load", lambda k: k.lower().startswith("smartload")),
    ("Gen Setting", "Generator", lambda k: k.startswith("gen")),
    ("Batt Setting", "More battery settings", lambda k: k.startswith(("batt", "equalization", "lithium", "noBms")) or k == "temperatureCoefficientOfBattery"),
    ("Work Mode Setting", "Work mode (more)", lambda k: k in ("zeroExportToCtEnable", "exmeterForCT", "zeroExportFunction", "zeroExportAdjustmentPower")),
    ("Grid Setting", "Grid limits", lambda k: k.startswith("grid") or k in ("exportPowerLimit", "deratingByVoltageEnable")),
    ("Grid Setting", "Output", lambda k: k.startswith("acOutput") or k in ("backUpDelay", "outputMode", "overLoadProtectionResetEnable")),
    ("Parallel Setup", "Parallel (more)", lambda k: k == "parallelEnable"),
]


def placement(key: str) -> tuple[str, str, str | None, int]:
    """(tab, card, app label or None, order within the card)."""
    if key in _APP:
        return _APP[key]
    tab, card = next(((t, c) for t, c, match in _TOPICS if match(key)), ("Basic Setup", "System"))
    return tab, card, None, 100


def parse_options(rang: str | None) -> dict[str, str]:
    """'0:Disable 1:Enable' -> {'0': 'Disable', '1': 'Enable'}; ranges like '0~135' -> {}."""
    if not rang or ":" not in rang:
        return {}
    parts = re.split(r"(?:^|\s)(-?\d+):", rang)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def _rang(key: str, params: dict[str, dict]) -> str:
    return RANGE_OVERRIDE[key][0] if key in RANGE_OVERRIDE else ((params.get(key) or {}).get("rang") or "").strip()


def unit_of(key: str, params: dict[str, dict]) -> str | None:
    if key in RANGE_OVERRIDE:
        return RANGE_OVERRIDE[key][1]
    u = ((params.get(key) or {}).get("unit") or "").strip()
    if not u or parse_options(_rang(key, params)):
        return None
    return UNITS.get(u.upper(), u)


def name_of(key: str, params: dict[str, dict]) -> str:
    if key in _APP:
        return _APP[key][2]
    if key in _NAMES:
        return _NAMES[key]
    d = ((params.get(key) or {}).get("definition") or "").strip()
    # the API reuses short labels ("Restart", "Shut Down", "Start Discharge") for different keys: add a hint when ambiguous
    dup = d and sum(1 for p in params.values() if (p.get("definition") or "").strip() == d) > 1
    pretty = re.sub(r"(?<!^)(?=[A-Z])", " ", key).capitalize()
    if not dup:
        return d or pretty
    low = key.lower()
    hint = "voltage" if low.endswith(("voltage", "volt")) else "SOC" if low.endswith("soc") else pretty
    return f"{d} ({hint})"


@dataclass(frozen=True)
class Spec:
    """How a setting may be written: one of the options (raw value -> label), or a number in [low, high]."""

    options: dict[str, str]
    low: float | None = None
    high: float | None = None
    step: float = 1
    unit: str | None = None


_RANGE = re.compile(r"^\s*(-?[\d.]+)\s*~\s*(-?[\d.]+)\s*$")


def spec(key: str, params: dict[str, dict]) -> Spec | None:
    """The writable form of a setting from the inverter's own definition; None when it must not or cannot be written."""
    if is_blocked(key) or key in SKIP or key not in params:
        return None
    rang = _rang(key, params)
    if opts := parse_options(rang):
        return Spec(opts)
    m = _RANGE.match(rang)
    if not m:
        return None
    low, high, unit = float(m.group(1)), float(m.group(2)), unit_of(key, params)
    step = 0.1 if unit == "V" or not (low.is_integer() and high.is_integer()) else 1
    return Spec({}, low, high, step, unit)


def validate(key: str, value, params: dict[str, dict]):
    """The value to send (int/float) or ValueError with a readable reason."""
    s = spec(key, params)
    if s is None:
        raise ValueError(f"{key} can't be changed from Home Assistant")
    if s.options:
        if str(value) not in s.options:
            raise ValueError(f"{value} is not one of {', '.join(f'{k}={v}' for k, v in s.options.items())}")
        return int(value)
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} is not a number") from None
    if not math.isfinite(num) or not s.low <= num <= s.high:
        raise ValueError(f"{value} is outside {s.low:g}~{s.high:g}")
    if s.step == 1:
        if not num.is_integer():
            raise ValueError(f"{key} takes whole numbers")
        return int(num)
    return round(num, 1)


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate_rule(key: str, rule, current: dict | None, params: dict[str, dict]) -> dict:
    """Validate a time-of-use rule (ecoRuleN) against the inverter's definitions of its fields
    (ecoRuleN.soc, .power, ...) and return the full rule to send, merged over the current one."""
    fields = {f.split(".", 1)[1] for f in params if f.startswith(f"{key}.")}
    if key not in ECO_RULES or "soc" not in fields:
        raise ValueError(f"{key} is not a time-of-use rule of this inverter")
    if not isinstance(rule, dict):
        raise ValueError("rule must be an object")
    if unknown := set(rule) - fields:
        raise ValueError(f"unknown rule fields: {', '.join(sorted(unknown))}")
    out = dict(current or {})
    out.update({k: v for k, v in rule.items() if v is not None})
    if missing := {"startTime", "stopTime", "soc", "power"} - set(out):
        raise ValueError(f"the rule needs {', '.join(sorted(missing))}")
    for t in ("startTime", "stopTime"):
        if not _HHMM.match(str(out[t])):
            raise ValueError(f"{t} must be HH:MM")
    if out["startTime"] >= out["stopTime"] and out["stopTime"] != "00:00":
        raise ValueError("start time must be before stop time")
    for f in fields - {"startTime", "stopTime"}:
        if f in rule:
            out[f] = validate(f"{key}.{f}", out[f], params)
        elif f in out:  # untouched: sent as a number where it is one, else as the inverter reported it
            try:
                out[f] = validate(f"{key}.{f}", out[f], params)
            except ValueError:
                pass
    return out


def display(key: str, value, params: dict[str, dict]):
    if isinstance(value, list):
        return ", ".join(str(v).title() for v in value) or "—"
    if key not in RANGE_OVERRIDE and (opts := parse_options(_rang(key, params))):
        return opts.get(str(value), str(value))
    return value


def eco_rule_summary(rule: dict) -> str | None:
    if not rule:
        return None
    text = f"{rule.get('startTime', '?')}–{rule.get('stopTime', '?')}"
    soc, power = rule.get("soc"), rule.get("power")
    mode = str(rule.get("ruleMode", ""))
    if mode:  # IVGM / T-REX: explicit rule mode
        return text + {"0": " · off", "1": f" · charge from grid to {soc}% at {power} W",
                       "2": f" · discharge / sell down to {soc}% at {power} W"}.get(mode, f" · AC couple at {power} W")
    src = "grid" if str(rule.get("gridChangingEnable")) == "1" else "generator" if str(rule.get("genChangingEnable")) == "1" else None
    if src:
        return text + f" · charge from {src} to {soc}% at {power} W"
    if str(soc) == "100":
        return text + " · keep the battery full"
    return text + f" · use battery down to {soc}%"
