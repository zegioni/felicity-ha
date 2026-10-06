// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 zegioni — https://github.com/zegioni/felicity-ha (see NOTICE.md)
// Felicity Inverter Integration Cloud: the dashboard card (tabs Overview / Charts / Settings / Work mode / Tariff),
// single-purpose widgets built from it, and the "Solar" sidebar panel.
// Each render builds one HTML string; _patch() morphs it into the shadow DOM so scroll position and focus survive.

// ---- formatting ----
const ESC = (x) => String(x ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const FMT = (w) => {  // power without sign (flow nodes say the direction in words)
  if (w === null || w === undefined || isNaN(w)) return "—";
  const a = Math.abs(w);
  return a >= 1000 ? (a / 1000).toFixed(2) + " kW" : Math.round(a) + " W";
};
const SFMT = (w) => (w < 0 ? "−" : "") + FMT(w);  // power with sign (charts)
const KWH = (v) => (v === null || v === undefined || isNaN(v) ? "—" : (+v).toFixed(1) + " kWh");
const PAD = (n) => String(n).padStart(2, "0");
const HH = (h) => { const m = Math.round(h * 60); return `${PAD(Math.floor(m / 60) % 24)}:${PAD(m % 60)}`; };
const DUR = (h) => {
  if (h === null || h === undefined) return "—";
  const m = Math.round(h * 60);
  return h >= 48 ? `${Math.round(h / 24)} days` : m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
};
const CLOCK = (t) => new Date(t).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const CUR = { UAH: "₴", EUR: "€", GBP: "£", USD: "$", PLN: "zł", CZK: "Kč", CHF: "CHF", SEK: "kr", NOK: "kr", DKK: "kr", RON: "lei", HUF: "Ft" };
const MONEY = (v, c, digits = 2) => {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const sym = CUR[c] || c || "", n = Math.abs(+v).toFixed(digits), sign = v < -0.004 ? "−" : "";
  return ["EUR", "GBP", "USD"].includes(c) ? `${sign}${sym}${n}` : `${sign}${n} ${sym}`;
};
// prices keep their third decimal when they have one (1.728 ₴ night rate); money is shown to the kopeck / cent
const PRICE = (v, c) => (v === null || v === undefined ? "—" : MONEY(v, c, Math.abs(v * 100 - Math.round(v * 100)) > 1e-6 ? 3 : 2) + "/kWh");
// hass.callApi rejects with {error, status_code, body: {error}} (not an Error)
const ERR = (e) => (e && e.body && e.body.error) || (e && (e.error || e.message)) || String(e);

// ---- dates and tariff zones ----
// local calendar day `offset` days ago as YYYY-MM-DD (date arithmetic, so DST days are counted right)
const DAY = (offset = 0) => {
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() - offset);
  return `${d.getFullYear()}-${PAD(d.getMonth() + 1)}-${PAD(d.getDate())}`;
};
const DAYS_AGO = (date) => Math.round((new Date(DAY(0) + "T12:00:00") - new Date(date + "T12:00:00")) / 864e5);
const MINS = (s) => { const [h, m] = String(s).split(":").map(Number); return h * 60 + (m || 0); };
const ISO_DAY = (d) => String(((d.getDay() + 6) % 7) + 1);  // 1 = Monday … 7 = Sunday
// same rules as tariff.py: a range may cross midnight (its start's weekday counts), 00:00–24:00 is a whole day
const inRange = (r, m, wd, yd) => {
  const a = MINS(r[0]), b = MINS(r[1]), days = r[2] || "1234567";
  if (isNaN(a) || isNaN(b) || a === b) return false;
  const s = a % 1440, e = b % 1440;
  return s < e ? m >= s && m < e && days.includes(wd) : (m >= s && days.includes(wd)) || (m < e && days.includes(yd));
};
// the zone at a local time: later zones win on overlap, uncovered time falls to the default zone
const zoneAt = (t, d) => {
  const m = d.getHours() * 60 + d.getMinutes(), wd = ISO_DAY(d), yd = ISO_DAY(new Date(d.getFullYear(), d.getMonth(), d.getDate() - 1));
  let hit = null;
  for (const z of t.zones || []) if ((z.ranges || []).some((r) => inRange(r, m, wd, yd))) hit = z;
  return hit || (t.zones || []).find((z) => z.id === t.default_zone) || null;
};
const MONDAY = () => { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d; };

// ---- hand-drawn line icons (24x24, stroke = currentColor) ----
const SVG = (body, size = 26) => `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
const ICON = {
  sun: (on) => SVG(`<circle cx="12" cy="12" r="4.2" fill="currentColor" fill-opacity=".25"/>
    <g class="${on ? "spin" : ""}" style="transform-origin:12px 12px">${[0, 45, 90, 135, 180, 225, 270, 315]
      .map((a) => `<line x1="12" y1="2.6" x2="12" y2="5" transform="rotate(${a} 12 12)"/>`).join("")}</g>`),
  panel: (size) => SVG(`<path d="M4 6h16l-2 9H6z"/><path d="M5.5 10.5h13M10 6l-.8 9M14 6l.8 9"/><path d="M12 15v4M8.5 19h7"/>`, size),
  grid: () => SVG(`<path d="M8 21 12 3l4 18"/><path d="M6.5 7h11M5 11h14"/><path d="M9.2 15.5h5.6M8.5 18.5 14 14M15.5 18.5 10 14"/>`),
  home: () => SVG(`<path d="M3.5 11 12 4l8.5 7"/><path d="M5.5 9.5V20h13V9.5"/><path d="M10 20v-5.5h4V20"/><path d="M15.5 6V4.5h2v3.2"/>`),
  inverter: () => SVG(`<rect x="5" y="3.5" width="14" height="17" rx="2.5"/><path d="M8.5 7.5h7"/><path d="m12.8 10.2-2.6 3.6h3.6l-2.6 3.6"/>`),
  battery: (soc, charging) => {
    const h = Math.max(0, Math.min(100, soc ?? 0)) / 100 * 13;
    return SVG(`<rect x="7" y="4.5" width="10" height="16.5" rx="2.2"/><path d="M10 2.8h4"/>
      <rect x="8.8" y="${19.3 - h}" width="6.4" height="${h}" rx="1" fill="currentColor" stroke="none" opacity=".85"/>
      ${charging ? `<path d="m12.9 9.2-2.4 3.5h3l-2.4 3.5" stroke="#141922" stroke-width="1.6"/>` : ""}`);
  },
  generator: () => SVG(`<rect x="3.5" y="7" width="17" height="11" rx="2"/><path d="M7 7V5h4v2M7 18v1.5M17 18v1.5"/><path d="m13.2 9.2-2.4 3.4h3l-2.4 3.4"/>`),
  plug: () => SVG(`<path d="M9 3v5M15 3v5"/><path d="M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0z"/><path d="M12 17v4"/>`),
};

// ---- static data ----
const ACTIVE_W = 5;         // below this a power counts as idle
const BATTERY_IDLE_W = 20;  // the battery reports a few watts of noise
const MIN_KWH = 0.05;       // smaller daily figures are not worth a line
const COLOR = { sun: "#ffc93c", grid: "#5aa9ff", home: "#ff7eb6", port: "#ff9f43", charge: "#3ddc84", use: "#c08bff" };
// columns of /api/felicity/history rows (["t", *SERIES] in views.py)
const COL = { t: 0, pv: 1, pv1: 2, pv2: 3, grid: 4, bat: 5, home: 6, soc: 7, pv1v: 8, pv2v: 9, gridv: 10 };
// value name -> "<source>.<api field>" exposed by every Felicity sensor as attributes {sn, field}
const FIELDS = {
  pv_power: "live.pvTotalPower", pv1_power: "live.pvPower", pv2_power: "live.pv2Power", grid_power: "live.acTtlInpower",
  battery_power: "live.emsPower", load_power: "live.acTotalOutActPower", battery_soc: "live.emsSoc",
  battery_voltage: "live.emsVoltage", battery_current: "live.emsCurrent", battery_temp: "live.tempMax",
  gen_power: "live.genTotalPower", smart_power: "live.smartTotalPower", home_meter: "live.meterPower",
  pv1_volt: "live.pvVolt", pv2_volt: "live.pv2Volt", pv1_curr: "live.pvInCurr", pv2_curr: "live.pv2InCurr",
  pv3_power: "live.pv3Power", pv3_volt: "live.pv3Volt", pv3_curr: "live.pv3InCurr",
  pv4_power: "live.pv4Power", pv4_volt: "live.pv4Volt", pv4_curr: "live.pv4InCurr",
  battery2_power: "live.emsPower2", battery2_soc: "live.emsSoc2",  // T-REX: a second battery
  pv_today: "energy.generateEnergy", load_today: "energy.offGridEnergy", grid_import_today: "energy.gridInput",
  grid_export_today: "energy.feedOutput", battery_charge_today: "energy.batCharEnergy", battery_discharge_today: "energy.batDisEnergy",
};
// tabs of the full card: [id, label, sections]
const VIEWS = [["overview", "Overview", ["flow", "day", "all"]], ["charts", "Charts", ["explore"]], ["settings", "Settings", ["settings"]],
  ["work", "Work mode", ["tou"]], ["tariff", "Tariff", ["tariff"]]];
const SETTING_TABS = ["Mode settings", "Basic Setup", "Batt Setting", "Grid Setting", "Gen Setting", "Work Mode Setting",
  "Profession Setting", "Grid Code", "Parallel Setup"];
// chart explorer: parameters grouped like the Felicity app's data filter. [field, label, unit]
const EXPLORER_FIELDS = [
  ["Solar", [["pvVolt", "PV1 voltage", "V"], ["pv2Volt", "PV2 voltage", "V"], ["pv3Volt", "PV3 voltage", "V"], ["pv4Volt", "PV4 voltage", "V"],
    ["pvInCurr", "PV1 current", "A"], ["pv2InCurr", "PV2 current", "A"], ["pv3InCurr", "PV3 current", "A"], ["pv4InCurr", "PV4 current", "A"],
    ["pvPower", "PV1 power", "W"], ["pv2Power", "PV2 power", "W"], ["pv3Power", "PV3 power", "W"], ["pv4Power", "PV4 power", "W"], ["pvTotalPower", "Total solar power", "W"]]],
  ["Grid", [["acRInVolt", "Grid voltage", "V"], ["acRInCurr", "Grid current", "A"], ["acRInFreq", "Grid frequency", "Hz"],
    ["acTtlInpower", "Grid power (+ buying)", "W"], ["ctPower", "CT power", "W"]]],
  ["Grid, three-phase", [["acSInVolt", "Grid L2 voltage", "V"], ["acTInVolt", "Grid L3 voltage", "V"], ["acSInCurr", "Grid L2 current", "A"],
    ["acTInCurr", "Grid L3 current", "A"], ["acRInPower", "Grid L1 power", "W"], ["acSInPower", "Grid L2 power", "W"], ["acTInPower", "Grid L3 power", "W"]]],
  ["Home (backup output)", [["acROutVolt", "Output voltage", "V"], ["acROutCurr", "Output current", "A"], ["acROutFreq", "Output frequency", "Hz"],
    ["acTotalOutActPower", "Home power", "W"], ["acTotalOutAppaPower", "Home apparent power", "VA"]]],
  ["Home, three-phase", [["acSOutVolt", "Output L2 voltage", "V"], ["acTOutVolt", "Output L3 voltage", "V"], ["acSOutCurr", "Output L2 current", "A"],
    ["acTOutCurr", "Output L3 current", "A"], ["acROutPower", "Output L1 power", "W"], ["acSOutPower", "Output L2 power", "W"], ["acTOutPower", "Output L3 power", "W"]]],
  ["Home load behind the meter", [["meterPower", "Home load power", "W"]]],
  ["Battery", [["emsVoltage", "Battery voltage", "V"], ["emsCurrent", "Battery current (+ charging)", "A"], ["emsPower", "Battery power (+ charging)", "W"], ["emsSoc", "Battery level", "%"]]],
  ["Battery 2", [["emsVoltage2", "Battery 2 voltage", "V"], ["emsCurrent2", "Battery 2 current (+ charging)", "A"], ["emsPower2", "Battery 2 power (+ charging)", "W"], ["emsSoc2", "Battery 2 level", "%"]]],
  ["Generator", [["genVoltage", "Generator voltage", "V"], ["genCurrent", "Generator current", "A"], ["genFrequency", "Generator frequency", "Hz"], ["genTotalPower", "Generator power", "W"],
    ["genVoltage2", "Generator L2 voltage", "V"], ["genVoltage3", "Generator L3 voltage", "V"], ["genPower", "Generator L1 power", "W"],
    ["genPower2", "Generator L2 power", "W"], ["genPower3", "Generator L3 power", "W"]]],
  ["Smart load", [["smartLoadVolt", "Smart load voltage", "V"], ["smartLoadCurr", "Smart load current", "A"], ["smartLoadFreq", "Smart load frequency", "Hz"], ["smartTotalPower", "Smart load power", "W"]]],
  ["Micro inverter", [["microInvVolt", "Micro inverter voltage", "V"], ["microInvCurr", "Micro inverter current", "A"], ["microInvFreq", "Micro inverter frequency", "Hz"], ["microInvTotalPower", "Micro inverter power", "W"]]],
  ["Inverter", [["inverterAVolt", "Inverter voltage", "V"], ["inverterACurr", "Inverter current", "A"], ["inverterAPower", "Inverter power", "W"]]],
  ["Temperature", [["tempMax", "Temperature (max sensor)", "°C"], ["tempMin", "Temperature (min sensor)", "°C"], ["devTempMax", "Inverter temperature", "°C"]]],
];
const FIELD_META = Object.fromEntries(EXPLORER_FIELDS.flatMap(([, items]) => items.map(([f, name, unit]) => [f, { name, unit }])));
const EXPLORER_PRESETS = [
  { id: "balance", name: "Power balance", fields: ["pvTotalPower", "acTtlInpower", "emsPower", "acTotalOutActPower", "meterPower"],
    hint: "Where the power came from and went to, minute by minute: solar, grid (above 0 = buying), battery (above 0 = charging) and the home." },
  { id: "battery", name: "Battery health", fields: ["emsVoltage", "emsCurrent", "emsSoc", "tempMax"],
    hint: "Voltage should rise smoothly while charging and fall slowly while the home uses it. Sudden drops under load or high temperature are worth a look." },
  { id: "grid", name: "Grid quality", fields: ["acRInVolt", "acRInFreq", "acRInCurr"],
    hint: "Mains voltage should stay about 207–253 V and frequency close to 50 Hz. Zero voltage means a blackout; spikes and dips show an unstable grid." },
  { id: "output", name: "Home supply", fields: ["acROutVolt", "acROutFreq", "acTotalOutActPower", "acTotalOutAppaPower"],
    hint: "What the inverter gives to the house. Apparent power (VA) much higher than real power (W) means motors or old power supplies; peaks show the biggest loads." },
  { id: "solar", name: "Solar strings", fields: ["pvVolt", "pv2Volt", "pvInCurr", "pv2InCurr", "pvPower", "pv2Power"],
    hint: "Compare the two panel strings: on a clear day their curves should look alike. One string much lower points to shade, dirt or a wiring problem." },
  { id: "inverter", name: "Inverter load & heat", fields: ["inverterAPower", "acTotalOutActPower", "tempMax", "tempMin", "devTempMax"],
    hint: "How hard the inverter works and how warm it gets. Long periods near the rated power with rising temperature mean it is close to its limit." },
  { id: "custom", name: "Custom…", fields: [], hint: "Pick any parameters below — values with the same unit share one chart. Your choice is remembered on this device." },
];
const SERIES_COLORS = ["#ffc93c", "#5aa9ff", "#3ddc84", "#ff7eb6", "#c08bff", "#ff9f43", "#2ec4b6", "#e9eef7", "#ffe066", "#7ea8ff"];
const UNIT_TITLE = { W: "Power", VA: "Apparent power", V: "Voltage", A: "Current", Hz: "Frequency", "%": "Battery level", "°C": "Temperature" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ZONE_COLORS = ["#c08bff", "#3ddc84", "#2ec4b6", "#ff7eb6"];

// UI state survives Home Assistant re-creating the card element (it does so after load / on resource reload)
const UI = { day: 0, unit: "day", stab: null, sn: null, view: "overview", preset: null, custom: null,
  tdraft: null, tbase: null, tdraftSn: null, terr: "", tsaving: false };

const STYLES = `<style>
  :host{display:block;-webkit-text-size-adjust:100%;text-size-adjust:100%}
  ha-card{padding:16px;background:radial-gradient(120% 90% at 50% 0%,#24304a 0%,#141922 60%);color:#e9eef7;overflow:hidden;
    box-sizing:border-box;max-width:100%;container-type:inline-size}
  button{all:unset;cursor:pointer;box-sizing:border-box}
  button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #ffb547;outline-offset:1px}
  input,select{background:#ffffff12;color:#fff;border:1px solid #ffffff18;border-radius:8px;font:inherit;min-width:0;max-width:100%;box-sizing:border-box}
  select option{background:#1d2533;color:#fff}
  @media (hover:none) and (pointer:coarse){input:not([type=checkbox]):not([type=radio]):not([type=color]),select{font-size:16px!important}}
  .title{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}
  .title b{font-size:18px;font-weight:600}.title span{font-size:12px;opacity:.6}
  .head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:-4px 0 12px;flex-wrap:wrap}
  .head.mini{margin:-6px 0 6px}
  .vtabs{display:flex;gap:4px;max-width:100%;overflow-x:auto;scrollbar-width:none;background:#ffffff0d;border-radius:12px;padding:3px}
  .vtabs button{white-space:nowrap;font-size:13px;font-weight:600;padding:6px 14px;border-radius:9px;color:#e9eef7aa}
  .vtabs button.on{background:#ffffff22;color:#fff}
  .invsel span,.invsel select{font-size:12.5px;opacity:.8}.invsel select{padding:4px 8px}
  /* overview: battery box, energy flow, price panel */
  .top{display:grid;gap:12px}
  .bottom{display:grid;gap:12px;margin-top:12px}
  .stage{position:relative;height:368px}.stage.port{height:450px}.stage.port .invnode{top:42%}
  .flow-svg{position:absolute;inset:0;width:100%;height:100%}
  .line{stroke:#ffffff14;stroke-width:3;fill:none}
  .flow{stroke-width:3;fill:none;stroke-dasharray:6 10;stroke-linecap:round}
  .flow.idle{opacity:0}.flow.in{animation:mv 1.2s linear infinite}.flow.out{animation:mv 1.2s linear infinite reverse}
  @keyframes mv{to{stroke-dashoffset:-32}}
  .invnode{position:absolute;left:50%;top:50%;width:52px;height:52px;margin:-26px 0 0 -26px;border-radius:50%;background:#1d2533;box-shadow:0 0 0 2px #ffffff30;display:grid;place-items:center;color:#cfd8e6;z-index:1}
  .node{position:absolute;z-index:1;width:34%;text-align:center}
  .node.sun{left:0;top:0}.node.grid{right:0;top:0}.node.bat{left:0;bottom:0}.node.home{right:0;bottom:0}.node.port{left:33%;bottom:0}
  .ic{width:46px;height:46px;border-radius:15px;display:grid;place-items:center;margin:0 auto 4px;box-shadow:inset 0 0 0 1px currentColor}
  .ic svg{display:block}.spin{animation:spin 12s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
  .val{font-size:22px;font-weight:700;letter-spacing:-.3px;font-variant-numeric:tabular-nums}
  .lbl{font-size:12px;opacity:.7;line-height:1.3}.lbl span{opacity:.85;font-weight:600}
  .node .val,.node .lbl{text-shadow:0 0 4px #141922,0 0 8px #141922}
  .chips{display:flex;gap:6px;justify-content:center;margin-top:6px}
  .chip{border:1px solid;border-radius:10px;padding:3px 7px;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;background:#0003}
  .chip span{display:flex;gap:3px;align-items:center;justify-content:center;font-size:10px;font-weight:400;opacity:.65}
  .chip small{display:block;font-size:10px;font-weight:500;opacity:.6;white-space:nowrap}
  .td{display:inline-grid;gap:1px;margin-top:6px;padding:4px 9px;border-radius:10px;background:#ffffff0b;font-size:11px;line-height:1.35;font-variant-numeric:tabular-nums}
  .td div{display:flex;gap:6px;justify-content:space-between}.td i{font-style:normal;opacity:.6;white-space:nowrap}.td b{font-weight:600;white-space:nowrap}
  .pz{display:inline-flex;align-items:center;justify-content:center;gap:5px;margin-top:6px;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:600;max-width:100%;box-sizing:border-box;
    background:color-mix(in srgb,var(--z) 18%,transparent);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--z) 45%,transparent)}
  .pz i{width:7px;height:7px;border-radius:50%;background:var(--z)}
  .bat-box{display:flex;gap:16px;align-items:center;background:#ffffff0a;border-radius:16px;padding:12px 14px}
  .soc{position:relative;width:84px;height:84px;flex:none}
  .soc svg{transform:rotate(-90deg)}.soc .bg{stroke:#ffffff14}.soc circle{fill:none;stroke-width:8}
  .soc .pct{position:absolute;inset:0;display:grid;place-items:center;font-size:20px;font-weight:700}
  .bat-info{flex:1;display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;font-size:13px}
  .bat-info div span{display:block;font-size:11px;opacity:.6}
  .eta{grid-column:1/-1;font-size:12px;opacity:.85}.eta small{opacity:.6}
  .tp{background:#ffffff0a;border-radius:16px;padding:12px 14px;display:grid;gap:6px;min-width:0}
  .tp-h{display:flex;justify-content:space-between;align-items:center;font-size:12px;opacity:.75}
  .tp-h button,.lnk{font-size:12px;font-weight:600;color:#ffb547}
  .tp-now{display:flex;align-items:center;gap:8px;font-size:15px}.tp-now strong{margin-left:auto;font-size:22px;font-variant-numeric:tabular-nums;letter-spacing:-.3px}
  .tp-next,.tp-t{font-size:12px;opacity:.75}.tp-t{margin-top:4px}
  .tp-s{display:flex;justify-content:space-between;gap:8px;font-size:12.5px;margin-top:2px}.tp-s span{opacity:.75}.tp-s b{white-space:nowrap}
  .tp-hint{font-size:11px;opacity:.6;line-height:1.4}
  code{font-size:11px;background:#ffffff12;border-radius:5px;padding:1px 5px;word-break:break-all}
  .pos{color:#3ddc84}.neg{color:#ffb547}
  .zd{display:inline-block;width:9px;height:9px;border-radius:3px;background:var(--z);margin-right:6px;flex:none}.zd.big{width:12px;height:12px;margin:0}
  .ztab{display:grid;font-size:12.5px;font-variant-numeric:tabular-nums}
  .zt{display:grid;grid-template-columns:minmax(0,1fr) auto auto auto;gap:4px 10px;align-items:center;padding:4px 0;border-bottom:1px solid #ffffff0c}
  .zt span{display:flex;align-items:center;min-width:0}.zt em{font-style:normal;font-size:11px;opacity:.55;text-align:right}.zt b{text-align:right;font-weight:600}
  .zt.total{border-bottom:0;font-weight:700}.zt.total span{opacity:.85}
  .strip{position:relative;height:22px;border-radius:7px;overflow:hidden;background:#ffffff0d;margin-top:4px}
  .sg{position:absolute;top:0;bottom:0;background:color-mix(in srgb,var(--z) 70%,transparent);border-right:1px solid #141922;box-sizing:border-box;display:grid;place-items:center;overflow:hidden}
  .sg span{font-size:10px;font-weight:700;color:#0d1118;white-space:nowrap}
  .nowm{position:absolute;top:-2px;bottom:-2px;width:2px;margin-left:-1px;background:#fff;box-shadow:0 0 0 1px #141922}
  .ticks{position:relative;height:14px;font-size:9.5px;opacity:.55}.ticks span{position:absolute;transform:translateX(-50%)}
  .ticks span:first-child{transform:none}.ticks span:last-child{transform:translateX(-100%)}
  /* sections */
  .hist{background:#ffffff0a;border-radius:16px;padding:10px 10px 8px;min-width:0;max-width:100%;box-sizing:border-box;overflow:hidden}
  .sec{font-size:15px;font-weight:600;margin:2px 2px 8px}
  .xintro{font-size:12.5px;opacity:.8;margin:2px 2px 10px;line-height:1.45;max-width:760px}
  .ch-empty{font-size:12px;opacity:.6;padding:24px 0;text-align:center}
  .tipwarn{font-size:12px;background:#ffb5471a;color:#ffd27a;border-radius:10px;padding:7px 10px;margin:6px 0}
  .okbox{font-size:12.5px;line-height:1.45;background:#3ddc841a;color:#9df0c0;border-radius:10px;padding:8px 10px;margin-bottom:8px}
  .pills{display:flex;gap:6px;margin-bottom:8px;overflow-x:auto;scrollbar-width:none;padding-bottom:2px;max-width:100%}.pills::-webkit-scrollbar{display:none}
  .pills button{white-space:nowrap;flex:none;font-size:13px;padding:7px 12px;border-radius:12px;background:#ffffff0d;color:#e9eef7cc}
  .pills button.on{background:#ffffff26;color:#fff;font-weight:600}
  .tabs{display:flex;gap:6px;margin-bottom:6px}
  .tabs button{font-size:12px;padding:4px 10px;border-radius:9px;color:#e9eef7;opacity:.6}.tabs button.on{background:#ffffff1a;opacity:1;font-weight:600}
  .nav{display:flex;align-items:center;gap:8px;margin-bottom:8px;max-width:420px}
  .nav button{width:38px;height:38px;flex:none;display:grid;place-items:center;border-radius:9px;background:#ffffff14;font-size:18px}
  .nav button[disabled]{opacity:.25;cursor:default}
  .datebtn{position:relative;flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:38px;border-radius:10px;background:#ffffff0d;cursor:pointer;padding:2px 8px}
  .datebtn b{font-size:13px}.datebtn small{font-size:10.5px;opacity:.6}
  .datebtn input{position:absolute;inset:0;opacity:0;width:100%;height:100%;cursor:pointer;border:0;padding:0;margin:0}
  .datebtn input::-webkit-calendar-picker-indicator{position:absolute;inset:0;width:100%;height:100%;cursor:pointer;opacity:0}
  /* charts */
  .days{display:grid;gap:10px;margin-top:8px}
  .dbox{padding:10px 12px 8px;border-radius:12px;background:#ffffff08;border:1px solid #ffffff12;min-width:0}
  .dbox-h{font-size:13px;font-weight:600;margin:0 0 3px}
  .dbox-t{display:flex;flex-wrap:wrap;gap:2px 10px;font-size:12px;font-variant-numeric:tabular-nums;margin-bottom:6px;opacity:.95}
  .dbox-t .tot{display:flex;flex-wrap:wrap;gap:2px 10px}.dbox-t i{font-style:normal}.dbox-t b{font-weight:700}
  .totals{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:4px 0 8px;text-align:center}
  .totals span{display:block;font-size:10px;opacity:.6}.totals b{font-size:16px;font-variant-numeric:tabular-nums}
  .chart{position:relative}.ch{width:100%;display:block}
  .gl{stroke:#ffffff10}.gl.zero{stroke:#ffffff30}.ax{fill:#e9eef799;font-size:10px;font-family:inherit}
  .hv{stroke:#ffffff70;stroke-width:1}
  .scrub{position:absolute;inset:0 0 18px 0;touch-action:pan-y;cursor:crosshair;-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent}
  .tip{position:absolute;top:4px;left:42px;background:#0d1118e6;border:1px solid #ffffff20;border-radius:10px;padding:6px 8px;font-size:11px;display:flex;flex-direction:column;gap:1px;pointer-events:none}
  .tip[hidden]{display:none}.tip .hint{opacity:.55;font-size:10px}
  .legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11px;opacity:.8;margin-top:4px;justify-content:center}
  .legend span::before{content:"";display:inline-block;width:12px;height:3px;border-radius:2px;background:var(--c);margin-right:5px;vertical-align:middle}
  .legend .dash::before{background:repeating-linear-gradient(90deg,var(--c) 0 3px,transparent 3px 6px)}
  .legend .muted{opacity:.6}.legend .muted::before{display:none}
  .xstats{display:flex;flex-wrap:wrap;gap:2px 12px;font-size:11.5px;margin:0 2px 2px}.xstats i{font-style:normal}.xstats b{font-weight:600}
  .xcharts{display:grid;gap:10px;margin-top:8px}
  .xmiss{font-size:11px;opacity:.55;margin-top:6px}
  .picker{display:grid;gap:8px;margin:0 0 10px;padding:10px;border-radius:12px;background:#ffffff08}
  .pg-h{font-size:11px;opacity:.6;margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}
  .pg-c{display:flex;flex-wrap:wrap;gap:5px}
  .pg-c button{font-size:12px;padding:5px 10px;border-radius:999px;background:#ffffff10;color:#e9eef7cc}
  .pg-c button.on{background:#ff9f4333;color:#ffb547;box-shadow:inset 0 0 0 1px #ff9f4388}
  /* settings */
  .sread{font-size:11px;opacity:.55;margin:0 2px 8px}
  .scards{display:grid;gap:10px;align-items:start;min-width:0}.scol{display:flex;flex-direction:column;gap:10px;min-width:0}
  .scard{background:#ffffff0a;border-radius:14px;padding:8px 12px;min-width:0}
  .scard-h{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:700;margin:2px 0}
  .srow{display:grid;grid-template-columns:minmax(90px,1fr) minmax(0,58%);align-items:center;gap:2px 8px;padding:3px 0;min-height:32px;border-bottom:1px solid #ffffff0c;font-size:12.5px}
  .srow:last-child{border-bottom:0}.srow span{opacity:.7}.srow .lbl2{line-height:1.3}
  .srow small{font-size:10px;opacity:.5;margin-left:6px;white-space:nowrap}
  .srow b{font-weight:600;background:#ffffff0f;border-radius:8px;padding:3px 8px;text-align:right;max-width:60%;font-variant-numeric:tabular-nums}
  .srow.ro b{opacity:.7;background:transparent;padding:3px 0}
  .srow .warn{background:none;padding:0;margin-right:5px;color:#ffb547;font-weight:700}
  .srow .ctl{display:flex;align-items:center;justify-content:flex-end;gap:6px;min-width:0;max-width:100%}
  .srow select,.srow input{padding:3px 7px;font-size:12.5px;font-weight:600}
  .srow select{width:auto;flex:0 1 auto;text-overflow:ellipsis}.srow input{width:70px;text-align:right}
  .srow .u{font-style:normal;opacity:.5;font-size:11px;width:22px;flex:none;text-align:left}
  .srow.changed select,.srow.changed input{border-color:#ff9f43;box-shadow:0 0 0 1px #ff9f4366}
  .srow.changed .tgl span{box-shadow:0 0 0 2px #ff9f43}
  .tgl{position:relative;width:36px;height:20px;display:inline-block;cursor:pointer}
  .tgl input{opacity:0;width:0;height:0;position:absolute}
  .tgl span{position:absolute;inset:0;border-radius:11px;background:#ffffff26;transition:.2s}
  .tgl span::after{content:"";position:absolute;width:14px;height:14px;left:3px;top:3px;border-radius:50%;background:#fff;transition:.2s}
  .tgl input:checked+span{background:#3ddc84}.tgl input:checked+span::after{transform:translateX(16px)}
  .tgl.sm{width:30px;height:17px}.tgl.sm span::after{width:11px;height:11px}.tgl.sm input:checked+span::after{transform:translateX(13px)}
  .send{background:#ff9f43;color:#141922;font-weight:700;font-size:12px;border-radius:8px;padding:5px 10px}
  .send[hidden]{display:none}.send.risky{background:#ffb547}.send.confirm{background:#ff5d5d;color:#fff}.send[disabled]{opacity:.6;cursor:default}
  .send.big{padding:9px 16px;font-size:13px;margin:8px 0}
  .st{grid-column:1/-1;font-style:normal;font-size:11px;width:100%;text-align:right;opacity:.85}.st.ok{color:#3ddc84}.st.err{color:#ff6b6b}
  .tou-t{margin-top:8px;font-size:12px;overflow-x:auto}.tou-t .tt{min-width:400px}
  .tou-t .th,.tou-t .tr{display:grid;grid-template-columns:40px 40px 62px 62px 64px 50px 52px;gap:6px;align-items:center;padding:4px 0}
  .tou-t .th{opacity:.55;font-size:11px}.tou-t .tr{border-top:1px solid #ffffff10;font-variant-numeric:tabular-nums}
  .tou-t .tr input,.tou-t .tr select{border-radius:7px;padding:3px 5px;font-size:12px;width:100%}
  .tou-t .tr.changed input,.tou-t .tr.changed select{border-color:#ff9f4388}
  /* work mode */
  .tou-head{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12.5px;opacity:.85;margin-bottom:8px}
  .seg{fill:#0d1118;font-size:11px;font-weight:700}.now{stroke:#fff;stroke-width:2}
  .pstrip{margin:0 0 6px}.pstrip .strip{margin:0}
  .rules{display:grid;gap:6px;margin:10px 0}
  .rule{display:flex;gap:10px;align-items:center;font-size:13px;background:#ffffff08;border-radius:10px;padding:7px 10px}
  .rule.on{background:#ffffff16;box-shadow:inset 0 0 0 1px #ffffff30}
  .rule b{font-variant-numeric:tabular-nums;min-width:96px}.rule span:last-of-type{opacity:.85}
  .rule em{margin-left:auto;font-style:normal;font-size:11px;background:#3ddc8433;color:#3ddc84;border-radius:6px;padding:2px 6px}
  .dot{width:10px;height:10px;border-radius:3px;flex:none}
  /* tariff */
  .tgrid{display:grid;gap:12px}.tgrid>div,.tcol{min-width:0}.tcol{display:grid;gap:12px;align-content:start}
  .te-l{font-size:12px;opacity:.75;margin:10px 2px 6px}.te-l small{opacity:.7}
  .te-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;min-width:0}
  .tf{display:grid;gap:4px;font-size:11.5px;opacity:.9;min-width:0}.tf.wide{grid-column:1/-1}
  .tf input,.zc input,.zc select{padding:6px 8px;font-size:13px}.tf input{width:100%}
  .zcs{display:grid;gap:8px}
  .zc{border-radius:12px;padding:8px 10px;background:#ffffff08;box-shadow:inset 3px 0 0 var(--z);min-width:0}
  .zc-h{display:flex;gap:8px;align-items:center;min-width:0}
  .zc-h input[type=color]{width:30px;height:30px;padding:2px;flex:none;cursor:pointer}
  .zname{flex:1;width:0;font-weight:600}
  .zprice{display:flex;align-items:center;gap:4px}.zprice input{width:84px;text-align:right}.zprice i{font-style:normal;font-size:11px;opacity:.6;white-space:nowrap}
  .zr{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px}
  .rg{display:flex;align-items:center;gap:4px;background:#ffffff0a;border-radius:9px;padding:3px}
  .rg .tm{width:58px;text-align:center;font-variant-numeric:tabular-nums}.rg select{font-size:12px;padding:5px 4px}
  .x{width:28px;height:28px;display:grid;place-items:center;border-radius:7px;opacity:.55;font-size:12px}.x:hover{opacity:1;background:#ffffff14}
  .add{font-size:12px;font-weight:600;color:#ffb547;padding:5px 8px;border-radius:8px}.add:hover{background:#ffb5471a}
  .add.big{margin-top:2px;justify-self:start}
  .def{display:flex;align-items:center;gap:5px;font-size:11.5px;opacity:.75;margin-left:auto;cursor:pointer}
  .week{display:grid;gap:3px;min-width:0}
  .wk{display:grid;grid-template-columns:34px 1fr;align-items:center;gap:6px;font-size:11px}.wk>div{min-width:0}.wk>span{opacity:.6}.wk.on>span{opacity:1;font-weight:700}
  .wk .strip{height:16px;margin:0}
  .te-bar{display:flex;align-items:center;gap:8px;justify-content:flex-end;margin-top:12px;font-size:12px}
  .te-bar span{margin-right:auto;opacity:.65}.te-bar span.err{color:#ff6b6b;opacity:1}.te-bar.on span{opacity:1;color:#ffb547}
  .te-bar .send{padding:7px 14px;font-size:13px}
  .ghost{font-size:12px;padding:5px 10px;border-radius:8px;background:#ffffff14}
  .howto{margin:0;padding-left:18px;display:grid;gap:8px;font-size:12.5px;line-height:1.45}
  .ents{display:grid;gap:6px;margin-top:6px}
  .lnk{display:inline-block;margin-top:10px}
  @container (min-width: 900px) {
    .top{grid-template-columns:1fr minmax(380px,520px) 1fr;align-items:center}
    .top-l .bat-box{flex-direction:column;text-align:center}.top-l .bat-info{width:100%}
    .days{grid-template-columns:repeat(3,1fr);gap:16px;align-items:stretch}
    .xcharts{grid-template-columns:1fr 1fr;gap:16px}.xcharts.one{grid-template-columns:1fr}
    .tgrid{grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);align-items:start}
  }
  @container (max-width: 899px) { .top-c{order:-1} .nav{max-width:none} }
  @container (max-width: 420px) { .td{padding:3px 6px;font-size:10.5px;max-width:100%;box-sizing:border-box}.td div{gap:4px} }
</style>`;

class FelicityFlowCard extends HTMLElement {
  // ---- lifecycle ----

  setConfig(config) {
    this._cfg = config;
    this._key = "";
    this._hist = {};       // GET cache: key -> {at, rows, fields, error, data}
    this._loading = new Set();
    this._hover = null;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  getCardSize() { return 8; }

  // sections view: take the full width of the section (the energy-flow widget can be half width)
  getGridOptions() {
    const flowOnly = this._cfg && this._cfg.show && this._cfg.show.length === 1 && this._cfg.show[0] === "flow";
    return { columns: "full", min_columns: flowOnly ? 6 : 12, rows: "auto" };
  }

  connectedCallback() {
    if (!this._ro) this._ro = new ResizeObserver(() => {
      const w = Math.round(this._cw() / 20);
      if (w !== this._lastW) { this._lastW = w; this._rerender(); }
    });
    this._ro.observe(this);
  }

  disconnectedCallback() { if (this._ro) this._ro.disconnect(); }

  set hass(hass) {
    this._hass = hass;
    // index all Felicity sensors by inverter serial: live/energy values and settings
    const idx = {}, settings = {}, inv = new Set();
    for (const [id, st] of Object.entries(hass.states)) {
      const a = st.attributes;
      if (!a.sn || !id.startsWith("sensor.felicity_")) continue;
      if (a.field) { (idx[a.sn] ||= {})[a.field] = st; if (a.field === "live.emsSoc" || a.field === "live.pvTotalPower") inv.add(a.sn); }
      else if (a.key !== undefined) (settings[a.sn] ||= {})[a.key] = st;
    }
    this._inverters = [...inv].sort();
    if (this._cfg.sn && this._inverters.includes(this._cfg.sn)) UI.sn = this._cfg.sn;
    else if (!this._inverters.includes(UI.sn)) UI.sn = this._inverters[0] || null;
    this._idx = idx[UI.sn] || {};
    this._set = settings[UI.sn] || {};
    const v = {};
    for (const [k, f] of Object.entries(FIELDS)) v[k] = this._idx[f] ? this._num(this._idx[f]) : null;
    this._v = v;
    let setStamp = "";
    for (const st of Object.values(this._set)) setStamp += st.last_updated;
    const key = JSON.stringify(v) + UI.day + UI.sn + UI.view + (this._histStamp || "") + setStamp;
    if (key !== this._key) this._rerender(key);
  }

  _num(st) {
    if (!st || st.state === "unknown" || st.state === "unavailable") return null;
    const v = parseFloat(st.state);
    return isNaN(v) ? null : v;
  }

  get _sn() { return UI.sn; }

  // background redraws wait while the user types in a field (a redraw would replace it and close the keyboard)
  _typing() {
    const a = this.shadowRoot && this.shadowRoot.activeElement;
    return !!a && (a.tagName === "SELECT" || a.tagName === "TEXTAREA" || (a.tagName === "INPUT" && !["checkbox", "radio", "date", "color"].includes(a.type)));
  }

  _rerender(key = "") {
    if (this._typing()) { this._key = ""; return; }  // the next update redraws
    this._key = key;
    if (this._v) this._render();
  }

  // a redraw the user asked for (tab, button, picker): always, even with a field focused
  _redraw() {
    this._key = "";
    if (this._v) this._render();
  }

  // ---- data ----

  async _get(key, path, ttl) {
    const h = this._hist[key];
    const fresh = h && (h.error ? Date.now() - h.at < 30000 : !ttl || Date.now() - h.at < ttl);
    if (fresh || this._loading.has(key)) return;
    this._loading.add(key);
    try {
      const r = await this._hass.callApi("GET", path);
      this._hist[key] = { at: Date.now(), rows: r.rows || [], fields: r.fields, data: r };
    } catch (e) {
      this._hist[key] = { at: Date.now(), rows: [], error: ERR(e) };
    }
    this._loading.delete(key);
    this._histStamp = key + this._hist[key].at;
    this._rerender();
  }

  // minute history of a day ({rows} with COL columns), fetched when first read
  _dayHist(date) {
    const key = `h:${this._sn}:${date}`;
    this._get(key, `felicity/history?sn=${this._sn}&date=${date}`, date === DAY(0) ? 120000 : 0);
    return this._hist[key];
  }

  // daily / monthly energy totals
  _series(unit) {
    const key = `s:${this._sn}:${unit}`;
    this._get(key, `felicity/series?sn=${this._sn}&unit=${unit}&count=${unit === "day" ? 60 : 12}`, 600000);
    return this._hist[key];
  }

  // tariff payload of a day: null while loading, {error} when it failed
  _tar(date) {
    if (!this._sn) return null;
    const key = `t:${this._sn}:${date}`;
    this._get(key, `felicity/tariff?sn=${this._sn}&date=${date}`, date === DAY(0) ? 60000 : 600000);
    const h = this._hist[key];
    return !h ? null : h.data && h.data.tariff ? h.data : { error: h.error || "no data" };
  }

  _staleTariff() { for (const [k, h] of Object.entries(this._hist)) if (k.startsWith("t:")) h.at = 0; }

  // kWh from W samples (~1/min); gaps over 10 min are not bridged
  _kwh(rows, i, sign) {
    let e = 0, prev = null;
    for (const r of rows) {
      if (r[0] === null || r[i] === null) { prev = null; continue; }
      if (prev && r[0] - prev[0] <= 600000) e += Math.max(0, sign * r[i]) * (r[0] - prev[0]) / 3.6e9;
      prev = r;
    }
    return e;
  }

  // the card's width (measured once per render)
  _cw() { return this._width || this.getBoundingClientRect().width || 400; }

  // ---- chart primitives ----

  // series: [{i: column, col, name, sign?, area?, dash?, soc?}]; opts: {w, h, fit (scale to the data), unit, bands}
  _lineChart(id, rows, date, series, opts = {}) {
    const hasSoc = series.some((s) => s.soc);
    const W = Math.round(opts.w || 360), H = opts.h || 150, L = 34, R = hasSoc ? 30 : 16, T = 14, B = 18;
    const [y, mo, d] = date.split("-").map(Number);
    const at = (h) => new Date(y, mo - 1, d, 0, h * 60).getTime();  // local time of an hour of this day (DST-proof)
    const d0 = at(0), d1 = at(24);
    const X = (t) => L + (t - d0) / (d1 - d0) * (W - L - R);
    const val = (r, s) => (r[s.i] === null || r[s.i] === undefined ? null : s.sign ? Math.max(0, s.sign * r[s.i]) : r[s.i]);
    const plain = series.filter((s) => !s.soc);
    let lo = 0, hi = 500, step;
    if (opts.fit) {  // e.g. 49.9–50.1 Hz, 220–240 V
      let mn = Infinity, mx = -Infinity;
      for (const r of rows) for (const s of plain) { const v = val(r, s); if (v !== null) { mn = Math.min(mn, v); mx = Math.max(mx, v); } }
      if (!isFinite(mn)) { mn = 0; mx = 1; }
      if (opts.unit === "%") { mn = 0; mx = 100; }
      const span = Math.max(mx - mn, Math.abs(mx) * 0.02, 0.1), raw = span / 4;
      const mag = Math.pow(10, Math.floor(Math.log10(raw)));
      step = [1, 2, 2.5, 5, 10].find((m) => m * mag >= raw) * mag;
      lo = Math.floor((mn - span * 0.05) / step) * step; hi = Math.ceil((mx + span * 0.05) / step) * step;
      if (mn >= 0 && lo < 0) lo = 0;
      if (opts.unit === "%") { lo = 0; hi = 100; }
    } else {
      for (const r of rows) for (const s of plain) { const v = val(r, s); if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
      step = hi - lo > 4000 ? 2000 : hi - lo > 2000 ? 1000 : 500;
      lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    }
    const Y = (w) => T + (hi - w) / (hi - lo) * (H - T - B);
    const Ys = (p) => T + (100 - p) / 100 * (H - T - B);
    const path = (s, f) => {
      const segs = [];
      let dd = "", prev = null, first = null, last = null;
      for (const r of rows) {
        const v = val(r, s);
        if (v === null || r[0] === null) { prev = null; continue; }
        const x = X(r[0]).toFixed(1), yy = f(v).toFixed(1);
        if (prev === null || r[0] - prev > 600000) { if (dd) segs.push([dd, first, last]); dd = `M${x} ${yy}`; first = x; }
        else dd += `L${x} ${yy}`;
        last = x; prev = r[0];
      }
      if (dd) segs.push([dd, first, last]);
      return segs.map(([p, a, b]) => (s.area ? `<path d="${p}L${b} ${Y(0)}L${a} ${Y(0)}Z" fill="${s.col}" fill-opacity=".22" stroke="none"/>` : "") +
        `<path d="${p}" fill="none" stroke="${s.col}" stroke-width="1.6" stroke-linejoin="round" ${s.dash ? 'stroke-dasharray="3 3"' : ""}/>`).join("");
    };
    let g = "";
    for (const b of opts.bands || []) g += `<rect x="${X(at(b.from)).toFixed(1)}" y="${T}" width="${(X(at(b.to)) - X(at(b.from))).toFixed(1)}" height="${H - T - B}" fill="${b.col}" fill-opacity="${b.op}"/>`;
    for (let k = 0, n = Math.round((hi - lo) / step); k <= n; k++) {
      const w = lo + k * step;
      g += `<line x1="${L}" x2="${W - R}" y1="${Y(w)}" y2="${Y(w)}" class="gl${Math.abs(w) < step / 1e6 ? " zero" : ""}"/><text x="${L - 4}" y="${Y(w) + 3}" class="ax" text-anchor="end">${opts.fit ? +w.toFixed(3) : w / 1000}</text>`;
    }
    g += `<text x="${L}" y="7" class="ax">${opts.fit ? opts.unit || "" : "kW"}</text>`;
    for (let hr = 0; hr <= 24; hr += 6) g += `<text x="${X(at(hr))}" y="${H - 4}" class="ax" text-anchor="middle">${PAD(hr)}:00</text>`;
    if (hasSoc) for (const p of [0, 50, 100]) g += `<text x="${W - R + 4}" y="${Ys(p) + 3}" class="ax">${p}%</text>`;
    let hover = "", tip = "";
    if (this._hover && this._hover.id === id) {
      const t = d0 + this._hover.x * (d1 - d0);
      let best = null;
      for (const r of rows) if (r[0] !== null && (!best || Math.abs(r[0] - t) < Math.abs(best[0] - t))) best = r;
      if (best && Math.abs(best[0] - t) < 15 * 60000) {
        const x = X(best[0]);
        hover = `<line x1="${x}" x2="${x}" y1="${T}" y2="${H - B}" class="hv"/>`;
        tip = `<b>${CLOCK(best[0])}</b>` + series.map((s) => {
          const v = val(best, s);
          const text = s.soc ? `${v ?? "—"}%` : opts.fit ? `${v ?? "—"} ${opts.unit || ""}` : SFMT(v);
          return `<span style="color:${s.col}">${ESC(s.name)} ${text}</span>`;
        }).join("");
      }
    }
    return `<div class="chart" data-chart="${id}"><svg viewBox="0 0 ${W} ${H}" class="ch">${g}
      ${series.filter((s) => s.soc).map((s) => path(s, Ys)).join("")}${plain.map((s) => path(s, Y)).join("")}${hover}
      <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" class="hit"/></svg><div class="scrub"></div>
      <div class="tip"${tip ? "" : " hidden"}>${tip}</div></div>
      <div class="legend">${series.map((s) => `<span style="--c:${s.col}" class="${s.dash ? "dash" : ""}">${ESC(s.name)}</span>`).join("")}</div>`;
  }

  // 24 h strip of tariff zones (positioned in percent, so it fits any width)
  _strip(bands, zones, now = true) {
    const seg = bands.map(([a, b, id]) => {
      const z = zones[id] || {};
      return `<div class="sg" style="left:${a / 24 * 100}%;width:${(b - a) / 24 * 100}%;--z:${z.color || "#555"}" title="${ESC(z.name)} ${HH(a)}–${HH(b)}">${b - a >= 3 ? `<span>${ESC(z.name)}</span>` : ""}</div>`;
    }).join("");
    const n = new Date(), nh = n.getHours() + n.getMinutes() / 60;
    return `<div class="strip">${seg}${now ? `<div class="nowm" style="left:${nh / 24 * 100}%"></div>` : ""}</div>`;
  }

  _ticks() {
    return `<div class="ticks">${[0, 6, 12, 18, 24].map((h) => `<span style="left:${h / 24 * 100}%">${PAD(h)}:00</span>`).join("")}</div>`;
  }

  _dayNav() {
    const date = DAY(UI.day);
    const label = new Date(date + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "long", year: "numeric" });
    const rel = UI.day === 0 ? "today" : UI.day === 1 ? "yesterday" : `${UI.day} days ago`;
    // the date button opens the native date picker (calendar on phones) through a transparent date input on top of it
    return `<div class="nav"><button data-nav="1" aria-label="Previous day">‹</button>
      <label class="datebtn"><b>${label}</b><small>${rel}</small>
        <input type="date" data-date value="${date}" max="${DAY(0)}" aria-label="Pick a date"></label>
      <button data-nav="-1" ${UI.day === 0 ? "disabled" : ""} aria-label="Next day">›</button></div>`;
  }

  _empty(h, what = "No data for this day") {
    return `<div class="ch-empty">${!h ? "Loading…" : h.error ? "Error: " + ESC(h.error) : what}</div>`;
  }

  // ---- overview ----

  _overview(T, tabbed) {
    const v = this._v, ok = T && !T.error, rt = ok ? T.runtime : null;
    const ah = this._set.batteryCapacity ? Number(this._set.batteryCapacity.state) : 0;
    const cap = this._cfg.battery_capacity_kwh || (rt && rt.capacity_kwh) || (ah ? Math.round(ah * 51.2 / 100) / 10 : 16);
    const reserve = rt ? rt.reserve : 20;
    // IVAM: everything is on the backup output; IVGM (hybrid on-grid): backup output + home load behind the meter
    const pv = v.pv_power ?? 0, grid = v.grid_power ?? 0, bat = (v.battery_power ?? 0) + (v.battery2_power ?? 0), load = (v.load_power ?? 0) + (v.home_meter ?? 0);
    const soc = v.battery_soc;
    const charging = bat > BATTERY_IDLE_W, discharging = bat < -BATTERY_IDLE_W;  // Felicity: negative = discharging
    let eta = "";
    const at = (h) => h < 24 ? ", ~" + CLOCK(Date.now() + h * 36e5) : "";
    if (soc !== null && !charging && rt && rt.hours !== null) {
      eta = `Lasts about <b>${DUR(rt.hours)}</b> at your usage (until ${reserve}%${at(rt.hours)})<br><small>average home use ${FMT(rt.avg_load_w)} over the last ${rt.window_min} min</small>`;
    } else if (soc !== null && discharging) {
      const h = ((soc - reserve) / 100) * cap * 1000 / Math.abs(bat);
      if (h > 0) eta = `Lasts about <b>${DUR(h)}</b> at this usage (until ${reserve}%${at(h)})`;
    } else if (soc !== null && charging) {
      const h = ((100 - soc) / 100) * cap * 1000 / bat;
      eta = `Full in about <b>${DUR(h)}</b> (until 100%${at(h)})<br><small>at the current charging power ${FMT(bat)}</small>`;
    }
    const socCol = soc === null ? "#888888" : soc < 25 ? "#ff5d5d" : soc < 50 ? "#ffb547" : "#3ddc84";
    const ring = 2 * Math.PI * 34, dash = soc === null ? 0 : ring * soc / 100;
    const port = this._set.smartPortModeEnable;  // IVAM has a GEN / smart-load port; IVGM does not
    const isSmart = !!port && /smart/i.test(port.state);
    const portPw = (isSmart ? v.smart_power : v.gen_power) ?? 0;
    // flow lines from each node to the inverter; dir +1 = toward the inverter
    const flow = (id, active, dir, col) => `<path data-from="${id}" class="line"/><path data-from="${id}" class="flow ${!active ? "idle" : dir > 0 ? "in" : "out"}" style="stroke:${col}"/>`;
    const node = (cls, icon, label, value, sub, col, extra = "") => `
      <div class="node ${cls}">
        <div class="ic" style="background:linear-gradient(${col}26,${col}26),#18202d;color:${col}">${icon}</div>
        <div class="val">${value}</div>
        <div class="lbl">${label}<br><span>${sub}</span></div>${extra}
      </div>`;
    // today's numbers next to each node, like the Sunsynk card
    const today = (rows) => `<div class="td">${rows.filter(Boolean).map(([l, x, c]) => `<div><i>${l}</i><b${c ? ` style="color:${c}"` : ""}>${x}</b></div>`).join("")}</div>`;
    const pvChips = ["pv1", "pv2", "pv3", "pv4"].filter((k) => this._idx[FIELDS[k + "_power"]]).map((k) => `<div class="chip" style="border-color:${COLOR.sun}55">
        <span>${ICON.panel(12)} PV${k.slice(2)}</span>${FMT(v[k + "_power"] ?? 0)}
        <small>${v[k + "_volt"] === null ? "—" : Math.round(v[k + "_volt"])} V · ${v[k + "_curr"] === null ? "—" : (+v[k + "_curr"]).toFixed(1)} A</small></div>`).join("");
    const phases = this._idx["live.acSInPower"] ? `<div class="chips">${["acRInPower", "acSInPower", "acTInPower"].map((f, i) =>
      `<div class="chip" style="border-color:${COLOR.grid}55"><span>L${i + 1}</span>${FMT(this._num(this._idx["live." + f]) ?? 0)}</div>`).join("")}</div>` : "";
    const cur = ok ? T.tariff.currency : null, day = ok && !T.day.error ? T.day : null, now = ok ? T.now : null;
    const pricePill = now && now.price !== null ? `<div class="pz" style="--z:${now.zone ? now.zone.color : COLOR.grid}"><i></i>${now.zone ? ESC(now.zone.name) + " · " : ""}${PRICE(now.price, cur)}</div>` : "";
    return `<div class="title"><b>${ESC(this._cfg.title || "Home energy")}</b><span>${this._updatedText()}</span></div>
      <div class="top">
        <div class="top-l"><div class="bat-box">
          <div class="soc">
            <svg width="84" height="84"><circle class="bg" cx="42" cy="42" r="34"/>
              <circle cx="42" cy="42" r="34" style="stroke:${socCol};stroke-dasharray:${dash} ${ring};stroke-linecap:round"/></svg>
            <div class="pct">${soc === null ? "—" : Math.round(soc) + "%"}</div>
          </div>
          <div class="bat-info">
            <div><span>Voltage</span>${v.battery_voltage ?? "—"} V</div>
            <div><span>Current</span>${v.battery_current ?? "—"} A</div>
            <div><span>Temperature</span>${v.battery_temp ?? "—"} °C</div>
            <div><span>Energy left</span>${soc === null ? "—" : ((soc / 100) * cap).toFixed(1) + " of " + cap.toFixed(1) + " kWh"}</div>
            ${v.battery2_soc === null ? "" : `<div><span>Battery 2</span>${Math.round(v.battery2_soc)}% · ${SFMT(v.battery2_power ?? 0)}</div>`}
            <div class="eta">${eta}</div>
          </div>
        </div></div>
        <div class="top-c"><div class="stage${port ? " port" : ""}">
          <svg class="flow-svg">
            ${flow("sun", pv > ACTIVE_W, 1, COLOR.sun)}
            ${flow("grid", Math.abs(grid) > ACTIVE_W, grid > 0 ? 1 : -1, COLOR.grid)}
            ${flow("bat", Math.abs(bat) > BATTERY_IDLE_W, discharging ? 1 : -1, socCol)}
            ${flow("home", load > ACTIVE_W, -1, COLOR.home)}
            ${port ? flow("port", portPw > ACTIVE_W, isSmart ? -1 : 1, COLOR.port) : ""}
          </svg>
          <div class="invnode">${ICON.inverter()}</div>
          ${node("sun", ICON.sun(pv > ACTIVE_W), "Solar", FMT(pv), pv > ACTIVE_W ? "producing now" : "not producing", COLOR.sun,
            (pvChips ? `<div class="chips">${pvChips}</div>` : "") + today([["Today", KWH(v.pv_today)]]))}
          ${node("grid", ICON.grid(), "Grid", FMT(grid), grid > ACTIVE_W ? "buying from grid" : grid < -ACTIVE_W ? "selling to grid" : "not used now", COLOR.grid,
            phases + pricePill + today([["Bought today", KWH(v.grid_import_today)], day && ["Cost today", MONEY(day.grid_cost, cur)],
              v.grid_export_today > MIN_KWH && ["Sold today", KWH(v.grid_export_today)]]))}
          ${node("bat", ICON.battery(soc, charging), "Battery", FMT(bat), charging ? "charging" : discharging ? "powering the home" : "resting", socCol,
            today([["Charged today", KWH(v.battery_charge_today), COLOR.charge], ["Used today", KWH(v.battery_discharge_today), COLOR.use]]))}
          ${node("home", ICON.home(), "Home", FMT(load), "using now", COLOR.home, today([["Used today", KWH(v.load_today)]]))}
          ${port ? node("port", isSmart ? ICON.plug() : ICON.generator(), isSmart ? "Smart load" : "Generator", FMT(portPw),
            portPw > ACTIVE_W ? (isSmart ? "load is on" : "generator running") : (isSmart ? "load is off" : "generator off"), COLOR.port) : ""}
        </div></div>
        <div class="top-r">${this._tariffPanel(T, tabbed)}</div>
      </div>`;
  }

  // draw each flow line from its node icon to the inverter (measured, so it matches any width)
  _layoutLines() {
    const stage = this.shadowRoot.querySelector(".stage"), inv = stage && stage.querySelector(".invnode");
    if (!inv) return;
    const sb = stage.getBoundingClientRect();
    if (!sb.width) return;
    stage.querySelector(".flow-svg").setAttribute("viewBox", `0 0 ${sb.width} ${sb.height}`);
    const c = (el) => { const b = el.getBoundingClientRect(); return [b.left - sb.left + b.width / 2, b.top - sb.top + b.height / 2]; };
    const [ix, iy] = c(inv);
    for (const id of ["sun", "grid", "bat", "home", "port"]) {
      const ic = stage.querySelector(`.node.${id} .ic`);
      if (!ic) continue;
      const [x, y] = c(ic);
      stage.querySelectorAll(`[data-from="${id}"]`).forEach((p) => p.setAttribute("d", `M${x.toFixed(1)} ${y.toFixed(1)} L${ix.toFixed(1)} ${iy.toFixed(1)}`));
    }
  }

  _updatedText() {
    const last = this._idx["live.createTime"];  // when the cloud received the record; else the last value change
    const t = last ? new Date(last.state).getTime() || 0
      : Math.max(0, ...Object.values(this._idx).filter((st) => st.attributes.field.startsWith("live.")).map((st) => new Date(st.last_updated).getTime()));
    if (!t) return "Felicity cloud";
    const m = Math.round((Date.now() - t) / 60000);
    return m <= 1 ? "Updated just now" : m < 60 ? `Updated ${m} min ago` : `Updated ${Math.round(m / 60)} h ago`;
  }

  _dayCharts() {
    const cw = this._cw(), wide = cw >= 900, colW = (wide ? (cw - 48 - 32) / 3 - 4 : Math.max(300, cw - 56)) - 24, h0 = wide ? 170 : 120;
    const date = DAY(UI.day), h = this._dayHist(date), nav = this._dayNav();
    if (!h || !h.rows.length) return nav + this._empty(h);
    const r = h.rows, k = (i, sign) => this._kwh(r, i, sign).toFixed(1);
    const e = { pv: k(COL.pv, 1), pv1: k(COL.pv1, 1), pv2: k(COL.pv2, 1), bought: k(COL.grid, 1), charged: k(COL.bat, 1), used: k(COL.bat, -1), home: k(COL.home, 1) };
    const T = this._tar(date), ok = T && !T.error, zs = ok ? this._zones(T) : {};
    const zoneBands = ok ? T.bands.map(([from, to, id]) => ({ from, to, col: (zs[id] || {}).color || "#888", op: 0.13 })) : [];
    const cost = ok && !T.day.error ? `<i>· <b>${MONEY(T.day.grid_cost, T.tariff.currency)}</b></i>` : "";
    const totals = (items) => `<span class="tot">${items.map(([n, x, c]) => `<i style="color:${c}">${n} <b>${x}</b></i>`).join("")}<i>kWh</i></span>`;
    const box = (title, total, chart) => `<div class="dbox"><div class="dbox-h">${title}</div><div class="dbox-t"><span>${total}</span></div>${chart}</div>`;
    const maxV = (i) => Math.max(0, ...r.map((x) => x[i] || 0)).toFixed(0);
    // grid voltage: range while the grid was there, and the minutes it was not (blackouts)
    const gv = r.map((x) => x[COL.gridv]).filter((v) => v !== null && v !== undefined), on = gv.filter((v) => v >= 100);
    const off = gv.length - on.length, offMin = r.length > 1 ? Math.round(off * (r[r.length - 1][0] - r[0][0]) / 60000 / (r.length - 1)) : 0;
    const gridTot = on.length ? `min <b>${Math.min(...on).toFixed(0)}</b> · max <b>${Math.max(...on).toFixed(0)}</b> V` +
      (off ? ` · <i style="color:#ff5d5d">no grid ~${DUR(offMin / 60)}</i>` : "") : "no grid";
    return nav + `<div class="days">` +
      box("Power through the day", totals([["Home used", e.home, COLOR.home], ["Bought", e.bought, COLOR.grid], ["Battery charged", e.charged, COLOR.charge],
        ["Battery used", e.used, COLOR.use], ...(+e.pv > 0 ? [["Solar", e.pv, COLOR.sun]] : [])]), this._lineChart("p", r, date, [
        { i: COL.pv, col: COLOR.sun, name: "Solar" }, { i: COL.grid, col: COLOR.grid, name: "Grid" },
        { i: COL.bat, col: COLOR.charge, name: "Battery" }, { i: COL.home, col: COLOR.home, name: "Home" },
        { i: COL.soc, soc: true, col: "#ffffff80", name: "SOC", dash: true }], { h: wide ? 170 : 150, w: colW })) +
      box("Solar production", totals([["PV1", e.pv1, "#ffb547"], ["PV2", e.pv2, "#ffe066"], ["Total", e.pv, "#ff9f43"]]), this._lineChart("pv", r, date, [
        { i: COL.pv1, col: "#ffb547", name: "PV1", area: true }, { i: COL.pv2, col: "#ffe066", name: "PV2", area: true },
        { i: COL.pv, col: "#ff9f43", name: "Total" }], { h: h0, w: colW })) +
      box("Bought from grid", totals([["Bought", e.bought, COLOR.grid]]).replace("</span>", cost + "</span>"), this._lineChart("g", r, date, [
        { i: COL.grid, sign: 1, col: COLOR.grid, name: "Bought from grid", area: true }], { h: h0, w: colW, bands: zoneBands }) +
        (ok && T.bands.length && T.day.zones ? `<div class="legend">${T.day.zones.map((z) => `<span style="--c:${z.color}">${ESC(z.name)} ${(+z.grid_kwh).toFixed(1)} kWh</span>`).join("")}</div>` : "")) +
      box("Battery charging and use", totals([["Charged", e.charged, COLOR.charge], ["Used", e.used, COLOR.use]]), this._lineChart("b", r, date, [
        { i: COL.bat, sign: -1, col: COLOR.use, name: "Used by the home", area: true },
        { i: COL.bat, sign: 1, col: COLOR.charge, name: "Charge", area: true }], { h: h0, w: colW })) +
      box("Solar panel voltage", `highest PV1 ${maxV(COL.pv1v)} V · PV2 ${maxV(COL.pv2v)} V`,
        this._lineChart("pvv", r, date, [{ i: COL.pv1v, col: "#ffb547", name: "PV1" }, { i: COL.pv2v, col: "#ffe066", name: "PV2" }],
          { h: h0, w: colW, fit: true, unit: "V" })) +
      box("Grid voltage", gridTot, this._lineChart("gv", r, date, [{ i: COL.gridv, col: COLOR.grid, name: "Grid voltage" }],
        { h: h0, w: colW, fit: true, unit: "V" })) + `</div>`;
  }

  // series rows from the first day / month with data; columns: t, gridInput, feedOutput, generateEnergy, batCharEnergy, batDisEnergy, offGridEnergy, gridTiedEnergy
  _allRows() {
    const s = this._series(UI.unit);
    if (!s || !s.rows.length) return null;
    const rows = s.rows.filter((r) => r[1] !== null), f = rows.findIndex((r) => r.slice(1).some((v) => v));
    return f < 0 ? [] : rows.slice(f);
  }

  _allTime() {
    const unit = UI.unit, s = this._series(unit);
    const tabs = `<div class="tabs">${[["day", "Daily"], ["month", "Monthly"]].map(([u, t]) => `<button data-unit="${u}" class="${u === unit ? "on" : ""}">${t}</button>`).join("")}</div>`;
    const rows = this._allRows();
    if (!rows || !rows.length) return tabs + this._empty(s, s ? "No data" : "Loading… (first time up to 20 s)");
    const ser = [[1, COLOR.grid, "Grid"], [5, COLOR.use, "Battery"], [3, COLOR.sun, "Solar"]];
    const sum = (i) => rows.reduce((a, r) => a + (r[i] || 0), 0);
    const cw = this._cw(), W = Math.round(cw >= 900 ? cw - 48 : Math.max(300, cw - 56)), H = cw >= 900 ? 190 : 150, L = 30, R = 6, T = 14, B = 18;
    let hi = 5;
    for (const r of rows) for (const [i] of ser) hi = Math.max(hi, r[i] || 0);
    const step = hi > 200 ? 100 : hi > 80 ? 40 : hi > 40 ? 20 : hi > 20 ? 10 : 5;
    hi = Math.ceil(hi * 1.05 / step) * step;
    const n = rows.length, slot = (W - L - R) / n, bw = Math.max(1.2, Math.min(7, slot / 3.6));
    const Y = (v) => T + (1 - v / hi) * (H - T - B);
    let g = "";
    for (let v = 0; v <= hi; v += step) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" class="gl${v === 0 ? " zero" : ""}"/><text x="${L - 4}" y="${Y(v) + 3}" class="ax" text-anchor="end">${v}</text>`;
    g += `<text x="${L}" y="7" class="ax">kWh</text>`;
    const every = Math.ceil(n / 7);
    rows.forEach((r, j) => {
      const x0 = L + j * slot + (slot - bw * ser.length) / 2;
      ser.forEach(([i, col], m) => {
        const v = r[i] || 0;
        if (v > 0) g += `<rect x="${(x0 + m * bw).toFixed(1)}" y="${Y(v).toFixed(1)}" width="${(bw - 0.4).toFixed(1)}" height="${(Y(0) - Y(v)).toFixed(1)}" rx="1" fill="${col}"/>`;
      });
      if (j % every === 0) g += `<text x="${L + j * slot + slot / 2}" y="${H - 4}" class="ax" text-anchor="middle">${unit === "day" ? r[0].slice(8, 10) + "." + r[0].slice(5, 7) : MONTHS[+r[0].slice(5, 7) - 1]}</text>`;
    });
    let tip = "", hv = "";
    if (this._hover && this._hover.id === "a") {
      const j = Math.min(n - 1, Math.floor(this._hover.x * n)), r = rows[j];
      hv = `<rect x="${L + j * slot}" y="${T}" width="${slot}" height="${H - T - B}" fill="#ffffff10"/>`;
      tip = `<b>${unit === "day" ? new Date(r[0] + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "long" }) : MONTHS[+r[0].slice(5, 7) - 1] + " " + r[0].slice(0, 4)}</b>` +
        ser.map(([i, col, name]) => `<span style="color:${col}">${name} ${(r[i] || 0).toFixed(1)} kWh</span>`).join("") +
        `<span style="color:${COLOR.home}">Home ${(r[6] || 0).toFixed(1)} kWh</span>` + (unit === "day" ? `<span class="hint">tap again / click to open the day</span>` : "");
    }
    return tabs + `<div class="totals">
        <div><span>Bought from grid</span><b style="color:${COLOR.grid}">${sum(1).toFixed(0)}</b></div>
        <div><span>Used from battery</span><b style="color:${COLOR.use}">${sum(5).toFixed(0)}</b></div>
        <div><span>Solar produced</span><b style="color:${COLOR.sun}">${sum(3).toFixed(0)}</b></div>
        <div><span>Home used</span><b style="color:${COLOR.home}">${sum(6).toFixed(0)}</b></div></div>
      <div class="chart" data-chart="a"><svg viewBox="0 0 ${W} ${H}" class="ch">${g}${hv}
        <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" class="hit"/></svg><div class="scrub"></div>
        <div class="tip"${tip ? "" : " hidden"}>${tip}</div></div>
      <div class="legend">${ser.map(([, col, name]) => `<span style="--c:${col}">${name}</span>`).join("")}<span class="muted">kWh · ${unit === "day" ? "since the first day with data" : "last 12 months"}</span></div>`;
  }

  // ---- charts tab: ready-made diagnostic sets and a custom parameter picker (like the Felicity app's data filter) ----

  _explorer() {
    const preset = EXPLORER_PRESETS.find((p) => p.id === UI.preset) || EXPLORER_PRESETS[0];
    let fields = preset.fields;
    if (preset.id === "custom") {
      if (!UI.custom) { try { UI.custom = JSON.parse(localStorage.getItem("felicity-custom-fields") || "null"); } catch (e) { UI.custom = null; } }
      fields = UI.custom && UI.custom.length ? UI.custom : ["pvTotalPower", "acTtlInpower", "emsPower"];
      UI.custom = fields;
    }
    const date = DAY(UI.day), key = `x:${this._sn}:${date}:${fields.join(",")}`;
    this._get(key, `felicity/hist?sn=${this._sn}&date=${date}&fields=${fields.join(",")}`, UI.day === 0 ? 120000 : 0);
    const h = this._hist[key];
    const head = `<div class="pills">${EXPLORER_PRESETS.map((p) => `<button data-preset="${p.id}" class="${p.id === preset.id ? "on" : ""}">${p.name}</button>`).join("")}</div>
      <div class="xintro">${preset.hint}</div>` +
      (preset.id === "custom" ? `<div class="picker">${EXPLORER_FIELDS.map(([group, items]) => `<div class="pg"><div class="pg-h">${group}</div><div class="pg-c">${items.map(([f, name]) =>
        `<button data-field="${f}" class="${fields.includes(f) ? "on" : ""}">${name}</button>`).join("")}</div></div>`).join("")}</div>` : "") + this._dayNav();
    if (!h || !h.rows.length) return head + this._empty(h);
    // one chart per unit, sharing the time axis
    const got = h.fields || [], byUnit = {};
    got.forEach((f, n) => { const m = FIELD_META[f] || { name: f, unit: "" }; (byUnit[m.unit] ||= []).push({ i: n + 1, col: SERIES_COLORS[n % SERIES_COLORS.length], name: m.name }); });
    const missing = fields.filter((f) => !got.includes(f)).map((f) => (FIELD_META[f] || { name: f }).name);
    const units = Object.entries(byUnit), one = units.length === 1;
    const cw = this._cw(), wide = cw >= 900 && !one, colW = (wide ? (cw - 48 - 16) / 2 - 4 : Math.max(300, cw - 56)) - 24;
    const charts = units.map(([unit, series], n) => {
      const isPower = unit === "W";
      const stats = series.map((s) => {
        const vals = h.rows.map((r) => r[s.i]).filter((x) => x !== null && x !== undefined);
        if (!vals.length) return "";
        const f = (x) => (isPower ? SFMT(x) : `${Math.round(x * 10) / 10} ${unit}`);
        return `<i style="color:${s.col}">${ESC(s.name)}: <b>${f(Math.min(...vals))} … ${f(Math.max(...vals))}</b>, avg ${f(vals.reduce((a, b) => a + b, 0) / vals.length)}</i>`;
      }).join("");
      return `<div class="dbox"><div class="dbox-h">${UNIT_TITLE[unit] || ESC(unit)}</div><div class="dbox-t xstats">${stats}</div>` +
        this._lineChart(`x${n}`, h.rows, date, series, { h: cw >= 900 ? 190 : 150, w: colW, fit: !isPower, unit }) + `</div>`;
    }).join("");
    return head + `<div class="xcharts${one ? " one" : ""}">${charts}</div>` + (missing.length ? `<div class="xmiss">Not reported by this inverter: ${ESC(missing.join(", "))}</div>` : "");
  }

  // ---- settings tab: the inverter's settings laid out like the Felicity app (tabs -> cards -> rows) ----

  _settingsData() {
    const tabs = {};
    let readAt = 0;
    for (const st of Object.values(this._set)) {
      const a = st.attributes;
      if (!a.group || !a.card) continue;
      const label = (a.friendly_name || "").replace(/^Felicity (inverter|battery) \d+ /, "");
      ((tabs[a.group] ||= {})[a.card] ||= []).push({ a, label, order: a.order ?? 100, state: st.state, unit: a.unit_of_measurement || "" });
      readAt = Math.max(readAt, new Date(st.last_updated).getTime());
    }
    return { tabs, readAt };
  }

  _status(key) {
    const st = (this._sendState || {})[key];
    if (!st) return "";
    if (st.state === "sending") return `<em class="st">sending…</em>`;
    if (st.state === "sent") return `<em class="st ok">✓ sent${(this._pending || {})[key] ? " · waiting for the inverter to confirm…" : ""}</em>`;
    return `<em class="st err" title="${ESC(st.msg)}">✕ ${ESC(st.msg || "error")}</em>`;
  }

  _settingRow(r, fmtV) {
    const a = r.a, key = a.key, label = ESC(r.label);
    if (!a.writable) return `<div class="srow ro"><span class="lbl2">${label}</span><span class="ctl"><b title="Read-only in Home Assistant">${ESC(fmtV(r))}</b><i class="u">${r.unit && fmtV(r) !== "—" ? ESC(r.unit) : ""}</i></span>${this._status(key)}</div>`;
    const edits = this._edits || {}, changed = key in edits, pend = this._pendingFor(key, a.raw);
    const opts = a.options ? Object.entries(a.options) : null;
    const isToggle = opts && opts.length === 2 && opts.some(([, l]) => /^disable$/i.test(l)) && opts.some(([, l]) => /^enable$/i.test(l));
    const cur = changed ? String(edits[key]) : String(pend !== undefined ? pend : a.raw);
    let ctl;
    if (isToggle) {
      const onV = opts.find(([, l]) => /^enable$/i.test(l))[0], offV = opts.find(([, l]) => /^disable$/i.test(l))[0];
      ctl = `<label class="tgl"><input type="checkbox" data-key="${key}" data-on="${onV}" data-off="${offV}"${cur === onV ? " checked" : ""} aria-label="${label}"><span></span></label><i class="u"></i>`;
    } else if (opts) {
      ctl = `<select data-key="${key}" aria-label="${label}">${opts.map(([v, l]) => `<option value="${ESC(v)}"${v === cur ? " selected" : ""}>${ESC(l)}</option>`).join("")}</select><i class="u"></i>`;
    } else {
      const num = changed ? edits[key] : pend !== undefined ? pend : ["unknown", "unavailable", ""].includes(r.state) ? "" : Number(r.state);
      const [mn, mx] = a.range || [];
      ctl = `<input type="number" inputmode="decimal" data-key="${key}" value="${num}" ${mn !== undefined ? `min="${mn}" max="${mx}"` : ""} step="${a.step || "any"}" aria-label="${label}"><i class="u">${ESC(r.unit)}</i>`;
    }
    const confirming = (this._confirm || {})[key];
    const btn = `<button class="send${a.risky ? " risky" : ""}${confirming ? " confirm" : ""}" data-send="${key}"${changed ? "" : " hidden"}>${confirming ? "Confirm" : "Send"}</button>`;
    return `<div class="srow${changed ? " changed" : ""}"><span class="lbl2">${a.risky ? '<b class="warn" title="Risky: needs confirmation">⚠</b>' : ""}${label}${a.range ? `<small>${a.range[0]}–${a.range[1]}</small>` : ""}</span>
      <span class="ctl">${ctl}${btn}</span>${this._status(key)}${confirming ? `<em class="st err">Risky setting — press Confirm to send</em>` : ""}</div>`;
  }

  // a value we sent is shown until the inverter's re-read setting matches it (max 2 min)
  _pendingFor(key, raw) {
    const p = (this._pending || {})[key];
    if (!p) return undefined;
    const same = typeof p.value === "object" ? Object.entries(p.value).every(([f, v]) => String((raw || {})[f]) === String(v)) : String(raw) === String(p.value);
    if (same || Date.now() - p.at > 120000) { delete this._pending[key]; return undefined; }
    return p.value;
  }

  // POST one setting (or a whole time-of-use rule); shows "sent" until the inverter confirms it
  async _post(key, value, confirm) {
    (this._sendState ||= {})[key] = { state: "sending" };
    this._rerender();
    try {
      await this._hass.callApi("POST", "felicity/setting", { sn: this._sn, key, value, confirm });
      const sent = this._sendState[key] = { state: "sent" };
      (this._pending ||= {})[key] = { value: typeof value === "object" ? { ...value } : value, at: Date.now() };
      setTimeout(() => { if (this._sendState[key] === sent) { delete this._sendState[key]; this._rerender(); } }, 45000);
      return true;
    } catch (e) {
      this._sendState[key] = { state: "error", msg: ERR(e) };
      return false;
    } finally {
      this._rerender();
    }
  }

  async _sendRule(key) {
    const value = (this._redits || {})[key];
    if (value && await this._post(key, value, false)) { delete this._redits[key]; this._rerender(); }
  }

  async _send(key) {
    const value = (this._edits || {})[key];
    if (value === undefined) return;
    const risky = !!(this._set[key] && this._set[key].attributes.risky);
    this._confirm ||= {};
    if (risky && !this._confirm[key]) {  // first click arms, the second sends
      this._confirm[key] = true;
      setTimeout(() => { if (this._confirm[key]) { delete this._confirm[key]; this._rerender(); } }, 8000);
      this._rerender();
      return;
    }
    delete this._confirm[key];
    if (await this._post(key, value, risky)) { delete this._edits[key]; this._rerender(); }
  }

  _ruleTable(rules) {
    const modeful = rules.some((r) => r.a.ruleMode !== undefined);
    const MODES = { 0: "Off", 1: "Charge", 2: "Discharge", 3: "AC couple" };
    const ed = this._redits || {};
    const row = (r) => {
      const key = r.a.key, pend = this._pendingFor(key, r.a);
      const a = { ...r.a, ...(pend || {}), ...(ed[key] || {}) }, changed = !!ed[key];
      const cb = (f, label) => `<label class="tgl sm"><input type="checkbox" data-rule="${key}" data-f="${f}"${String(a[f]) === "1" ? " checked" : ""} aria-label="${label}"><span></span></label>`;
      const mode = modeful
        ? `<span><select data-rule="${key}" data-f="ruleMode" aria-label="Mode">${Object.entries(MODES).map(([v, l]) => `<option value="${v}"${String(a.ruleMode) === v ? " selected" : ""}>${l}</option>`).join("")}</select></span>`
        : `${cb("gridChangingEnable", "Charge from grid")}${cb("genChangingEnable", "Charge from generator")}`;
      const time = (f) => `<input type="text" inputmode="numeric" pattern="[0-2][0-9]:[0-5][0-9]" maxlength="5" placeholder="HH:MM" data-rule="${key}" data-f="${f}" value="${ESC(a[f])}" aria-label="${f === "startTime" ? "Start" : "Stop"}">`;
      return `<div class="tr${changed ? " changed" : ""}">${mode}${time("startTime")}${time("stopTime")}
        <input type="number" inputmode="numeric" data-rule="${key}" data-f="power" value="${ESC(a.power)}" min="0" max="${ESC(a.power_max || 6000)}" step="50" aria-label="Power W">
        <input type="number" inputmode="numeric" data-rule="${key}" data-f="soc" value="${ESC(a.soc)}" min="0" max="100" aria-label="Battery %">
        <button class="send" data-rsend="${key}"${changed ? "" : " hidden"}>Send</button>${this._status(key)}</div>`;
    };
    return `<div class="tou-t"><div class="tt"><div class="th">${modeful ? "<span>Mode</span>" : "<span>Grid</span><span>Gen</span>"}<span>Start</span><span>Stop</span><span>Power W</span><span>Batt %</span><span></span></div>` +
      rules.map(row).join("") + `</div></div>`;
  }

  _settings() {
    const { tabs, readAt } = this._settingsData();
    const names = [...SETTING_TABS, ...Object.keys(tabs).filter((t) => !SETTING_TABS.includes(t)).sort()].filter((t) => tabs[t]);
    if (!names.length) return `<div class="ch-empty">No settings yet (they are read every 10 minutes)</div>`;
    const tab = names.includes(UI.stab) ? UI.stab : names[0];
    const pills = `<div class="pills">${names.map((t) => `<button data-stab="${ESC(t)}" class="${t === tab ? "on" : ""}">${ESC(t)}</button>`).join("")}</div>`;
    const fmtV = (r) => {
      if (["unknown", "unavailable", ""].includes(r.state)) return "—";
      const n = Number(r.state);
      return !isNaN(n) && r.unit ? (Number.isInteger(n) ? n : n.toFixed(1)) : r.state;
    };
    const cards = Object.entries(tabs[tab]).sort((a, b) => Math.min(...a[1].map((r) => r.order)) - Math.min(...b[1].map((r) => r.order)) || a[0].localeCompare(b[0]));
    const htmls = cards.map(([card, rows]) => {
      rows.sort((a, b) => a.order - b.order || a.label.localeCompare(b.label));
      const rules = rows.filter((r) => r.a.key.startsWith("ecoRule"));
      const body = rows.filter((r) => !r.a.key.startsWith("ecoRule")).map((r) => this._settingRow(r, fmtV)).join("") + (rules.length ? this._ruleTable(rules) : "");
      return [rows.length + (rules.length ? 3 : 0) + 1.5, `<div class="scard"><div class="scard-h">${ESC(card)}</div>${body}</div>`];
    });
    // balanced columns: each card goes into the currently shortest column (keeps reading order roughly)
    const ncol = Math.max(1, Math.floor((this._cw() - 32) / 300));
    const cols = Array.from({ length: ncol }, () => ({ h: 0, items: [] }));
    for (const [h, el] of htmls) { const c = cols.reduce((a, b) => (b.h < a.h ? b : a)); c.items.push(el); c.h += h; }
    return `${pills}<div class="tipwarn">⚠ Changes go straight to your inverter. A wrong value can cut power, harm the battery or the inverter, or void the warranty — change only what you understand, at your own risk.</div><div class="sread">Read from the inverter at ${readAt ? CLOCK(readAt) : "—"} · change a value and tap Send · ⚠ = needs a second confirmation</div>
      <div class="scards" style="grid-template-columns:repeat(${ncol},minmax(0,1fr))">${cols.map((c) => `<div class="scol">${c.items.join("")}</div>`).join("")}</div>`;
  }

  // ---- work mode: time-of-use rules as a 24 h timeline + today's SOC / grid / battery over the rule bands ----

  _rules() {
    const keys = Object.keys(this._set).filter((k) => /^ecoRule\d+$/.test(k)).sort((a, b) => +a.slice(7) - +b.slice(7));
    return keys.map((k) => {
      const a = this._set[k].attributes;
      if (!a.startTime || !a.stopTime) return null;
      const from = MINS(a.startTime) / 60, stop = a.stopTime === "23:59" ? 24 : MINS(a.stopTime) / 60;
      const spans = from < stop ? [[from, stop]] : [[from, 24], [0, stop]];  // a rule may run past midnight
      const soc = +a.soc;
      const days = Array.isArray(a.daysOfEffectiveWeek) && a.daysOfEffectiveWeek.length && a.daysOfEffectiveWeek.length < 7
        ? " · " + a.daysOfEffectiveWeek.map((d) => String(d).slice(0, 2).toLowerCase()).join(" ") : "";
      let kind, text, col;
      if (a.ruleMode !== undefined) {  // IVGM / T-REX: explicit rule mode
        const m = String(a.ruleMode);
        if (m === "0") return null;
        if (m === "1") { kind = "charge"; col = COLOR.grid; text = `Charge from grid to ${soc}% · ${FMT(+a.power)}`; }
        else if (m === "2") { kind = "sell"; col = COLOR.use; text = `Discharge / sell down to ${soc}% · ${FMT(+a.power)}`; }
        else { kind = "ac"; col = "#2ec4b6"; text = `AC couple · ${FMT(+a.power)}`; }
      } else {
        const src = a.gridChangingEnable === "1" ? "grid" : a.genChangingEnable === "1" ? "generator" : null;
        if (src) { kind = "charge"; col = COLOR.grid; text = `Charge from ${src} to ${soc}% · ${FMT(+a.power)}`; }
        else if (soc >= 100) { kind = "hold"; col = "#9aa6b8"; text = "Keep battery at 100% (charges from grid if Grid Charge is on)"; }
        else { kind = "use"; col = COLOR.charge; text = `Use battery down to ${soc}%`; }
      }
      return { spans, start: a.startTime, stop: a.stopTime, kind, text: text + days, col, soc };
    }).filter(Boolean);
  }

  _tou() {
    const rules = this._rules();
    const sv = (key) => (this._set[key] ? this._set[key].state : "—");
    const W = Math.round(Math.max(300, this._cw() - 56)), H = 56, L = 14, R = 14;
    const X = (h) => L + h / 24 * (W - L - R);
    const LABEL = { charge: (r) => "⚡ " + r.soc + "%", hold: () => "keep 100%", sell: (r) => "sell ≥ " + r.soc + "%", ac: () => "AC couple", use: (r) => "≥ " + r.soc + "%" };
    let bar = "";
    for (const r of rules) for (const [a, b] of r.spans)
      bar += `<rect x="${X(a).toFixed(1)}" y="6" width="${Math.max(1, X(b) - X(a) - 1).toFixed(1)}" height="26" rx="6" fill="${r.col}" fill-opacity="${r.kind === "hold" ? 0.35 : 0.8}"/>` +
        (X(b) - X(a) > 60 ? `<text x="${((X(a) + X(b)) / 2).toFixed(1)}" y="23" class="seg" text-anchor="middle">${LABEL[r.kind](r)}</text>` : "");
    const now = new Date(), nh = now.getHours() + now.getMinutes() / 60;
    bar += `<line x1="${X(nh)}" x2="${X(nh)}" y1="2" y2="36" class="now"/>`;
    for (let h = 0, tick = W < 560 ? 6 : 3; h <= 24; h += tick) bar += `<text x="${X(h)}" y="${H - 4}" class="ax" text-anchor="${h === 0 ? "start" : h === 24 ? "end" : "middle"}">${PAD(h)}:00</text>`;
    const cur = rules.find((r) => r.spans.some(([a, b]) => nh >= a && nh < b));
    const list = rules.map((r) => `<div class="rule${cur === r ? " on" : ""}"><span class="dot" style="background:${r.col}"></span>
        <b>${ESC(r.start)}–${ESC(r.stop)}</b><span>${ESC(r.text)}</span>${cur === r ? "<em>now</em>" : ""}</div>`).join("");
    // today's history with the rule bands behind it
    const date = DAY(0), h = this._dayHist(date);
    const bands = rules.flatMap((r) => r.spans.map(([from, to]) => ({ from, to, col: r.col, op: r.kind === "hold" ? 0.06 : 0.1 })));
    const chart = !h || !h.rows.length ? this._empty(h, "No data") : this._lineChart("t", h.rows, date, [
      { i: COL.grid, col: COLOR.grid, name: "Grid" }, { i: COL.bat, col: COLOR.charge, name: "Battery (above 0 = charging)" },
      { i: COL.soc, soc: true, col: "#ffffff", name: "SOC", dash: true }], { h: 190, w: W - 24, bands });
    // the tariff's zones under the schedule, and a hint when a grid-charge rule runs in a dearer zone
    const T = this._tar(date);
    let priceRow = "", warn = "";
    if (T && !T.error && T.bands.length) {
      const zs = this._zones(T), cheapest = Math.min(...T.tariff.zones.map((z) => z.price));
      priceRow = `<div class="pstrip" style="padding:0 ${(R / W * 100).toFixed(2)}% 0 ${(L / W * 100).toFixed(2)}%">${this._strip(T.bands, zs, false)}</div>`;
      const dear = rules.filter((r) => r.kind === "charge").map((r) => {
        const zones = T.bands.filter(([a, b, id]) => r.spans.some(([f, t]) => a < t && b > f) && zs[id] && zs[id].price > cheapest).map(([, , id]) => zs[id]);
        return zones.length ? `${ESC(r.start)}–${ESC(r.stop)} charges from the grid during ${[...new Set(zones.map((z) => `${ESC(z.name)} (${PRICE(z.price, T.tariff.currency)})`))].join(", ")}` : null;
      }).filter(Boolean);
      if (dear.length) warn = `<div class="tipwarn">💡 ${dear.join("; ")}. Charging in the cheapest zone (${PRICE(cheapest, T.tariff.currency)}) costs less.</div>`;
    }
    const legend = { charge: "Charge from grid", use: "Use battery", hold: "Keep at 100%", sell: "Discharge / sell", ac: "AC couple" };
    return `<div class="tou-head"><span>Schedule: <b>${/enable/i.test(sv("timeOfUseEnable")) ? "on" : "off"}</b></span><span>Days: <b>${ESC(sv("touEffectiveWeek"))}</b></span>
        <span>Charging from the grid: <b>${/enable/i.test(sv("genChargeEnable")) ? "allowed" : "not allowed"}</b></span></div>
      <svg viewBox="0 0 ${W} ${H}" class="ch">${bar}</svg>${priceRow}${warn}
      <div class="legend">${[...new Map(rules.map((r) => [r.kind, r])).values()].map((r) => `<span style="--c:${r.col}">${legend[r.kind]}</span>`).join("")}</div>
      <div class="rules">${list}</div>
      <div class="dbox" style="margin-top:10px"><div class="dbox-h">Today: how the rules worked</div><div class="dbox-t"><span>SOC now <b>${this._v.battery_soc === null ? "—" : Math.round(this._v.battery_soc)}%</b></span></div>${chart}</div>`;
  }

  // ---- tariff: price now, cost by zone, the editor, the Energy dashboard ----

  // zones of the tariff that was valid on the payload's date
  _zones(T) { return Object.fromEntries(((T.day_tariff ? T.day_tariff.zones : T.tariff.zones) || []).map((z) => [z.id, z])); }

  _zoneTable(T, d) {
    if (!d || d.error) return this._empty(d && { error: d.error }, "");
    const cur = T.tariff.currency;
    const rows = d.zones.map((z) => `<div class="zt"><span><i class="zd" style="--z:${z.color}"></i>${ESC(z.name)}</span>
        <b>${(+z.grid_kwh).toFixed(2)} kWh</b><em>${z.price == null ? "" : "× " + PRICE(z.price, cur)}</em><b>${MONEY(z.grid_cost, cur)}</b></div>`).join("");
    return `<div class="ztab">${rows}<div class="zt total"><span>Total bought</span><b>${(+d.grid_kwh).toFixed(2)} kWh</b><em></em><b>${MONEY(d.grid_cost, cur)}</b></div></div>`;
  }

  // whole days only: during a day, grid energy stored in the battery shows as a loss until the home uses it
  _savedRow(d, cur, label) {
    return `<div class="tp-s"><span>${label} <small>vs. buying everything from the grid</small></span><b class="${d.saved < 0 ? "neg" : "pos"}">${MONEY(d.saved, cur)}</b></div>`;
  }

  _tariffPanel(T, tabbed) {
    if (!T || T.error) return `<div class="tp">${this._empty(T, "")}</div>`;
    const cur = T.tariff.currency, n = T.now, z = n.zone, d = T.day;
    const next = n.next_zone ? `<div class="tp-next">${ESC(n.next_zone.name)} from <b>${CLOCK(n.next_at)}</b> · ${PRICE(n.next_zone.price, cur)} · in ${DUR(Math.max(0, (new Date(n.next_at) - Date.now()) / 36e5))}</div>` : "";
    const Y = this._tar(DAY(1)), yd = Y && !Y.error && !Y.day.error ? Y.day : null;
    return `<div class="tp">
      <div class="tp-h"><span>Electricity price now</span>${tabbed ? `<button data-view="tariff">Tariff ›</button>` : ""}</div>
      <div class="tp-now">${z ? `<span class="zd big" style="--z:${z.color}"></span><b>${ESC(z.name)}</b>` : "<b>Dynamic price</b>"}<strong>${PRICE(n.price, cur)}</strong></div>${next}
      ${T.bands.length ? this._strip(T.bands, this._zones(T)) + this._ticks() : ""}
      <div class="tp-t">Bought from the grid today</div>${this._zoneTable(T, d)}
      ${yd && yd.home_kwh > MIN_KWH ? this._savedRow(yd, cur, "Saved yesterday") : ""}
      ${d.export_kwh > MIN_KWH ? `<div class="tp-s"><span>Sold to grid ${(+d.export_kwh).toFixed(1)} kWh</span><b class="pos">${MONEY(d.export_earned, cur)}</b></div>` : ""}</div>`;
  }

  // the editor works on a copy (UI.tdraft) until Save; an untouched copy follows the saved tariff
  _draft(T) {
    const saved = JSON.stringify(T.tariff);
    const untouched = UI.tdraft && JSON.stringify(UI.tdraft) === UI.tbase;
    if (!UI.tdraft || UI.tdraftSn !== this._sn || (untouched && UI.tbase !== saved)) {
      UI.tdraft = JSON.parse(saved); UI.tbase = saved; UI.tdraftSn = this._sn; UI.terr = "";
    }
    return UI.tdraft;
  }

  // times covered by more than one zone (15-minute steps over this week): the lower zone in the list is used there
  _overlaps(t) {
    const mon = MONDAY(), found = new Map();
    for (let i = 0; i < 7; i++) for (let m = 0; m < 1440; m += 15) {
      const d = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + i, 0, m);
      const wd = ISO_DAY(d), yd = ISO_DAY(new Date(d.getFullYear(), d.getMonth(), d.getDate() - 1));
      const hits = (t.zones || []).filter((z) => (z.ranges || []).some((r) => inRange(r, m, wd, yd)));
      if (hits.length > 1) {
        const k = hits.map((z) => z.name).join(" & ");
        (found.get(k) || found.set(k, { slots: new Set(), win: hits[hits.length - 1].name }).get(k)).slots.add(m / 15);
      }
    }
    if (!found.size) return "";
    // contiguous runs of 15-minute slots (wrapping past midnight)
    const runs = (slots) => {
      const out = [];
      for (let s = 0; s < 96; s++) {
        if (!slots.has(s) || (slots.has((s + 95) % 96) && slots.size < 96)) continue;
        let e = s;
        while (slots.has((e + 1) % 96) && e - s < 95) e++;
        out.push(`${HH(s / 4)}–${HH(((e + 1) % 96) / 4)}`);
      }
      return out.join(", ") || "all day";
    };
    return `<div class="tipwarn">⚠ Overlapping hours: ${[...found].map(([k, f]) => `${ESC(k)} at ${runs(f.slots)} — <b>${ESC(f.win)}</b> is used there`).join("; ")}.</div>`;
  }

  _weekPreview(t) {
    const zones = Object.fromEntries((t.zones || []).map((z) => [z.id, z])), mon = MONDAY(), today = (new Date().getDay() + 6) % 7;
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((name, i) => {
      const bands = [];
      for (let m = 0; m < 1440; m += 15) {
        const z = zoneAt(t, new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + i, 0, m)), id = z ? z.id : null;
        if (bands.length && bands[bands.length - 1][2] === id) bands[bands.length - 1][1] = (m + 15) / 60;
        else bands.push([m / 60, (m + 15) / 60, id]);
      }
      return `<div class="wk${i === today ? " on" : ""}"><span>${name}</span><div>${this._strip(bands, zones, i === today)}</div></div>`;
    }).join("") + `<div class="wk"><span></span><div>${this._ticks()}</div></div>`;
  }

  _tariffEditor(T, t, ro) {
    const dis = ro ? " disabled" : "", dyn = t.mode === "entity", sym = ESC(CUR[t.currency] || t.currency);
    const changed = JSON.stringify(t) !== JSON.stringify(T.tariff);
    const days = (r, v) => `<select data-rf="${r}" aria-label="Days"${dis}>${Object.entries(T.day_sets).map(([k, n]) => `<option value="${k}"${k === v ? " selected" : ""}>${n}</option>`).join("")}</select>`;
    const zones = dyn ? (() => {
      const opts = Object.values(this._hass.states).filter((s) => /\/(k|M)?Wh$/.test(s.attributes.unit_of_measurement || "") && !s.entity_id.startsWith("sensor.electricity_tariff_"))
        .map((s) => `<option value="${s.entity_id}">${ESC(s.attributes.friendly_name || s.entity_id)}</option>`).join("");
      const st = this._hass.states[t.price_entity];
      return `<label class="tf wide">Sensor with the current price per kWh
          <input data-tf="price_entity" list="felicity-prices" value="${ESC(t.price_entity)}" placeholder="sensor.nordpool_kwh_…"${dis}>
          <datalist id="felicity-prices">${opts}</datalist></label>
        <div class="tp-hint">${st ? `Now: <b>${ESC(st.state)} ${ESC(st.attributes.unit_of_measurement)}</b>. ` : "Not found yet. "}Works with Nord Pool, ENTSO-E, Tibber, Octopus and any sensor in ${ESC(t.currency)}/kWh (per MWh is converted). Costs use the price recorded at each minute.</div>`;
    })() : t.zones.map((z, i) => `<div class="zc" style="--z:${ESC(z.color)}">
        <div class="zc-h"><input type="color" data-zf="${i}:color" value="${ESC(z.color)}" aria-label="Colour"${dis}><input class="zname" data-zf="${i}:name" value="${ESC(z.name)}" aria-label="Zone name"${dis}>
          <label class="zprice"><input type="number" inputmode="decimal" step="0.001" min="0" data-zf="${i}:price" value="${ESC(z.price)}" aria-label="Price"${dis}><i>${sym}/kWh</i></label>
          ${!ro && t.zones.length > 1 ? `<button class="x" data-zdel="${i}" aria-label="Remove zone">✕</button>` : ""}</div>
        <div class="zr">${(z.ranges || []).map((r, j) => `<div class="rg"><input class="tm" data-rf="${i}:${j}:0" value="${ESC(r[0])}" maxlength="5" aria-label="From"${dis}><span>–</span><input class="tm" data-rf="${i}:${j}:1" value="${ESC(r[1])}" maxlength="5" aria-label="To"${dis}>
            ${days(`${i}:${j}:2`, r[2] || "1234567")}${ro ? "" : `<button class="x" data-rdel="${i}:${j}" aria-label="Remove hours">✕</button>`}</div>`).join("")}
          ${ro ? "" : `<button class="add" data-radd="${i}">+ hours</button>`}
          <label class="def"><input type="radio" name="fdef" data-zdef="${i}"${t.default_zone === z.id ? " checked" : ""}${dis}> all other hours</label></div></div>`).join("") +
      (ro || t.zones.length >= 8 ? "" : `<button class="add big" data-zadd>+ Add zone</button>`);
    const bar = ro ? `<div class="tp-hint">Only administrators can change the tariff.</div>` : `<div class="te-bar${changed ? " on" : ""}">
        <span class="${UI.terr ? "err" : ""}">${UI.terr ? ESC(UI.terr) : changed ? "Not saved yet" : "Saved"}</span>
        ${changed ? `<button class="ghost" data-tdiscard>Discard</button><button class="send" data-tsave${UI.tsaving ? " disabled" : ""}>${UI.tsaving ? "Saving…" : "Save tariff"}</button>` : ""}</div>`;
    const presets = `<div class="pills">${Object.entries(T.presets).map(([k, p]) => `<button data-tpreset="${k}"${dis}>${ESC(p.name)}</button>`).join("")}
      <button data-tblank${dis}>Empty — start from scratch</button></div>`;
    return `<div class="hist"><div class="sec">Your tariff</div>
      ${dyn ? "" : `<div class="xintro">Set your own zones: tap a name, price or time to change it, add hours or whole zones.
        Time not covered by any zone uses the one marked <i>all other hours</i>.</div>`}
      <div class="te-grid">
        <label class="tf">Currency<input data-tf="currency" value="${ESC(t.currency)}" maxlength="4"${dis}></label>
        <label class="tf">Paid for energy sold, per kWh<input type="number" inputmode="decimal" step="0.01" min="0" data-tf="export_price" value="${ESC(t.export_price)}"${dis}></label>
        <label class="tf">Battery reserve, %<input type="number" inputmode="numeric" min="0" max="100" data-tf="battery_reserve" value="${ESC(t.battery_reserve)}"
          placeholder="${T.runtime && T.tariff.battery_reserve == null ? `${T.runtime.reserve}% from the inverter` : "empty = from the inverter"}"${dis}></label>
      </div>
      ${dyn ? "" : `<div class="te-l">Zones and their hours <small>(HH:MM; a range may cross midnight, e.g. 23:00 – 07:00)</small></div>`}
      <div class="zcs">${zones}</div>
      ${dyn ? "" : this._overlaps(t) + `<div class="te-l">Week preview</div><div class="week">${this._weekPreview(t)}</div>`}
      ${bar}
      ${ro ? "" : `<div class="te-l">Or fill from a template <small>(replaces the zones above; nothing is saved until you press Save)</small></div>${presets}`}</div>`;
  }

  _costByDay(T) {
    const date = DAY(UI.day), D = UI.day === 0 ? T : this._tar(date);
    if (!D || D.error) return `<div class="hist"><div class="sec">Cost by day</div>${this._dayNav()}${this._empty(D, "")}</div>`;
    const dt = D.day_tariff || {}, since = dt.from ? dt.from.slice(0, 10) : null;
    const note = UI.day === 0 ? "" : !since ? "Priced with the first known tariff."
      : since > date ? `Priced with the earliest saved tariff (from ${since}); nothing older is known.`
      : `Priced with the tariff valid from ${since}${dt.changed ? " (it changed during this day: each part is priced with its own tariff)" : ""}.`;
    return `<div class="hist"><div class="sec">Cost by day</div>${this._dayNav()}
      ${D.bands.length ? this._strip(D.bands, this._zones(D), UI.day === 0) + this._ticks() : ""}${this._zoneTable(D, D.day)}
      ${UI.day > 0 && !D.day.error && D.day.home_kwh > MIN_KWH ? this._savedRow(D.day, T.tariff.currency, "Saved that day") : ""}
      ${note ? `<div class="tp-hint">${note}</div>` : ""}</div>`;
  }

  _energyHelp(T) {
    const e = T.entities || {}, code = (id) => (id ? `<code>${ESC(id)}</code>` : `<i>appears after saving</i>`), zs = this._zones(T);
    const zoneList = T.tariff.mode === "schedule" && T.tariff.zones.length > 1;
    const manual = `<ol class="howto">
        <li><b>Settings → Dashboards → Energy → Electricity grid → Add consumption.</b></li>
        ${zoneList ? `<li>Zone by zone: add each sensor below and choose <i>Use a static price</i> with its zone price.
          <div class="ents">${Object.entries(e.imports || {}).map(([id, ent]) => { const z = zs[id] || { name: id }; return `<div><i class="zd" style="--z:${z.color || "#888"}"></i>${ESC(z.name)} · ${PRICE(z.price, T.tariff.currency)}<br>${code(ent)}</div>`; }).join("")}</div></li>`
        : `<li>Consumed energy: ${code(Object.values(e.imports || {})[0])}, then <i>Use an entity with current price</i> → ${code(e.price)}.</li>`}
        <li>Sold energy (if you sell): ${code(e.meter_grid_export)} with a static price of ${PRICE(T.tariff.export_price, T.tariff.currency)}.</li>
        <li>Solar: ${code(e.meter_solar)}. Battery: ${code(e.meter_battery_discharge)} out, ${code(e.meter_battery_charge)} in.</li>
      </ol>
      <div class="tipwarn">Do not also add the cloud “Grid import today” sensor for the grid — the import would be counted twice.</div>`;
    const status = T.energy === "managed"
      ? `<div class="okbox">✓ Set up by this integration: the grid by tariff zone with its price, solar, the battery and live power. It follows your tariff — prices and zones are updated when you save.</div>`
      : T.energy === "empty"
        ? `<div class="tp-hint">The Energy dashboard is empty. Set it up from your tariff in one click: grid by zone with prices, solar, battery and live power.</div>
          ${T.admin ? `<button class="send big" data-esetup>Set up the Energy dashboard</button>${UI.eerr ? `<div class="tipwarn">${ESC(UI.eerr)}</div>` : ""}` : ""}`
        : `<div class="tp-hint">Your Energy dashboard has its own set-up, so it is left as it is. To use the tariff there:</div>${manual}`;
    return `<div class="hist"><div class="sec">Energy dashboard</div>${status}
      <div class="tp-hint">Also created: ${code(e.zone)} (current zone, for automations), ${code(e.grid_cost)}, ${code(e.saved)}, ${code(e.battery_runtime)}.
        The <i>Energy: …</i> sensors never go down within a day, so the dashboard does not count resets twice.</div>
      <a class="lnk" href="/config/energy" target="_top">Open Energy settings ›</a></div>`;
  }

  _tariffView() {
    const T = this._tar(DAY(0));
    if (!T || T.error) return this._empty(T, "");
    return `<div class="tgrid"><div>${this._tariffEditor(T, this._draft(T), !T.admin)}</div><div class="tcol">${this._costByDay(T)}${this._energyHelp(T)}</div></div>`;
  }

  async _saveTariff() {
    UI.tsaving = true;
    this._rerender();
    try {
      const r = await this._hass.callApi("POST", "felicity/tariff", { sn: this._sn, tariff: UI.tdraft });
      UI.tdraft = null; UI.terr = "";
      const h = this._hist[`t:${this._sn}:${DAY(0)}`];
      if (h && h.data) h.data.tariff = r.tariff;  // the editor shows the normalised saved tariff at once
      this._staleTariff();
      if (r.reload) setTimeout(() => { this._staleTariff(); this._rerender(); }, 12000);  // new zones: the integration reloads
    } catch (e) {
      UI.terr = ERR(e);
    }
    UI.tsaving = false;
    this._rerender();
  }

  // ---- render and events ----

  _header(tabbed) {
    const label = (sn) => `Inverter …${sn.slice(-4)}`;
    const picker = this._inverters.length > 1 && !this._cfg.sn
      ? `<select data-inv aria-label="Inverter">${this._inverters.map((sn) => `<option value="${sn}"${sn === this._sn ? " selected" : ""}>${label(sn)}</option>`).join("")}</select>`
      : this._sn && tabbed ? `<span>${label(this._sn)}</span>` : "";
    if (!tabbed) return picker ? `<div class="head mini"><span></span><div class="invsel">${picker}</div></div>` : "";
    return `<div class="head"><div class="vtabs">${VIEWS.map(([k, t]) => `<button data-view="${k}" class="${k === UI.view ? "on" : ""}" aria-pressed="${k === UI.view}">${t}</button>`).join("")}</div>
      <div class="invsel">${picker}</div></div>`;
  }

  _render() {
    this._width = this.getBoundingClientRect().width || 400;
    const tabbed = !this._cfg.show;
    const show = this._cfg.show || (VIEWS.find(([k]) => k === UI.view) || VIEWS[0])[2];
    const T = show.some((s) => ["flow", "price", "tariff"].includes(s)) ? this._tar(DAY(0)) : null;
    const section = (id, title, body) => `<div class="hist ${id}"><div class="sec">${title}</div>${body()}</div>`;
    const bottom = [
      show.includes("day") && section("day", "Day details", () => this._dayCharts()),
      show.includes("all") && section("all", "History", () => this._allTime()),
      show.includes("explore") && section("explore", "Charts", () => this._explorer()),
      show.includes("settings") && section("settings", "Inverter settings", () => this._settings()),
      show.includes("tou") && section("tou", "Charging schedule", () => this._tou()),
      show.includes("price") && this._tariffPanel(T, false),
      show.includes("tariff") && this._tariffView(),
    ].filter(Boolean).join("");
    this._patch(`${STYLES}<ha-card>${this._header(tabbed)}${show.includes("flow") ? this._overview(T, tabbed) : ""}
      ${bottom ? `<div class="bottom">${bottom}</div>` : ""}</ha-card>`);
    this._width = 0;
    this._layoutLines();
    requestAnimationFrame(() => this._layoutLines());
    this._bind();
  }

  // Update the DOM in place: only nodes whose markup changed are replaced, so the page keeps its scroll position
  _patch(html) {
    const root = this.shadowRoot;
    if (!root.firstElementChild) { root.innerHTML = html; return; }
    const tpl = document.createElement("template");
    tpl.innerHTML = html;
    const hasText = (el) => [...el.childNodes].some((x) => x.nodeType === 3 && x.textContent.trim());
    const morph = (oldP, newP, depth) => {
      const o = [...oldP.children], n = [...newP.children];
      if (o.length !== n.length) { oldP.replaceChildren(...n); return; }
      n.forEach((nc, i) => {
        const oc = o[i];
        if (oc.outerHTML === nc.outerHTML) return;
        const sameShell = oc.tagName === nc.tagName && oc.getAttributeNames().join() === nc.getAttributeNames().join() &&
          oc.getAttributeNames().every((a) => oc.getAttribute(a) === nc.getAttribute(a));
        if (sameShell && depth < 6 && oc.children.length && nc.children.length && oc.tagName !== "svg" && !hasText(oc) && !hasText(nc)) morph(oc, nc, depth + 1);
        else oc.replaceWith(nc);
      });
    };
    morph(root, tpl.content, 0);
  }

  _bind() {
    this._bound ||= new WeakSet();
    const once = (el) => { if (this._bound.has(el)) return false; this._bound.add(el); return true; };
    const on = (sel, type, fn) => this.shadowRoot.querySelectorAll(sel).forEach((el) => once(el) && el.addEventListener(typeof type === "function" ? type(el) : type, (ev) => fn(el, ev)));
    const goto = (days) => { UI.day = Math.max(0, days); this._hover = null; this._redraw(); };
    on("input[data-date]", "change", (el) => el.value && goto(DAYS_AGO(el.value)));
    on("[data-nav]", "click", (b) => goto(UI.day + +b.dataset.nav));
    on("[data-view]", "click", (b) => { UI.view = b.dataset.view; this._hover = null; this._redraw(); });
    on("select[data-inv]", "change", (el) => {
      UI.sn = el.value; this._hover = null;
      // edits, pending values and send states belong to one inverter
      this._edits = {}; this._redits = {}; this._pending = {}; this._sendState = {}; this._confirm = {}; UI.tdraft = null;
      this.hass = this._hass;
    });
    on("[data-preset]", "click", (b) => { UI.preset = b.dataset.preset; this._hover = null; this._redraw(); });
    on("[data-field]", "click", (b) => {
      const f = b.dataset.field, cur = UI.custom || [];
      UI.custom = cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f];
      try { localStorage.setItem("felicity-custom-fields", JSON.stringify(UI.custom)); } catch (e) { /* private mode */ }
      this._hover = null; this._redraw();
    });
    on("[data-stab]", "click", (b) => { UI.stab = b.dataset.stab; this._redraw(); });
    on("[data-unit]", "click", (b) => { UI.unit = b.dataset.unit; this._hover = null; this._redraw(); });
    this._bindSettings(on);
    this._bindTariff(on, once);
    this._bindCharts(once);
  }

  _bindSettings(on) {
    const kind = (el) => (el.tagName === "SELECT" || el.type === "checkbox" ? "change" : "input");
    // typing only marks the row as changed; nothing is redrawn until Send
    on("select[data-key], input[data-key]", kind, (el) => {
      const key = el.dataset.key, ent = this._set[key];
      const isBox = el.type === "checkbox", val = isBox ? (el.checked ? el.dataset.on : el.dataset.off) : el.value;
      this._edits ||= {};
      const same = !ent || val === "" || String(val) === String(ent.attributes.raw) || (!isBox && el.tagName === "INPUT" && Number(val) === Number(ent.state));
      if (same) delete this._edits[key]; else this._edits[key] = el.tagName === "SELECT" || isBox ? val : Number(val);
      const row = el.closest(".srow"), btn = row && row.querySelector(".send");
      if (row) row.classList.toggle("changed", key in this._edits);
      if (btn) btn.hidden = !(key in this._edits);
    });
    on("[data-rule]", kind, (el) => {
      const key = el.dataset.rule, f = el.dataset.f, cur = this._set[key] ? this._set[key].attributes : {};
      const val = el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value;
      this._redits ||= {};
      const e = { ...(this._redits[key] || {}) };
      if (String(cur[f] ?? "") === String(val)) delete e[f]; else e[f] = val;
      if (Object.keys(e).length) this._redits[key] = e; else delete this._redits[key];
      const row = el.closest(".tr"), btn = row && row.querySelector("[data-rsend]");
      if (row) row.classList.toggle("changed", key in this._redits);
      if (btn) btn.hidden = !(key in this._redits);
    });
    on("[data-rsend]", "click", (b) => this._sendRule(b.dataset.rsend));
    on("[data-send]", "click", (b) => this._send(b.dataset.send));
  }

  // tariff editor: typing updates the draft quietly; leaving a field and the buttons redraw (preview, save bar)
  _bindTariff(on, once) {
    const apply = (el) => {
      const t = UI.tdraft, num = (v) => (v === "" ? "" : Number(v));
      if (el.dataset.tf) t[el.dataset.tf] = el.type === "number" ? (el.value === "" && el.dataset.tf === "battery_reserve" ? null : num(el.value)) : el.value;
      else if (el.dataset.zf) { const [i, f] = el.dataset.zf.split(":"); t.zones[+i][f] = f === "price" ? num(el.value) : el.value; }
      else { const [i, j, k] = el.dataset.rf.split(":").map(Number); const r = t.zones[i].ranges[j]; r[k] = el.value; if (r[2] === "1234567") r.length = 2; }
      UI.terr = "";
    };
    this.shadowRoot.querySelectorAll("[data-tf],[data-zf],[data-rf]").forEach((el) => {
      if (!once(el)) return;
      el.addEventListener("input", () => apply(el));
      el.addEventListener("change", () => { apply(el); this._redraw(); });
    });
    const act = (sel, fn) => on(sel, (el) => (el.type === "radio" ? "change" : "click"), (el) => { fn(el); UI.terr = ""; this._redraw(); });
    act("[data-tpreset]", (b) => {
      const T = this._tar(DAY(0)), p = JSON.parse(JSON.stringify(T.presets[b.dataset.tpreset]));
      delete p.name;
      UI.tdraft = { ...UI.tdraft, ...p };
      if (!p.currency && p.mode === "schedule") UI.tdraft.currency = T.ha_currency || UI.tdraft.currency;
    });
    act("[data-tblank]", () => { UI.tdraft = { ...UI.tdraft, mode: "schedule", default_zone: "z1", zones: [{ id: "z1", name: "Zone 1", price: 0, color: COLOR.grid, ranges: [] }] }; });
    act("[data-zadd]", () => UI.tdraft.zones.push({ id: "z" + Date.now().toString(36), name: "Zone " + (UI.tdraft.zones.length + 1), price: 0,
      color: ZONE_COLORS[UI.tdraft.zones.length % ZONE_COLORS.length], ranges: [["00:00", "06:00"]] }));
    act("[data-zdel]", (b) => UI.tdraft.zones.splice(+b.dataset.zdel, 1));
    act("[data-zdef]", (b) => { UI.tdraft.default_zone = UI.tdraft.zones[+b.dataset.zdef].id; });
    act("[data-radd]", (b) => UI.tdraft.zones[+b.dataset.radd].ranges.push(["00:00", "06:00"]));
    act("[data-rdel]", (b) => { const [i, j] = b.dataset.rdel.split(":").map(Number); UI.tdraft.zones[i].ranges.splice(j, 1); });
    act("[data-tdiscard]", () => { UI.tdraft = null; });
    on("[data-tsave]", "click", () => !UI.tsaving && this._saveTariff());
    on("[data-esetup]", "click", async (b) => {
      b.textContent = "Setting up…"; b.disabled = true; UI.eerr = "";
      try { await this._hass.callApi("POST", "felicity/tariff", { sn: this._sn, energy: "setup" }); } catch (e) { UI.eerr = ERR(e); }
      this._staleTariff(); this._redraw();
    });
  }

  // charts: the mouse follows the pointer; on touch, a horizontal drag scrubs (touch-action: pan-y keeps page scrolling)
  // and on the all-time chart a second tap on the same bar (a click with the mouse) opens that day
  _bindCharts(once) {
    let frame = 0;
    const redraw = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; this._rerender(); }); };
    this.shadowRoot.querySelectorAll(".chart").forEach((c) => {
      const scrub = c.querySelector(".scrub");
      if (!scrub || !once(scrub)) return;
      const id = c.dataset.chart, daily = () => id === "a" && UI.unit === "day";
      const frac = (x) => { const b = (c.querySelector(".hit") || scrub).getBoundingClientRect(); return Math.max(0, Math.min(0.9999, (x - b.left) / b.width)); };
      const show = (x) => { this._hover = { id, x: frac(x) }; redraw(); };
      const bar = (x) => { const rows = this._allRows() || []; return rows.length ? Math.min(rows.length - 1, Math.floor(frac(x) * rows.length)) : null; };
      const openBar = (x) => {
        const j = bar(x);
        if (j === null) return;
        UI.day = DAYS_AGO(this._allRows()[j][0]); this._hover = null; this._rerender();
        const sec = this.shadowRoot.querySelector(".hist");
        if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
      };
      let start = null, moved = false;
      scrub.addEventListener("pointermove", (ev) => {
        if (ev.pointerType === "mouse") return show(ev.clientX);
        if (start === null) return;
        if (Math.abs(ev.clientX - start) > 4) moved = true;
        show(ev.clientX);
      });
      scrub.addEventListener("pointerleave", (ev) => { if (ev.pointerType === "mouse") { this._hover = null; redraw(); } });
      scrub.addEventListener("pointerdown", (ev) => { if (ev.pointerType !== "mouse") { start = ev.clientX; moved = false; show(ev.clientX); } });
      scrub.addEventListener("pointerup", (ev) => {
        if (ev.pointerType === "mouse") { if (daily()) openBar(ev.clientX); return; }
        const tapped = !moved;
        start = null;
        if (!daily() || !tapped) return;
        const j = bar(ev.clientX);
        if (this._lastBar === j) { this._lastBar = null; openBar(ev.clientX); } else this._lastBar = j;
      });
      scrub.addEventListener("pointercancel", () => { start = null; });  // the vertical swipe became a page scroll
    });
  }
}
if (!customElements.get("felicity-flow-card")) customElements.define("felicity-flow-card", FelicityFlowCard);

// ---- single-purpose widgets built from the same card (each can be placed on any dashboard) ----
const WIDGETS = [
  ["felicity-energy-flow-card", "Felicity: energy flow", "Live flow solar / grid / battery / home with today's totals", ["flow"]],
  ["felicity-day-charts-card", "Felicity: day charts", "Power, solar, grid, battery and PV voltage charts for any day", ["day"]],
  ["felicity-all-time-card", "Felicity: all-time energy", "Daily / monthly energy bars since the first day", ["all"]],
  ["felicity-settings-card", "Felicity: inverter settings", "All inverter settings like in the Felicity app, editable", ["settings"]],
  ["felicity-charts-card", "Felicity: chart explorer", "Ready-made diagnostic chart sets and a custom parameter picker", ["explore"]],
  ["felicity-tou-card", "Felicity: time-of-use schedule", "Work mode schedule timeline and today's result", ["tou"]],
  ["felicity-price-card", "Felicity: electricity price", "Price now, next tariff zone and today's cost by zone", ["price"]],
  ["felicity-tariff-card", "Felicity: tariff", "Tariff editor, cost of any day and Energy dashboard setup", ["tariff"]],
];
for (const [tag, , , show] of WIDGETS) {
  if (customElements.get(tag)) continue;
  customElements.define(tag, class extends FelicityFlowCard {
    setConfig(c) { super.setConfig({ ...c, show: c.show || show }); }
    static getStubConfig() { return {}; }
  });
}

// ---- the full-screen "Solar" sidebar panel registered by the integration ----
class FelicityPanel extends HTMLElement {
  set hass(h) {
    if (!this._card) {
      this.attachShadow({ mode: "open" }).innerHTML = `<style>:host{display:block;min-height:100vh;background:var(--primary-background-color)}
        .bar{display:flex;align-items:center;gap:8px;height:56px;padding:0 12px;color:var(--app-header-text-color,#fff);
          background:var(--app-header-background-color);font-size:20px}
        .bar button{all:unset;cursor:pointer;width:40px;height:40px;display:grid;place-items:center}
        .wrap{padding:12px;box-sizing:border-box}</style>
        <div class="bar"><button aria-label="Menu">☰</button>Solar</div><div class="wrap"></div>`;
      this.shadowRoot.querySelector("button").addEventListener("click", () =>
        this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
      this._card = document.createElement("felicity-flow-card");
      this._card.setConfig({});
      this.shadowRoot.querySelector(".wrap").appendChild(this._card);
      this.narrow = this._narrow;
    }
    this._card.hass = h;
  }

  set narrow(n) {
    this._narrow = n;
    if (this._card) this.shadowRoot.querySelector(".bar button").style.display = n ? "" : "none";
  }
}
if (!customElements.get("felicity-panel")) customElements.define("felicity-panel", FelicityPanel);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "felicity-flow-card")) {  // the file can be loaded twice (resource + panel)
  window.customCards.push({ type: "felicity-flow-card", name: "Felicity: full dashboard", description: "Overview, settings and work mode with tabs and inverter picker", preview: true });
  for (const [type, name, description] of WIDGETS) window.customCards.push({ type, name, description, preview: true });
}
