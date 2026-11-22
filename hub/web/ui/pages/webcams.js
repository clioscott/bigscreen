// Webcams: live video sources (hub/webcams.py decodes them), how each is cropped for every place on the wall,
// its colour and its caption, and adding it to the rotation (panels webcam / webcam_full / webcam_wall).
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import Sortable from "sortablejs";
import { store, api, fail, toast, seed, go, clone, dirty, previewOnDisplay, playOnDisplay, rotationAction } from "../core.js";
import { GALLERY, CITIES, CATS, snap, thumb } from "./webcam_gallery.js";
import { CameraMap } from "./cameramap.js";
import { useSlippyMap } from "./slippymap.js";
import { CaptionRows } from "./caption_rows.js";

const DEFAULT_SHARPEN = 0.5;          // hub/webcams.py DEFAULT_SHARPEN: a webcam with no Sharpness of its own
export const SHAPES = {
  left: { label: "Left side", panel: "webcam", side: "left", icon: "PanelLeft" },
  right: { label: "Right side", panel: "webcam", side: "right", icon: "PanelRight" },
  full: { label: "Full width", panel: "webcam_full", side: "right", icon: "RectangleHorizontal", note: "ticker and clock stay" },
  tall: { label: "Above the clock", panel: "webcam_tall", side: "right", icon: "PanelBottomClose", note: "covers the ticker; the clock and date stay" },
  wall: { label: "Whole wall", panel: "webcam_wall", side: "right", icon: "Maximize", note: "covers the ticker and clock" },
};
// caption layouts and sections (display/modules/webcam_caption.py draws them)
const LAYOUTS = {                     // (the other styles went with rows: display/modules/webcam_caption.py)
  tag: { label: "Tag", icon: "Tag", note: "A box in a corner with an accent edge, in rows you arrange" },
  none: { label: "None", icon: "EyeOff", note: "No caption" },
};
const FONTS = [{ value: "tiny", label: "Tiny" }, { value: "small", label: "Small" }, { value: "medium", label: "Medium" }, { value: "large", label: "Large" },
  { value: "xlarge", label: "Extra large" }];
const SECTIONS = { name: "Name", place: "Place", time: "Local time", age: "Image time", weather: "Temperature", sun: "Sunrise / sunset", date: "Date",
  offset: "Time difference", daynight: "Day / night icon", live: "LIVE badge", credit: "Credit", custom: "Your text",
  shield: "Highway shield", conditions: "Conditions", feels: "Feels like", hilo: "High / low", wind: "Wind", humidity: "Humidity",
  over: "What it's over", coords: "Coordinates (live)", orbit: "Next sunrise / sunset", below: "Local time below", crew: "People aboard",
  mission: "Mission" };
// a webcam in space (location: Space): the ISS's sections instead of the time and weather (display/modules/space.py)
const SPACE_KEYS = ["over", "coords", "orbit", "below", "crew", "mission"];
const SPACE_GROUPS = [{ label: "About the webcam", keys: ["name", "credit", "custom", "live"] },
  { label: "In space", keys: SPACE_KEYS }];
// the section chips, grouped (weather ones need the webcam's location)
const SECTION_GROUPS = [{ label: "About the webcam", keys: ["name", "place", "credit", "custom", "live", "shield"] },
  { label: "Time", keys: ["time", "date", "offset", "age", "daynight"] },
  { label: "Weather there", keys: ["weather", "conditions", "feels", "hilo", "wind", "humidity", "sun"] }];
const WX_KEYS = ["weather", "sun", "conditions", "feels", "hilo", "wind", "humidity"];
const ORDERABLE = ["shield", "live", "name", "place", "time", "age", "weather", "conditions", "feels", "hilo", "wind", "humidity", "date", "offset", "sun",
  "custom", "credit", "coords", "orbit", "below", "crew", "mission"];
// the Custom layout: where each section goes (column, line) to start with; mirrors webcam_caption.DEFAULT_SLOTS
const DEFAULT_SLOTS = { shield: [1, 1], live: [1, 1], name: [1, 1], place: [1, 2], time: [1, 2], age: [1, 2], weather: [1, 3], date: [1, 3], offset: [1, 3],
  sun: [1, 3], custom: [1, 3], credit: [1, 3], conditions: [1, 3], feels: [1, 3], hilo: [1, 3], wind: [1, 4], humidity: [1, 4],
  coords: [1, 3], orbit: [1, 3], below: [1, 3], crew: [1, 4], mission: [1, 4] };
const TEXT_ALIGNS = [{ value: "auto", label: "Auto" }, { value: "left", label: "Left", icon: "AlignLeft" }, { value: "center", label: "Centre", icon: "AlignCenter" },
  { value: "right", label: "Right", icon: "AlignRight" }];
const CONTAINERS = [{ value: "box", label: "Box" }, { value: "strip", label: "Full-width strip" }, { value: "outline", label: "Outlined" }, { value: "board", label: "Board" }];
const LINE_SIZES = [{ value: "headline", label: "Headline" }, { value: "detail", label: "Detail" }, { value: "big", label: "Big" }];
const ALIGNS = [{ value: "left", label: "Left" }, { value: "center", label: "Centre" }, { value: "right", label: "Right" }];
const HEADLINES = [{ value: "name", label: "Name" }, { value: "place", label: "Place" }, { value: "custom", label: "Your text" }, { value: "time", label: "Time" }];
const LINES = [{ value: "auto", label: "Fit (wraps if needed)" }, { value: "stacked", label: "One per line" }, { value: "inline", label: "Beside the headline" }];
const SEPS = [{ value: "dot", label: "·" }, { value: "slash", label: "/" }, { value: "pipe", label: "|" }, { value: "dash", label: "-" }, { value: "space", label: "space" }];
const POSITIONS = ["tl", "tc", "tr", "bl", "bc", "br"];
// a shield beside the box rather than in the grid (a strip spans the zone, so the wall puts it inside instead)
const shieldOutside = (ov) => ["left", "right"].includes(ov.shield?.place) && ov.container !== "strip";
// the columns with text, as webcam_caption._l_custom counts them for the headline, the last column and the
// Divider (a shield in its line counts; one inside the box or outside it doesn't)
const customCols = (ov, hasText) => [...new Set(Object.keys(DEFAULT_SLOTS)
  .filter((k) => ov.show?.[k] && hasText(k) && (k !== "shield" || ov.shield?.place === "line"))
  .map((k) => (ov.slots?.[k] || DEFAULT_SLOTS[k])[0]))].sort();
const DEFAULT_OVERLAY = { style: "tag", corner: "bl", when: "always", font: "small", accent: "#e8202a", text: "#ffffff",
  bg: "#000000", opacity: 0.7, time_format: "24h", fade: true, show_sec: 12, every_min: 3, custom_text: "", headline: "name",
  lines: "auto", separator: "space", uppercase: false, order: [...ORDERABLE], jp_font: "auto", jp_face: "auto", place_beside: false, wx_beside: false, wx_beside_box: false, temp_icon: false,
  jp_caption: { on: false, text: "", face: "k8x12", size: 12, corner: "auto" },
  show: { name: true, place: true, time: true, date: false, offset: false, daynight: false, weather: true, sun: false,
    age: false, live: true, credit: false, custom: false, shield: false, conditions: false, feels: false, hilo: false, wind: false, humidity: false },
  shield: { route: "99", kind: "auto", style: "pixel", place: "inside", size: "box" } };
// what a newly added webcam starts with, on top of DEFAULT_OVERLAY (existing webcams keep their own settings):
// name, place (beside the name) and LIVE; local time and the time difference; the temperature (icon in front) and
// conditions; 12 h clock, medium text, 30 fps
const NEW_ORDER = ["shield", "live", "name", "place", "time", "offset", "weather", "conditions"];
export const WEBCAM_DELAY = 35;        // hub/webcams.py DELAY_SEC: how far behind live every webcam plays
export const WEBCAM_SEC = 40;          // how long a webcam shows in the Webcams list by default (display/modules/webcam.py WEBCAM_SEC)
const NEW_WEBCAM = { fps: 30,
  overlay: { font: "medium", time_format: "12h", place_beside: true, temp_icon: true,
    order: [...NEW_ORDER, ...ORDERABLE.filter((k) => !NEW_ORDER.includes(k))],
    show: { name: true, place: true, live: true, time: true, offset: true, weather: true, conditions: true } } };
const newOverlay = () => {
  const o = { ...clone(DEFAULT_OVERLAY), ...clone(NEW_WEBCAM.overlay) };
  o.show = { ...clone(DEFAULT_OVERLAY.show), ...NEW_WEBCAM.overlay.show };
  return o;
};
// the caption's highway shield (display/modules/shields.py draws it; the Custom layout only)
const SHIELD_PRESETS = [
  { group: "BC", routes: ["1", "7", "10", "11", "13", "15", "17", "91", "99"] },
  { group: "Yellowhead · Crowsnest", routes: ["5", "16", "3"] },
  { group: "Washington", routes: ["I-5", "I-405", "US 97", "SR 539", "SR 543", "SR 9"] },
];
const SHIELD_KINDS = [{ value: "auto", label: "By number" }, { value: "bc", label: "BC" }, { value: "tch", label: "Trans-Canada" },
  { value: "yellowhead", label: "Yellowhead" }, { value: "crowsnest", label: "Crowsnest" }, { value: "interstate", label: "Interstate" },
  { value: "us", label: "US Highway" }, { value: "wa", label: "Washington" }];
const SHIELD_PLACES = [{ value: "inside", label: "Inside the box" }, { value: "left", label: "Outside left" },
  { value: "right", label: "Outside right" }, { value: "line", label: "In its line" }];
const SHIELD_SIZES = [{ value: "box", label: "Box height" }, ...[16, 20, 24, 28, 32, 40, 48, 56, 64].map((h) => ({ value: h, label: h + " px" }))];
const SHIELD_PRESET_ROUTES = SHIELD_PRESETS.flatMap((g) => g.routes);
const SHIELD_STYLES = [{ value: "pixel", label: "Pixel art" }, { value: "real", label: "Real sign" }];
const shieldSrc = (route, kind = "auto", h = 24, style = "pixel") =>
  `/api/shields/preview.png?${new URLSearchParams({ route, kind, h, scale: 2, style })}`;
// still sources (a DriveBC camera by id, or any JPEG) and the query that names a source to the hub
const STILL = ["drivebc", "image"];
const DRIVEBC_IMAGE = (id) => `https://www.drivebc.ca/images/${id}.jpg`;
const srcUrl = (s) => (s?.type === "drivebc" ? DRIVEBC_IMAGE(s.id) : s?.url || "");
const srcParams = (s) => ({ type: s?.type || "stream", ...(s?.type === "drivebc" ? { id: s.id } : { url: s?.url || "" }),
  ...(s?.refresh_sec ? { refresh: s.refresh_sec } : {}) });
const hasSource = (s) => (s?.type === "drivebc" ? Number.isInteger(s.id) && s.id > 0 : !!s?.url);
const LOOKS = { N: "north", NE: "northeast", E: "east", SE: "southeast", S: "south", SW: "southwest", W: "west", NW: "northwest" };
const TIMEZONES = ["America/Vancouver", "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York", "America/Halifax",
  "Pacific/Honolulu", "America/Anchorage", "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Rome", "Atlantic/Reykjavik",
  "Africa/Johannesburg", "Africa/Windhoek", "Asia/Tokyo", "Asia/Seoul", "Asia/Hong_Kong", "Asia/Singapore", "Australia/Sydney", "Pacific/Auckland"];

const slug = (s) => (s || "webcam").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 32) || "webcam";
const host = (u) => { try { return new URL(u).host.replace(/^www\./, ""); } catch { return u || ""; } };
const qs = (o) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
export const camEntry = (id, shape) => ({ panel: SHAPES[shape].panel, cam: id, rules: [], dwell_sec: WEBCAM_SEC });

// where a webcam's weather comes from: its weather city (picked by name), else its own coordinates
const wxPoint = (c) => (c?.wx_city && Number.isFinite(c.wx_city.lat) ? c.wx_city
  : Number.isFinite(c?.lat) && Number.isFinite(c?.lon) ? { lat: c.lat, lon: c.lon } : null);
// a location's time zone, local time and weather (hub /api/webcams/local, from Open-Meteo)
const localOf = (lat, lon) => api(`/api/webcams/local?${qs({ lat, lon })}`);
function useStatus() {
  const status = ref({ feeds: [], zones: { left: [295, 130], right: [152, 130], full: [448, 130], tall: [448, 175], wall: [448, 192] } });
  let t;
  const load = async () => { try { status.value = await api("/api/webcams/status"); } catch {} };
  onMounted(() => { load(); t = setInterval(load, 3000); });
  onBeforeUnmount(() => clearInterval(t));
  const feedFor = (cam) => status.value.feeds.find((f) => f.url === srcUrl(cam?.source) && f.type === (cam?.source?.type || "stream"));
  const healthFor = (cid) => status.value.health?.[cid] || null;          // hub/jobs/webcam_health.py
  return { status, feedFor, healthFor };
}
// "down for 1:25" (h:mm) when the hub's last check found the webcam down (no picture, or a black one); else null
const hm = (sec) => { const m = Math.max(0, Math.floor(sec / 60)); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`; };
function downPill(h) {
  if (!h || h.up !== false) return null;
  const why = h.why === "black" ? "the picture is black" : h.why || "no picture";
  return { cls: "bad", text: `down for ${hm(Date.now() / 1000 - (h.since || h.checked || Date.now() / 1000))}`, title: `The hub's checks: ${why}. The wall skips it until it's back.` };
}
function feedPill(f) {
  if (!f) return { cls: "", text: "idle" };
  if (f.state === "live" && f.still) return { cls: "ok", text: `still · ${Math.round((f.image_age_sec || 0) / 60)} min old` };
  if (f.state === "live") return { cls: "ok", text: `live · ${f.measured_fps} fps${f.viewers ? "" : " · warm"}` };
  if (f.state === "starting") return { cls: "warn", text: "tuning in…" };
  return { cls: "bad", text: f.error ? `retrying: ${f.error}` : f.state };
}
function usedIn(id) {
  const lc = store.draft.modules.layout_cycle || {};
  const out = [];
  for (const side of ["left", "right"]) (lc[side] || []).forEach((e, i) => { if (e.cam === id && e.panel?.startsWith("webcam")) out.push({ side, i, e }); });
  return out;
}

// A current still from the hub (/api/webcams/snapshot.jpg). The hub answers at once: the still, or 503 while it grabs
// one (ask again after Retry-After), or 404 when the stream is down (then YouTube's thumbnail, or nothing).
const youtubeId = (url) => { try { const u = new URL(url); return u.searchParams.get("v") || ""; } catch { return ""; } };
const SnapImg = {
  props: { source: Object, vid: String },                 // vid: the YouTube id to fall back on when the URL has none (…/@channel/live)
  setup(props) {
    const url = ref("");
    const state = ref("loading");
    let alive = true, timer = null, blobUrl = "";
    const load = async (tries = 0) => {
      if (!alive || !hasSource(props.source)) return;
      if (props.source.type === "drivebc") { url.value = `/api/webcams/drivebc/${props.source.id}.jpg`; state.value = "ok"; return; }
      try {
        const r = await fetch(snap(props.source));
        if (r.ok) {
          const b = await r.blob();
          if (!alive) return;
          if (blobUrl) URL.revokeObjectURL(blobUrl);
          url.value = blobUrl = URL.createObjectURL(b);
          state.value = "ok";
          return;
        }
        if (r.status === 503 && tries < 100) {        // a first-ever fill of the whole gallery takes a minute or two
          timer = setTimeout(() => load(tries + 1), 1000 * (Number(r.headers.get("Retry-After")) || 4));
          return;
        }
      } catch {}
      const id = props.vid || (props.source.type === "youtube" ? youtubeId(props.source.url) : "");
      if (id) { url.value = thumb({ id }); state.value = "ok"; } else state.value = "none";
    };
    // only once it is on screen: a long gallery would otherwise queue a grab on the hub for every webcam in it
    const box = ref(null);
    let io = null;
    onMounted(() => {
      io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { io.disconnect(); io = null; load(); } }, { rootMargin: "200px" });
      io.observe(box.value);
    });
    watch(() => srcUrl(props.source), () => { if (io) return; clearTimeout(timer); state.value = "loading"; url.value = ""; load(); });
    onBeforeUnmount(() => { alive = false; clearTimeout(timer); io?.disconnect(); if (blobUrl) URL.revokeObjectURL(blobUrl); });
    return { url, state, box };
  },
  template: `<img v-if="url" :src="url" alt=""><span v-else ref="box" class="wc-snap-wait">{{ state === 'loading' ? 'live frame…' : '' }}</span>`,
};

// --- intersection and traffic cameras: the cities' own intersection cameras and DriveBC's highway ones (stills) ------
// By city (each split into the city's intersection cameras and DriveBC's highway cameras) or bridge, as a list or on a
// map. Click one to add it as a webcam; "Select several" picks many at once for the camera grid (or as webcams).
const BRIDGES = "__bridges";
const sameSrc = (a, b) => !!a && !!b && a.type === b.type && (a.type === "drivebc" ? a.id === b.id : a.url === b.url);
const camSrc = (c) => c.source || { type: "drivebc", id: c.id };
const ownCam = (c) => !!c.credit && c.credit !== "DriveBC";          // a city's (or SFU's) camera, not DriveBC's
const TrafficCams = {
  components: { CameraMap },
  props: { cams: Array, grid: Object },                    // [[id, cam]] of the webcams set up; the camera grid
  emits: ["add", "grid"],
  setup(props, { emit }) {
    const data = ref({ crossings: [], cities: [], count: 0 });
    const loading = ref(true);
    const city = ref(store.trafficCity || "");
    const view = ref(store.trafficView || "list");
    const query = ref("");
    const picking = ref(!!store.trafficPick);
    const picked = ref([]);
    store.trafficPick = false;
    // the cities' own cameras are crawled by the hub in the background (daily); while a first crawl runs, ask again
    const crawling = ref(false);
    let tries = 0, again = null;
    const load = async () => {
      try {
        data.value = await api("/api/webcams/traffic");
        if (!city.value) city.value = data.value.cities[0]?.city || BRIDGES;
        crawling.value = !!data.value.crawling;
        if (crawling.value && tries++ < 20) again = setTimeout(load, 10000);
      } catch (e) { fail(e); }
      loading.value = false;
    };
    onMounted(load);
    onBeforeUnmount(() => clearTimeout(again));
    watch(city, (c) => { store.trafficCity = c; });
    watch(view, (v) => { store.trafficView = v; });
    const have = (c) => props.cams.find(([, x]) => sameSrc(x.source, camSrc(c)))?.[0];
    const inGrid = (c) => (props.grid?.cams || []).some((g) => sameSrc(g.source, camSrc(c)));
    const thumb = (c) => c.thumb || `/api/webcams/drivebc/${c.id}.jpg`;
    const look = (c) => c.look || LOOKS[c.orientation] || "";
    const titleOf = (c) => c._label || c.name;
    const why = (c) => [c.caption, c.credit, c.turns && "This camera can turn: the direction is DriveBC's last known one.",
                        c.corrected && "Direction checked by eye (DriveBC's label was wrong)."].filter(Boolean).join("\n");
    const match = (c) => `${c.name} ${c.caption} ${c.highway} ${c.city} ${c.credit || ""}`.toLowerCase().includes(query.value.toLowerCase());
    const allCams = computed(() => data.value.cities.flatMap((c) => c.cams));
    const tabs = computed(() => [{ key: BRIDGES, label: "Bridges & tunnels", n: data.value.crossings.reduce((n, x) => n + x.cams.length, 0) },
      ...data.value.cities.map((c) => ({ key: c.city, label: c.city, n: c.cams.length, own: c.cams.filter(ownCam).length }))]);
    // the sections shown: a search across everything, a bridge each, or a city's own cameras (by who runs them) then DriveBC's
    const groups = computed(() => {
      if (query.value) return [{ key: "q", title: "Matching cameras", cams: allCams.value.filter(match).slice(0, 160) }];
      if (city.value === BRIDGES) {
        return data.value.crossings.map((x) => ({ key: x.name, title: x.name, note: x.note, icon: "Construction",
          cams: x.cams.map((c) => ({ ...c, _label: (x.label || x.short) + (look(c) ? " · looking " + look(c) : "") })) }));
      }
      const cams = data.value.cities.find((c) => c.city === city.value)?.cams || [];
      const byCredit = {};
      for (const c of cams.filter(ownCam)) (byCredit[c.credit] = byCredit[c.credit] || []).push(c);
      return [...Object.entries(byCredit).map(([credit, cs]) => ({ key: credit, title: `Intersection cameras · ${credit}`, icon: "TrafficCone", cams: cs })),
        { key: "dbc", title: "Highway cameras · DriveBC", icon: "Cctv", cams: cams.filter((c) => !ownCam(c)) }].filter((g) => g.cams.length);
    });
    const shown = computed(() => groups.value.flatMap((g) => g.cams));
    const isPicked = (c) => picked.value.some((p) => sameSrc(camSrc(p), camSrc(c)));
    const toggle = (c) => { const i = picked.value.findIndex((p) => sameSrc(camSrc(p), camSrc(c))); if (i >= 0) picked.value.splice(i, 1); else picked.value.push(c); };
    const pickAll = (cs) => { for (const c of cs) if (!isPicked(c) && !inGrid(c)) picked.value.push(c); };
    const asWebcam = (c) => ({ label: titleOf(c), place: c.city || "", credit: c.credit || "DriveBC", tz: "America/Vancouver",
      source: { ...camSrc(c) }, lat: c.lat, lon: c.lon });
    const click = (c) => {
      if (picking.value) return toggle(c);
      if (have(c)) return go(`webcams/${have(c)}`);
      emit("add", asWebcam(c));
      toast(`Added ${titleOf(c)} to your webcams. Save to keep it.`);
    };
    const toGrid = () => {
      const add = picked.value.filter((c) => !inGrid(c));
      emit("grid", add.map((c) => ({ name: titleOf(c), source: { ...camSrc(c) }, thumb: thumb(c), ...(c.lat != null ? { lat: c.lat, lon: c.lon } : {}) })));
      toast(`${add.length} camera${add.length === 1 ? "" : "s"} added to the camera grid. Save to keep them.`);
      picked.value = [];
      picking.value = false;
    };
    const toWebcams = () => {
      const add = picked.value.filter((c) => !have(c));
      for (const c of add) emit("add", asWebcam(c));
      toast(`${add.length} webcam${add.length === 1 ? "" : "s"} added. Save to keep them.`);
      picked.value = [];
      picking.value = false;
    };
    // the map: the cities' cameras as a layer (one marker per intersection); DriveBC's are the map's own
    const cityAll = computed(() => allCams.value.filter(ownCam));
    const addedIds = computed(() => [...props.cams.filter(([, x]) => x.source?.type === "drivebc").map(([, x]) => x.source.id),
      ...cityAll.value.filter((c) => have(c) || (picking.value && isPicked(c))).map((c) => c.id)]);
    const mapFocus = computed(() => {
      if (city.value === BRIDGES || query.value) return null;
      const pts = (data.value.cities.find((c) => c.city === city.value)?.cams || []).filter((c) => c.lat != null);
      if (!pts.length) return null;
      const lons = pts.map((c) => +c.lon), lats = pts.map((c) => +c.lat);
      return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
    });
    const fromMap = (c) => (picking.value ? toggle(c) : click({ ...c, _label: c.name }));
    const openFromMap = (c) => go(`webcams/${have(c)}`);
    return { data, loading, crawling, city, view, query, picking, picked, tabs, groups, shown, have, inGrid, thumb, titleOf, why, look, isPicked,
      toggle, pickAll, click, toGrid, toWebcams, cityAll, addedIds, mapFocus, fromMap, openFromMap, BRIDGES };
  },
  template: `<div class="card">
    <div class="card-head" style="flex-wrap:wrap; row-gap:8px"><Icon name="TrafficCone" :size="16"/><h3 style="white-space:nowrap">Intersection &amp; traffic cameras</h3>
      <span class="faint" style="font-size:12px; white-space:nowrap">{{ data.count || '…' }} stills</span><span class="spacer"></span>
      <input class="input" v-model="query" placeholder="Search every camera" style="max-width:210px; height:28px">
      <Seg v-model="view" :options="[{value:'list', label:'List', icon:'LayoutGrid'}, {value:'map', label:'Map', icon:'Map'}]"/>
      <button class="btn sm" :class="{primary: picking}" @click="picking = !picking; picked = []"><Icon :name="picking ? 'X' : 'ListChecks'" :size="14"/>{{ picking ? 'Done selecting' : 'Select several' }}</button></div>
    <div class="card-body" v-if="!query" style="padding-bottom:4px"><div class="row" style="gap:6px; flex-wrap:wrap">
      <button v-for="t in tabs" :key="t.key" type="button" class="chip" :class="{off: city !== t.key}" style="height:26px; padding:0 10px"
              @click="city = t.key" :title="t.own ? t.own + ' of the city’s own intersection cameras' : ''">
        <Icon v-if="t.key === BRIDGES" name="Construction" :size="13"/>{{ t.label }} <span class="faint">{{ t.n }}</span>
        <span v-if="t.own" class="wc-own" title="Has its own intersection cameras"></span></button></div></div>
    <div v-if="picking" class="wc-pickbar">
      <Icon name="ListChecks" :size="15"/><b>{{ picked.length }} selected</b>
      <button class="btn sm ghost" @click="pickAll(shown)" :disabled="!shown.length" v-if="view === 'list'">Select all {{ shown.length }} shown</button>
      <button class="btn sm ghost" @click="picked = []" :disabled="!picked.length">Clear</button>
      <span class="spacer"></span>
      <button class="btn sm" @click="toWebcams" :disabled="!picked.length"><Icon name="Video" :size="13"/>Add as webcams</button>
      <button class="btn sm primary" @click="toGrid" :disabled="!picked.length"><Icon name="Grid2x2" :size="13"/>Add to the camera grid</button></div>
    <div v-if="crawling" class="callout info" style="margin:8px 16px 0; font-size:12.5px"><Icon name="LoaderCircle" :size="14" class="spin"/>
      The hub is fetching the cities’ own intersection cameras (Vancouver, Richmond, Surrey, Langley, SFU); they appear here as each city arrives.</div>
    <div v-if="loading" class="card-body faint">Loading cameras…</div>
    <CameraMap v-else-if="view === 'map'" addable :added="addedIds" :extra="cityAll" :focus="mapFocus" @add="fromMap" @open="openFromMap"/>
    <template v-else>
      <div v-for="g in groups" :key="g.key" class="card-body" style="padding-top:6px">
        <div class="wc-subhead row" style="gap:6px"><Icon :name="g.icon || 'Search'" :size="13"/>{{ g.title }} <span class="faint">{{ g.cams.length }}</span>
          <span class="spacer"></span><button v-if="picking" class="btn sm ghost" @click="pickAll(g.cams)">Select these</button></div>
        <div class="faint" style="font-size:12px; margin:-2px 0 6px" v-if="g.note">{{ g.note }}</div>
        <div class="wc-gallery">
          <button v-for="c in g.cams" :key="c.id" type="button" class="wc-pick" :class="{have: have(c), off: !c.on, picked: picking && isPicked(c)}"
                  @click="click(c)" :title="why(c)">
            <span class="wc-thumb"><img :src="thumb(c)" loading="lazy" alt="">
              <span class="wc-check" v-if="picking"><Icon :name="isPicked(c) ? 'SquareCheck' : 'Square'" :size="16"/></span>
              <span class="wc-have" v-if="have(c)"><Icon name="Check" :size="13"/>webcam</span>
              <span class="wc-have grid" v-else-if="inGrid(c)"><Icon name="Grid2x2" :size="12"/>in grid</span>
              <span class="wc-have" v-else-if="!c.on" style="color:var(--bad)">offline</span></span>
            <b>{{ titleOf(c) }}<span v-if="c.turns" class="faint" title="Can turn"> ↻</span></b>
            <span class="faint wc-note">{{ query ? c.city + ' · ' : '' }}{{ c.credit || c.caption }}</span>
          </button></div></div>
      <div class="card-body faint" v-if="!groups.length">{{ query ? 'No camera matches “' + query + '”.' : 'No cameras here.' }}</div>
    </template>
  </div>`,
};

// --- live webcams: the ready-made gallery (webcam_gallery.js) by city and kind, as a grid or on a map ---------------
// the map: one dot per webcam, coloured by kind; webcams too close together at this zoom share a numbered dot
const GalleryMap = {
  components: { SnapImg },
  props: { cams: Array, have: Function, fitKey: String },          // fitKey changes → fit the map to the cams again
  emits: ["pick"],
  setup(props) {
    const el = ref(null);
    const { size, view, px, onScreen, tiles, fit, down, move, up, wheel, zoomAt, dragged } =
      useSlippyMap(el, { lon: -98, lat: 45, z: 3 }, { minZ: 2, maxZ: 16 });
    const sel = ref([]);                                           // the webcams picked (several when a dot is shared)
    const fitAll = () => {
      const pts = props.cams.filter((c) => c.lat != null);
      if (!pts.length) return;
      const lons = pts.map((c) => c.lon), lats = pts.map((c) => c.lat);
      fit([Math.min(...lons) - 0.01, Math.min(...lats) - 0.01, Math.max(...lons) + 0.01, Math.max(...lats) + 0.01], 13);
    };
    onMounted(fitAll);
    watch(() => props.fitKey, () => { sel.value = []; fitAll(); });
    const dots = computed(() => {
      const cell = 22, by = {};
      for (const c of props.cams) {
        if (c.lat == null) continue;
        const p = px(c.lon, c.lat);
        if (!onScreen(p, 30)) continue;
        const k = `${Math.round(p.x / cell)},${Math.round(p.y / cell)}`;
        (by[k] = by[k] || { key: k, cams: [], x: 0, y: 0 }).cams.push(c);
        by[k].x += p.x; by[k].y += p.y;
      }
      return Object.values(by).map((d) => ({ ...d, x: d.x / d.cams.length, y: d.y / d.cams.length,
        col: (CATS[d.cams[0].cat] || {}).col || "#7c8cff", on: d.cams.some((c) => sel.value.includes(c)) }));
    });
    const pick = (d) => {
      if (dragged()) return;
      const spread = new Set(d.cams.map((c) => `${c.lat.toFixed(3)},${c.lon.toFixed(3)}`)).size > 1;
      if (spread && view.value.z < 15) {                              // a crowd: zoom in on it
        const lons = d.cams.map((c) => c.lon), lats = d.cams.map((c) => c.lat);
        fit([Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)], Math.min(16, view.value.z + 4));
        return;
      }
      sel.value = d.cams;
    };
    const kinds = computed(() => [...new Set(props.cams.map((c) => c.cat))].map((k) => ({ k, ...CATS[k] })).filter((k) => k.label));
    return { el, size, tiles, down, move, up, wheel, zoomAt, dots, pick, sel, fitAll, kinds, CATS };
  },
  template: `<div style="display:flex; min-height:0; border-top:1px solid var(--line)">
      <div ref="el" @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="up" @wheel="wheel"
           style="position:relative; flex:1; height:calc(100vh - 260px); min-height:420px; overflow:hidden; background:#0e1116; cursor:grab; touch-action:none; user-select:none">
        <img v-for="t in tiles" :key="t.key" :src="t.src.replace('{layer}', 'esri_dark')" draggable="false"
             :style="{position: 'absolute', left: t.x + 'px', top: t.y + 'px', width: '256px', height: '256px', pointerEvents: 'none'}" alt="">
        <img v-for="t in tiles" :key="'n' + t.key" :src="t.src.replace('{layer}', 'esri_dark_labels')" draggable="false"
             :style="{position: 'absolute', left: t.x + 'px', top: t.y + 'px', width: '256px', height: '256px', pointerEvents: 'none', opacity: .8}" alt="">
        <svg :width="size.w" :height="size.h" style="position:absolute; inset:0">
          <g v-for="d in dots" :key="d.key" @click.stop="pick(d)" style="cursor:pointer">
            <circle :cx="d.x" :cy="d.y" :r="d.cams.length > 1 ? 9 : 5.5" :fill="d.col" stroke="#000" stroke-width="1.4"/>
            <text v-if="d.cams.length > 1" :x="d.x" :y="d.y + 3.5" text-anchor="middle" font-size="10" font-weight="700" fill="#000">{{ d.cams.length }}</text>
            <circle v-if="d.on" :cx="d.x" :cy="d.y" :r="d.cams.length > 1 ? 13 : 10" fill="none" stroke="#fff" stroke-width="1.5"/>
            <title>{{ d.cams.map(c => c.label).join('\\n') }}</title>
          </g>
        </svg>
        <div style="position:absolute; left:10px; top:10px; display:flex; flex-direction:column; gap:4px" @pointerdown.stop>
          <button class="btn sm icon-only" @click="zoomAt(1, size.w / 2, size.h / 2)"><Icon name="Plus" :size="14"/></button>
          <button class="btn sm icon-only" @click="zoomAt(-1, size.w / 2, size.h / 2)"><Icon name="Minus" :size="14"/></button>
          <button class="btn sm icon-only" @click="fitAll" title="Show them all"><Icon name="Maximize2" :size="14"/></button>
        </div>
        <div style="position:absolute; left:10px; bottom:8px; max-width:calc(100% - 20px); display:flex; flex-wrap:wrap; gap:4px 12px; font-size:12px; white-space:nowrap; background:rgba(0,0,0,.6); padding:4px 9px; border-radius:7px" @pointerdown.stop>
          <span v-for="k in kinds" :key="k.k"><span :style="{background: k.col, display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%'}"></span> {{ k.label }}</span>
          <span class="faint">numbered dot = several webcams: click to zoom in</span>
        </div>
        <div style="position:absolute; right:6px; top:4px; font-size:10.5px; color:#9aa; background:rgba(0,0,0,.5); padding:1px 5px; border-radius:4px" @pointerdown.stop>Basemap © Esri, HERE, Garmin, © OpenStreetMap contributors</div>
        <div v-if="!cams.length" class="faint" style="position:absolute; inset:0; display:grid; place-items:center; pointer-events:none">No webcam matches.</div>
      </div>
      <div style="width:320px; flex:none; border-left:1px solid var(--line); overflow:auto; height:calc(100vh - 260px); min-height:420px">
        <div v-if="sel.length" class="card-body stack">
          <div v-for="c in sel" :key="c.id" class="stack" style="gap:6px">
            <span class="wc-thumb" style="border-radius:8px; overflow:hidden"><SnapImg :source="c.source" :vid="c.id"/><span class="live">LIVE</span></span>
            <div><b>{{ c.label }}</b><div class="faint" style="font-size:12.5px">{{ c.place }} · {{ CATS[c.cat]?.label }}</div></div>
            <div class="faint" style="font-size:12.5px">{{ c.note }}<span v-if="c.credit"> · {{ c.credit }}</span></div>
            <div class="row">
              <button v-if="!have(c)" class="btn sm primary" @click="$emit('pick', c)"><Icon name="Plus" :size="13"/>Add as a webcam</button>
              <button v-else class="btn sm" @click="$emit('pick', c)"><Icon name="Check" :size="13"/>Added · open it</button></div>
          </div>
          <button class="btn sm ghost" @click="sel = []"><Icon name="X" :size="13"/>Close</button>
        </div>
        <div v-else class="card-body faint" style="font-size:12.5px">Click a dot to see that webcam's live picture and add it.
          Drag to pan, scroll to zoom.</div>
      </div>
    </div>`,
};

const LiveGallery = {
  components: { SnapImg, GalleryMap },
  props: { have: Function },
  emits: ["pick"],
  setup() {
    const city = ref(store.galleryCity || CITIES[0].key);
    const cat = ref("");
    const mode = ref(store.galleryMode || "grid");
    const query = ref("");
    watch(city, (c) => { store.galleryCity = c; cat.value = ""; });
    watch(mode, (m) => { store.galleryMode = m; });
    const inCity = computed(() => (city.value === "all" ? GALLERY : GALLERY.filter((c) => c.city === city.value)));
    const match = (c) => !query.value || `${c.label} ${c.place} ${c.note} ${c.credit} ${c.cityLabel}`.toLowerCase().includes(query.value.toLowerCase());
    const shown = computed(() => (query.value ? GALLERY : inCity.value).filter((c) => (query.value || !cat.value || c.cat === cat.value) && match(c)));
    const cats = computed(() => Object.entries(CATS).map(([k, v]) => ({ k, ...v, n: inCity.value.filter((c) => c.cat === k).length })).filter((x) => x.n));
    // the grid, a section per kind (or per city while searching)
    const groups = computed(() => {
      if (query.value) {
        return CITIES.map((x) => ({ key: x.key, label: x.label, icon: "MapPin", cams: shown.value.filter((c) => c.city === x.key) })).filter((g) => g.cams.length);
      }
      return Object.entries(CATS).map(([k, v]) => ({ key: k, label: v.label, icon: v.icon, cams: shown.value.filter((c) => c.cat === k) })).filter((g) => g.cams.length);
    });
    return { CITIES, city, cat, cats, mode, query, shown, groups, total: GALLERY.length };
  },
  template: `<div class="card">
    <div class="card-head"><Icon name="Youtube" :size="16"/><h3>Live YouTube webcams</h3>
      <span class="faint" style="font-size:12px">{{ total }} 24/7 streams by city · click one to add it</span><span class="spacer"></span>
      <input class="input" v-model="query" placeholder="Search every city" style="max-width:200px; height:28px">
      <Seg v-model="mode" :options="[{value:'grid', label:'Grid', icon:'LayoutGrid'}, {value:'map', label:'Map', icon:'Map'}]"/></div>
    <div class="card-body stack" style="gap:8px; padding-bottom:6px" v-if="!query">
      <div class="row" style="gap:6px; flex-wrap:wrap">
        <button v-for="x in CITIES" :key="x.key" type="button" class="chip" :class="{off: city !== x.key}" style="height:26px; padding:0 10px"
                @click="city = x.key">{{ x.label }} <span class="faint">{{ x.cams.length }}</span></button>
        <button type="button" class="chip" :class="{off: city !== 'all'}" style="height:26px; padding:0 10px" @click="city = 'all'">
          <Icon name="Globe" :size="13"/>Everywhere <span class="faint">{{ total }}</span></button></div>
      <div class="row" style="gap:6px; flex-wrap:wrap">
        <button type="button" class="chip" :class="{off: cat}" style="height:24px; padding:0 9px; font-size:12px" @click="cat = ''">All kinds</button>
        <button v-for="x in cats" :key="x.k" type="button" class="chip" :class="{off: cat !== x.k}" style="height:24px; padding:0 9px; font-size:12px"
                @click="cat = cat === x.k ? '' : x.k"><span :style="{background: x.col, width: '7px', height: '7px', borderRadius: '50%', display: 'inline-block'}"></span>
          {{ x.label }} <span class="faint">{{ x.n }}</span></button></div>
    </div>
    <GalleryMap v-if="mode === 'map'" :cams="shown" :have="have" :fit-key="query ? 'q|' + query : city + '|' + cat" @pick="$emit('pick', $event)"/>
    <template v-else>
      <div class="card-body" v-for="g in groups" :key="g.key" style="padding-top:4px">
        <div class="wc-subhead"><Icon :name="g.icon" :size="13" style="vertical-align:-2px"/> {{ g.label }}</div>
        <div class="wc-gallery">
          <button v-for="c in g.cams" :key="c.id" type="button" class="wc-pick" :class="{have: have(c)}" @click="$emit('pick', c)" :title="c.note">
            <span class="wc-thumb"><SnapImg :source="c.source" :vid="c.id"/><span class="live">LIVE</span>
              <span class="wc-have" v-if="have(c)"><Icon name="Check" :size="13"/>added</span></span>
            <b>{{ c.label }}</b><span class="faint">{{ c.place }}</span><span class="faint wc-note">{{ c.note }}</span>
          </button></div></div>
      <div class="card-body faint" v-if="!groups.length">No webcam matches{{ query ? ' “' + query + '”' : '' }}.</div>
    </template>
  </div>`,
};

// modules.webcams with its list and grid, in saved and draft alike (opening a page isn't a change); a first list is
// every webcam, in the order the display already plays them without one
function seedWebcams() {
  const wc = seed(["modules", "webcams"], { cams: {} });
  seed(["modules", "webcams", "playlist"], Object.keys(wc.cams || {}).map((cam) => ({ cam, sec: WEBCAM_SEC, on: true })));
  seed(["modules", "webcams", "grid"], { cams: [], interval_ms: 200, hold_sec: 3, labels: true });
  seed(["modules", "webcams", "grid", "label"], { lines: [["street1"], ["street2"]], dir: "tag", at: "bottom" });
  seed(["modules", "webcams", "grid", "near"], { on: false, radius: 1, unit: "km", kinds: "city" });
  return wc;
}

// --- the rotation's webcam entries: the Webcams list (no cam), the camera grid, and older one-webcam entries ------------
const isListEntry = (e) => e.panel?.startsWith("webcam") && !e.panel.startsWith("webcam_grid") && !e.cam;
const rotationEntries = (test) => {
  const lc = store.draft.modules.layout_cycle || {};
  return ["left", "right"].flatMap((side) => (lc[side] || []).map((e, i) => ({ side, i, e })).filter((x) => test(x.e)));
};
const shapeOfPanel = (e, side) => (e.panel.endsWith("_wall") ? "wall" : e.panel.endsWith("_full") ? "full" : e.panel.endsWith("_tall") ? "tall" : side);
const LIST_SHAPES = { left: "webcam", right: "webcam", full: "webcam_full", tall: "webcam_tall", wall: "webcam_wall" };
const GRID_SHAPES = { left: "webcam_grid", right: "webcam_grid", full: "webcam_grid_full", tall: "webcam_grid_tall", wall: "webcam_grid_wall" };
const addEntry = (panel, side) => {
  store.draft.modules.layout_cycle[side].push({ panel, rules: [], advance: "cycles", cycles: 1, max_sec: 3600 });
};

// Where something plays in the rotation, with a button to put it there (and a link to the Rotation page)
const InRotation = {
  props: { found: Array, what: String, shapes: Object },
  setup(props) {
    const shape = ref("full");
    const add = () => {
      const s = SHAPES[shape.value];
      addEntry(props.shapes[shape.value], s.side);
      toast(`${props.what} added to the ${s.side} rotation (${s.label.toLowerCase()}). Save to keep it.`);
    };
    return { shape, add, SHAPES, shapeOfPanel };
  },
  template: `<div class="wc-rot">
    <Icon name="RefreshCw" :size="15"/>
    <template v-if="found.length"><span>In the rotation:</span>
      <span v-for="(u, k) in found" :key="k" class="pill accent">{{ u.side }} side · {{ SHAPES[shapeOfPanel(u.e, u.side)].label.toLowerCase() }}{{ u.e.variants?.length ? ' · other sizes at set times' : '' }}{{ u.e.enabled === false ? ' · off' : '' }}</span>
      <a href="#/rotation" class="btn sm ghost">Rules, sizes and timing<Icon name="ArrowRight" :size="13"/></a></template>
    <template v-else><span>Not in the rotation yet.</span><span class="spacer"></span>
      <select class="input" v-model="shape" style="width:auto; height:28px"><option v-for="(s, k) in SHAPES" :key="k" :value="k">{{ s.label }}</option></select>
      <button class="btn sm primary" @click="add"><Icon name="ListPlus" :size="14"/>Add to the rotation</button></template>
  </div>`,
};

// --- Your webcams: the Webcams list (order, on/off, seconds), played by the rotation's one Webcams entry --------------
const PlaylistTab = {
  components: { SnapImg, InRotation },
  emits: ["add", "find"],
  setup() {
    const wc = store.draft.modules.webcams;
    const cams = computed(() => wc.cams || {});
    const list = computed(() => wc.playlist);
    const { feedFor, healthFor } = useStatus();
    // "Fetch all feeds": the hub looks every webcam up, one at a time (POST starts it, GET follows it)
    const fetchState = ref({ running: false, done: 0, total: 0, results: {} });
    const fetching = computed(() => fetchState.value.running);
    let fetchTimer = null;
    const followFetch = async () => {
      try { fetchState.value = await api("/api/webcams/fetch_all"); } catch {}
      if (fetchState.value.running) { fetchTimer = setTimeout(followFetch, 1500); return; }
      const r = Object.values(fetchState.value.results || {});
      if (!r.length) return;
      const bad = r.filter((x) => !x.ok).length;
      if (fetchState.value.stopped) toast(`Stopped: ${fetchState.value.stopped}`, "bad");
      else toast(bad ? `${r.length - bad} of ${r.length} feeds ready; ${bad} didn't come up` : `All ${r.length} feeds ready`, bad ? "bad" : "ok");
    };
    const fetchAll = async () => {
      if (dirty.value.length) toast("Fetching the saved webcams (save first to include new ones)");
      try { fetchState.value = await api("/api/webcams/fetch_all", { method: "POST" }); } catch (e) { return fail(e); }
      clearTimeout(fetchTimer);
      fetchTimer = setTimeout(followFetch, 1000);
    };
    onBeforeUnmount(() => clearTimeout(fetchTimer));
    const fetchResult = (cid) => fetchState.value.results?.[cid] || null;
    const found = computed(() => rotationEntries(isListEntry));
    const single = computed(() => rotationEntries((e) => e.panel?.startsWith("webcam") && !e.panel.startsWith("webcam_grid") && e.cam));
    // older rotations had one entry per webcam: fold them into the list (their order first), with one Webcams entry
    const combine = () => {
      const lc = store.draft.modules.layout_cycle;
      const first = single.value[0];
      const order = [...new Set(single.value.map((u) => u.e.cam))];
      const rest = wc.playlist.filter((x) => !order.includes(x.cam));
      wc.playlist = [...order.map((cam) => ({ ...(wc.playlist.find((x) => x.cam === cam) || { cam }), on: true,
        sec: single.value.find((u) => u.e.cam === cam).e.dwell_sec || WEBCAM_SEC })), ...rest.map((x) => ({ ...x, on: false }))];
      for (const side of ["left", "right"]) lc[side] = (lc[side] || []).filter((e) => !(e.panel?.startsWith("webcam") && !e.panel.startsWith("webcam_grid") && e.cam));
      if (!found.value.length) {
        const entry = { panel: first.e.panel, rules: first.e.rules || [], advance: "cycles", cycles: 1, max_sec: 3600, ...(first.e.variants ? { variants: first.e.variants } : {}) };
        lc[first.side].splice(Math.min(first.i, lc[first.side].length), 0, entry);
      }
      toast(`${order.length} webcams combined into the list, with one Webcams entry in the rotation. Save to keep it.`);
    };
    const on = computed(() => list.value.filter((x) => x.on !== false && cams.value[x.cam]));
    const total = computed(() => on.value.reduce((n, x) => n + (x.sec || WEBCAM_SEC), 0));
    const srcHost = (s) => (s?.type === "drivebc" ? `DriveBC camera ${s.id}` : host(s?.url));
    const kindOf = (s) => (STILL.includes(s?.type) ? "Still" : "Live");
    const mins = (s) => (s >= 90 ? `${Math.round(s / 6) / 10} min` : `${s} s`);
    return { LIST_SHAPES, WEBCAM_SEC, WEBCAM_DELAY, wc, cams, list, on, total, found, single, combine, feedFor, feedPill, healthFor, downPill, fetchAll, fetching, fetchState, fetchResult, srcHost, kindOf, hasSource, mins, go };
  },
  template: `<div class="stack">
    <div class="card"><div class="card-body stack" style="gap:10px">
      <InRotation :found="found" what="Webcams" :shapes="LIST_SHAPES"/>
      <div v-if="single.length" class="callout" style="font-size:12.5px"><Icon name="Combine" :size="14"/>
        <span>{{ single.length }} webcam{{ single.length === 1 ? ' is a separate rotation entry' : 's are separate rotation entries' }}. Combine them into this list,
          so the rotation has one Webcams entry and the order lives here.
          <button class="btn sm primary" style="margin-left:6px" @click="combine">Combine into the list</button></span></div>
      <div class="row" style="gap:18px; align-items:flex-end">
        <Field label="Each turn plays" hint="Then the rotation moves on">
          <div class="row nowrap" style="gap:8px"><Seg :modelValue="wc.per_turn === 'all' ? 'all' : 'some'"
              @update:modelValue="v => wc.per_turn = v === 'all' ? 'all' : (on.length > 1 ? Math.min(on.length, 50) : 1)"
              :options="[{ value: 'all', label: 'The whole list' }, { value: 'some', label: 'Some' }]"/>
            <Stepper v-if="wc.per_turn !== 'all'" :modelValue="wc.per_turn || 1" @update:modelValue="v => wc.per_turn = v" :min="1" :max="50"
              :unit="(wc.per_turn || 1) === 1 ? ' webcam' : ' webcams'"/></div></Field>
        <Field label="Each turn starts" hint="At the top, or where the last turn stopped">
          <Seg :modelValue="wc.from_top === false ? 'carry' : 'top'" @update:modelValue="v => wc.from_top = v !== 'carry'"
            :options="[{ value: 'top', label: 'At the top' }, { value: 'carry', label: 'Where it stopped' }]"/></Field>
        <Field label="Behind live" hint="Every webcam plays this far behind real time, so nearby cameras line up when the wall cuts between them">
          <Stepper :modelValue="wc.delay_sec || WEBCAM_DELAY" @update:modelValue="v => wc.delay_sec = v" :min="10" :max="120" :step="5" unit=" s"/></Field>
        <div class="faint" style="font-size:12.5px; padding-bottom:6px">{{ on.length }} of {{ list.length }} on · the whole list takes {{ mins(total) }}</div>
      </div>
    </div></div>
    <div class="card">
      <div class="card-head"><Icon name="ListOrdered" :size="16"/><h3 style="white-space:nowrap">The list</h3><span class="faint ellipsis" style="font-size:12px; min-width:0">drag to reorder · switch off to skip</span>
        <span class="spacer"></span>
        <button class="btn sm" :disabled="fetching" @click="fetchAll" title="Look up every webcam now, one at a time, so each starts without waiting">
          <Icon :name="fetching ? 'LoaderCircle' : 'RefreshCw'" :size="14" :class="{spin: fetching}"/>{{ fetching ? 'Fetching ' + fetchState.done + ' of ' + fetchState.total + '…' : 'Fetch all feeds' }}</button>
        <button class="btn sm" @click="$emit('add')"><Icon name="Plus" :size="14"/>Add by URL</button>
        <button class="btn sm" @click="$emit('find')"><Icon name="Search" :size="14"/>Find webcams</button></div>
      <div v-sortable="{ list: list, handle: '.handle' }">
        <div v-for="(it, i) in list" :key="it.cam" class="list-row wc-list-row" :class="{off: it.on === false}">
          <span class="handle"><Icon name="GripVertical" :size="16"/></span>
          <span class="mono faint" style="width:18px; text-align:right">{{ i + 1 }}</span>
          <span class="wc-row-thumb" style="cursor:pointer" @click="go('webcams/' + it.cam)"><SnapImg v-if="hasSource(cams[it.cam]?.source)" :source="cams[it.cam].source"/></span>
          <div class="grow" style="min-width:0; cursor:pointer" @click="go('webcams/' + it.cam)"><b>{{ cams[it.cam]?.label || it.cam }}</b>
            <div class="faint ellipsis" style="font-size:12.5px"><span class="wc-kind" :class="kindOf(cams[it.cam]?.source).toLowerCase()">{{ kindOf(cams[it.cam]?.source) }}</span>
              {{ [cams[it.cam]?.place, srcHost(cams[it.cam]?.source)].filter(Boolean).join(' · ') }}</div></div>
          <span v-if="fetchResult(it.cam)" class="pill" :class="fetchResult(it.cam).ok ? 'ok' : 'bad'" :title="fetchResult(it.cam).msg">{{ fetchResult(it.cam).ok ? 'fetched' : 'fetch failed' }}</span>
          <span v-if="downPill(healthFor(it.cam))" class="pill bad" :title="downPill(healthFor(it.cam)).title">{{ downPill(healthFor(it.cam)).text }}</span>
          <span v-else class="pill" :class="feedPill(feedFor(cams[it.cam])).cls">{{ feedPill(feedFor(cams[it.cam])).text }}</span>
          <Stepper :modelValue="it.sec || WEBCAM_SEC" @update:modelValue="v => it.sec = v" :min="5" :max="3600" :step="5" unit=" s" title="How long it shows"/>
          <Toggle :modelValue="it.on !== false" @update:modelValue="v => it.on = v" title="In the rotation"/>
          <button class="btn sm ghost icon-only" title="Crop, colour and caption" @click="go('webcams/' + it.cam)"><Icon name="Pencil" :size="14"/></button>
        </div>
      </div>
      <div class="empty" v-if="!list.length">No webcams yet. <a href="javascript:void 0" @click="$emit('find')">Find some</a> or add one by URL.</div>
    </div>
  </div>`,
};

// --- Camera grid: still cameras bulk-added from the intersection list, tiles filling the frame, one after another ------
const GridTab = {
  components: { InRotation },
  emits: ["find"],
  setup() {
    const g = store.draft.modules.webcams.grid;
    const found = computed(() => rotationEntries((e) => e.panel?.startsWith("webcam_grid")));
    const shape = ref(found.value[0] ? shapeOfPanel(found.value[0].e, found.value[0].side) : "full");
    const img = ref("");
    const busy = ref(false);
    let timer = null, url = "";
    const render = async () => {
      timer = null;
      if (!g.cams.length && !g.near?.on) { img.value = ""; return; }
      busy.value = true;
      try {
        const r = await fetch("/api/webcams/grid/preview.png", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ grid: clone(g), shape: shape.value, scale: 3 }) });
        if (!r.ok) throw new Error(await r.text());
        if (url) URL.revokeObjectURL(url);
        img.value = url = URL.createObjectURL(await r.blob());
      } catch (e) { fail(e); } finally { busy.value = false; }
    };
    const queue = () => { clearTimeout(timer); timer = setTimeout(render, 400); };
    // the tiles' labels (display/modules/cam_names.py): which parts go on which line, the direction's place, the wording
    const lab = computed(() => g.label);                    // seeded by seedWebcams
    // nearby cameras: every camera within the radius of a point joins the grid (the hub works them out each time)
    const near = computed(() => g.near);
    const matches = ref(null);
    let nearTimer = null;
    const findNear = async () => {
      const n = g.near;
      if (!n.on || !Number.isFinite(n.lat) || !Number.isFinite(n.lon)) { matches.value = null; return; }
      try {
        matches.value = await api(`/api/webcams/near?${qs({ lat: n.lat, lon: n.lon, radius: n.radius || 1, unit: n.unit || "km", kinds: n.kinds || "city" })}`);
      } catch (e) { fail(e); }
    };
    watch(() => JSON.stringify(g.near), () => { clearTimeout(nearTimer); nearTimer = setTimeout(findNear, 300); }, { immediate: true });
    const useHome = async () => {
      try { const h = await api("/api/webcams/home"); g.near.lat = +h.lat.toFixed(5); g.near.lon = +h.lon.toFixed(5); } catch (e) { fail(e); }
    };
    // "49.1666, -123.1336" pasted into either field fills both
    const setNearCoord = (key, ev) => {
      const raw = ev.target.value.trim();
      const both = raw.match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/);
      if (both) { g.near.lat = +both[1]; g.near.lon = +both[2]; return; }
      const v = Number(raw);
      if (raw === "") delete g.near[key];
      else if (Number.isFinite(v) && Math.abs(v) <= (key === "lat" ? 90 : 180)) g.near[key] = v;
      else { toast(`${key === "lat" ? "Latitude" : "Longitude"}: a number please`, "bad"); ev.target.value = g.near[key] ?? ""; }
    };
    const inList = (c) => g.cams.some((x) => sameSrc(x.source, c.source));
    const nearThumb = (c) => (c.source.type === "drivebc" ? `/api/webcams/drivebc/${c.source.id}.jpg` : c.thumb || snap(c.source));
    const PARTS = [{ value: "street1", label: "Street 1" }, { value: "street2", label: "Street 2" }, { value: "dir", label: "Direction" },
      { value: "view", label: "Camera no." }, { value: "name", label: "Whole name" }];
    const partsShown = computed(() => PARTS.filter((p) => p.value !== "dir" || lab.value.dir === "line"));
    const ORDER = PARTS.map((p) => p.value);
    const togglePart = (ln, p) => {
      const i = ln.indexOf(p);
      if (i >= 0) ln.splice(i, 1); else { ln.push(p); ln.sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b)); }
    };
    watch(() => [g.cams.map((c) => JSON.stringify(c.source) + c.name).join("|"), g.labels, g.rows, g.cols, g.group_views, JSON.stringify(g.label),
      JSON.stringify(g.near), shape.value],
      queue, { immediate: true });
    onBeforeUnmount(() => { clearTimeout(timer); if (url) URL.revokeObjectURL(url); });
    // the hub's own thumbnail for the camera (kept with it when it was added), else a snapshot
    const thumbOf = (c) => (c.source.type === "drivebc" ? `/api/webcams/drivebc/${c.source.id}.jpg`
      : /^\/api\/webcams\/city\/[\w-]+\.jpg$/.test(c.thumb || "") ? c.thumb : snap(c.source));
    // what the wall plays: the hub fetches at most GRID_MAX cameras (it tells us with the nearby matches)
    const playing = computed(() => [...g.cams, ...(g.near?.on && matches.value ? matches.value.cams.filter((c) => !inList(c)) : [])]
      .slice(0, matches.value?.max || 5000));
    const total = computed(() => playing.value.length);
    // squares: an intersection's views share one when grouped (display/modules/cam_names.py place_key, for the count)
    const SAME = { avenue: "ave", av: "ave", road: "rd", street: "st", boulevard: "blvd", highway: "hwy", drive: "dr", place: "pl",
      crescent: "cres", parkway: "pkwy", court: "ct", lane: "ln", gate: "gt", block: "blk", connector: "conn", commons: "cmns" };
    const same = (s) => s.toLowerCase().replace(/\./g, " ").replace(/\b[a-z]+\b/g, (w) => SAME[w] || w).replace(/\s+/g, " ").trim();
    const placeOf = (name) => {
      const n = String(name || "").replace(/\s+/g, " ").trim(), sep = n.includes(" - ") ? " - " : n.includes(" · ") ? " · " : "";
      const main = sep ? n.slice(0, n.lastIndexOf(sep)) : n;
      const m = main.match(/^(.+?)(?:\s+|(?<=\.))(?:and|&|at|\/)\s+(.+)$/i);
      return m && m[1].trim() && m[2].trim() ? [same(m[1]), same(m[2])].sort().join("|") : same(main);
    };
    const squares = computed(() => (g.group_views === false ? total.value : new Set(playing.value.map((c) => placeOf(c.name))).size));
    const secs = computed(() => (squares.value * (g.interval_ms || 200)) / 1000 + (g.hold_sec ?? 3));
    // unsaved changes go to the wall as a preview first (long enough for the whole reveal), so it plays this grid
    const play = async () => {
      const s = SHAPES[shape.value];
      if (dirty.value.length) await previewOnDisplay(Math.max(120, Math.ceil(secs.value) + 90), { hook: false });
      playOnDisplay({ panel: GRID_SHAPES[shape.value], advance: "cycles", cycles: 1, max_sec: Math.max(600, Math.ceil(secs.value) + 60) },
        { side: s.side, hold: false });
    };
    return { GRID_SHAPES, g, lab, near, matches, useHome, setNearCoord, inList, nearThumb, partsShown, togglePart, found, shape, img, busy, thumbOf, secs, total, squares, play, render, SHAPES, dirty };
  },
  template: `<div class="stack">
    <div class="card"><div class="card-body stack" style="gap:10px">
      <div class="faint" style="font-size:13px">Tiles filling the whole frame, each with its camera's name. Each camera lands in the next tile in turn (top row left to right, then the next row),
        replacing what was there, until every camera below has appeared; then the rotation moves on.</div>
      <InRotation :found="found" what="The camera grid" :shapes="GRID_SHAPES"/>
    </div></div>
    <div class="two-col">
      <div class="card"><div class="card-head"><Icon name="Grid2x2" :size="16"/><h3>Cameras</h3><span class="faint" style="font-size:12px">{{ g.cams.length }} · drag to reorder</span>
          <span class="spacer"></span><ConfirmButton v-if="g.cams.length" small label="Remove all" @confirm="g.cams.splice(0)"/>
          <button class="btn sm primary" @click="$emit('find')"><Icon name="Plus" :size="14"/>Add cameras</button></div>
        <div class="card-body">
          <div class="wc-grid-list" v-sortable="{ list: g.cams, handle: '.wc-grid-cam' }">
            <div v-for="(c, i) in g.cams" :key="JSON.stringify(c.source)" class="wc-grid-cam" :title="c.name">
              <img :src="thumbOf(c)" loading="lazy" alt=""><span class="n">{{ i + 1 }}</span>
              <button class="x" title="Remove" @click.stop="g.cams.splice(i, 1)"><Icon name="X" :size="12"/></button>
              <span class="name">{{ c.name }}</span></div>
          </div>
          <div class="empty" v-if="!g.cams.length">No cameras added by hand. Use <b>Add cameras</b>, then <b>Select several</b> in the
            intersection list, or turn on <b>Nearby cameras</b>.</div>
        </div>
        <div class="card-head" style="border-top:1px solid var(--line)"><Icon name="LocateFixed" :size="16"/><h3>Nearby cameras</h3>
          <span class="faint" style="font-size:12px">every camera near a point, kept up to date</span><span class="spacer"></span>
          <Toggle :modelValue="!!near.on" @update:modelValue="v => near.on = v" title="Add every camera near a point"/></div>
        <div class="card-body stack" v-if="near.on">
          <div class="row" style="gap:12px; align-items:flex-end">
            <Field label="Around" hint="Latitude, longitude (or paste both into one)">
              <div class="row nowrap" style="gap:6px"><input class="input" style="width:110px" :value="near.lat ?? ''" @change="setNearCoord('lat', $event)" placeholder="49.1666">
                <input class="input" style="width:120px" :value="near.lon ?? ''" @change="setNearCoord('lon', $event)" placeholder="-123.1336">
                <button class="btn sm" @click="useHome"><Icon name="House" :size="13"/>Home</button></div></Field>
            <Field label="Within"><div class="row nowrap" style="gap:6px">
              <Stepper :modelValue="near.radius ?? 1" @update:modelValue="v => near.radius = v" :min="0.25" :max="50" :step="0.25"/>
              <Seg :modelValue="near.unit || 'km'" @update:modelValue="v => near.unit = v" :options="[{value:'km', label:'km'}, {value:'mi', label:'miles'}]"/></div></Field>
            <Field label="Which"><Seg :modelValue="near.kinds || 'city'" @update:modelValue="v => near.kinds = v"
              :options="[{value:'city', label:'Intersections'}, {value:'all', label:'+ DriveBC highways'}]"/></Field>
          </div>
          <div class="faint" style="font-size:12.5px" v-if="!Number.isFinite(near.lat) || !Number.isFinite(near.lon)">Set a point (or use Home) to find the cameras around it.</div>
          <template v-else-if="matches">
            <div style="font-size:12.5px"><b>{{ matches.count }}</b> camera{{ matches.count === 1 ? '' : 's' }} within {{ near.radius ?? 1 }} {{ near.unit || 'km' }},
              nearest first<span class="faint" v-if="matches.capped"> (the grid takes the nearest {{ matches.max }})</span>.
              <span class="faint">They play after the ones added by hand; new cameras in the area join by themselves.</span></div>
            <div class="wc-grid-list">
              <div v-for="c in matches.cams.slice(0, 24)" :key="JSON.stringify(c.source)" class="wc-grid-cam" :title="c.name + ' · ' + c.credit"
                   :style="{opacity: inList(c) ? .45 : 1}"><img :src="nearThumb(c)" loading="lazy" alt="">
                <span class="n">{{ c.km < 1 ? Math.round(c.km * 1000) + ' m' : c.km.toFixed(1) + ' km' }}</span><span class="name">{{ c.name }}</span></div></div>
            <div class="faint" style="font-size:12px" v-if="matches.cams.length > 24">… and {{ matches.cams.length - 24 }} more</div>
          </template>
        </div></div>
      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">LIVE</span><h3>On the wall</h3><span class="spacer"></span>
            <select class="input" v-model="shape" style="width:auto; height:28px"><option v-for="(s, k) in SHAPES" :key="k" :value="k">{{ s.label }}</option></select></div>
          <div class="card-body stack">
            <div class="led"><img v-if="img" :src="img" alt="" :style="{opacity: busy ? .6 : 1}"><div v-else class="placeholder">{{ busy ? 'Fetching cameras…' : 'Add cameras to see the grid' }}</div></div>
            <div class="faint" style="font-size:12px">With every tile filled (the last cameras in the grid). The wall fills it one tile at a time.</div>
            <div class="row"><button class="btn sm" @click="play" :disabled="!g.cams.length && !near.on"><Icon name="Play" :size="14"/>Play once on the wall</button>
              <span class="faint" style="font-size:12px">{{ total }} cameras<template v-if="squares !== total"> at {{ squares }} intersections</template>
                · takes about {{ Math.round(secs) }} s{{ dirty.length ? ' · shows your unsaved changes' : '' }}.
                The wall fetches every camera first, then flicks through them.</span></div>
          </div></div>
        <div class="card"><div class="card-head"><Icon name="SlidersHorizontal" :size="16"/><h3>Timing and labels</h3></div>
          <div class="card-body fields">
            <Field label="A new camera every" hint="200 ms is five a second"><Stepper :modelValue="g.interval_ms ?? 200" @update:modelValue="v => g.interval_ms = v" :min="50" :max="5000" :step="50" unit=" ms"/></Field>
            <Field label="One square per intersection" hint="Its views (NB, SB, EB, WB…) take turns in the same square">
              <Toggle :modelValue="g.group_views !== false" @update:modelValue="v => g.group_views = v"/></Field>
            <Field v-if="g.group_views !== false" label="Next view every" hint="Faster when a square has more views than fit before it's replaced, so every view shows">
              <Stepper :modelValue="g.view_ms ?? 1000" @update:modelValue="v => g.view_ms = v" :min="200" :max="10000" :step="100" unit=" ms"/></Field>
            <Field label="Hold at the end" hint="Once the last camera is in"><Stepper :modelValue="g.hold_sec ?? 3" @update:modelValue="v => g.hold_sec = v" :min="0" :max="30" unit=" s"/></Field>
            <Field label="Rows"><Stepper :modelValue="g.rows ?? 2" @update:modelValue="v => g.rows = v" :min="1" :max="4"/></Field>
            <Field label="Columns" hint="Auto fits the frame: 6 across full width with 2 rows">
              <div class="row nowrap" style="gap:8px"><Seg :modelValue="g.cols == null || g.cols === 'auto' ? 'auto' : 'set'"
                @update:modelValue="v => g.cols = v === 'auto' ? 'auto' : 6" :options="[{value:'auto', label:'Auto'}, {value:'set', label:'Set'}]"/>
                <Stepper v-if="g.cols != null && g.cols !== 'auto'" :modelValue="g.cols" @update:modelValue="v => g.cols = v" :min="1" :max="10"/></div></Field>
          </div></div>
        <div class="card"><div class="card-head"><Icon name="Tag" :size="16"/><h3>Labels</h3><span class="spacer"></span>
            <Toggle :modelValue="g.labels !== false" @update:modelValue="v => g.labels = v" title="Labels on the tiles"/></div>
          <div class="card-body stack" v-if="g.labels !== false">
            <div class="faint" style="font-size:12px">Each camera's name is split into its two streets and its direction
              (e.g. <i>No. 4 Road at Westminster Highway - Eastbound</i> → <b>No. 4 Rd</b> / <b>Westminster Hwy</b> / <b>EB</b>).</div>
            <Field label="Lines" hint="What each line shows, top to bottom; click to add or remove a part">
              <div class="stack" style="gap:6px">
                <div v-for="(ln, i) in lab.lines" :key="i" class="row nowrap" style="gap:6px">
                  <span class="mono faint" style="width:14px">{{ i + 1 }}</span>
                  <div class="row" style="gap:4px"><button v-for="p in partsShown" :key="p.value" type="button" class="chip" :class="{off: !ln.includes(p.value)}"
                    style="height:24px; padding:0 9px; font-size:12px" @click="togglePart(ln, p.value)">{{ p.label }}</button></div>
                  <span class="spacer"></span>
                  <button class="btn sm ghost icon-only" title="Remove this line" @click="lab.lines.splice(i, 1)" :disabled="lab.lines.length < 2"><Icon name="X" :size="13"/></button></div>
                <button class="btn sm ghost" style="align-self:flex-start" @click="lab.lines.push([])" v-if="lab.lines.length < 3"><Icon name="Plus" :size="13"/>Add a line</button>
              </div></Field>
            <div class="row" style="gap:16px; align-items:flex-start">
              <Field label="Direction"><Seg :modelValue="lab.dir || 'tag'" @update:modelValue="v => lab.dir = v"
                :options="[{value:'tag', label:'Corner tag'}, {value:'line', label:'In a line'}, {value:'off', label:'Off'}]"/></Field>
              <Field label="Names at"><Seg :modelValue="lab.at || 'bottom'" @update:modelValue="v => lab.at = v"
                :options="[{value:'bottom', label:'Bottom'}, {value:'top', label:'Top'}]"/></Field>
            </div>
            <div class="faint" style="font-size:12px; margin-top:-6px" v-if="(lab.dir || 'tag') === 'tag'">The tag sits in the opposite corner ({{ (lab.at || 'bottom') === 'bottom' ? 'top right' : 'bottom right' }}).</div>
            <div class="row" style="gap:16px; align-items:flex-start">
              <Field label="Wording"><Seg :modelValue="lab.short === false ? 'full' : 'short'" @update:modelValue="v => { lab.short = v === 'short'; lab.dir_style = v; }"
                :options="[{value:'short', label:'Short (Rd, EB)'}, {value:'full', label:'Full (Road, Eastbound)'}]"/></Field>
              <Field label="Text size"><Seg :modelValue="lab.size || 'tiny'" @update:modelValue="v => lab.size = v"
                :options="[{value:'tiny', label:'Tiny'}, {value:'small', label:'Small'}]"/></Field>
            </div>
          </div></div>
      </div>
    </div>
  </div>`,
};

// --- the page: your webcams, the camera grid, and where to find more -------------------------------------------------
const TABS = [{ value: "list", label: "Your webcams", icon: "ListOrdered" }, { value: "grid", label: "Camera grid", icon: "Grid2x2" },
  { value: "live", label: "Live YouTube cams", icon: "Youtube" }, { value: "traffic", label: "Intersection & traffic cams", icon: "TrafficCone" }];
const WebcamList = {
  components: { PlaylistTab, GridTab, TrafficCams, LiveGallery },
  setup() {
    const wc = seedWebcams();
    const cams = computed(() => Object.entries(store.draft.modules.webcams.cams || {}));
    const tab = computed({ get: () => (store.route.params[0] === "~grid" ? "grid" : store.webcamTab || "list"),
      set: (v) => { store.webcamTab = v; if (store.route.params[0]) go("webcams"); } });
    const add = (from, open = true) => {
      const all = store.draft.modules.webcams.cams || (store.draft.modules.webcams.cams = {});
      const f = from || { label: "New webcam", source: { type: "youtube", url: "" } };
      const base = clone({ label: f.label, place: f.place || "", credit: f.credit || "", tz: f.tz || "", source: f.source,
        ...(f.lat != null ? { lat: f.lat, lon: f.lon } : {}) });
      let id = slug(base.label), n = 2;
      while (all[id]) id = `${slug(base.label)}_${n++}`;
      const overlay = newOverlay();
      if (STILL.includes(f.source?.type)) Object.assign(overlay.show, { age: true, live: false });   // a still isn't LIVE
      all[id] = { fps: NEW_WEBCAM.fps, ...base, overlay, crops: {} };
      store.draft.modules.webcams.playlist.push({ cam: id, sec: WEBCAM_SEC, on: true });
      if (!base.tz && Number.isFinite(base.lat) && Number.isFinite(base.lon))      // its local time: the location's zone
        localOf(base.lat, base.lon).then((r) => { if (r.tz && all[id] && !all[id].tz) all[id].tz = r.tz; }).catch(() => {});
      if (open) go(`webcams/${id}`);
      return id;
    };
    // the gallery: ready-made webcams; one you already have opens its page instead
    const haveId = (c) => cams.value.find(([, x]) => x.source?.url === c.source.url || (x.source?.type === "youtube" && youtubeId(x.source.url) === c.id))?.[0];
    const pick = (c) => {
      const have = haveId(c);
      if (have) return go(`webcams/${have}`);
      add(c, false);
      toast(`Added ${c.label} to your webcams. Save to keep it.`);
    };
    const toGrid = (list) => { store.draft.modules.webcams.grid.cams.push(...list); };
    const find = () => { tab.value = "traffic"; };
    const findForGrid = () => { store.trafficPick = true; tab.value = "traffic"; };
    const counts = computed(() => ({ list: store.draft.modules.webcams.playlist.length, grid: store.draft.modules.webcams.grid.cams.length }));
    return { wc, cams, tab, add, haveId, pick, toGrid, find, findForGrid, counts, TABS, store };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Webcams</h1>
      <p>Live video and traffic cameras on the wall. The rotation has one <b>Webcams</b> entry: it plays the list below, in this order.
        The <b>camera grid</b> flicks through many intersection cameras at once. The hub fetches and decodes everything; the Pi only shows finished frames.</p></div>
      <button class="btn primary" @click="add()"><Icon name="Plus" :size="15"/>Add by URL</button></div>
    <div class="wc-tabs"><button v-for="t in TABS" :key="t.value" type="button" :class="{on: tab === t.value}" @click="tab = t.value">
      <Icon :name="t.icon" :size="15"/>{{ t.label }}<span v-if="counts[t.value] != null" class="n">{{ counts[t.value] }}</span></button></div>
    <PlaylistTab v-if="tab === 'list'" @add="add()" @find="tab = 'live'"/>
    <GridTab v-else-if="tab === 'grid'" @find="findForGrid"/>
    <LiveGallery v-else-if="tab === 'live'" :have="haveId" @pick="pick"/>
    <TrafficCams v-else :cams="cams" :grid="wc.grid" @add="add($event, false)" @grid="toGrid"/>
    <div class="faint" style="font-size:12.5px; margin-top:10px" v-if="tab !== 'grid'">Add by URL takes any YouTube live page, an HLS / MJPEG / RTSP stream,
      a DriveBC camera number, or any still image that updates.</div>
  </div>`,
};

// --- crop editor: the whole source picture with the zone-shaped crop box on it ----------------------------------
const CropEditor = {
  props: { cam: Object, shape: String, zone: Array },
  setup(props) {
    const img = ref(null);
    const nat = ref([640, 360]);
    const err = ref(false);
    let t;
    onMounted(() => { t = setInterval(() => { const i = img.value; if (i?.naturalWidth) nat.value = [i.naturalWidth, i.naturalHeight]; }, 500); });
    onBeforeUnmount(() => { clearInterval(t); if (img.value) img.value.src = ""; });   // end the stream (frees the hub's viewer)
    const src = computed(() => hasSource(props.cam.source) ? `/api/webcams/source.mjpeg?${qs({ ...srcParams(props.cam.source), fps: props.cam.fps || 15 })}` : "");
    // the stream's own resolution and what the hub decodes (hub/app.py /api/webcams/info)
    const info = ref(null);
    let infoT;
    const loadInfo = async () => {
      if (!hasSource(props.cam.source)) return;
      const want = JSON.stringify(props.cam.source);
      try { const i = await api(`/api/webcams/info?${qs(srcParams(props.cam.source))}`); if (want === JSON.stringify(props.cam.source)) info.value = i; } catch {}
    };
    watch(src, () => { err.value = false; info.value = null; loadInfo(); });
    onMounted(() => { loadInfo(); infoT = setInterval(loadInfo, 4000); });
    onBeforeUnmount(() => clearInterval(infoT));
    const native = computed(() => info.value?.native || null);
    // the tightest crop: one stream pixel per LED (zooming further would only enlarge pixels)
    const minW = computed(() => native.value?.w ? Math.min(1, props.zone[0] / native.value.w) : 0.125);   // (800% until it's known)
    const maxZoom = computed(() => Math.max(100, Math.floor(100 / minW.value)));
    const aspect = computed(() => props.zone[0] / props.zone[1]);
    const fit = () => {
      const [sw, sh] = nat.value;
      const w = Math.min(1, (sh * aspect.value) / sw);
      const h = (w * sw) / aspect.value / sh;
      return { x: +((1 - w) / 2).toFixed(5), y: +Math.max(0, (1 - h) / 2).toFixed(5), w: +w.toFixed(5) };
    };
    // Tall with no crop of its own borrows the full-width one (same width and centre, taller), as the wall does
    // (display/modules/webcam.py crop_for)
    const borrowed = () => {
      const f = props.cam.crops?.full;
      if (props.shape !== "tall" || !f?.w) return null;
      const [sw, sh] = nat.value;
      const src = sw / sh || 16 / 9, fullAspect = 448 / 134;
      let cw = f.w, x = f.x || 0;
      const hFull = (cw * src) / fullAspect;
      let hTall = (cw * src) / aspect.value;
      if (hTall > 1) { cw = cw / hTall; x += (f.w - cw) / 2; hTall = 1; }
      const y = Math.min(Math.max(0, (f.y || 0) + hFull / 2 - hTall / 2), 1 - hTall);
      return { x: +Math.max(0, x).toFixed(5), y: +y.toFixed(5), w: +cw.toFixed(5) };
    };
    const crop = computed(() => props.cam.crops?.[props.shape] || borrowed() || fit());
    const box = computed(() => {
      const [sw, sh] = nat.value;
      const c = crop.value;
      const w = Math.min(c.w, (sh * aspect.value) / sw);
      const h = (w * sw) / aspect.value / sh;
      return { x: Math.min(Math.max(0, c.x), 1 - w), y: Math.min(Math.max(0, c.y), 1 - h), w, h };
    });
    // Whole pixels: the crop is exactly k × the viewport in the stream's own pixels (k = 1, 2, 3…), on whole pixels,
    // so the hub scales it down by plain averaging, k × k pixels into each LED (hub/webcams.py crop_box)
    const snap = computed(() => !!props.cam.crops?.[props.shape]?.snap);
    const kMax = computed(() => { const n = native.value, [zw, zh] = props.zone; return n?.w ? Math.max(1, Math.floor(Math.min(n.w / zw, n.h / zh))) : 0; });
    const kOf = (w) => Math.round((w * native.value.w) / props.zone[0]);
    const set = (c, wantSnap = snap.value) => {
      const [sw, sh] = nat.value;
      let w = Math.max(minW.value, Math.min(c.w, 1, (sh * aspect.value) / sw));
      const n = native.value, on = wantSnap && kMax.value > 0;
      if (on) w = (Math.min(kMax.value, Math.max(1, kOf(w))) * props.zone[0]) / n.w;
      const h = (w * sw) / aspect.value / sh;
      let x = Math.min(Math.max(0, c.x), 1 - w), y = Math.min(Math.max(0, c.y), 1 - h);
      if (on) { x = Math.round(x * n.w) / n.w; y = Math.round(y * n.h) / n.h; }
      const out = { x: +x.toFixed(6), y: +y.toFixed(6), w: +w.toFixed(6) };
      if (on) out.snap = true;
      (props.cam.crops || (props.cam.crops = {}))[props.shape] = out;
    };
    const toggleSnap = (on) => set({ ...box.value }, on);
    let drag = null;
    const down = (ev) => {
      const r = ev.currentTarget.parentElement.getBoundingClientRect();
      drag = { r, sx: ev.clientX, sy: ev.clientY, b: { ...box.value } };
      ev.currentTarget.setPointerCapture(ev.pointerId);
    };
    const move = (ev) => {
      if (!drag) return;
      set({ x: drag.b.x + (ev.clientX - drag.sx) / drag.r.width, y: drag.b.y + (ev.clientY - drag.sy) / drag.r.height, w: drag.b.w });
    };
    const up = () => { drag = null; };
    const zoomBy = (k) => {
      const b = box.value;
      const w = Math.max(minW.value, Math.min(1, b.w * k));
      const [sw, sh] = nat.value;
      const h = (w * sw) / aspect.value / sh;
      set({ x: b.x + (b.w - w) / 2, y: b.y + (b.h - h) / 2, w });
    };
    const wheel = (ev) => {                  // Ctrl + wheel zooms; a plain wheel still scrolls the page
      if (!ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault();
      if (snap.value && kMax.value) {        // Whole pixels: one step of k each notch
        const k = Math.min(kMax.value, Math.max(1, kOf(box.value.w) + (ev.deltaY > 0 ? 1 : -1)));
        return zoomBy((k * props.zone[0]) / native.value.w / box.value.w);
      }
      zoomBy(ev.deltaY > 0 ? 1.08 : 1 / 1.08);
    };
    const zoom = computed({ get: () => Math.round(100 / box.value.w), set: (v) => zoomBy(100 / v / box.value.w) });
    const reset = () => {
      if (snap.value && kMax.value) {        // Whole pixels: the largest whole-pixel crop, centred
        const [sw, sh] = nat.value, w = (kMax.value * props.zone[0]) / native.value.w, h = (w * sw) / aspect.value / sh;
        return set({ x: (1 - w) / 2, y: Math.max(0, (1 - h) / 2), w });
      }
      if (props.cam.crops) delete props.cam.crops[props.shape];
    };
    const oneToOne = () => zoomBy(minW.value / box.value.w);
    const res = (w, h) => (h >= 2000 ? "4K " : h >= 1400 ? "1440p " : h >= 1000 ? "1080p " : h >= 700 ? "720p " : h ? h + "p " : "") + `${w}×${h}`;
    const codec = (c) => ({ avc1: "H.264", h264: "H.264", vp09: "VP9", vp9: "VP9", av01: "AV1", hevc: "HEVC", jpeg: "still" })[c] || c || "";
    // the numbers under the picture: what the stream has, what this crop takes of it, and what the wall gets
    const stats = computed(() => {
      const n = native.value, f = info.value?.feed, b = box.value, [zw, zh] = props.zone;
      if (!n) return null;
      const loW = f?.size?.[0] || Math.min(info.value.lo_w || 640, n.w);   // the lo picture (narrower sources: theirs)
      const cropW = Math.round(b.w * n.w), cropH = Math.round(b.h * n.h);
      const others = [...new Set((info.value.formats || []).map(([, h]) => h).filter((h) => h !== n.h))].sort((a, c) => c - a).map((h) => h + "p").join(", ");
      const out = { native: `${res(n.w, n.h)}${n.fps ? " · " + Math.round(n.fps) + " fps" : ""}${n.vcodec ? " · " + codec(n.vcodec) : ""}`,
        crop: `${cropW}×${cropH} px`, zone: `${zw}×${zh} LEDs`, scale: cropW / zw, others,
        k: snap.value ? kOf(b.w) : 0 };
      if (f?.source) {
        const s = f.source, hi = f.hi;
        // what the wall's frames are cut from: the detail picture when it covers this crop, else the 640-wide one
        const inHi = hi && b.x * s.w >= hi.x - 1 && b.y * s.h >= hi.y - 1 && (b.x + b.w) * s.w <= hi.x + hi.w + 1 && (b.y + b.h) * s.h <= hi.y + hi.h + 1;
        out.decoding = `${res(s.w, s.h)} ${codec(s.vcodec)}${s.dash ? " (DASH segments)" : ""} · ${f.hw ? "hardware (video engine)" : "software"}` +
          (hi ? ` · detail picture ${hi.out_w}×${hi.out_h}${hi.w < s.w || hi.h < s.h ? " (the crops' region)" : ""}` : ` · ${loW} px wide`);
        if (f.in_step && f.delay) out.timing = { delay: Math.round(f.delay), lag: f.latency != null ? Math.max(0, Math.round(f.latency)) : null,
          own: Math.round(f.delay) > (info.value.delay_sec || WEBCAM_DELAY) + 0.5 };
        else if (f.state === "live" && s.w) out.timing = { off: true };
        out.used = (b.w * (inHi ? hi.full_w : loW)) / zw;
        out.pending = out.used < Math.min(out.scale, info.value.oversample || 1.5) * 0.85;
        if (out.k) {                                   // Whole pixels: exact only when cut from the stream's own pixels
          out.kNow = Math.round((b.w * s.w) / zw);     // its whole multiple on the stream decoded now
          out.pending = !(inHi && hi.full_w >= s.w - 1) || Math.abs(out.used - out.kNow) > 0.05;
        }
      }
      return out;
    });
    const fmt = (v) => (v >= 10 ? v.toFixed(0) : v.toFixed(1));
    // the stream's resolution: Auto (the smallest with the detail the crops need) or one chosen for this webcam
    const quality = computed(() => String(props.cam.quality ?? "auto"));
    const setQuality = (v) => {
      if (v === "auto") delete props.cam.quality;
      else props.cam.quality = v === "max" ? "max" : +v;
    };
    const heights = computed(() => [...new Set([...(info.value?.formats || []).map(([, h]) => h), native.value?.h].filter((h) => h >= 720))].sort((a, b) => b - a));
    const resName = (h) => (h >= 2160 ? "4K (" + h + "p)" : h >= 1440 ? "1440p (2K)" : h + "p");
    return { img, src, err, box, down, move, up, wheel, zoom, reset, nat, maxZoom, oneToOne, stats, fmt, snap, toggleSnap, kMax, quality, setQuality, heights, resName, native };
  },
  template: `<div class="stack">
    <div class="wc-src" @wheel="wheel">
      <img v-if="src && !err" ref="img" :src="src" alt="" @error="err = true">
      <div v-else class="placeholder">{{ src ? 'The hub could not open this source (see Test above).' : 'Enter a source URL above.' }}</div>
      <div v-if="src && !err" class="wc-crop" :style="{left: box.x * 100 + '%', top: box.y * 100 + '%', width: box.w * 100 + '%', height: box.h * 100 + '%'}"
        @pointerdown.prevent="down" @pointermove="move" @pointerup="up" @pointercancel="up"><span>{{ zone[0] }}×{{ zone[1] }}</span></div>
    </div>
    <div class="row">
      <span class="faint">Zoom</span><div style="flex:1; min-width:160px"><Slider v-model="zoom" :min="100" :max="maxZoom" :step="5" unit="%"/></div>
      <button class="btn sm" @click="reset" title="The largest centred crop"><Icon name="Scan" :size="14"/>Fit</button>
      <button class="btn sm" @click="oneToOne" :disabled="!stats" title="Zoom in until one pixel of the stream is one LED (the most detail there is)"><Icon name="ZoomIn" :size="14"/>1:1</button>
      <slot/>
    </div>
    <div class="row" :title="kMax ? '' : 'Needs the resolution of the stream (shown once it has been fetched)'">
      <Toggle :modelValue="snap" @update:modelValue="toggleSnap" :disabled="!kMax"/>
      <span>Whole pixels</span>
      <span class="faint" style="font-size:12px">zoom only in exact steps (1:1, 2:1, 3:1…): each LED is the plain average of a k × k block of the stream's own pixels, with no in-between blur</span>
    </div>
    <div v-if="cam.source?.type === 'youtube'" class="row">
      <span class="faint">Stream</span>
      <select class="input" style="width:auto" :value="quality" @change="setQuality($event.target.value)">
        <option value="auto">Auto: what the crops need</option>
        <option value="max">The highest it has{{ native ? ': ' + resName(native.h) : '' }}</option>
        <option v-for="h in heights" :key="h" :value="String(h)">{{ resName(h) }}</option>
      </select>
      <span class="faint" style="font-size:12px">{{ quality === 'auto' ? 'the hub picks the smallest stream that gives every crop its full detail' : 'every place on the wall is cut from this stream, whatever the crop (more work for the hub); applies once saved' }}</span>
    </div>
    <div v-if="stats" class="wc-stats">
      <div><span>Livestream</span><b>{{ stats.native }}</b><i v-if="stats.others">also {{ stats.others }}</i></div>
      <div v-if="stats.decoding"><span>Hub decodes</span><b>{{ stats.decoding }}</b></div>
      <div v-if="stats.timing && !stats.timing.off"><span>Behind live</span><b>{{ stats.timing.delay }} s</b>
        <i>{{ stats.timing.own ? "later than the others: the stream itself is " + stats.timing.lag + " s behind" : "in step with every webcam" + (stats.timing.lag != null ? " (the stream itself is " + stats.timing.lag + " s behind)" : "") }}</i></div>
      <div v-else-if="stats.timing"><span>Behind live</span><b>not in step</b><i>this stream doesn't date its video: it plays as it arrives</i></div>
      <div><span>Cropped area</span><b>{{ stats.crop }}</b><i>of the stream</i></div>
      <div><span>Viewport</span><b>{{ stats.zone }}</b></div>
      <div v-if="stats.k"><span>Scaling</span><b>exactly {{ stats.k }}:1</b>
        <i>{{ stats.k === 1 ? "every LED is one stream pixel" : "each LED averages " + stats.k + " × " + stats.k + " stream pixels" }} · whole pixels, steps 1:1 to {{ kMax }}:1</i></div>
      <div v-else><span>Scaling</span><b>{{ fmt(stats.scale) }} stream px per LED</b>
        <i>{{ stats.scale <= 1.02 ? "1:1, the most detail there is" : "scaled down " + fmt(stats.scale) + "×" }} · zoom up to {{ maxZoom }}% (1:1)</i></div>
      <div v-if="stats.used != null"><span>Wall gets</span><b :class="{warn: stats.pending}">{{ fmt(stats.used) }} px per LED</b>
        <i>{{ stats.k ? (stats.pending ? "not exact yet: comes once the crop is held still for a moment (or saved)" : "exactly " + stats.kNow + " x " + stats.kNow + " stream pixels averaged per LED") : stats.pending ? "more detail comes once the crop is held still for a moment (or saved)" : "full detail for this crop" }}</i></div>
    </div>
    <div v-else-if="src && !err" class="hint faint" style="font-size:12px">Finding the stream's resolution…</div>
    <div class="hint faint" style="font-size:12px">Drag the box to choose what shows; Ctrl + scroll on the picture zooms; 1:1 zooms until one stream pixel is one LED. Each place on the wall keeps its own crop.</div>
  </div>`,
};

// --- the wall preview: the hub renders exactly what the display would draw, caption included ------------------------
const WallPreview = {
  props: { cam: Object, shape: String, camId: String },
  setup(props) {
    const token = ref("");
    const zone = ref([448, 130]);
    const dropped = ref([]);
    const err = ref("");
    let pending = null, timer = null, lastSrc = "";
    const send = async () => {
      timer = null;
      const srcKey = JSON.stringify([props.cam.source, props.cam.fps]);
      if (!hasSource(props.cam.source)) { token.value = ""; return; }
      const fresh = srcKey !== lastSrc;
      lastSrc = srcKey;
      try {
        const r = await api("/api/webcams/preview", { json: { token: fresh ? undefined : token.value || undefined, cam: clone(props.cam), shape: props.shape, id: props.camId } });
        zone.value = r.zone;
        dropped.value = r.dropped || [];
        if (r.token !== token.value) token.value = r.token;
        err.value = "";
      } catch (e) { err.value = e.message; }
    };
    const queue = () => { if (!timer) timer = setTimeout(send, 150); };
    watch(() => [props.cam, props.shape], queue, { deep: true, immediate: true });
    const img = ref(null);
    onBeforeUnmount(() => { clearTimeout(timer); if (img.value) img.value.src = ""; });
    const src = computed(() => token.value ? `/api/webcams/preview/${token.value}.mjpeg` : "");
    return { src, zone, err, img, dropped, SECTIONS };
  },
  template: `<div class="led" :style="{maxWidth: Math.min(zone[0] * 3 + 22, 1366) + 'px'}">
    <img v-if="src" ref="img" :src="src" :style="{aspectRatio: zone[0] + ' / ' + zone[1]}" alt="">
    <div v-else class="placeholder">{{ err || 'No source yet' }}</div></div>
    <div v-if="dropped.length" class="callout" style="font-size:12.5px; margin-top:8px"><Icon name="EyeOff" :size="14"/>
      <span>No room here for: <b>{{ dropped.map(k => SECTIONS[k] || k).join(', ') }}</b>. Try “Fit” or “One per line”, a smaller text size,
        a wider place on the wall, or the Custom layout.</span></div>`,
};

// --- editor -------------------------------------------------------------------------------------------------------
const WebcamEditor = {
  components: { CropEditor, WallPreview, CaptionRows },
  setup() {
    seedWebcams();
    const id = computed(() => store.route.params[0]);
    const cam = computed(() => store.draft.modules.webcams.cams?.[id.value]);
    const shape = ref("tall");                             // the crop shown first (and added to the rotation): above the clock
    const { status, feedFor, healthFor } = useStatus();
    const zone = computed(() => status.value.zones[shape.value] || [448, 130]);
    // the Japanese fonts a caption can use (webcam_caption.JP_FACES), and a sample of the chosen one in its own words
    const jpFaces = computed(() => status.value.jp_faces || []);
    // the separate Japanese caption: its font's sizes, a sample of its own text, where it goes
    const jc = computed(() => ov.value?.jp_caption || {});
    const jcFace = computed(() => jpFaces.value.find((x) => x.id === jc.value.face) || null);
    const jcSizes = computed(() => (jcFace.value?.sizes || []).map(([h]) => ({ value: h, label: `${h} px` })));
    const jcSetFace = (id) => {                         // keep the size when the new font has it, else its nearest
      const f = jpFaces.value.find((x) => x.id === id);
      jc.value.face = id;
      if (f && !f.sizes.some(([h]) => h === jc.value.size))
        jc.value.size = f.sizes.reduce((a, [h]) => (Math.abs(h - jc.value.size) < Math.abs(a - jc.value.size) ? h : a), f.sizes[0][0]);
    };
    const jcSample = computed(() => {
      const f = jcFace.value;
      if (!f) return "";
      const path = (f.sizes.find(([h]) => h === jc.value.size) || f.sizes[0])[1];
      const text = (jc.value.text || "").split("\n").map((s) => s.trim()).filter(Boolean).join("  ") || "歌舞伎町一番街";
      return `/api/fonts/preview.png?${qs({ path, text: " ", jp: text.slice(0, 120), scale: 2, width: 300 })}`;
    });
    const JC_POSITIONS = [{ value: "auto", label: "Next to the caption" }, { value: "tl", label: "Top left" }, { value: "tc", label: "Top" },
      { value: "tr", label: "Top right" }, { value: "bl", label: "Bottom left" }, { value: "bc", label: "Bottom" }, { value: "br", label: "Bottom right" }];
    const jpSample = computed(() => {
      const f = jpFaces.value.find((x) => x.id === ov.value?.jp_face);
      if (!f) return "";
      const own = [cam.value?.label, cam.value?.place].join(" ").replace(/[\u0000-\u2e7f]+/g, " ").trim();
      const path = f.sizes[f.sizes.length - 1][1];
      return `/api/fonts/preview.png?${qs({ path, text: " ", jp: own || "渋谷 歌舞伎町 東京 交差点", scale: 2, width: 300 })}`;
    });
    // what the caption will say there: the location's time zone (filled in when the webcam has none), time and weather
    const local = ref(null);
    let localTimer = null;
    watch(() => [id.value, cam.value?.lat, cam.value?.lon, cam.value?.wx_city?.lat, cam.value?.wx_city?.lon], () => {
      clearTimeout(localTimer);
      local.value = null;
      const c = cam.value;
      const pt = wxPoint(c);
      if (!pt) return;
      localTimer = setTimeout(async () => {
        try {
          const r = await localOf(pt.lat, pt.lon);
          if (cam.value !== c) return;
          local.value = r;
          if (!c.tz && r.tz) {
            c.tz = r.tz;
            // only opened, not edited (same location as saved): fill the saved copy too, so it isn't an unsaved change
            const sc = store.saved?.modules?.webcams?.cams?.[id.value];
            if (sc && !sc.tz && sc.lat === c.lat && sc.lon === c.lon && !c.wx_city) sc.tz = r.tz;
          }
        } catch {}
      }, 600);
    }, { immediate: true });
    onBeforeUnmount(() => clearTimeout(localTimer));
    // fill in fields older or hand-written webcams lack, in saved and draft alike (opening one isn't a change);
    // runs on every webcam opened: the editor stays mounted when going from one webcam to another
    const fill = (cid) => {
      for (const root of [store.saved, store.draft]) {
        const c = root?.modules?.webcams?.cams?.[cid];
        if (!c) continue;
        c.source = c.source || { type: "youtube", url: "" };
        c.overlay = { ...clone(DEFAULT_OVERLAY), ...(c.overlay || {}) };
        c.overlay.show = { ...DEFAULT_OVERLAY.show, ...(c.overlay.show || {}) };
        const ord = (c.overlay.order || []).filter((k) => ORDERABLE.includes(k));
        if (!ord.includes("live")) ord.unshift("live");      // older orders don't list LIVE: it leads (as on the wall)
        if (!ord.includes("shield")) ord.unshift("shield");  // nor the shield
        c.overlay.shield = { ...DEFAULT_OVERLAY.shield, ...(c.overlay.shield || {}) };
        c.overlay.jp_caption = { ...DEFAULT_OVERLAY.jp_caption, ...(c.overlay.jp_caption || {}) };
        c.overlay.order = [...ord, ...ORDERABLE.filter((k) => !ord.includes(k))];
        c.colour = c.colour || {};
        c.crops = c.crops || {};
      }
    };
    watch(id, fill, { immediate: true, flush: "sync" });
    const ov = computed(() => cam.value.overlay);
    const colour = computed(() => cam.value.colour);
    const probe = ref(null);
    const testing = ref(false);
    const test = async () => {
      testing.value = true;
      try {
        probe.value = await api("/api/webcams/probe", { json: { source: cam.value.source } });
        if (probe.value.ok && probe.value.title && (!cam.value.label || cam.value.label === "New webcam")) cam.value.label = probe.value.title.slice(0, 40);
      } catch (e) { probe.value = { ok: false, error: e.message }; } finally { testing.value = false; }
    };
    watch(() => cam.value?.source?.url, () => { probe.value = null; });
    const used = computed(() => usedIn(id.value));
    // its place in the Webcams list (the rotation's Webcams entry plays the list)
    const inList = computed(() => (store.draft.modules.webcams.playlist || []).find((x) => x.cam === id.value) || null);
    const listPos = computed(() => (store.draft.modules.webcams.playlist || []).findIndex((x) => x.cam === id.value) + 1);
    const addToList = () => {
      const wc = store.draft.modules.webcams;
      if (!Array.isArray(wc.playlist)) wc.playlist = Object.keys(wc.cams).filter((k) => k !== id.value).map((cam) => ({ cam, sec: WEBCAM_SEC, on: true }));
      wc.playlist.push({ cam: id.value, sec: WEBCAM_SEC, on: true });
    };
    const capTab = ref("theme");
    const copyCrop = () => {
      const c = cam.value.crops?.[shape.value];
      if (!c) return;
      const z = status.value.zones, a = z[shape.value][0] / z[shape.value][1];
      for (const k of Object.keys(SHAPES)) {
        if (k === shape.value) continue;
        const w = Math.min(1, c.w * Math.sqrt(a / (z[k][0] / z[k][1])));        // about the same area of the picture
        cam.value.crops[k] = { x: +Math.max(0, c.x + (c.w - w) / 2).toFixed(5), y: c.y, w: +w.toFixed(5) };
      }
      toast("Every size now centres on this crop (adjust each if needed)");
    };
    const play = async (hold) => {
      if (dirty.value.length) await previewOnDisplay(120, { hook: false });   // so the wall shows this crop and caption
      const s = SHAPES[shape.value];
      playOnDisplay(camEntry(id.value, shape.value), { side: s.side, hold });
    };
    const remove = () => {
      const lc = store.draft.modules.layout_cycle;
      for (const side of ["left", "right"]) lc[side] = (lc[side] || []).filter((e) => !(e.cam === id.value && e.panel?.startsWith("webcam")));
      delete store.draft.modules.webcams.cams[id.value];
      const pl = store.draft.modules.webcams.playlist;
      if (Array.isArray(pl)) store.draft.modules.webcams.playlist = pl.filter((x) => x.cam !== id.value);
      go("webcams");
    };
    const captionToAll = () => {
      for (const c of Object.values(store.draft.modules.webcams.cams)) if (c !== cam.value) c.overlay = clone(ov.value);
      toast("Every webcam now uses this caption (save to keep it)");
    };
    const toggleShow = (k) => { ov.value.show[k] = !ov.value.show[k]; };
    // the detail sections that are on, in their order (drag to reorder; the headline isn't a detail)
    const orderOn = computed(() => (ov.value.order || []).filter((k) => k !== "live" && k !== "shield" && ov.value.show[k] && k !== (ov.value.headline || "name")));
    // sections that will actually have text on the wall (the renderer skips empty ones)
    const signMissing = ref(false);                       // the shield preview didn't load (no real sign for it)
    // real-sign presets load one after another (each may be a first fetch from Commons on the hub)
    const presetsLoaded = ref(1);
    watch(() => ov.value?.shield?.style, () => { presetsLoaded.value = 1; });
    const presetShown = (r) => ov.value.shield.style !== "real" || SHIELD_PRESET_ROUTES.indexOf(r) < presetsLoaded.value;
    const presetDone = () => { presetsLoaded.value++; };
    // On Earth or in space (the ISS): in space the caption's sections come from the ISS's orbit, crew and mission
    const setSpace = (v) => {
      if (v === "space") {
        cam.value.space = "iss";
        const sh = ov.value.show;
        for (const k of ["over", "orbit", "below", "crew"]) if (sh[k] === undefined) sh[k] = true;
        for (const k of ["coords", "mission"]) if (sh[k] === undefined) sh[k] = false;
      } else delete cam.value.space;
    };
    const hasText = (k) => (SPACE_KEYS.includes(k) ? true : WX_KEYS.includes(k) ? !!wxPoint(cam.value) : { place: !!cam.value.place, credit: !!cam.value.credit,
      custom: !!ov.value.custom_text?.trim(), shield: /\d/.test(String(ov.value.shield?.route ?? "")) }[k] ?? true);
    // is a section in the caption: in one of its rows (before rows: turned on)
    const inRows = (k) => (Array.isArray(ov.value.rows) ? ov.value.rows.some((r) => (r.items || []).some((i) => i.k === k)) : !!ov.value.show[k]);
    const needsLocation = computed(() => !cam.value.space && WX_KEYS.some((k) => inRows(k)) && !wxPoint(cam.value));
    // the weather city: search by name, pick one of the places with that name (region and country shown)
    const cityQ = ref("");
    const cityHits = ref(null);
    const citySearching = ref(false);
    const searchCity = async () => {
      const q = cityQ.value.trim();
      if (q.length < 2) return;
      citySearching.value = true;
      try { cityHits.value = (await api(`/api/webcams/geocode?${qs({ name: q })}`)).results; } catch (e) { fail(e); cityHits.value = []; }
      citySearching.value = false;
    };
    const pickCity = (g) => {
      cam.value.wx_city = { name: g.name, admin1: g.admin1, country: g.country, lat: g.lat, lon: g.lon, tz: g.tz };
      if (!cam.value.tz && g.tz) cam.value.tz = g.tz;
      cityHits.value = null;
      cityQ.value = "";
    };
    const clearCity = () => { delete cam.value.wx_city; };
    const cityLabel = (g) => [g.name, g.admin1, g.country].filter((x, i, a) => x && a.indexOf(x) === i).join(", ");
    const boxMode = computed({ get: () => (ov.value.box_w === "full" ? "full" : Number(ov.value.box_w) > 0 ? "min" : "auto"),
      set: (v) => { ov.value.box_w = v === "full" ? "full" : v === "min" ? Math.max(60, Number(ov.value.box_w) || 160) : "auto"; } });
    const galleryLoc = computed(() => GALLERY.find((g) => (g.source.url === cam.value.source?.url || g.id === youtubeId(cam.value.source?.url || "")) && g.lat != null) || null);
    const useGalleryLoc = () => { cam.value.lat = galleryLoc.value.lat; cam.value.lon = galleryLoc.value.lon; };
    // "Preview on display" (the save bar) puts this webcam on the wall for the preview, then releases it
    let held = null;                                     // what the preview hook put on the wall
    onMounted(() => {
      store.previewHook = (sec) => {
        const s = SHAPES[shape.value];
        const entry = camEntry(id.value, shape.value);
        held = { side: s.side, panel: entry.panel, cam: id.value };
        playOnDisplay(entry, { side: s.side, hold: true, ttl: sec });
      };
      store.previewEndHook = () => {                     // release it only if it's still what that side is playing
        const h = held;
        held = null;
        const p = store.display?.status?.panels?.[h?.side]?.playing;
        if (h && p?.source === "preview" && p.entry?.panel === h.panel && p.entry?.cam === h.cam) rotationAction("stop", h.side);
      };
    });
    onBeforeUnmount(() => { store.previewHook = null; store.previewEndHook = null; });
    // a latitude/longitude field: empty clears it; anything that isn't a number in range is refused (not saved as null)
    const setCoord = (key, ev, lim) => {
      const raw = ev.target.value.trim().replace(",", ".");
      if (raw === "") { delete cam.value[key]; return; }
      const v = Number(raw);
      if (!Number.isFinite(v) || Math.abs(v) > lim) { toast(`${key === "lat" ? "Latitude" : "Longitude"} must be a number between -${lim} and ${lim}`, "bad"); ev.target.value = cam.value[key] ?? ""; return; }
      cam.value[key] = v;
    };
    const moveSection = (k, d) => {
      const o = ov.value.order;
      const on = orderOn.value;
      const i = on.indexOf(k), j = i + d;
      if (j < 0 || j >= on.length) return;
      const a = o.indexOf(k), b = o.indexOf(on[j]);
      [o[a], o[b]] = [o[b], o[a]];
    };
    const customCrop = (s) => !!cam.value.crops?.[s];
    // hours: the list passes the webcam over outside them (its own local time; display/modules/webcam.py in_hours)
    const toggleHours = (on) => {
      if (on) cam.value.hours = { from: "18:00", to: "07:00" };
      else delete cam.value.hours;
    };
    const hoursNote = computed(() => {
      const tz = cam.value.tz || cam.value.wx_city?.tz;
      let here = "";
      try {
        here = new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23", ...(tz ? { timeZone: tz } : {}) }).format(new Date());
      } catch {}
      const where = tz ? "in " + tz + ", not your time" : "no time zone for this webcam yet (set its location), so your time for now";
      if (!cam.value.hours) return "every hour of the day";
      const [a, b] = [cam.value.hours.from, cam.value.hours.to];
      const on = !here || a === b ? null : a < b ? here >= a && here < b : here >= a || here < b;
      return where + (here ? " · it's " + here + " there now: " + (on ? "in the list" : "skipped") : "");
    });
    return { inRows, setSpace, SPACE_GROUPS, toggleHours, hoursNote, id, cam, shape, WEBCAM_SEC, zone, ov, colour, DEFAULT_SHARPEN, probe, testing, test, used, inList, listPos, addToList, capTab, copyCrop, boxMode, SECTION_GROUPS, TEXT_ALIGNS, play, remove, captionToAll, toggleShow, customCrop,
      orderOn, moveSection, setCoord, hasSource, STILL, needsLocation, galleryLoc, useGalleryLoc, hasText, customCols, SHIELD_PRESETS, SHIELD_KINDS, SHIELD_PLACES, SHIELD_SIZES, SHIELD_STYLES, shieldSrc, signMissing, presetShown, presetDone,
      CONTAINERS, feedFor, feedPill, healthFor, downPill, local, cityQ, cityHits, citySearching, searchCity, pickCity, clearCity, cityLabel, wxPoint, jpFaces, jpSample, jc, jcSizes, jcSetFace, jcSample, JC_POSITIONS, SHAPES, LAYOUTS, FONTS, SECTIONS, HEADLINES, LINES, SEPS, POSITIONS, TIMEZONES, go };
  },
  template: `<div class="page" v-if="cam">
    <div class="page-head"><div class="grow">
      <a href="#/webcams" class="faint" style="font-size:12.5px"><Icon name="ArrowLeft" :size="13"/> Webcams</a>
      <h1>{{ cam.label || id }}</h1></div>
      <span v-if="downPill(healthFor(id))" class="pill bad" :title="downPill(healthFor(id)).title">{{ downPill(healthFor(id)).text }}</span>
      <span class="pill" :class="feedPill(feedFor(cam)).cls">{{ feedPill(feedFor(cam)).text }}</span>
      <ConfirmButton small label="Delete webcam" @confirm="remove"/></div>

    <div class="two-col">
      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="Link" :size="16"/><h3>Source</h3></div>
          <div class="card-body stack">
            <Seg v-model="cam.source.type" :options="[{value:'youtube', label:'YouTube live', icon:'Youtube'}, {value:'stream', label:'Stream URL', icon:'Radio'},
              {value:'drivebc', label:'DriveBC camera', icon:'Cctv'}, {value:'image', label:'Still image', icon:'Image'}]"/>
            <div class="row nowrap">
              <input v-if="cam.source.type === 'drivebc'" class="input grow" type="number" min="1" :value="cam.source.id ?? ''"
                     @change="cam.source.id = Number($event.target.value) || undefined" placeholder="DriveBC camera number, e.g. 18 (Lions Gate)">
              <input v-else class="input grow" v-model.lazy.trim="cam.source.url" :placeholder="cam.source.type === 'youtube' ? 'https://www.youtube.com/watch?v=…  or  …/@channel/live'
                : cam.source.type === 'image' ? 'https://…/camera.jpg (any still image that updates)' : 'https://…/index.m3u8, an MJPEG URL, rtsp://…'">
              <button class="btn" :disabled="testing || !hasSource(cam.source)" @click="test"><Icon :name="testing ? 'LoaderCircle' : 'PlugZap'" :size="14" :class="{spin: testing}"/>Test</button></div>
            <div class="faint" style="font-size:12px" v-if="cam.source.type === 'drivebc'">Find cameras under Webcams → Traffic cams. DriveBC posts a new picture every few minutes.</div>
            <div v-if="probe" class="callout" :class="probe.ok ? 'ok' : 'bad'" style="font-size:12.5px">
              <Icon :name="probe.ok ? 'CircleCheck' : 'CircleX'" :size="14"/> {{ probe.ok ? (probe.title ? 'Found: ' + probe.title : 'The hub can open it') : probe.error }}</div>
            <div class="fields">
              <Field label="Name" hint="Each line shows on its own line in the caption"><textarea class="input" rows="2" v-model="cam.label"
                style="resize:vertical; min-height:58px; padding-top:6px; line-height:1.35"></textarea></Field>
              <Field label="Place" hint="Shown under the name"><input class="input" v-model="cam.place" placeholder="Tokyo, Japan"></Field>
              <Field label="Credit" hint="Who runs the camera"><input class="input" v-model="cam.credit" placeholder="FNN"></Field>
              <Field label="Time zone" hint="For the caption's local time"><input class="input" v-model.trim="cam.tz" list="wc-tz" placeholder="Asia/Tokyo">
                <datalist id="wc-tz"><option v-for="z in TIMEZONES" :key="z" :value="z"/></datalist></Field>
              <Field label="Frame rate" hint="Smoother costs the hub more" v-if="!STILL.includes(cam.source.type)"><Stepper v-model="cam.fps" :min="1" :max="30" unit=" fps"/></Field>
              <Field label="Fetch every" hint="How often the image is fetched again" v-if="cam.source.type === 'image'">
                <Stepper :modelValue="cam.source.refresh_sec || 60" @update:modelValue="v => cam.source.refresh_sec = v" :min="20" :max="3600" :step="10" unit=" s"/></Field>
              <Field label="Location" :hint="cam.space ? 'In space: the caption can say what the ISS is over, its sunrises and sunsets, who is aboard' : 'Latitude, longitude: for the caption’s weather and sunrise/sunset'">
                <div class="stack" style="gap:6px"><Seg :modelValue="cam.space ? 'space' : 'earth'" @update:modelValue="setSpace" :options="[{ value: 'earth', label: 'On Earth' }, { value: 'space', label: 'Space (ISS)' }]"/>
                <div v-if="!cam.space" class="row nowrap" style="gap:6px"><input class="input" style="width:50%" :value="cam.lat ?? ''" @change="setCoord('lat', $event, 90)" placeholder="49.2888">
                  <input class="input" style="width:50%" :value="cam.lon ?? ''" @change="setCoord('lon', $event, 180)" placeholder="-123.1111"></div></div></Field>
            </div>
            <Field v-if="!cam.space" label="Weather from a city" hint="Instead of the coordinates: pick the city (its region and country are shown, as several places share a name)">
              <div v-if="cam.wx_city" class="row nowrap" style="gap:8px"><span class="pill accent"><Icon name="CloudSun" :size="13"/> {{ cityLabel(cam.wx_city) }}</span>
                <button class="btn sm ghost" @click="clearCity"><Icon name="X" :size="13"/>Use the coordinates</button></div>
              <div v-else class="stack" style="gap:6px">
                <div class="row nowrap" style="gap:6px"><input class="input grow" v-model="cityQ" placeholder="New Orleans  ·  or narrow it: White Rock, BC" @keydown.enter.prevent="searchCity">
                  <button class="btn" :disabled="citySearching || cityQ.trim().length < 2" @click="searchCity"><Icon :name="citySearching ? 'LoaderCircle' : 'Search'" :size="14" :class="{spin: citySearching}"/>Find</button></div>
                <div v-if="cityHits && !cityHits.length" class="faint" style="font-size:12.5px">No place by that name.</div>
                <div v-if="cityHits && cityHits.length" class="stack" style="gap:4px">
                  <button v-for="g in cityHits" :key="g.lat + ',' + g.lon" type="button" class="btn sm ghost" style="justify-content:flex-start" @click="pickCity(g)">
                    <Icon name="MapPin" :size="13"/>{{ cityLabel(g) }}<span class="faint" v-if="g.population">· {{ g.population.toLocaleString() }} people</span></button></div>
              </div></Field>
            <div v-if="local" class="faint" style="font-size:12.5px"><Icon name="MapPin" :size="13"/> There now: <b>{{ local.time }}</b>
              <template v-if="local.temp != null"> · {{ Math.round(local.temp) }}°C{{ local.conditions ? ' ' + local.conditions.toLowerCase() : '' }}</template>
              <template v-if="local.tz"> · {{ local.tz }}{{ cam.tz && cam.tz !== local.tz ? ' (the time zone above says ' + cam.tz + ')' : '' }}</template></div>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="Crop" :size="16"/><h3>Picture</h3></div>
          <div class="card-body stack">
            <div class="seg"><button v-for="(s, k) in SHAPES" :key="k" type="button" :class="{on: shape === k}" @click="shape = k">
              <Icon :name="s.icon" :size="14"/>{{ s.label }}<span v-if="customCrop(k)" class="faint" title="Has its own crop">•</span></button></div>
            <CropEditor :cam="cam" :shape="shape" :zone="zone"><button class="btn sm ghost" @click="copyCrop" :disabled="!customCrop(shape)"
              title="Give every other size a crop centred on this one"><Icon name="Copy" :size="14"/>Use for every size</button></CropEditor>
            <div class="fields">
              <Field label="Brightness"><Slider :modelValue="colour.brightness ?? 1" @update:modelValue="v => colour.brightness = v" :min="0.4" :max="2" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></Field>
              <Field label="Contrast"><Slider :modelValue="colour.contrast ?? 1" @update:modelValue="v => colour.contrast = v" :min="0.5" :max="2" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></Field>
              <Field label="Saturation"><Slider :modelValue="colour.saturation ?? 1" @update:modelValue="v => colour.saturation = v" :min="0" :max="2" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></Field>
              <Field label="Sharpness"><Slider :modelValue="colour.sharpen ?? DEFAULT_SHARPEN" @update:modelValue="v => colour.sharpen = v" :min="0" :max="2" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></Field>
            </div>
          </div></div>
      </div>

      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">LIVE</span><h3>On the wall · {{ SHAPES[shape].label.toLowerCase() }} ({{ zone[0] }}×{{ zone[1] }})</h3></div>
          <div class="card-body stack">
            <WallPreview :cam="cam" :shape="shape" :cam-id="id"/>
            <div v-if="needsLocation" class="callout" style="font-size:12.5px"><Icon name="MapPin" :size="14"/>
              <span>Weather and sunrise/sunset need this webcam's location.
                <button v-if="galleryLoc" class="btn sm" style="margin-left:6px" @click="useGalleryLoc">Use {{ galleryLoc.label }}'s location</button>
                <template v-else> Set it under Source → Location (latitude, longitude).</template></span></div>
            <div class="faint" style="font-size:12px" v-if="SHAPES[shape].note">{{ SHAPES[shape].note }}</div>
            <div class="row">
              <button class="btn sm" @click="play(false)"><Icon name="Play" :size="14"/>Play once on the wall</button>
              <button class="btn sm" @click="play(true)"><Icon name="Pause" :size="14"/>Hold on the wall</button>
            </div>
            <div class="wc-rot" v-if="inList">
              <Icon name="ListOrdered" :size="15"/><span>No. {{ listPos }} in the <a href="#/webcams">Webcams list</a></span><span class="spacer"></span>
              <span class="faint">shows for</span><Stepper :modelValue="inList.sec || WEBCAM_SEC" @update:modelValue="v => inList.sec = v" :min="5" :max="3600" :step="5" unit=" s"/>
              <Toggle :modelValue="inList.on !== false" @update:modelValue="v => inList.on = v" title="In the rotation"/></div>
            <div class="wc-rot" v-else><Icon name="ListOrdered" :size="15"/><span>Not in the Webcams list.</span><span class="spacer"></span>
              <button class="btn sm primary" @click="addToList"><Icon name="ListPlus" :size="14"/>Add to the list</button></div>
            <div class="wc-rot"><Icon name="Globe" :size="15"/>
              <Toggle :modelValue="!!cam.hours" @update:modelValue="toggleHours" title="Only at certain hours of the webcam's own day"/>
              <span>Only between, <b>webcam's local time</b>:</span>
              <template v-if="cam.hours"><input class="input" type="time" style="width:auto" v-model="cam.hours.from">
                <span>and</span><input class="input" type="time" style="width:auto" v-model="cam.hours.to"></template>
              <span class="faint" style="font-size:12px">{{ hoursNote }}</span></div>
            <div class="wc-rot" v-if="cam.space"><Icon name="Sun" :size="15"/>
              <Toggle :modelValue="cam.daylight_only ?? true" @update:modelValue="v => cam.daylight_only = v" title="Only in orbital daylight"/>
              <span>Only while the ISS is in daylight</span>
              <span class="faint" style="font-size:12px">skipped on the night side of the orbit: its whole turn must be in sunlight</span></div>
            <div class="wc-rot"><Icon name="MonitorOff" :size="15"/>
              <Toggle :modelValue="cam.skip_plain ?? !!cam.space" @update:modelValue="v => cam.skip_plain = v" title="Skip a stand-in picture"/>
              <span>Skip it when the picture is frozen or one plain colour</span>
              <span class="faint" style="font-size:12px">a "loss of signal" card instead of the video (NASA's ISS stream has one); passed over like a black picture</span></div>
            <div class="faint" style="font-size:12px" v-if="used.length">Also its own rotation entry ({{ used.map(u => u.side).join(', ') }}):
              <a href="#/webcams">combine it into the list</a> on the Webcams page.</div>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="Captions" :size="16"/><h3>Caption</h3>
            <span class="spacer"></span><button class="btn sm ghost" @click="captionToAll" title="Copy this caption look to every webcam"><Icon name="Copy" :size="14"/>Use on all webcams</button></div>
          <div class="card-body stack">
            <div class="seg wc-captabs"><button v-for="t in [{v:'theme', l:'Theme', i:'Palette'}, {v:'content', l:'What it says', i:'Text'}, {v:'look', l:'Size & look', i:'Ruler'}, {v:'when', l:'When', i:'Clock'}, {v:'japanese', l:'Japanese', i:'Languages'}]"
              :key="t.v" type="button" :class="{on: capTab === t.v}" @click="capTab = t.v" :disabled="!['theme', 'japanese'].includes(t.v) && ov.style === 'none'"><Icon :name="t.i" :size="14"/>{{ t.l }}</button></div>
            <div v-if="capTab === 'theme'" class="wc-layouts"><button v-for="(l, k) in LAYOUTS" :key="k" type="button" class="wc-layout" :class="{on: ov.style === k}" @click="ov.style = k" :title="l.note">
              <Icon :name="l.icon" :size="15"/><b>{{ l.label }}</b><span class="faint">{{ l.note }}</span></button></div>
            <template v-if="ov.style !== 'none' && capTab === 'look'">
              <div class="row" style="gap:16px; align-items:flex-start">
                <Field label="Position"><div class="wc-corners wc-six">
                  <button v-for="c in POSITIONS" :key="c" type="button" :class="{on: ov.corner === c}" @click="ov.corner = c" :title="c"></button></div></Field>
                <Field label="Text size" v-if="ov.style !== 'dot'"><Seg v-model="ov.font" :options="FONTS"/></Field>
                <Field label="Japanese text size" hint="Kana and kanji only; the English keeps the text size" v-if="ov.style !== 'dot'">
                  <Seg :modelValue="ov.jp_font || 'auto'" @update:modelValue="v => ov.jp_font = v" :options="[{ value: 'auto', label: 'Same as text' }, ...FONTS]"/></Field>
              </div>
              <div class="row" style="gap:16px; align-items:flex-start" v-if="ov.style !== 'dot'">
                <Field label="Japanese font" hint="Approved Japanese fonts (Fonts tab); each comes in the sizes listed">
                  <select class="input" style="width:auto" :value="ov.jp_face || 'auto'" @change="ov.jp_face = $event.target.value">
                    <option value="auto">Matched to the text size</option>
                    <option v-for="f in jpFaces" :key="f.id" :value="f.id">{{ f.name }} · {{ f.sizes.map(s => s[0]).join(', ') }} px</option></select>
                  <img v-if="jpSample" :src="jpSample" alt="" style="image-rendering:pixelated; margin-top:6px; max-width:100%; display:block"></Field>
              </div>
              <div class="row" style="gap:16px; align-items:flex-start" v-if="ov.style !== 'dot'">
                <Field label="Text alignment" hint="Auto follows the position"><Seg :modelValue="ov.text_align || 'auto'" @update:modelValue="v => ov.text_align = v" :options="TEXT_ALIGNS"/></Field>
                <Field label="Label width" v-if="['tag', 'outline'].includes(ov.style)" hint="As wide as its text, at least a width, or edge to edge">
                  <div class="row nowrap" style="gap:8px"><Seg v-model="boxMode" :options="[{value:'auto', label:'Fit text'}, {value:'min', label:'At least'}, {value:'full', label:'Full width'}]"/>
                    <Stepper v-if="boxMode === 'min'" :modelValue="ov.box_w" @update:modelValue="v => ov.box_w = v" :min="20" :max="448" :step="10" unit=" px"/></div></Field>
              </div>
              <div class="row" style="gap:16px; align-items:flex-start" v-if="ov.style !== 'dot'">
                <Field label="Distance from the edge"><div class="seg mini"><button v-for="m in [-1, 0, 2, 4, 8, 12, 16]" :key="m" type="button" :class="{on: (ov.margin ?? -1) === m}"
                  @click="ov.margin = m">{{ m < 0 ? 'Layout’s own' : m + ' px' }}</button></div></Field>
                <Field label="Padding" v-if="['tag', 'outline'].includes(ov.style)"><Stepper :modelValue="ov.pad ?? 2" @update:modelValue="v => ov.pad = v" :min="0" :max="10" unit=" px"/></Field>
              </div>
            </template>
            <template v-if="ov.style !== 'none' && capTab === 'content'">
              <Field label="Rows" hint="Each row, top to bottom; drag pieces to reorder them, between rows, or to Not shown. Separators come from the palette; every piece has its own size">
                <CaptionRows :cam="cam" :ov="ov" :labels="SECTIONS" :has-text="hasText"/></Field>
              <ToggleRow v-if="cam.space && inRows('crew')" :modelValue="ov.crew_flags !== false" @update:modelValue="v => ov.crew_flags = v"
                label="Flags for the people aboard" hint="7 aboard: 3, 3, 1 with each country's flag after its count; off: just 7 aboard"/>
              <Field v-if="cam.space && inRows('coords')" label="Coordinates" hint="Updated every frame as the ISS moves">
                <div class="row" style="gap:8px"><Seg :modelValue="ov.coord_format || 'nsew'" @update:modelValue="v => ov.coord_format = v"
                  :options="[{ value: 'nsew', label: '12.35°S 145.68°W' }, { value: 'signed', label: '-12.35°, -145.68°' }, { value: 'dms', label: '12°21′00″S' }]"/>
                  <Stepper :modelValue="ov.coord_digits ?? 2" @update:modelValue="v => ov.coord_digits = v" :min="0" :max="4" :unit="' decimals'"/></div></Field>
              <ToggleRow v-if="cam.space && inRows('mission')" :modelValue="!!ov.mission_days" @update:modelValue="v => ov.mission_days = v"
                label="How long the mission has run" hint="Expedition 75 · day 120"/>
              <ToggleRow v-if="inRows('weather')" :modelValue="!!ov.temp_icon" @update:modelValue="v => ov.temp_icon = v"
                label="Weather icon in front of the temperature" hint="Off: just the temperature"/>
            </template>
            <template v-if="ov.style !== 'none' && capTab === 'content'">
              <Field label="Highway shield" v-if="inRows('shield')" hint="Pick a sign, or type a route; inside, the box grows to fit it">
                <div class="stack" style="gap:8px">
                  <div v-for="g in SHIELD_PRESETS" :key="g.group" class="row" style="gap:4px; align-items:center">
                    <span class="faint" style="font-size:11.5px; width:150px">{{ g.group }}</span>
                    <button v-for="r in g.routes" :key="r" type="button" class="wc-shield" :class="{on: ov.shield.route === r && ov.shield.kind === 'auto'}"
                      @click="ov.shield.route = r; ov.shield.kind = 'auto'" :title="r"><img v-if="presetShown(r)" :src="shieldSrc(r, 'auto', 24, ov.shield.style)" alt="" height="24"
                        @load="presetDone" @error="presetDone"></button></div>
                  <div class="row" style="gap:10px; align-items:flex-end">
                    <Field label="Route"><input class="input" style="width:90px" v-model.trim="ov.shield.route" maxlength="8" placeholder="99"></Field>
                    <Field label="Sign"><select class="input" v-model="ov.shield.kind"><option v-for="s in SHIELD_KINDS" :key="s.value" :value="s.value">{{ s.label }}</option></select></Field>
                    <img v-if="ov.shield.route" :src="shieldSrc(ov.shield.route, ov.shield.kind, 32, ov.shield.style)" alt="" height="32"
                      :style="{imageRendering: ov.shield.style === 'real' ? 'auto' : 'pixelated'}" @load="signMissing = false" @error="signMissing = true">
                  </div>
                  <Field label="Look" hint="Pixel art: redrawn for the LEDs at set sizes. Real sign: the sign itself (Wikimedia Commons), scaled to any size">
                    <Seg v-model="ov.shield.style" :options="SHIELD_STYLES"/></Field>
                  <div class="callout" style="font-size:12.5px" v-if="signMissing && ov.shield.route"><Icon name="Info" :size="14"/>
                    <span>{{ ov.shield.style === 'real' ? 'No real sign for this route on Wikimedia Commons (or it couldn’t be fetched); the wall uses the pixel art.' : 'This route has no number to draw.' }}</span></div>
                  <Field label="Where"><Seg v-model="ov.shield.place" :options="SHIELD_PLACES"/></Field>
                  <Field label="Size" v-if="ov.shield.place !== 'line'" hint="Box height matches the caption box; a bigger size stands out of a box it's beside, or grows the box it's in">
                    <div class="seg mini"><button v-for="s in SHIELD_SIZES" :key="s.value" type="button" :class="{on: ov.shield.size === s.value}" @click="ov.shield.size = s.value">{{ s.label }}</button></div></Field>
                  <div class="faint" style="font-size:12px" v-else>The shield takes its line's height; place it in the arrangement below.</div>
                </div></Field>
              <Field label="Image time" v-if="inRows('age')" hint="When the still was taken (DriveBC gives it to the second)">
                <Seg :modelValue="ov.taken_format || 'time'" @update:modelValue="v => ov.taken_format = v"
                     :options="[{value:'time', label:'taken 17:05:42'}, {value:'relative', label:'3 min ago'}, {value:'both', label:'Both'}]"/></Field>
              <Field label="Your text" v-if="inRows('custom')"><input class="input" v-model="ov.custom_text" maxlength="40" placeholder="Scramble Crossing, from the Starbucks"></Field>
              <div class="callout info" v-if="needsLocation" style="font-size:12.5px">
                <Icon name="MapPin" :size="14"/> The weather sections need this webcam's location (Source → Location).</div>
              <div class="row" style="gap:16px; align-items:flex-start">
                <Field label="Clock"><Seg v-model="ov.time_format" :options="[{value:'24h', label:'24 h'}, {value:'12h', label:'12 h'}]"/></Field>
              </div>
            </template>
            <template v-if="ov.style !== 'none' && capTab === 'look'">
              <div class="row" style="gap:16px; align-items:flex-start">
                <Field label="Accent"><ColorPick v-model="ov.accent"/></Field>
                <Field label="Text" v-if="ov.style !== 'dot'"><ColorPick v-model="ov.text"/></Field>
                <Field label="Background"><ColorPick v-model="ov.bg"/></Field>
                <Field label="Background strength"><div style="width:150px"><Slider v-model="ov.opacity" :min="0" :max="1" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></div></Field>
              </div>
              <ToggleRow v-if="ov.style !== 'dot'" v-model="ov.uppercase" label="ALL CAPS"/>
              <ToggleRow v-if="!['dot', 'ticker'].includes(ov.style)" :modelValue="!!ov.wx_beside" @update:modelValue="v => ov.wx_beside = v"
                label="Weather icon beside the caption" :hint="Number.isFinite(cam.lat) ? 'A bigger icon just outside the box, outlined in black' : 'Needs a location for the webcam (Source → Location)'"/>
              <ToggleRow v-if="ov.wx_beside && ov.style === 'tag'" :modelValue="!!ov.wx_beside_box" @update:modelValue="v => ov.wx_beside_box = v"
                label="Inside the caption box" hint="The box grows sideways to take in the icon"/>
            </template>
            <template v-if="ov.style !== 'none' && capTab === 'when'">
              <div class="row" style="gap:16px; align-items:flex-start">
                <Field label="Shows"><Seg v-model="ov.when" :options="[{value:'always', label:'Always'}, {value:'intro', label:'First 10 s'}, {value:'periodic', label:'Every few minutes'}]"/></Field>
                <template v-if="ov.when === 'periodic'">
                  <Field label="For"><Stepper v-model="ov.show_sec" :min="3" :max="60" unit=" s"/></Field>
                  <Field label="Every"><Stepper v-model="ov.every_min" :min="1" :max="30" unit=" min"/></Field></template>
                <Field label="Fade" v-if="ov.when !== 'always'"><Toggle v-model="ov.fade"/></Field>
              </div>
            </template>
            <template v-if="capTab === 'japanese'">
              <ToggleRow :modelValue="!!jc.on" @update:modelValue="v => jc.on = v" label="Show a Japanese caption"
                hint="Its own box with your text, in its own Japanese font and size, on the caption's background"/>
              <template v-if="jc.on">
                <Field label="Text" hint="Each line shows as a line"><textarea class="input" rows="3" v-model="jc.text" maxlength="400"
                  style="resize:vertical; min-height:72px; padding-top:6px; line-height:1.35" placeholder="歌舞伎町一番街"></textarea></Field>
                <div class="row" style="gap:16px; align-items:flex-start">
                  <Field label="Japanese font" hint="Approved Japanese fonts (Fonts tab)">
                    <select class="input" style="width:auto" :value="jc.face" @change="jcSetFace($event.target.value)">
                      <option v-for="f in jpFaces" :key="f.id" :value="f.id">{{ f.name }} · {{ f.sizes.map(s => s[0]).join(', ') }} px</option></select></Field>
                  <Field label="Size" v-if="jcSizes.length > 1"><Seg :modelValue="jc.size" @update:modelValue="v => jc.size = v" :options="jcSizes"/></Field>
                </div>
                <img v-if="jcSample" :src="jcSample" alt="" style="image-rendering:pixelated; max-width:100%; display:block">
                <Field label="Position" hint="Next to the caption: above it in a bottom corner, below it in a top one">
                  <Seg :modelValue="jc.corner || 'auto'" @update:modelValue="v => jc.corner = v" :options="JC_POSITIONS"/></Field>
              </template>
            </template>
            <div class="faint" style="font-size:12px" v-if="capTab === 'content'">When a row is too wide, its last pieces are left out (or it continues on the next line, with Wraps);
              its first piece is never left out, only cut short. A section with nothing to show right now drops out with the separator before it.</div>
          </div></div>
      </div>
    </div>
  </div>
  <div class="page" v-else><div class="empty">No webcam “{{ id }}”. <a href="#/webcams">Back to webcams</a></div></div>`,
};

export const Webcams = {
  components: { WebcamList, WebcamEditor },
  setup() { return { store }; },
  template: `<WebcamEditor v-if="store.route.params[0] && store.route.params[0] !== '~grid'"/><WebcamList v-else/>`,
};
