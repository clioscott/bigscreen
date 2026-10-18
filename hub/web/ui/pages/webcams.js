// Webcams: live video sources (hub/webcams.py decodes them), how each is cropped for every place on the wall,
// its colour and its caption, and adding it to the rotation (panels webcam / webcam_full / webcam_wall).
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, seed, go, clone, dirty, previewOnDisplay, playOnDisplay, rotationAction } from "../core.js";
import { GALLERY, snap, thumb } from "./webcam_gallery.js";

export const SHAPES = {
  left: { label: "Left side", panel: "webcam", side: "left", icon: "PanelLeft" },
  right: { label: "Right side", panel: "webcam", side: "right", icon: "PanelRight" },
  full: { label: "Full width", panel: "webcam_full", side: "right", icon: "RectangleHorizontal", note: "ticker and clock stay" },
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
  custom: { label: "Custom", icon: "LayoutGrid", note: "Your own columns and lines" },
  outline: { label: "Outlined", icon: "Type", note: "Text with a black edge, no box" },
  dot: { label: "LIVE dot", icon: "CircleDot", note: "Just a blinking dot and the time" },
  none: { label: "None", icon: "EyeOff", note: "No caption" },
};
const FONTS = [{ value: "tiny", label: "Tiny" }, { value: "small", label: "Small" }, { value: "medium", label: "Medium" }, { value: "large", label: "Large" }];
const SECTIONS = { name: "Name", place: "Place", time: "Local time", weather: "Weather", sun: "Sunrise / sunset", date: "Date",
  offset: "Time difference", daynight: "Day / night icon", live: "LIVE badge", credit: "Credit", custom: "Your text" };
const ORDERABLE = ["live", "name", "place", "time", "weather", "date", "offset", "sun", "custom", "credit"];
// the Custom layout: where each section goes (column, line) to start with; mirrors webcam_caption.DEFAULT_SLOTS
const DEFAULT_SLOTS = { live: [1, 1], name: [1, 1], place: [1, 2], time: [1, 2], weather: [1, 3], date: [1, 3], offset: [1, 3],
  sun: [1, 3], custom: [1, 3], credit: [1, 3] };
const CONTAINERS = [{ value: "box", label: "Box" }, { value: "strip", label: "Full-width strip" }, { value: "outline", label: "Outlined" }, { value: "board", label: "Board" }];
const LINE_SIZES = [{ value: "headline", label: "Headline" }, { value: "detail", label: "Detail" }, { value: "big", label: "Big" }];
const ALIGNS = [{ value: "left", label: "Left" }, { value: "center", label: "Centre" }, { value: "right", label: "Right" }];
const HEADLINES = [{ value: "name", label: "Name" }, { value: "place", label: "Place" }, { value: "custom", label: "Your text" }, { value: "time", label: "Time" }];
const LINES = [{ value: "auto", label: "Fit (wraps if needed)" }, { value: "stacked", label: "One per line" }, { value: "inline", label: "Beside the headline" }];
const SEPS = [{ value: "dot", label: "·" }, { value: "slash", label: "/" }, { value: "pipe", label: "|" }, { value: "dash", label: "-" }, { value: "space", label: "space" }];
const POSITIONS = ["tl", "tc", "tr", "bl", "bc", "br"];
const DEFAULT_OVERLAY = { style: "tag", corner: "bl", when: "always", font: "small", accent: "#e8202a", text: "#ffffff",
  bg: "#000000", opacity: 0.7, time_format: "24h", fade: true, show_sec: 12, every_min: 3, custom_text: "", headline: "name",
  lines: "auto", separator: "dot", uppercase: false, order: [...ORDERABLE],
  show: { name: true, place: true, time: true, date: false, offset: false, daynight: false, weather: false, sun: false,
    live: true, credit: false, custom: false } };
const TIMEZONES = ["America/Vancouver", "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York", "America/Halifax",
  "Pacific/Honolulu", "America/Anchorage", "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Rome", "Atlantic/Reykjavik",
  "Africa/Johannesburg", "Africa/Windhoek", "Asia/Tokyo", "Asia/Seoul", "Asia/Hong_Kong", "Asia/Singapore", "Australia/Sydney", "Pacific/Auckland"];

const slug = (s) => (s || "webcam").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 32) || "webcam";
const host = (u) => { try { return new URL(u).host.replace(/^www\./, ""); } catch { return u || ""; } };
const qs = (o) => Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
export const camEntry = (id, shape) => ({ panel: SHAPES[shape].panel, cam: id, rules: [], dwell_sec: 60 });

function useStatus() {
  const status = ref({ feeds: [], zones: { left: [295, 130], right: [152, 130], full: [448, 130], wall: [448, 192] } });
  let t;
  const load = async () => { try { status.value = await api("/api/webcams/status"); } catch {} };
  onMounted(() => { load(); t = setInterval(load, 3000); });
  onBeforeUnmount(() => clearInterval(t));
  const feedFor = (cam) => status.value.feeds.find((f) => f.url === cam?.source?.url && f.type === (cam?.source?.type || "stream"));
  return { status, feedFor };
}
function feedPill(f) {
  if (!f) return { cls: "", text: "idle" };
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
  props: { source: Object },
  setup(props) {
    const url = ref("");
    const state = ref("loading");
    let alive = true, timer = null, blobUrl = "";
    const load = async (tries = 0) => {
      if (!alive || !props.source?.url) return;
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
      const id = props.source.type === "youtube" ? youtubeId(props.source.url) : "";
      if (id) { url.value = thumb({ id }); state.value = "ok"; } else state.value = "none";
    };
    onMounted(() => load());
    watch(() => props.source?.url, () => { clearTimeout(timer); state.value = "loading"; url.value = ""; load(); });
    onBeforeUnmount(() => { alive = false; clearTimeout(timer); if (blobUrl) URL.revokeObjectURL(blobUrl); });
    return { url, state };
  },
  template: `<img v-if="url" :src="url" alt=""><span v-else class="wc-snap-wait">{{ state === 'loading' ? 'live frame…' : '' }}</span>`,
};

// --- list ---------------------------------------------------------------------------------------------------
const WebcamList = {
  components: { SnapImg },
  setup() {
    const wc = seed(["modules", "webcams"], { cams: {} });
    const cams = computed(() => Object.entries(store.draft.modules.webcams.cams || {}));
    const { feedFor } = useStatus();
    const add = (from, open = true) => {
      const all = store.draft.modules.webcams.cams || (store.draft.modules.webcams.cams = {});
      const f = from || { label: "New webcam", source: { type: "youtube", url: "" } };
      const base = clone({ label: f.label, place: f.place || "", credit: f.credit || "", tz: f.tz || "", source: f.source,
        ...(f.lat != null ? { lat: f.lat, lon: f.lon } : {}) });
      let id = slug(base.label), n = 2;
      while (all[id]) id = `${slug(base.label)}_${n++}`;
      all[id] = { fps: 15, ...base, overlay: clone(DEFAULT_OVERLAY), crops: {} };
      if (open) go(`webcams/${id}`);
      return id;
    };
    // the gallery: ready-made webcams; one you already have opens its page instead
    const haveId = (c) => cams.value.find(([, x]) => x.source?.url === c.source.url)?.[0];
    const pick = (c) => {
      const have = haveId(c);
      if (have) return go(`webcams/${have}`);
      add(c, false);
      toast(`Added ${c.label}. Save to keep it; open it to crop it or add it to the rotation.`);
    };
    const usedText = (id) => {
      const u = usedIn(id);
      return u.length ? `in the rotation ×${u.length}` : "not in the rotation";
    };
    return { wc, cams, feedFor, feedPill, add, host, usedText, go, GALLERY, haveId, pick };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Webcams</h1>
      <p>Live video on the wall. The hub decodes each stream (the Pi only shows finished frames), crops it for the place it plays and
        adds a caption. Add webcams here, then put them in the rotation like any other screen.</p></div>
      <button class="btn primary" @click="add()"><Icon name="Plus" :size="15"/>Add webcam</button></div>
    <div class="card">
      <div class="list-row" v-for="[id, cam] in cams" :key="id" style="cursor:pointer" @click="go('webcams/' + id)">
        <span class="wc-row-thumb"><SnapImg v-if="cam.source?.url" :source="cam.source"/></span>
        <div class="grow" style="min-width:0"><b>{{ cam.label || id }}</b>
          <div class="faint ellipsis" style="font-size:12.5px">{{ [cam.place, host(cam.source?.url), usedText(id)].filter(Boolean).join(' · ') }}</div></div>
        <span class="pill" :class="feedPill(feedFor(cam)).cls">{{ feedPill(feedFor(cam)).text }}</span>
        <button class="btn sm ghost icon-only" title="Edit"><Icon name="Pencil" :size="14"/></button>
      </div>
      <div class="empty" v-if="!cams.length">No webcams yet.</div>
    </div>
    <div class="card" style="margin-top:16px" v-for="g in GALLERY" :key="g.group">
      <div class="card-head"><Icon :name="g.icon" :size="16"/><h3>{{ g.group }}</h3><span class="faint" style="font-size:12px">live now · click to add</span></div>
      <div class="card-body wc-gallery">
        <button v-for="c in g.cams" :key="c.id" type="button" class="wc-pick" :class="{have: haveId(c)}" @click="pick(c)" :title="c.note">
          <span class="wc-thumb"><SnapImg :source="c.source"/><span class="live">LIVE</span>
            <span class="wc-have" v-if="haveId(c)"><Icon name="Check" :size="13"/>added</span></span>
          <b>{{ c.label }}</b><span class="faint">{{ c.place }}</span><span class="faint wc-note">{{ c.note }}</span>
        </button>
      </div></div>
    <div class="faint" style="font-size:12.5px; margin-top:10px">Or use Add webcam with any YouTube live page, or an HLS / MJPEG / RTSP stream URL.</div>
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
    const src = computed(() => props.cam.source?.url ? `/api/webcams/source.mjpeg?${qs({ type: props.cam.source.type || "stream", url: props.cam.source.url, fps: props.cam.fps || 15 })}` : "");
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
      if (!props.cam.source?.url) { token.value = ""; return; }
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
  components: { CropEditor, WallPreview },
  setup() {
    seed(["modules", "webcams"], { cams: {} });
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
    const addToRotation = () => {
      const s = SHAPES[shape.value];
      store.draft.modules.layout_cycle[s.side].push(camEntry(id.value, shape.value));
      toast(`Added to the ${s.side} rotation (${s.label.toLowerCase()}, 60 s). Save to keep it; Rotation has the rules.`);
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
      go("webcams");
    };
    const captionToAll = () => {
      for (const c of Object.values(store.draft.modules.webcams.cams)) if (c !== cam.value) c.overlay = clone(ov.value);
      toast("Every webcam now uses this caption (save to keep it)");
    };
    const toggleShow = (k) => { ov.value.show[k] = !ov.value.show[k]; };
    // the detail sections that are on, in their order (drag to reorder; the headline isn't a detail)
    const orderOn = computed(() => (ov.value.order || []).filter((k) => k !== "live" && ov.value.show[k] && k !== (ov.value.headline || "name")));
    // --- the Custom layout: column and line for each section, order within a line, line sizes, column alignment ---
    const slotOf = (k) => (ov.value.slots?.[k] || DEFAULT_SLOTS[k] || [1, 3]);
    const setSlot = (k, i, v) => {
      const slots = ov.value.slots || (ov.value.slots = {});
      const cur = [...slotOf(k)];
      cur[i] = v;
      slots[k] = cur;
    };
    // sections that will actually have text on the wall (the renderer skips empty ones)
    const hasText = (k) => ({ place: !!cam.value.place, credit: !!cam.value.credit, custom: !!ov.value.custom_text?.trim(),
      weather: Number.isFinite(cam.value.lat), sun: Number.isFinite(cam.value.lat) }[k] ?? true);
    const needsLocation = computed(() => (ov.value.show.weather || ov.value.show.sun) && !(Number.isFinite(cam.value.lat) && Number.isFinite(cam.value.lon)));
    const galleryLoc = computed(() => GALLERY.flatMap((g) => g.cams).find((g) => g.source.url === cam.value.source?.url && g.lat != null) || null);
    const useGalleryLoc = () => { cam.value.lat = galleryLoc.value.lat; cam.value.lon = galleryLoc.value.lon; };
    const customRows = computed(() => (ov.value.order || []).filter((k) => ov.value.show[k] && hasText(k))
      .map((k) => ({ k, col: slotOf(k)[0], line: slotOf(k)[1] }))
      .sort((a, b) => a.col - b.col || a.line - b.line));
    const moveInLine = (k, d) => {                        // swap with the neighbour on the same column and line
      const same = customRows.value.filter((r) => r.col === slotOf(k)[0] && r.line === slotOf(k)[1]).map((r) => r.k);
      const i = same.indexOf(k), j = i + d;
      if (j < 0 || j >= same.length) return;
      const o = ov.value.order, a = o.indexOf(k), b = o.indexOf(same[j]);
      [o[a], o[b]] = [o[b], o[a]];
    };
    const sameLine = (k) => customRows.value.filter((r) => r.col === slotOf(k)[0] && r.line === slotOf(k)[1]).map((r) => r.k);
    const usedLines = computed(() => [...new Set(customRows.value.map((r) => `${r.col}.${r.line}`))]);
    const usedCols = computed(() => [...new Set(customRows.value.map((r) => r.col))].sort());
    const lineSize = (key) => ov.value.line_sizes?.[key] || (key === `${usedCols.value[0] || 1}.1` ? "headline" : "detail");
    const setLineSize = (key, v) => { (ov.value.line_sizes || (ov.value.line_sizes = {}))[key] = v; };
    const colAlign = (c) => ov.value.columns?.[c - 1]?.align || (usedCols.value.length > 1 && c === usedCols.value.at(-1) ? "right" : "left");
    const setColAlign = (c, v) => {
      const cols = ov.value.columns || (ov.value.columns = []);
      while (cols.length < c) cols.push({});
      cols[c - 1] = { ...cols[c - 1], align: v };
    };
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
    return { id, cam, shape, zone, ov, colour, probe, testing, test, used, addToRotation, play, remove, captionToAll, toggleShow, customCrop,
      orderOn, moveSection, setCoord, needsLocation, galleryLoc, useGalleryLoc, customRows, setSlot, moveInLine, sameLine, usedLines, usedCols, lineSize, setLineSize, colAlign,
      setColAlign, CONTAINERS, LINE_SIZES, ALIGNS, feedFor, feedPill, SHAPES, LAYOUTS, FONTS, SECTIONS, HEADLINES, LINES, SEPS, POSITIONS, TIMEZONES, go };
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
            <Seg v-model="cam.source.type" :options="[{value:'youtube', label:'YouTube live', icon:'Youtube'}, {value:'stream', label:'Stream URL', icon:'Radio'}]"/>
            <div class="row nowrap">
              <input class="input grow" v-model.lazy.trim="cam.source.url" :placeholder="cam.source.type === 'youtube' ? 'https://www.youtube.com/watch?v=…  or  …/@channel/live' : 'https://…/index.m3u8, an MJPEG URL, rtsp://…'">
              <button class="btn" :disabled="testing || !cam.source.url" @click="test"><Icon :name="testing ? 'LoaderCircle' : 'PlugZap'" :size="14" :class="{spin: testing}"/>Test</button></div>
            <div v-if="probe" class="callout" :class="probe.ok ? 'ok' : 'bad'" style="font-size:12.5px">
              <Icon :name="probe.ok ? 'CircleCheck' : 'CircleX'" :size="14"/> {{ probe.ok ? (probe.title ? 'Found: ' + probe.title : 'The hub can open it') : probe.error }}</div>
            <div class="fields">
              <Field label="Name"><input class="input" v-model="cam.label"></Field>
              <Field label="Place" hint="Shown under the name"><input class="input" v-model="cam.place" placeholder="Tokyo, Japan"></Field>
              <Field label="Credit" hint="Who runs the camera"><input class="input" v-model="cam.credit" placeholder="FNN"></Field>
              <Field label="Time zone" hint="For the caption's local time"><input class="input" v-model.trim="cam.tz" list="wc-tz" placeholder="Asia/Tokyo">
                <datalist id="wc-tz"><option v-for="z in TIMEZONES" :key="z" :value="z"/></datalist></Field>
              <Field label="Frame rate" hint="Smoother costs the hub more"><Stepper v-model="cam.fps" :min="1" :max="30" unit=" fps"/></Field>
              <Field label="Location" hint="Latitude, longitude: for the caption's weather and sunrise/sunset">
                <div class="row nowrap" style="gap:6px"><input class="input" style="width:50%" :value="cam.lat ?? ''" @change="setCoord('lat', $event, 90)" placeholder="49.2888">
                  <input class="input" style="width:50%" :value="cam.lon ?? ''" @change="setCoord('lon', $event, 180)" placeholder="-123.1111"></div></Field>
            </div>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="Crop" :size="16"/><h3>Picture</h3></div>
          <div class="card-body stack">
            <div class="seg"><button v-for="(s, k) in SHAPES" :key="k" type="button" :class="{on: shape === k}" @click="shape = k">
              <Icon :name="s.icon" :size="14"/>{{ s.label }}<span v-if="customCrop(k)" class="faint" title="Has its own crop">•</span></button></div>
            <CropEditor :cam="cam" :shape="shape" :zone="zone"/>
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
              <span class="spacer"></span>
              <button class="btn sm primary" @click="addToRotation"><Icon name="ListPlus" :size="14"/>Add to the rotation</button></div>
            <div class="faint" style="font-size:12.5px">
              <template v-if="used.length">In the rotation: <span v-for="(u, k) in used" :key="k" class="pill" style="margin-right:4px">{{ u.side }} · {{ SHAPES[u.e.panel === 'webcam' ? u.side : u.e.panel === 'webcam_full' ? 'full' : 'wall'].label.toLowerCase() }} · {{ u.e.dwell_sec || 30 }} s</span>
                <a href="#/rotation">rules and timing</a></template>
              <template v-else>Not in the rotation yet.</template></div>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="Captions" :size="16"/><h3>Caption</h3>
            <span class="spacer"></span><button class="btn sm ghost" @click="captionToAll" title="Copy this caption look to every webcam"><Icon name="Copy" :size="14"/>Use on all webcams</button></div>
          <div class="card-body stack">
            <div class="wc-layouts"><button v-for="(l, k) in LAYOUTS" :key="k" type="button" class="wc-layout" :class="{on: ov.style === k}" @click="ov.style = k" :title="l.note">
              <Icon :name="l.icon" :size="15"/><b>{{ l.label }}</b><span class="faint">{{ l.note }}</span></button></div>
            <template v-if="ov.style !== 'none'">
              <div class="row" style="gap:16px; align-items:flex-start">
                <Field label="Position"><div class="wc-corners wc-six">
                  <button v-for="c in POSITIONS" :key="c" type="button" :class="{on: ov.corner === c}" @click="ov.corner = c" :title="c"></button></div></Field>
                <Field label="Headline" v-if="!['dot', 'custom'].includes(ov.style)"><Seg v-model="ov.headline" :options="HEADLINES"/></Field>
                <Field label="Text size" v-if="ov.style !== 'dot'"><Seg v-model="ov.font" :options="FONTS"/></Field>
              </div>
              <Field label="Sections" hint="Click to turn on or off" v-if="ov.style !== 'dot'"><div class="row" style="gap:6px">
                <button v-for="(l, k) in SECTIONS" :key="k" type="button" class="chip" :class="{off: !ov.show[k]}" style="height:26px; padding:0 10px" @click="toggleShow(k)">{{ l }}</button></div></Field>
              <template v-if="ov.style === 'custom'">
                <Field label="Arrangement" hint="Pick a column and line for each section; ↑↓ order sections that share a line">
                  <table class="wc-arrange"><thead><tr><th>Section</th><th>Column</th><th>Line</th><th></th></tr></thead><tbody>
                    <tr v-for="r in customRows" :key="r.k"><td>{{ SECTIONS[r.k] }}</td>
                      <td><div class="seg mini"><button v-for="c in 3" :key="c" type="button" :class="{on: r.col === c}" @click="setSlot(r.k, 0, c)">{{ c }}</button></div></td>
                      <td><div class="seg mini"><button v-for="l in 4" :key="l" type="button" :class="{on: r.line === l}" @click="setSlot(r.k, 1, l)">{{ l }}</button></div></td>
                      <td class="nowrap"><template v-if="sameLine(r.k).length > 1">
                        <button class="btn sm ghost icon-only" @click="moveInLine(r.k, -1)" :disabled="sameLine(r.k)[0] === r.k" title="Earlier in the line"><Icon name="ChevronLeft" :size="13"/></button>
                        <button class="btn sm ghost icon-only" @click="moveInLine(r.k, 1)" :disabled="sameLine(r.k).at(-1) === r.k" title="Later in the line"><Icon name="ChevronRight" :size="13"/></button></template></td></tr>
                  </tbody></table></Field>
                <Field label="Line sizes"><div class="stack" style="gap:4px">
                  <div v-for="key in usedLines" :key="key" class="row nowrap" style="gap:8px"><span class="mono faint" style="width:90px">col {{ key.split('.')[0] }} · line {{ key.split('.')[1] }}</span>
                    <Seg :modelValue="lineSize(key)" @update:modelValue="v => setLineSize(key, v)" :options="LINE_SIZES"/></div></div></Field>
                <div class="row" style="gap:16px; align-items:flex-start">
                  <Field label="Container"><Seg v-model="ov.container" :options="CONTAINERS"/></Field>
                  <Field v-for="c in usedCols" :key="c" :label="'Column ' + c"><Seg :modelValue="colAlign(c)" @update:modelValue="v => setColAlign(c, v)" :options="ALIGNS"/></Field>
                  <Field label="Divider" v-if="usedCols.length > 1"><Toggle :modelValue="ov.divider !== false" @update:modelValue="v => ov.divider = v"/></Field>
                </div>
              </template>
              <Field label="Your text" v-if="ov.show.custom"><input class="input" v-model="ov.custom_text" maxlength="40" placeholder="Scramble Crossing, from the Starbucks"></Field>
              <div class="callout info" v-if="(ov.show.weather || ov.show.sun) && !(Number.isFinite(cam.lat) && Number.isFinite(cam.lon))" style="font-size:12.5px">
                <Icon name="MapPin" :size="14"/> Weather and sunrise/sunset need this webcam's location (Source → Location).</div>
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
              <div class="row" style="gap:16px; align-items:flex-start">
                <Field label="Accent"><ColorPick v-model="ov.accent"/></Field>
                <Field label="Text" v-if="ov.style !== 'dot'"><ColorPick v-model="ov.text"/></Field>
                <Field label="Background"><ColorPick v-model="ov.bg"/></Field>
                <Field label="Background strength"><div style="width:150px"><Slider v-model="ov.opacity" :min="0" :max="1" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></div></Field>
              </div>
              <ToggleRow v-if="ov.style !== 'dot'" v-model="ov.uppercase" label="ALL CAPS"/>
              <div class="row" style="gap:16px; align-items:flex-start">
                <Field label="Shows"><Seg v-model="ov.when" :options="[{value:'always', label:'Always'}, {value:'intro', label:'First 10 s'}, {value:'periodic', label:'Every few minutes'}]"/></Field>
                <template v-if="ov.when === 'periodic'">
                  <Field label="For"><Stepper v-model="ov.show_sec" :min="3" :max="60" unit=" s"/></Field>
                  <Field label="Every"><Stepper v-model="ov.every_min" :min="1" :max="30" unit=" min"/></Field></template>
                <Field label="Fade" v-if="ov.when !== 'always'"><Toggle v-model="ov.fade"/></Field>
              </div>
            </template>
            <div class="faint" style="font-size:12px">When space runs out, sections drop in this order before anything is cut: credit, your text,
              sunrise/sunset, time difference, date, weather, place.</div>
          </div></div>
      </div>
    </div>
  </div>
  <div class="page" v-else><div class="empty">No webcam “{{ id }}”. <a href="#/webcams">Back to webcams</a></div></div>`,
};

export const Webcams = {
  components: { WebcamList, WebcamEditor },
  setup() { return { store }; },
  template: `<WebcamEditor v-if="store.route.params[0]"/><WebcamList v-else/>`,
};
