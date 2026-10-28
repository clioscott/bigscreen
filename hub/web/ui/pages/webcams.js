// Webcams: live video sources (hub/webcams.py decodes them), how each is cropped for every place on the wall,
// its colour and its caption, and adding it to the rotation (panels webcam / webcam_full / webcam_wall).
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import Sortable from "sortablejs";
import { store, api, fail, toast, seed, go, clone, dirty, previewOnDisplay, playOnDisplay, rotationAction } from "../core.js";
import { GALLERY, CITIES, CATS, snap, thumb } from "./webcam_gallery.js";
import { CameraMap } from "./cameramap.js";
import { useSlippyMap } from "./slippymap.js";

export const SHAPES = {
  left: { label: "Left side", panel: "webcam", side: "left", icon: "PanelLeft" },
  right: { label: "Right side", panel: "webcam", side: "right", icon: "PanelRight" },
  full: { label: "Full width", panel: "webcam_full", side: "right", icon: "RectangleHorizontal", note: "ticker and clock stay" },
  tall: { label: "Above the clock", panel: "webcam_tall", side: "right", icon: "PanelBottomClose", note: "covers the ticker; the clock and date stay" },
  wall: { label: "Whole wall", panel: "webcam_wall", side: "right", icon: "Maximize", note: "covers the ticker and clock" },
};
// caption layouts and sections (display/modules/webcam_caption.py draws them)
const LAYOUTS = {
  tag: { label: "Tag", icon: "Tag", note: "A box in a corner with an accent edge" },
  bar: { label: "Bar", icon: "PanelBottom", note: "Full-width strip, time at the right" },
  lower_third: { label: "Lower third", icon: "Tv", note: "TV news: name on an accent block, details under it" },
  split: { label: "Split bar", icon: "Columns2", note: "Name and details left, a big local clock right" },
  postcard: { label: "Postcard", icon: "Mail", note: "Big centred title with a shadow" },
  chips: { label: "Chips", icon: "Tags", note: "A rounded chip for each section" },
  clock: { label: "World clock", icon: "Clock", note: "Local time, big, with the place under it" },
  signage: { label: "Departure board", icon: "TrainFront", note: "Amber lines on a dark board" },
  ticker: { label: "Ticker", icon: "ChevronsLeft", note: "Name in a tab, details scrolling past" },
  stack: { label: "Stack", icon: "List", note: "Each section on its own outlined line" },
  underline: { label: "Underline", icon: "Underline", note: "Big headline over an accent rule, no box" },
  custom: { label: "Custom", icon: "LayoutGrid", note: "Your own columns and lines" },
  outline: { label: "Outlined", icon: "Type", note: "Text with a black edge, no box" },
  dot: { label: "LIVE dot", icon: "CircleDot", note: "Just a blinking dot and the time" },
  none: { label: "None", icon: "EyeOff", note: "No caption" },
};
const FONTS = [{ value: "tiny", label: "Tiny" }, { value: "small", label: "Small" }, { value: "medium", label: "Medium" }, { value: "large", label: "Large" },
  { value: "xlarge", label: "Extra large" }];
const SECTIONS = { name: "Name", place: "Place", time: "Local time", age: "Image time", weather: "Temperature", sun: "Sunrise / sunset", date: "Date",
  offset: "Time difference", daynight: "Day / night icon", live: "LIVE badge", credit: "Credit", custom: "Your text",
  shield: "Highway shield", conditions: "Conditions", feels: "Feels like", hilo: "High / low", wind: "Wind", humidity: "Humidity" };
// the section chips, grouped (weather ones need the webcam's location)
const SECTION_GROUPS = [{ label: "About the webcam", keys: ["name", "place", "credit", "custom", "live", "shield"] },
  { label: "Time", keys: ["time", "date", "offset", "age", "daynight"] },
  { label: "Weather there", keys: ["weather", "conditions", "feels", "hilo", "wind", "humidity", "sun"] }];
const WX_KEYS = ["weather", "sun", "conditions", "feels", "hilo", "wind", "humidity"];
const ORDERABLE = ["shield", "live", "name", "place", "time", "age", "weather", "conditions", "feels", "hilo", "wind", "humidity", "date", "offset", "sun",
  "custom", "credit"];
// the Custom layout: where each section goes (column, line) to start with; mirrors webcam_caption.DEFAULT_SLOTS
const DEFAULT_SLOTS = { shield: [1, 1], live: [1, 1], name: [1, 1], place: [1, 2], time: [1, 2], age: [1, 2], weather: [1, 3], date: [1, 3], offset: [1, 3],
  sun: [1, 3], custom: [1, 3], credit: [1, 3], conditions: [1, 3], feels: [1, 3], hilo: [1, 3], wind: [1, 4], humidity: [1, 4] };
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
  lines: "auto", separator: "dot", uppercase: false, order: [...ORDERABLE],
  show: { name: true, place: true, time: true, date: false, offset: false, daynight: false, weather: false, sun: false,
    age: false, live: true, credit: false, custom: false, shield: false, conditions: false, feels: false, hilo: false, wind: false, humidity: false },
  shield: { route: "99", kind: "auto", style: "pixel", place: "inside", size: "box" } };
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
export const camEntry = (id, shape) => ({ panel: SHAPES[shape].panel, cam: id, rules: [], dwell_sec: 60 });

function useStatus() {
  const status = ref({ feeds: [], zones: { left: [295, 130], right: [152, 130], full: [448, 130], tall: [448, 175], wall: [448, 192] } });
  let t;
  const load = async () => { try { status.value = await api("/api/webcams/status"); } catch {} };
  onMounted(() => { load(); t = setInterval(load, 3000); });
  onBeforeUnmount(() => clearInterval(t));
  const feedFor = (cam) => status.value.feeds.find((f) => f.url === srcUrl(cam?.source) && f.type === (cam?.source?.type || "stream"));
  return { status, feedFor };
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
  seed(["modules", "webcams", "playlist"], Object.keys(wc.cams || {}).map((cam) => ({ cam, sec: 60, on: true })));
  seed(["modules", "webcams", "grid"], { cams: [], interval_ms: 200, hold_sec: 3, labels: true });
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
    const { feedFor } = useStatus();
    const found = computed(() => rotationEntries(isListEntry));
    const single = computed(() => rotationEntries((e) => e.panel?.startsWith("webcam") && !e.panel.startsWith("webcam_grid") && e.cam));
    // older rotations had one entry per webcam: fold them into the list (their order first), with one Webcams entry
    const combine = () => {
      const lc = store.draft.modules.layout_cycle;
      const first = single.value[0];
      const order = [...new Set(single.value.map((u) => u.e.cam))];
      const rest = wc.playlist.filter((x) => !order.includes(x.cam));
      wc.playlist = [...order.map((cam) => ({ ...(wc.playlist.find((x) => x.cam === cam) || { cam }), on: true,
        sec: single.value.find((u) => u.e.cam === cam).e.dwell_sec || 60 })), ...rest.map((x) => ({ ...x, on: false }))];
      for (const side of ["left", "right"]) lc[side] = (lc[side] || []).filter((e) => !(e.panel?.startsWith("webcam") && !e.panel.startsWith("webcam_grid") && e.cam));
      if (!found.value.length) {
        const entry = { panel: first.e.panel, rules: first.e.rules || [], advance: "cycles", cycles: 1, max_sec: 3600, ...(first.e.variants ? { variants: first.e.variants } : {}) };
        lc[first.side].splice(Math.min(first.i, lc[first.side].length), 0, entry);
      }
      toast(`${order.length} webcams combined into the list, with one Webcams entry in the rotation. Save to keep it.`);
    };
    const on = computed(() => list.value.filter((x) => x.on !== false && cams.value[x.cam]));
    const total = computed(() => on.value.reduce((n, x) => n + (x.sec || 60), 0));
    const srcHost = (s) => (s?.type === "drivebc" ? `DriveBC camera ${s.id}` : host(s?.url));
    const kindOf = (s) => (STILL.includes(s?.type) ? "Still" : "Live");
    const mins = (s) => (s >= 90 ? `${Math.round(s / 6) / 10} min` : `${s} s`);
    return { LIST_SHAPES, wc, cams, list, on, total, found, single, combine, feedFor, feedPill, srcHost, kindOf, hasSource, mins, go };
  },
  template: `<div class="stack">
    <div class="card"><div class="card-body stack" style="gap:10px">
      <InRotation :found="found" what="Webcams" :shapes="LIST_SHAPES"/>
      <div v-if="single.length" class="callout" style="font-size:12.5px"><Icon name="Combine" :size="14"/>
        <span>{{ single.length }} webcam{{ single.length === 1 ? ' is a separate rotation entry' : 's are separate rotation entries' }}. Combine them into this list,
          so the rotation has one Webcams entry and the order lives here.
          <button class="btn sm primary" style="margin-left:6px" @click="combine">Combine into the list</button></span></div>
      <div class="row" style="gap:18px; align-items:flex-end">
        <Field label="Each turn plays" hint="Then the rotation moves on; the next turn carries on down the list">
          <Stepper :modelValue="wc.per_turn || 1" @update:modelValue="v => wc.per_turn = v" :min="1" :max="50" :unit="(wc.per_turn || 1) === 1 ? ' webcam' : ' webcams'"/></Field>
        <div class="faint" style="font-size:12.5px; padding-bottom:6px">{{ on.length }} of {{ list.length }} on · the whole list takes {{ mins(total) }}</div>
      </div>
    </div></div>
    <div class="card">
      <div class="card-head"><Icon name="ListOrdered" :size="16"/><h3 style="white-space:nowrap">The list</h3><span class="faint ellipsis" style="font-size:12px; min-width:0">drag to reorder · switch off to skip</span>
        <span class="spacer"></span><button class="btn sm" @click="$emit('add')"><Icon name="Plus" :size="14"/>Add by URL</button>
        <button class="btn sm" @click="$emit('find')"><Icon name="Search" :size="14"/>Find webcams</button></div>
      <div v-sortable="{ list: list, handle: '.handle' }">
        <div v-for="(it, i) in list" :key="it.cam" class="list-row wc-list-row" :class="{off: it.on === false}">
          <span class="handle"><Icon name="GripVertical" :size="16"/></span>
          <span class="mono faint" style="width:18px; text-align:right">{{ i + 1 }}</span>
          <span class="wc-row-thumb" style="cursor:pointer" @click="go('webcams/' + it.cam)"><SnapImg v-if="hasSource(cams[it.cam]?.source)" :source="cams[it.cam].source"/></span>
          <div class="grow" style="min-width:0; cursor:pointer" @click="go('webcams/' + it.cam)"><b>{{ cams[it.cam]?.label || it.cam }}</b>
            <div class="faint ellipsis" style="font-size:12.5px"><span class="wc-kind" :class="kindOf(cams[it.cam]?.source).toLowerCase()">{{ kindOf(cams[it.cam]?.source) }}</span>
              {{ [cams[it.cam]?.place, srcHost(cams[it.cam]?.source)].filter(Boolean).join(' · ') }}</div></div>
          <span class="pill" :class="feedPill(feedFor(cams[it.cam])).cls">{{ feedPill(feedFor(cams[it.cam])).text }}</span>
          <Stepper :modelValue="it.sec || 60" @update:modelValue="v => it.sec = v" :min="5" :max="3600" :step="5" unit=" s" title="How long it shows"/>
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
      if (!g.cams.length) { img.value = ""; return; }
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
    watch(() => [g.cams.map((c) => JSON.stringify(c.source) + c.name).join("|"), g.labels, g.rows, g.cols, shape.value], queue, { immediate: true });
    onBeforeUnmount(() => { clearTimeout(timer); if (url) URL.revokeObjectURL(url); });
    // the hub's own thumbnail for the camera (kept with it when it was added), else a snapshot
    const thumbOf = (c) => (c.source.type === "drivebc" ? `/api/webcams/drivebc/${c.source.id}.jpg`
      : /^\/api\/webcams\/city\/[\w-]+\.jpg$/.test(c.thumb || "") ? c.thumb : snap(c.source));
    const secs = computed(() => (g.cams.length * (g.interval_ms || 200)) / 1000 + (g.hold_sec ?? 3));
    const play = () => {
      const s = SHAPES[shape.value];
      playOnDisplay({ panel: GRID_SHAPES[shape.value], advance: "cycles", cycles: 1, max_sec: 600 }, { side: s.side, hold: false });
    };
    return { GRID_SHAPES, g, found, shape, img, busy, thumbOf, secs, play, render, SHAPES, dirty };
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
          <div class="empty" v-if="!g.cams.length">No cameras yet. Use <b>Add cameras</b>, then <b>Select several</b> in the intersection list.</div>
        </div></div>
      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">LIVE</span><h3>On the wall</h3><span class="spacer"></span>
            <select class="input" v-model="shape" style="width:auto; height:28px"><option v-for="(s, k) in SHAPES" :key="k" :value="k">{{ s.label }}</option></select></div>
          <div class="card-body stack">
            <div class="led"><img v-if="img" :src="img" alt="" :style="{opacity: busy ? .6 : 1}"><div v-else class="placeholder">{{ busy ? 'Fetching cameras…' : 'Add cameras to see the grid' }}</div></div>
            <div class="faint" style="font-size:12px">With every tile filled (the last cameras in the grid). The wall fills it one tile at a time.</div>
            <div class="row"><button class="btn sm" @click="play" :disabled="!g.cams.length || dirty.length" :title="dirty.length ? 'Save first' : ''"><Icon name="Play" :size="14"/>Play once on the wall</button>
              <span class="faint" style="font-size:12px">Takes about {{ Math.round(secs) }} s</span></div>
          </div></div>
        <div class="card"><div class="card-head"><Icon name="SlidersHorizontal" :size="16"/><h3>Timing and labels</h3></div>
          <div class="card-body fields">
            <Field label="A new camera every" hint="200 ms is five a second"><Stepper :modelValue="g.interval_ms ?? 200" @update:modelValue="v => g.interval_ms = v" :min="50" :max="5000" :step="50" unit=" ms"/></Field>
            <Field label="Hold at the end" hint="Once the last camera is in"><Stepper :modelValue="g.hold_sec ?? 3" @update:modelValue="v => g.hold_sec = v" :min="0" :max="30" unit=" s"/></Field>
            <Field label="Rows"><Stepper :modelValue="g.rows ?? 2" @update:modelValue="v => g.rows = v" :min="1" :max="4"/></Field>
            <Field label="Columns" hint="Auto fits the frame: 6 across full width with 2 rows">
              <div class="row nowrap" style="gap:8px"><Seg :modelValue="g.cols == null || g.cols === 'auto' ? 'auto' : 'set'"
                @update:modelValue="v => g.cols = v === 'auto' ? 'auto' : 6" :options="[{value:'auto', label:'Auto'}, {value:'set', label:'Set'}]"/>
                <Stepper v-if="g.cols != null && g.cols !== 'auto'" :modelValue="g.cols" @update:modelValue="v => g.cols = v" :min="1" :max="10"/></div></Field>
            <Field label="Names" hint="Each tile's camera, small, along its bottom"><Toggle :modelValue="g.labels !== false" @update:modelValue="v => g.labels = v"/></Field>
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
      const overlay = clone(DEFAULT_OVERLAY);
      if (STILL.includes(f.source?.type)) Object.assign(overlay.show, { age: true, live: false });   // a still isn't LIVE
      all[id] = { fps: 15, ...base, overlay, crops: {} };
      store.draft.modules.webcams.playlist.push({ cam: id, sec: 60, on: true });
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
    watch(src, () => { err.value = false; });
    const aspect = computed(() => props.zone[0] / props.zone[1]);
    const fit = () => {
      const [sw, sh] = nat.value;
      const w = Math.min(1, (sh * aspect.value) / sw);
      const h = (w * sw) / aspect.value / sh;
      return { x: +((1 - w) / 2).toFixed(5), y: +Math.max(0, (1 - h) / 2).toFixed(5), w: +w.toFixed(5) };
    };
    const crop = computed(() => props.cam.crops?.[props.shape] || fit());
    const box = computed(() => {
      const [sw, sh] = nat.value;
      const c = crop.value;
      const w = Math.min(c.w, (sh * aspect.value) / sw);
      const h = (w * sw) / aspect.value / sh;
      return { x: Math.min(Math.max(0, c.x), 1 - w), y: Math.min(Math.max(0, c.y), 1 - h), w, h };
    });
    const set = (c) => {
      const [sw, sh] = nat.value;
      const w = Math.max(0.05, Math.min(c.w, 1, (sh * aspect.value) / sw));
      const h = (w * sw) / aspect.value / sh;
      const out = { x: +Math.min(Math.max(0, c.x), 1 - w).toFixed(5), y: +Math.min(Math.max(0, c.y), 1 - h).toFixed(5), w: +w.toFixed(5) };
      (props.cam.crops || (props.cam.crops = {}))[props.shape] = out;
    };
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
      const w = Math.max(0.05, Math.min(1, b.w * k));
      const [sw, sh] = nat.value;
      const h = (w * sw) / aspect.value / sh;
      set({ x: b.x + (b.w - w) / 2, y: b.y + (b.h - h) / 2, w });
    };
    const wheel = (ev) => {                  // Ctrl + wheel zooms; a plain wheel still scrolls the page
      if (!ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault();
      zoomBy(ev.deltaY > 0 ? 1.08 : 1 / 1.08);
    };
    const zoom = computed({ get: () => Math.round(100 / box.value.w), set: (v) => zoomBy(100 / v / box.value.w) });
    const reset = () => { if (props.cam.crops) delete props.cam.crops[props.shape]; };
    return { img, src, err, box, down, move, up, wheel, zoom, reset, nat };
  },
  template: `<div class="stack">
    <div class="wc-src" @wheel="wheel">
      <img v-if="src && !err" ref="img" :src="src" alt="" @error="err = true">
      <div v-else class="placeholder">{{ src ? 'The hub could not open this source (see Test above).' : 'Enter a source URL above.' }}</div>
      <div v-if="src && !err" class="wc-crop" :style="{left: box.x * 100 + '%', top: box.y * 100 + '%', width: box.w * 100 + '%', height: box.h * 100 + '%'}"
        @pointerdown.prevent="down" @pointermove="move" @pointerup="up" @pointercancel="up"><span>{{ zone[0] }}×{{ zone[1] }}</span></div>
    </div>
    <div class="row">
      <span class="faint">Zoom</span><div style="flex:1; min-width:160px"><Slider v-model="zoom" :min="100" :max="800" :step="5" unit="%"/></div>
      <button class="btn sm" @click="reset" title="The largest centred crop"><Icon name="Scan" :size="14"/>Fit</button>
      <slot/>
    </div>
    <div class="hint faint" style="font-size:12px">Drag the box to choose what shows; Ctrl + scroll on the picture zooms. Each place on the wall keeps its own crop.
      Source {{ nat[0] }}×{{ nat[1] }} (decoded at 640 px wide).</div>
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

// --- the Custom caption's arrangement: 3 columns × 4 lines, each line a left part and a right end --------------------
// ov.slots[k] = [column, line] or [column, line, "right"] (webcam_caption.custom_grid / right_kinds); sections sharing a
// part of a line follow ov.order. Dragging is SortableJS (mouse and touch); the DOM is put back and Vue redraws.
const CaptionGrid = {
  props: { ov: Object, hasText: Function },
  setup(props) {
    const root = ref(null);
    const sortables = [];
    const slotOf = (k) => props.ov.slots?.[k] || DEFAULT_SLOTS[k] || [1, 3];
    const shown = computed(() => {
      const o = props.ov.order || [];
      const outside = shieldOutside(props.ov);
      return [...o, ...ORDERABLE.filter((k) => !o.includes(k))]
        .filter((k) => k in DEFAULT_SLOTS && props.ov.show[k] && !(k === "shield" && outside));
    });
    const zone = (c, l, side) => shown.value.filter((k) => {
      const s = slotOf(k);
      return s[0] === c && s[1] === l && (s[2] === "right" ? "right" : "left") === side;
    });
    const filled = (c, l) => zone(c, l, "left").length + zone(c, l, "right").length > 0;
    const usedCols = computed(() => customCols(props.ov, props.hasText));   // blank chips show here but aren't drawn
    const lineSize = (c, l) => props.ov.line_sizes?.[`${c}.${l}`] || (c === (usedCols.value[0] || 1) && l === 1 ? "headline" : "detail");
    const setLineSize = (c, l, v) => { (props.ov.line_sizes || (props.ov.line_sizes = {}))[`${c}.${l}`] = v; };
    const colAlign = (c) => props.ov.columns?.[c - 1]?.align || (usedCols.value.length > 1 && c === usedCols.value.at(-1) ? "right" : "left");
    const setColAlign = (c, v) => {
      const cols = props.ov.columns || (props.ov.columns = []);
      while (cols.length < c) cols.push({});
      cols[c - 1] = { ...cols[c - 1], align: v };
    };
    // k goes to (c, l, side): before `before` if that's already there, else after the last section there
    const place = (k, c, l, side, before) => {
      const slots = props.ov.slots || (props.ov.slots = {});
      const there = zone(c, l, side).filter((x) => x !== k);
      slots[k] = side === "right" ? [c, l, "right"] : [c, l];
      const o = props.ov.order || (props.ov.order = [...ORDERABLE]);
      for (const x of ORDERABLE) if (!o.includes(x)) o.push(x);
      const anchor = there.includes(before) ? before : null;
      if (!anchor && !there.length) return;
      o.splice(o.indexOf(k), 1);
      o.splice(anchor ? o.indexOf(anchor) : o.indexOf(there.at(-1)) + 1, 0, k);
    };
    onMounted(() => {
      root.value.querySelectorAll(".cg-zone").forEach((el) => sortables.push(Sortable.create(el, {
        group: "caption-grid", animation: 140, draggable: ".cg-chip", ghostClass: "sortable-ghost", chosenClass: "sortable-chosen",
        onEnd(evt) {
          const { item, from, to, oldIndex, newIndex } = evt;
          const before = item.nextElementSibling?.dataset?.k || null;
          if (to !== from || oldIndex !== newIndex) {
            to.removeChild(item);                             // put the node back where Vue drew it; the data moves it
            from.insertBefore(item, from.children[oldIndex] || null);
          }
          if (from === to && oldIndex === newIndex) return;
          place(item.dataset.k, +to.dataset.col, +to.dataset.line, to.dataset.side, before);
        },
      })));
    });
    onBeforeUnmount(() => sortables.forEach((x) => x.destroy()));
    const label = (k) => (k === "live" ? "LIVE" : k === "shield" ? "Shield " + (props.ov.shield?.route || "") : SECTIONS[k]);
    const ALIGN_ICONS = { left: "AlignLeft", center: "AlignCenter", right: "AlignRight" };
    return { root, zone, filled, usedCols, lineSize, setLineSize, colAlign, setColAlign, label, LINE_SIZES, ALIGNS, ALIGN_ICONS };
  },
  template: `<div class="cg" ref="root">
    <div></div>
    <div v-for="c in 3" :key="'h' + c" class="cg-colhead" :class="{unused: !usedCols.includes(c)}">
      <span>Column {{ c }}</span>
      <div class="seg mini" v-if="usedCols.includes(c)"><button v-for="a in ALIGNS" :key="a.value" type="button" :class="{on: colAlign(c) === a.value}"
        @click="setColAlign(c, a.value)" :title="'Column ' + c + ': ' + a.label.toLowerCase()"><Icon :name="ALIGN_ICONS[a.value]" :size="12"/></button></div></div>
    <template v-for="l in 4" :key="'l' + l">
      <div class="cg-linehead">Line {{ l }}</div>
      <div v-for="c in 3" :key="c + '.' + l" class="cg-cell" :class="{filled: filled(c, l)}">
        <div class="cg-parts">
          <div class="cg-zone" :data-col="c" :data-line="l" data-side="left"><span v-for="k in zone(c, l, 'left')" :key="k" :data-k="k"
            class="cg-chip" :class="{live: k === 'live', shield: k === 'shield', blank: !hasText(k)}" :title="hasText(k) ? 'Drag to move' : 'Nothing to show yet'">{{ label(k) }}</span></div>
          <div class="cg-zone cg-right" :data-col="c" :data-line="l" data-side="right" title="Sections here sit at the line's right edge"><span
            v-for="k in zone(c, l, 'right')" :key="k" :data-k="k" class="cg-chip" :class="{live: k === 'live', shield: k === 'shield', blank: !hasText(k)}"
            :title="hasText(k) ? 'Drag to move' : 'Nothing to show yet'">{{ label(k) }}</span></div>
        </div>
        <select v-if="filled(c, l)" class="cg-size" :value="lineSize(c, l)" @change="setLineSize(c, l, $event.target.value)" title="Text size of this line">
          <option v-for="s in LINE_SIZES" :key="s.value" :value="s.value">{{ s.label }}</option></select>
      </div>
    </template>
  </div>`,
};

// --- editor -------------------------------------------------------------------------------------------------------
const WebcamEditor = {
  components: { CropEditor, WallPreview, CaptionGrid },
  setup() {
    seedWebcams();
    const id = computed(() => store.route.params[0]);
    const cam = computed(() => store.draft.modules.webcams.cams?.[id.value]);
    const shape = ref("full");
    const { status, feedFor } = useStatus();
    const zone = computed(() => status.value.zones[shape.value] || [448, 130]);
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
      if (!Array.isArray(wc.playlist)) wc.playlist = Object.keys(wc.cams).filter((k) => k !== id.value).map((cam) => ({ cam, sec: 60, on: true }));
      wc.playlist.push({ cam: id.value, sec: 60, on: true });
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
    const hasText = (k) => (WX_KEYS.includes(k) ? Number.isFinite(cam.value.lat) : { place: !!cam.value.place, credit: !!cam.value.credit,
      custom: !!ov.value.custom_text?.trim(), shield: /\d/.test(String(ov.value.shield?.route ?? "")) }[k] ?? true);
    const needsLocation = computed(() => WX_KEYS.some((k) => ov.value.show[k]) && !(Number.isFinite(cam.value.lat) && Number.isFinite(cam.value.lon)));
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
    return { id, cam, shape, zone, ov, colour, probe, testing, test, used, inList, listPos, addToList, capTab, copyCrop, boxMode, SECTION_GROUPS, TEXT_ALIGNS, play, remove, captionToAll, toggleShow, customCrop,
      orderOn, moveSection, setCoord, hasSource, STILL, needsLocation, galleryLoc, useGalleryLoc, hasText, customCols, SHIELD_PRESETS, SHIELD_KINDS, SHIELD_PLACES, SHIELD_SIZES, SHIELD_STYLES, shieldSrc, signMissing, presetShown, presetDone,
      CONTAINERS, feedFor, feedPill, SHAPES, LAYOUTS, FONTS, SECTIONS, HEADLINES, LINES, SEPS, POSITIONS, TIMEZONES, go };
  },
  template: `<div class="page" v-if="cam">
    <div class="page-head"><div class="grow">
      <a href="#/webcams" class="faint" style="font-size:12.5px"><Icon name="ArrowLeft" :size="13"/> Webcams</a>
      <h1>{{ cam.label || id }}</h1></div>
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
              <Field label="Name"><input class="input" v-model="cam.label"></Field>
              <Field label="Place" hint="Shown under the name"><input class="input" v-model="cam.place" placeholder="Tokyo, Japan"></Field>
              <Field label="Credit" hint="Who runs the camera"><input class="input" v-model="cam.credit" placeholder="FNN"></Field>
              <Field label="Time zone" hint="For the caption's local time"><input class="input" v-model.trim="cam.tz" list="wc-tz" placeholder="Asia/Tokyo">
                <datalist id="wc-tz"><option v-for="z in TIMEZONES" :key="z" :value="z"/></datalist></Field>
              <Field label="Frame rate" hint="Smoother costs the hub more" v-if="!STILL.includes(cam.source.type)"><Stepper v-model="cam.fps" :min="1" :max="30" unit=" fps"/></Field>
              <Field label="Fetch every" hint="How often the image is fetched again" v-if="cam.source.type === 'image'">
                <Stepper :modelValue="cam.source.refresh_sec || 60" @update:modelValue="v => cam.source.refresh_sec = v" :min="20" :max="3600" :step="10" unit=" s"/></Field>
              <Field label="Location" hint="Latitude, longitude: for the caption's weather and sunrise/sunset">
                <div class="row nowrap" style="gap:6px"><input class="input" style="width:50%" :value="cam.lat ?? ''" @change="setCoord('lat', $event, 90)" placeholder="49.2888">
                  <input class="input" style="width:50%" :value="cam.lon ?? ''" @change="setCoord('lon', $event, 180)" placeholder="-123.1111"></div></Field>
            </div>
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
              <span class="faint">shows for</span><Stepper :modelValue="inList.sec || 60" @update:modelValue="v => inList.sec = v" :min="5" :max="3600" :step="5" unit=" s"/>
              <Toggle :modelValue="inList.on !== false" @update:modelValue="v => inList.on = v" title="In the rotation"/></div>
            <div class="wc-rot" v-else><Icon name="ListOrdered" :size="15"/><span>Not in the Webcams list.</span><span class="spacer"></span>
              <button class="btn sm primary" @click="addToList"><Icon name="ListPlus" :size="14"/>Add to the list</button></div>
            <div class="faint" style="font-size:12px" v-if="used.length">Also its own rotation entry ({{ used.map(u => u.side).join(', ') }}):
              <a href="#/webcams">combine it into the list</a> on the Webcams page.</div>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="Captions" :size="16"/><h3>Caption</h3>
            <span class="spacer"></span><button class="btn sm ghost" @click="captionToAll" title="Copy this caption look to every webcam"><Icon name="Copy" :size="14"/>Use on all webcams</button></div>
          <div class="card-body stack">
            <div class="seg wc-captabs"><button v-for="t in [{v:'theme', l:'Theme', i:'Palette'}, {v:'content', l:'What it says', i:'Text'}, {v:'look', l:'Size & look', i:'Ruler'}, {v:'when', l:'When', i:'Clock'}]"
              :key="t.v" type="button" :class="{on: capTab === t.v}" @click="capTab = t.v" :disabled="t.v !== 'theme' && ov.style === 'none'"><Icon :name="t.i" :size="14"/>{{ t.l }}</button></div>
            <div v-if="capTab === 'theme'" class="wc-layouts"><button v-for="(l, k) in LAYOUTS" :key="k" type="button" class="wc-layout" :class="{on: ov.style === k}" @click="ov.style = k" :title="l.note">
              <Icon :name="l.icon" :size="15"/><b>{{ l.label }}</b><span class="faint">{{ l.note }}</span></button></div>
            <template v-if="ov.style !== 'none' && capTab === 'look'">
              <div class="row" style="gap:16px; align-items:flex-start">
                <Field label="Position"><div class="wc-corners wc-six">
                  <button v-for="c in POSITIONS" :key="c" type="button" :class="{on: ov.corner === c}" @click="ov.corner = c" :title="c"></button></div></Field>
                <Field label="Text size" v-if="ov.style !== 'dot'"><Seg v-model="ov.font" :options="FONTS"/></Field>
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
              <Field label="Headline" v-if="!['dot', 'custom'].includes(ov.style)"><Seg v-model="ov.headline" :options="HEADLINES"/></Field>
              <div v-if="ov.style !== 'dot'" class="stack" style="gap:8px">
                <Field v-for="g in SECTION_GROUPS" :key="g.label" :label="g.label"><div class="row" style="gap:6px">
                  <template v-for="k in g.keys" :key="k"><button v-if="k !== 'shield' || ov.style === 'custom'" type="button" class="chip" :class="{off: !ov.show[k]}"
                    style="height:26px; padding:0 10px" @click="toggleShow(k)">{{ SECTIONS[k] }}</button></template></div></Field></div>
            </template>
            <template v-if="ov.style !== 'none' && capTab === 'content'">
              <Field label="Highway shield" v-if="ov.style === 'custom' && ov.show.shield" hint="Pick a sign, or type a route; inside, the box grows to fit it">
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
              <template v-if="ov.style === 'custom'">
                <Field label="Arrangement" hint="Drag sections between columns and lines; drop one in a line's right end (→|) to push it to the right edge">
                  <CaptionGrid :ov="ov" :has-text="hasText"/></Field>
                <div class="row" style="gap:16px; align-items:flex-start">
                  <Field label="Container"><Seg v-model="ov.container" :options="CONTAINERS"/></Field>
                  <Field label="Divider" v-if="customCols(ov, hasText).length > 1"><Toggle :modelValue="ov.divider !== false" @update:modelValue="v => ov.divider = v"/></Field>
                </div>
              </template>
              <Field label="Image time" v-if="ov.show.age" hint="When the still was taken (DriveBC gives it to the second)">
                <Seg :modelValue="ov.taken_format || 'time'" @update:modelValue="v => ov.taken_format = v"
                     :options="[{value:'time', label:'taken 17:05:42'}, {value:'relative', label:'3 min ago'}, {value:'both', label:'Both'}]"/></Field>
              <Field label="Your text" v-if="ov.show.custom"><input class="input" v-model="ov.custom_text" maxlength="40" placeholder="Scramble Crossing, from the Starbucks"></Field>
              <div class="callout info" v-if="needsLocation" style="font-size:12.5px">
                <Icon name="MapPin" :size="14"/> The weather sections need this webcam's location (Source → Location).</div>
              <Field label="Order" hint="The details, top to bottom or left to right; the headline goes first" v-if="!['dot', 'custom'].includes(ov.style) && orderOn.length > 1">
                <div class="wc-order"><div v-for="(k, i) in orderOn" :key="k" class="wc-order-item">
                  <span class="mono faint">{{ i + 1 }}</span>{{ SECTIONS[k] }}<span class="spacer"></span>
                  <button class="btn sm ghost icon-only" @click="moveSection(k, -1)" :disabled="i === 0" title="Earlier"><Icon name="ChevronUp" :size="13"/></button>
                  <button class="btn sm ghost icon-only" @click="moveSection(k, 1)" :disabled="i === orderOn.length - 1" title="Later"><Icon name="ChevronDown" :size="13"/></button></div></div></Field>
              <div class="row" style="gap:16px; align-items:flex-start" v-if="ov.style !== 'dot'">
                <Field label="Details" v-if="ov.style !== 'custom'"><Seg :modelValue="ov.lines === 'wrap' ? 'auto' : ov.lines" @update:modelValue="v => ov.lines = v" :options="LINES"/></Field>
                <Field label="Separator"><Seg v-model="ov.separator" :options="SEPS"/></Field>
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
            <div class="faint" style="font-size:12px" v-if="capTab === 'content'">When space runs out, sections drop in this order before anything is cut: credit, your text,
              humidity, wind, high/low, feels like, conditions, sunrise/sunset, time difference, date, temperature, place.</div>
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
