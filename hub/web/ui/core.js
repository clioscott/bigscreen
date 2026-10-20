// Shared state: the saved config, a working draft that every page edits, live status, toasts.
import { reactive, computed } from "vue";

export const clone = (o) => (o === undefined ? undefined : JSON.parse(JSON.stringify(o)));
export const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export const store = reactive({
  saved: null,           // config as last saved on the hub
  draft: null,           // working copy edited by the pages
  status: null,          // /api/status
  display: null,         // /api/display/status
  catalog: null,         // /api/maps/catalog
  maps: { tracks: {}, packs: {} },
  preview: null,         // { until } while the display shows unsaved changes
  saving: false,
  toasts: [],
  route: { name: "overview", params: [] },
});

// ---- API --------------------------------------------------------------------
export async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
    init.method = init.method || "POST";
  }
  const r = await fetch(path, init);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
  return data;
}

// ---- toasts -----------------------------------------------------------------
let toastId = 0;
export function toast(msg, kind = "ok", ms) {
  const id = ++toastId;
  store.toasts.push({ id, msg, kind });
  setTimeout(() => { const i = store.toasts.findIndex((t) => t.id === id); if (i >= 0) store.toasts.splice(i, 1); }, ms || (kind === "bad" ? 7000 : 3200));
}
export const fail = (e) => toast(e?.message || String(e), "bad");

// ---- draft / save -----------------------------------------------------------
// Sections tracked for "unsaved changes"; [label, getter path]
export const SECTIONS = [
  ["Rotation", ["modules", "layout_cycle"]],
  ["Ticker", ["modules", "finance"]],
  ["Headlines", ["modules", "headlines"]],
  ["Weather maps", ["modules", "weather_maps"]],
  ["Webcams", ["modules", "webcams"]],
  ["Bridges now", ["modules", "bridges"]],
  ["Weather data", ["modules", "weather"]],
  ["Weather alerts", ["modules", "weather_alerts"]],
  ["Japan EEW", ["modules", "japan_eew"]],
  ["Alert Ready", ["modules", "naad_alerts"]],
  ["DriveBC", ["modules", "road_alerts"]],
  ["Earthquakes", ["modules", "bc_quakes"]],
  ["Tsunami", ["modules", "tsunami"]],
  ["Wildfire", ["modules", "wildfire"]],
  ["Alerts on the wall", ["modules", "alert_lifecycle"]],
  ["Font audit", ["hub", "fonts"]],
  ["Display", ["system", "display"]],
  ["Night mode", ["system", "night_mode"]],
];
const at = (obj, path) => path.reduce((o, k) => (o == null ? o : o[k]), obj);

export const dirty = computed(() => {
  if (!store.saved || !store.draft) return [];
  return SECTIONS.filter(([, p]) => !same(at(store.draft, p), at(store.saved, p))).map(([label, p]) => ({ label, path: p }));
});

// Fill in a missing default object in both draft and saved, so just opening an editor isn't an "unsaved change".
export function seed(path, value) {
  for (const root of [store.saved, store.draft]) {
    let o = root;
    for (const k of path.slice(0, -1)) o = o[k] = o[k] || {};
    const last = path[path.length - 1];
    if (o[last] === undefined) o[last] = clone(value);
  }
  return at(store.draft, path);
}

export function discard() {
  store.draft = clone(store.saved);
}

export async function save(reason = "") {
  store.saving = true;
  try {
    // Merge: sections changed here win; everything else comes from the hub's latest copy,
    // so a save never clobbers changes made elsewhere (another tab, a script).
    const latest = await api("/api/config");
    const next = clone(latest);
    for (const { path } of dirty.value) {
      let o = next;
      for (const k of path.slice(0, -1)) o = o[k] = o[k] || {};
      o[path[path.length - 1]] = clone(at(store.draft, path));
    }
    const res = await api("/api/config", { method: "PUT", json: { config: next, reason: reason || `edited ${dirty.value.map((d) => d.label).join(", ")}` } });
    store.saved = res.config;
    store.draft = clone(res.config);
    toast("Saved — the display updates within a few seconds");
    return res;
  } catch (e) { fail(e); } finally { store.saving = false; }
}

export async function previewOnDisplay(seconds = 120, { hook = true } = {}) {
  const overlay = {};
  for (const { path } of dirty.value) {
    let o = overlay;
    for (const k of path.slice(0, -1)) o = o[k] = o[k] || {};
    o[path[path.length - 1]] = clone(at(store.draft, path));
  }
  try {
    await api("/api/display/preview", { json: { overlay, ttl_sec: seconds } });
    store.preview = { until: Date.now() + seconds * 1000 };
    toast(`Showing your unsaved changes on the display for ${Math.round(seconds / 60)} min`);
    // a page can register store.previewHook to put what it edits on the wall meanwhile (a webcam, say)
    if (hook && store.previewHook) store.previewHook(seconds);
  } catch (e) { fail(e); }
}
export async function endPreview(keep) {
  try {
    if (keep) await save();
    await api("/api/display/preview/clear", { method: "POST" });
    store.previewEndHook?.();
  } catch (e) { fail(e); }
  store.preview = null;
}

// ---- polling ----------------------------------------------------------------
export async function loadAll() {
  const [cfg, catalog] = await Promise.all([api("/api/config"), api("/api/maps/catalog").catch(() => null)]);
  store.saved = cfg;
  store.draft = clone(cfg);
  migrateRotation();
  store.catalog = catalog;
  refreshStatus();
  refreshDisplay();
  refreshMaps();
  setInterval(refreshStatus, 4000);
  setInterval(refreshDisplay, 2500);
  setInterval(refreshMaps, 15000);
}
export async function refreshStatus() { try { store.status = await api("/api/status"); } catch {} }
export async function refreshDisplay() { try { store.display = await api("/api/display/status"); } catch { store.display = { reachable: false }; } }
export async function refreshMaps() { try { store.maps = await api("/api/maps/state"); } catch {} }

// ---- formatting ---------------------------------------------------------------
export function ago(sec) {
  if (sec == null || isNaN(sec)) return "never";
  if (sec < 45) return "just now";
  if (sec < 90) return "a minute ago";
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 172800) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} days ago`;
}
export function dur(sec) {
  if (sec == null) return "";
  if (sec < 90) return `${Math.round(sec)} s`;
  if (sec < 5400) return `${Math.round(sec / 60)} min`;
  const h = sec / 3600;
  return `${h % 1 ? h.toFixed(1) : h} h`;
}
export const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

// ---- routing (hash based: #/maps/precip_radar) ---------------------------------
export function go(path) { location.hash = "#/" + path; }
export function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  store.route = { name: parts[0] || "overview", params: parts.slice(1) };
}
window.addEventListener("hashchange", parseRoute);
parseRoute();

// ---- domain helpers -------------------------------------------------------------
export const PANEL_TYPES = {
  calendar: { label: "Calendar", icon: "CalendarDays", note: "Left side only", passes: false },
  client_calls_upcoming: { label: "Upcoming client calls", icon: "PhoneForwarded", passes: false },
  client_calls_past: { label: "Past client calls", icon: "History", passes: false },
  headlines: { label: "Headlines", icon: "Newspaper", passes: true, passWord: "full pass through every category" },
  weather: { label: "Weather info", icon: "CloudSun", sub: "weather_screen", passes: false },
  weather_full: { label: "Weather info (full width)", icon: "CloudSun", sub: "weather_screen", full: true, passes: false },
  map: { label: "Weather map", icon: "Radar", sub: "track", passes: true, passWord: "animation loop" },
  map_full: { label: "Weather map (full width)", icon: "Radar", sub: "track", full: true, passes: true, passWord: "animation loop" },
  weather_alert: { label: "Weather alert (full screen)", icon: "Siren", full: true, passes: true, passWord: "pass through every alert",
    note: "Takes over the whole wall, ticker and clock included" },
  naad_alert: { label: "Alert Ready (full screen)", icon: "Megaphone", full: true, passes: true, passWord: "pass through every alert", note: "Takes over the whole wall" },
  road_alert: { label: "DriveBC (full screen)", icon: "Car", full: true, passes: true, passWord: "pass through every event", note: "Takes over the whole wall" },
  quake_alert: { label: "Earthquakes (full screen)", icon: "Activity", full: true, passes: true, passWord: "pass through every quake", note: "Takes over the whole wall" },
  tsunami_alert: { label: "Tsunami (full screen)", icon: "Waves", full: true, passes: true, passWord: "pass through every message", note: "Takes over the whole wall" },
  fire_alert: { label: "Wildfire (full screen)", icon: "Flame", full: true, passes: true, passWord: "pass through every item", note: "Takes over the whole wall" },
  weather_alert_half: { label: "Weather alert (half width)", icon: "Siren", passes: true, passWord: "pass through every alert", note: "Right side: alerts past their full-wall showings" },
  naad_alert_half: { label: "Alert Ready (half width)", icon: "Megaphone", passes: true, passWord: "pass through every alert" },
  road_alert_half: { label: "DriveBC (half width)", icon: "Car", passes: true, passWord: "pass through every event" },
  quake_alert_half: { label: "Earthquakes (half width)", icon: "Activity", passes: true, passWord: "pass through every quake" },
  tsunami_alert_half: { label: "Tsunami (half width)", icon: "Waves", passes: true, passWord: "pass through every message" },
  fire_alert_half: { label: "Wildfire (half width)", icon: "Flame", passes: true, passWord: "pass through every item" },
  webcam: { label: "Webcam", icon: "Video", sub: "cam", passes: false },
  webcam_full: { label: "Webcam (full width)", icon: "Video", sub: "cam", full: true, passes: false },
  webcam_wall: { label: "Webcam (whole wall)", icon: "Video", sub: "cam", full: true, passes: false, note: "Covers the ticker and clock too" },
  bridges: { label: "Bridges now", icon: "Construction", passes: true, passWord: "pass through every bridge", note: "2×2, paging through the bridges" },
  bridges_full: { label: "Bridges now (full width)", icon: "Construction", full: true, passes: true, passWord: "pass through every bridge" },
  bridges_wall: { label: "Bridges now (whole wall)", icon: "Construction", full: true, passes: true, passWord: "pass through every bridge", note: "Covers the ticker and clock too" },
  blank: { label: "Blank", icon: "Square", passes: false },
};
// the stage of an alert's life on the wall (display/modules/alert_life.py) a rule can ask for
export const PHASES = { full: "new or still full-wall", half: "past its full-wall showings" };
export const ALERT_RULES = ["alert", "naad", "roads", "quakes", "tsunami", "wildfire"];
export const WEATHER_SCREENS = { current: "Current conditions", hourly: "Hourly forecast", forecast: "7-day forecast", warning: "Warnings" };

// ---- rotation rules (display/rules.py evaluates them) ------------------------------
export const ALERT_LEVELS = { statement: "Statement", advisory: "Advisory", watch: "Watch", warning: "Warning" };
export const HAZARDS = { rain: "Rain", snow: "Snow", freezing: "Freezing rain", wind: "Wind", thunder: "Thunderstorms", tornado: "Tornado",
  flood: "Flooding", heat: "Heat", cold: "Cold", frost: "Frost", fog: "Fog", smoke: "Smoke / air quality", general: "Other" };
export const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// label: how the condition reads after "Only when" / "Not when"; make(): a new rule's defaults
export const RULES = {
  calendar_events: { label: "the calendar has events on screen", icon: "CalendarDays", make: () => ({}) },
  alert: { label: "a weather alert is in effect", icon: "Siren", make: () => ({}) },
  forecast_warning: { label: "the city forecast lists a warning", icon: "TriangleAlert", make: () => ({}) },
  precip: { label: "it's raining or snowing", icon: "CloudRain", make: () => ({ within_h: 0, pop: 40 }) },
  dark: { label: "the sun is down", icon: "Moon", make: () => ({}) },
  night_mode: { label: "it's night-mode hours", icon: "MoonStar", make: () => ({}) },
  hours: { label: "it's between certain hours", icon: "Clock", make: () => ({ from: "07:00", to: "22:00" }) },
  days: { label: "it's certain days", icon: "CalendarRange", make: () => ({ days: [0, 1, 2, 3, 4] }) },
  fresh: { label: "its data is fresh", icon: "RefreshCw", make: () => ({ max_age_min: 30 }) },
  naad: { label: "an Alert Ready alert is in effect", icon: "Megaphone", make: () => ({}) },
  roads: { label: "DriveBC has an incident or closure", icon: "Car", make: () => ({}) },
  quakes: { label: "there's an earthquake to show", icon: "Activity", make: () => ({}) },
  tsunami: { label: "a tsunami message is in effect", icon: "Waves", make: () => ({}) },
  wildfire: { label: "there's a wildfire or evacuation to show", icon: "Flame", make: () => ({}) },
};
const LEGACY_WHEN = { calendar_has_events: [{ if: "calendar_events" }], no_events: [{ if: "calendar_events", not: true }],
  weather_has_alert: [{ if: "forecast_warning" }], weather_alert_active: [{ if: "alert" }] };

export function ruleText(r) {
  const t = RULES[r.if];
  if (!t) return r.if;
  let s = t.label;
  if (r.if === "alert") {
    const bits = [];
    if (r.min_level && r.min_level !== "statement") bits.push(`${ALERT_LEVELS[r.min_level].toLowerCase()} or worse`);
    if (r.hazards?.length) bits.push(r.hazards.map((h) => (HAZARDS[h] || h).toLowerCase()).join("/"));
    if (r.home) bits.push("for home");
    s = bits.length ? `a ${bits.join(", ")} alert is in effect` : s;
  } else if (r.if === "precip") s = r.within_h > 0 ? `rain or snow is likely within ${r.within_h} h (${r.pop ?? 40}%+)` : s;
  else if (r.if === "hours") s = `it's ${r.from}–${r.to}`;
  else if (r.if === "days") s = `it's ${(r.days || []).map((d) => DAYS[d]).join(", ") || "no day"}`;
  else if (r.if === "fresh") s = `its data is under ${r.max_age_min ?? 30} min old`;
  if (r.phase && ALERT_RULES.includes(r.if)) s += ` (${PHASES[r.phase] || r.phase})`;
  return (r.not ? "not when " : "when ") + s;
}

/** Rotation entries written before rules existed carry "when"; give them rules (in saved and draft alike, so
 *  the conversion alone isn't an unsaved change). */
export function migrateRotation() {
  for (const root of [store.saved, store.draft]) {
    const lc = root?.modules?.layout_cycle;
    if (!lc) continue;
    for (const side of ["left", "right"]) for (const e of lc[side] || []) {
      if (!Array.isArray(e.rules)) e.rules = clone(LEGACY_WHEN[e.when] || []);
      delete e.when;
      if (["weather_alert", "naad_alert", "road_alert", "quake_alert", "tsunami_alert", "fire_alert"].includes(e.panel) && e.priority === undefined) e.priority = "next";
      if (e.panel?.endsWith("_alert_half")) e.priority = e.priority ?? "";
    }
  }
}

// ---- the wall: play an entry now, rotation controls ---------------------------------
export async function playOnDisplay(entry, { side = "right", hold = false, ttl } = {}) {
  const e = clone(entry);
  delete e.enabled;
  try {
    await api("/api/display/play", { json: { side, entry: e, hold, ttl_sec: ttl } });
    toast(hold ? "Holding it on the wall" : "Playing it once on the wall, then back to the rotation");
    setTimeout(refreshDisplay, 400);
  } catch (err) { fail(err); }
}
export async function rotationAction(action, side) {
  try {
    await api("/api/display/rotation", { json: side ? { action, side } : { action } });
    setTimeout(refreshDisplay, 300);
  } catch (err) { fail(err); }
}
// jump the rotation to an entry: it plays now and the rotation carries on after it (the display finds the entry
// again by what it shows if the saved list differs from the index)
export async function rotationGoto(side, index, entry) {
  try {
    await api("/api/display/rotation", { json: { action: "goto", side, index, entry } });
    toast("Jumped there: the rotation carries on from it");
    setTimeout(refreshDisplay, 300);
  } catch (err) { fail(err); }
}
export function entryTitle(e) {
  if (!e) return "";
  const t = PANEL_TYPES[e.panel];
  if (e.panel?.startsWith("map")) {
    const tr = store.draft?.modules?.weather_maps?.tracks?.[e.track];
    return tr ? tr.label : `Map: ${e.track || "?"}`;
  }
  if (e.panel === "weather" || e.panel === "weather_full") return WEATHER_SCREENS[e.weather_screen || "current"] || t?.label;
  if (e.panel?.startsWith("webcam")) return store.draft?.modules?.webcams?.cams?.[e.cam]?.label || `Webcam: ${e.cam || "?"}`;
  if (e.panel === "quake") return "Earthquake warning";
  if (e.panel === "font_specimen") return "Font specimen";
  return t?.label || e.panel;
}

export function areaLabel(id, short = false) {
  const a = store.draft?.modules?.weather_maps?.areas?.[id];
  if (!a) return id;
  return short ? (a.short_label || a.label || id) : (a.label || id);
}
