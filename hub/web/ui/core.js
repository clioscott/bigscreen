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
  ["Weather data", ["modules", "weather"]],
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

export async function previewOnDisplay(seconds = 120) {
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
  } catch (e) { fail(e); }
}
export async function endPreview(keep) {
  try {
    if (keep) await save();
    await api("/api/display/preview/clear", { method: "POST" });
  } catch (e) { fail(e); }
  store.preview = null;
}

// ---- polling ----------------------------------------------------------------
export async function loadAll() {
  const [cfg, catalog] = await Promise.all([api("/api/config"), api("/api/maps/catalog").catch(() => null)]);
  store.saved = cfg;
  store.draft = clone(cfg);
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
  blank: { label: "Blank", icon: "Square", passes: false },
};
export const WEATHER_SCREENS = { current: "Current conditions", hourly: "Hourly forecast", forecast: "7-day forecast", warning: "Warnings" };
export const CONDITIONS = { always: "Always", calendar_has_events: "When the calendar has events", no_events: "When the calendar is empty", weather_has_alert: "When a weather alert is active" };

export function areaLabel(id, short = false) {
  const a = store.draft?.modules?.weather_maps?.areas?.[id];
  if (!a) return id;
  return short ? (a.short_label || a.label || id) : (a.label || id);
}
