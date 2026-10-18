// Webcams: live video sources (hub/webcams.py decodes them), how each is cropped for every place on the wall,
// its colour and its caption, and adding it to the rotation (panels webcam / webcam_full / webcam_wall).
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, seed, go, clone, dirty, previewOnDisplay, playOnDisplay } from "../core.js";

export const SHAPES = {
  left: { label: "Left side", panel: "webcam", side: "left", icon: "PanelLeft" },
  right: { label: "Right side", panel: "webcam", side: "right", icon: "PanelRight" },
  full: { label: "Full width", panel: "webcam_full", side: "right", icon: "RectangleHorizontal", note: "ticker and clock stay" },
  wall: { label: "Whole wall", panel: "webcam_wall", side: "right", icon: "Maximize", note: "covers the ticker and clock" },
};
const STYLES = [
  { value: "tag", label: "Tag", icon: "Tag" }, { value: "bar", label: "Bar", icon: "PanelBottom" },
  { value: "outline", label: "Outlined", icon: "Type" }, { value: "dot", label: "LIVE dot", icon: "CircleDot" },
  { value: "none", label: "None", icon: "EyeOff" },
];
const FONTS = [{ value: "tiny", label: "Tiny" }, { value: "small", label: "Small" }, { value: "medium", label: "Medium" }, { value: "large", label: "Large" }];
const SHOW = { name: "Name", place: "Place", time: "Local time", live: "LIVE badge", credit: "Credit" };
const DEFAULT_OVERLAY = { style: "tag", corner: "bl", when: "always", font: "small", accent: "#e8202a", text: "#ffffff",
  show: { name: true, place: true, time: true, live: true, credit: false } };
const TIMEZONES = ["America/Vancouver", "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York", "America/Halifax",
  "Pacific/Honolulu", "America/Anchorage", "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Rome", "Atlantic/Reykjavik",
  "Africa/Johannesburg", "Africa/Windhoek", "Asia/Tokyo", "Asia/Seoul", "Asia/Hong_Kong", "Asia/Singapore", "Australia/Sydney", "Pacific/Auckland"];
// a few starting points; anything yt-dlp or ffmpeg can open works
const SUGGESTIONS = [
  { label: "Shibuya Crossing", place: "Tokyo, Japan", tz: "Asia/Tokyo", credit: "FNN",
    source: { type: "youtube", url: "https://www.youtube.com/watch?v=dfVK7ld38Ys" } },
  { label: "Canada Place", place: "Vancouver", tz: "America/Vancouver", credit: "Vancouver City Views",
    source: { type: "youtube", url: "https://www.youtube.com/@VancouverCityViews/live" } },
];

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

// --- list ---------------------------------------------------------------------------------------------------
const WebcamList = {
  setup() {
    const wc = seed(["modules", "webcams"], { cams: {} });
    const cams = computed(() => Object.entries(store.draft.modules.webcams.cams || {}));
    const { feedFor } = useStatus();
    const add = (from) => {
      const all = store.draft.modules.webcams.cams || (store.draft.modules.webcams.cams = {});
      const base = clone(from || { label: "New webcam", source: { type: "youtube", url: "" } });
      let id = slug(base.label), n = 2;
      while (all[id]) id = `${slug(base.label)}_${n++}`;
      all[id] = { fps: 15, tz: "", place: "", credit: "", ...base, overlay: clone(DEFAULT_OVERLAY), crops: {} };
      go(`webcams/${id}`);
    };
    const usedText = (id) => {
      const u = usedIn(id);
      return u.length ? `in the rotation ×${u.length}` : "not in the rotation";
    };
    return { wc, cams, feedFor, feedPill, add, host, usedText, SUGGESTIONS, go };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Webcams</h1>
      <p>Live video on the wall. The hub decodes each stream (the Pi only shows finished frames), crops it for the place it plays and
        adds a caption. Add webcams here, then put them in the rotation like any other screen.</p></div>
      <button class="btn primary" @click="add()"><Icon name="Plus" :size="15"/>Add webcam</button></div>
    <div class="card">
      <div class="list-row" v-for="[id, cam] in cams" :key="id" style="cursor:pointer" @click="go('webcams/' + id)">
        <Icon name="Video" :size="18" style="color:var(--accent)"/>
        <div class="grow" style="min-width:0"><b>{{ cam.label || id }}</b>
          <div class="faint ellipsis" style="font-size:12.5px">{{ [cam.place, host(cam.source?.url), usedText(id)].filter(Boolean).join(' · ') }}</div></div>
        <span class="pill" :class="feedPill(feedFor(cam)).cls">{{ feedPill(feedFor(cam)).text }}</span>
        <button class="btn sm ghost icon-only" title="Edit"><Icon name="Pencil" :size="14"/></button>
      </div>
      <div class="empty" v-if="!cams.length">No webcams yet.</div>
    </div>
    <div class="card" style="margin-top:16px"><div class="card-head"><Icon name="Sparkles" :size="16"/><h3>Ideas to start from</h3></div>
      <div class="card-body row" style="gap:8px">
        <button class="btn sm" v-for="s in SUGGESTIONS" :key="s.label" @click="add(s)"><Icon name="Plus" :size="14"/>{{ s.label }}<span class="faint">· {{ s.place }}</span></button>
        <span class="faint" style="font-size:12.5px">Or paste any YouTube live page, or an HLS / MJPEG / RTSP stream URL.</span>
      </div></div>
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
    onBeforeUnmount(() => clearInterval(t));
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
  props: { cam: Object, shape: String },
  setup(props) {
    const token = ref("");
    const zone = ref([448, 130]);
    const err = ref("");
    let pending = null, timer = null, lastSrc = "";
    const send = async () => {
      timer = null;
      const srcKey = JSON.stringify([props.cam.source, props.cam.fps]);
      if (!props.cam.source?.url) { token.value = ""; return; }
      const fresh = srcKey !== lastSrc;
      lastSrc = srcKey;
      try {
        const r = await api("/api/webcams/preview", { json: { token: fresh ? undefined : token.value || undefined, cam: clone(props.cam), shape: props.shape } });
        zone.value = r.zone;
        if (r.token !== token.value) token.value = r.token;
        err.value = "";
      } catch (e) { err.value = e.message; }
    };
    const queue = () => { if (!timer) timer = setTimeout(send, 150); };
    watch(() => [props.cam, props.shape], queue, { deep: true, immediate: true });
    onBeforeUnmount(() => clearTimeout(timer));
    const src = computed(() => token.value ? `/api/webcams/preview/${token.value}.mjpeg` : "");
    return { src, zone, err };
  },
  template: `<div class="led" :style="{maxWidth: Math.min(zone[0] * 3 + 22, 1366) + 'px'}">
    <img v-if="src" :src="src" :style="{aspectRatio: zone[0] + ' / ' + zone[1]}" alt="">
    <div v-else class="placeholder">{{ err || 'No source yet' }}</div></div>`,
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
    // fill in fields older or hand-written webcams lack, in saved and draft alike (opening one isn't a change)
    for (const root of [store.saved, store.draft]) {
      const c = root?.modules?.webcams?.cams?.[id.value];
      if (!c) continue;
      c.source = c.source || { type: "youtube", url: "" };
      c.overlay = { ...clone(DEFAULT_OVERLAY), ...(c.overlay || {}) };
      c.overlay.show = { ...DEFAULT_OVERLAY.show, ...(c.overlay.show || {}) };
      c.colour = c.colour || {};
      c.crops = c.crops || {};
    }
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
      if (dirty.value.length) await previewOnDisplay();          // so the wall shows this crop and caption, not the saved ones
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
    const customCrop = (s) => !!cam.value.crops?.[s];
    return { id, cam, shape, zone, ov, colour, probe, testing, test, used, addToRotation, play, remove, captionToAll, toggleShow, customCrop,
      feedFor, feedPill, SHAPES, STYLES, FONTS, SHOW, TIMEZONES, go };
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
              <input class="input grow" v-model.trim="cam.source.url" :placeholder="cam.source.type === 'youtube' ? 'https://www.youtube.com/watch?v=…  or  …/@channel/live' : 'https://…/index.m3u8, an MJPEG URL, rtsp://…'">
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
            <WallPreview :cam="cam" :shape="shape"/>
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
            <Seg v-model="ov.style" :options="STYLES"/>
            <template v-if="ov.style !== 'none'">
              <div class="row" style="gap:16px; align-items:flex-start">
                <Field label="Corner"><div class="wc-corners">
                  <button v-for="c in ['tl','tr','bl','br']" :key="c" type="button" :class="{on: ov.corner === c}" @click="ov.corner = c" :title="c"></button></div></Field>
                <Field label="Text size" v-if="ov.style !== 'dot'"><Seg v-model="ov.font" :options="FONTS"/></Field>
                <Field label="Shows"><Seg v-model="ov.when" :options="[{value:'always', label:'Always'}, {value:'intro', label:'First 10 s'}]"/></Field>
              </div>
              <Field label="What it says" v-if="ov.style !== 'dot'"><div class="row" style="gap:6px">
                <button v-for="(l, k) in SHOW" :key="k" type="button" class="chip" :class="{off: !ov.show[k]}" style="height:26px; padding:0 10px" @click="toggleShow(k)">{{ l }}</button></div></Field>
              <div class="row" style="gap:16px">
                <Field label="Accent"><ColorPick v-model="ov.accent"/></Field>
                <Field label="Text" v-if="ov.style !== 'dot'"><ColorPick v-model="ov.text"/></Field>
              </div>
            </template>
            <div class="faint" style="font-size:12px">Long lines drop the credit, then the place, before anything is cut.</div>
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
