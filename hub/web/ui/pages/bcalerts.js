// The five BC alert pages, each a configuration of the shared page in bcpage.js.
import { makeBCPage } from "./bcpage.js";

const pill = (bg, fg = "#000") => ({ background: bg, color: fg, borderColor: bg });
const SEV = { extreme: "#ff2828", severe: "#ff8000", moderate: "#ffcd00", minor: "#5aaaff", unknown: "#aaaab4" };
const LV = { warning: "#ff1e1e", advisory: "#ff8c00", watch: "#ffdc00", information: "#3cc878" };
const ST = { ooc: "#ff3214", held: "#ffc800", uc: "#46d25a" };
const MMI = ["", "#fff", "#bfccff", "#a0e6ff", "#80ffff", "#7aff93", "#ffff00", "#ffc800", "#ff9100", "#ff0000", "#c80000"];
const ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"];
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");

export const Naad = makeBCPage({
  src: "naad", section: "naad_alerts", panel: "naad_alert", rule: "naad",
  title: "Alert Ready",
  intro: "Canada's public alerting system (the NAAD feed behind broadcast and phone alerts), live over its TCP stream. Shows BC alerts that no other screen covers — Amber Alerts, civil emergencies, hazardous materials, evacuation orders, 911 outages, boil-water notices. ECCC weather alerts are left to the weather alert screen; earthquake, tsunami, wildfire and road messages go to their own screens.",
  sources: ["NAAD System (Pelmorex) — live stream and archive"],
  fields: [
    { key: "area", type: "seg", label: "Area", options: [{ value: "metro", label: "Metro Vancouver" }, { value: "lower_mainland", label: "Lower Mainland" }, { value: "bc", label: "All of BC" }] },
    { key: "min_severity", type: "seg", label: "Show alerts of at least", options: ["unknown", "minor", "moderate", "severe", "extreme"].map((v) => ({ value: v, label: v === "unknown" ? "any" : v })) },
    { key: "takeover", type: "seg", label: "Take over the wall", hint: "Immediate = alerts flagged for immediate broadcast (Amber Alerts, civil emergencies)",
      options: [{ value: "immediate", label: "Immediate alerts" }, { value: "all", label: "Every new alert" }, { value: "never", label: "Never" }] },
    { key: "show_tests", type: "toggle", label: "Show test and exercise messages" },
  ],
  chip: (it) => ({ text: it.severity, style: pill(SEV[(it.severity || "unknown").toLowerCase()] || SEV.unknown) }),
  line: (it) => `${it.event}: ${it.headline || ""} — ${(it.areas || []).join(", ")}`,
  empty: "No Alert Ready messages for this area right now.",
  note: "Alerts update in place when the issuer updates or cancels them, and leave the wall when they expire.",
});

export const Roads = makeBCPage({
  src: "roads", section: "road_alerts", panel: "road_alert", rule: "roads",
  title: "DriveBC",
  intro: "Road incidents, closures and conditions in Metro Vancouver from DriveBC (the same events as BC's Open511 feed), each with the nearest DriveBC highway camera when there is one within a few kilometres. Highway shields are drawn as the real signs.",
  sources: ["DriveBC.ca — events and webcams"],
  fields: [
    { key: "incidents", type: "toggle", label: "Incidents", hint: "Crashes, vehicle fires, debris, hazards" },
    { key: "closures", type: "toggle", label: "Closures" },
    { key: "conditions", type: "toggle", label: "Road conditions", hint: "Snow, ice and slippery sections" },
    { key: "major_construction", type: "toggle", label: "Major construction", hint: "Long-running work with major delays, and scheduled closures" },
    { key: "planned_hours", type: "slider", label: "Planned events starting within", min: 0, max: 24, step: 1, unit: " h", hint: "0 = off" },
    { key: "cameras", type: "toggle", label: "Show the nearest DriveBC camera" },
    { key: "takeover", type: "seg", label: "Take over the wall", options: [{ value: "closures", label: "New highway, bridge or tunnel closures" }, { value: "never", label: "Never" }] },
  ],
  chip: (it) => ({ text: it.closed ? "CLOSED" : it.kind, style: pill(it.closed ? "#ff3228" : it.type === "INCIDENT" ? "#ff8c00" : "#ffc800") }),
  line: (it) => `${it.road} ${it.direction || ""} — ${it.description || ""}`,
  empty: "Nothing on the roads in Metro Vancouver that matches these settings.",
  note: "Only events touching Metro Vancouver (West Vancouver to Langley) are shown.",
});

export const Quakes = makeBCPage({
  src: "quakes", section: "bc_quakes", panel: "quake_alert", rule: "quakes",
  title: "Earthquakes",
  intro: "Earthquakes that matter in south-west BC from the USGS and Natural Resources Canada catalogues, merged: where it was, how deep, how many felt it, and the estimated shaking at home and nearby cities. Canada's earthquake early warnings arrive through Alert Ready and take over the wall with DROP · COVER · HOLD ON.",
  sources: ["USGS FDSN event service (incl. Did You Feel It?)", "NRCan FDSN event service", "NRCan early warning via Alert Ready"],
  fields: [
    { key: "near_mag", type: "slider", label: "Close quakes: magnitude at least", min: 1.5, max: 5, step: 0.1 },
    { key: "near_km", type: "slider", label: "…within", min: 25, max: 300, step: 5, unit: " km" },
    { key: "regional_mag", type: "slider", label: "Regional quakes: magnitude at least", min: 3, max: 6.5, step: 0.1 },
    { key: "regional_km", type: "slider", label: "…within", min: 100, max: 800, step: 25, unit: " km" },
    { key: "large_mag", type: "slider", label: "Large quakes: magnitude at least", min: 5, max: 8.5, step: 0.1 },
    { key: "large_km", type: "slider", label: "…within", min: 500, max: 3000, step: 100, unit: " km" },
    { key: "felt_reports", type: "slider", label: "…or felt by at least", min: 5, max: 500, step: 5, unit: " people" },
    { key: "home_mmi", type: "slider", label: "…or estimated shaking at home of at least (MMI)", min: 2, max: 6, step: 0.5 },
    { key: "show_hours", type: "slider", label: "Keep small quakes in the rotation for", min: 1, max: 48, step: 1, unit: " h" },
    { key: "large_show_hours", type: "slider", label: "Keep M5+ quakes for", min: 6, max: 96, step: 6, unit: " h" },
    { key: "takeover_mmi", type: "slider", label: "Take over the wall at estimated shaking at home of", min: 3, max: 7, step: 0.5 },
    { key: "eew", type: "toggle", label: "Show NRCan earthquake early warnings", hint: "Delivered through Alert Ready" },
  ],
  chip: (it) => (it.kind === "eew" ? { text: "EARLY WARNING", style: pill("#ff1e1e", "#fff") }
    : { text: `M${(+it.mag).toFixed(1)}`, style: pill(MMI[Math.max(1, Math.min(10, Math.round(it.mmi_home || 1)))]) }),
  line: (it) => (it.kind === "eew" ? it.headline : `${it.place_short} · ${it.depth?.toFixed?.(0)} km deep · ${when(it.time)} · shaking here ${ROMAN[Math.max(1, Math.min(10, Math.round(it.mmi_home || 1)))]}`),
  empty: "No earthquakes above these thresholds right now.",
  note: "Shaking estimates use the Atkinson & Wald (2007) intensity equation for western North America — a guide, not a measurement.",
});

export const Tsunami = makeBCPage({
  src: "tsunami", section: "tsunami", panel: "tsunami_alert", rule: "tsunami",
  title: "Tsunami",
  intro: "Tsunami messages for British Columbia from the US National Tsunami Warning Center (which issues them for BC), with BC's own zone alerts from Alert Ready, first-wave times from the bulletin, and live DFO tide gauges along the coast while a message is in effect. ECCC tsunami alerts are shown here rather than on the weather alert screen.",
  sources: ["NTWC (tsunami.gov) — Atom feed, CAP and text bulletins", "PTWC (Pacific-wide) for context", "DFO / CHS tide gauges (IWLS)"],
  fields: [
    { key: "info_min_mag", type: "slider", label: "Show information statements for quakes of at least", min: 6, max: 8.5, step: 0.1, hint: "Statements that say there's no threat to BC" },
    { key: "info_hours", type: "slider", label: "…for", min: 1, max: 24, step: 1, unit: " h" },
    { key: "takeover", type: "toggle", label: "Take over the wall for advisories, watches and warnings" },
    { key: "gauges", type: "toggle", label: "Read tide gauges during an event" },
  ],
  chip: (it) => ({ text: it.level, style: pill(LV[it.level] || "#888") }),
  line: (it) => `${it.mag ? "M" + it.mag.toFixed(1) + " " : ""}${it.region || ""} · ${it.bc_threat ? "THREAT TO BC" : "no threat to BC"} · ${when(it.issued)}`,
  empty: "No tsunami messages for BC right now.",
  note: "Travel-time rings on the Pacific map assume a deep-ocean wave speed of 720 km/h; the bulletin's own arrival times are shown when it gives them.",
});

export const Wildfire = makeBCPage({
  src: "wildfire", section: "wildfire", panel: "fire_alert", rule: "wildfire",
  title: "Wildfire",
  intro: "BC Wildfire Service fires and EMCR evacuation orders and alerts in south-west BC and the Okanagan: perimeters, stage of control, size and cause, plus today's fire danger rating and any campfire or open-burning bans.",
  sources: ["BCWS active fires and perimeters", "EMCR evacuation orders and alerts", "BCWS danger ratings and bans"],
  fields: [
    { key: "regions", type: "multi", label: "Regions", optionsFrom: "regions" },
    { key: "fires_of_note", type: "toggle", label: "Wildfires of note" },
    { key: "evacuations", type: "toggle", label: "Evacuation orders and alerts", hint: "Orders take over the wall" },
    { key: "near_min_ha", type: "slider", label: "Out-of-control fires of at least", min: 0, max: 1000, step: 5, unit: " ha" },
    { key: "near_km", type: "slider", label: "…within", min: 25, max: 300, step: 5, unit: " km" },
    { key: "close_km", type: "slider", label: "Any new fire within", min: 5, max: 60, step: 5, unit: " km", hint: "Discovered in the last 48 hours" },
    { key: "takeover", type: "toggle", label: "Take over the wall for new evacuation orders" },
  ],
  chip: (it) => (it.kind === "evac" ? { text: `EVAC ${it.status}`, style: pill(it.status === "Order" ? "#ff1e1e" : "#ff9600", "#fff") }
    : { text: (it.status_text || it.status || "").toUpperCase(), style: pill(ST[it.status] || "#ff3214") }),
  line: (it) => (it.kind === "evac" ? `${it.name} — ${it.event} (${it.agency})` : `${it.name} · ${it.ha} ha · ${it.km} km · ${(it.why || []).join(", ")}`),
  empty: "No wildfires or evacuations meeting these settings.",
  note: "Fire data refreshes every 10 minutes; evacuation areas get their own zoomed map.",
});
