// The Space Map (modules.iss_map; display/modules/iss_panel.py + issmap.py): the ISS's world map with its links, other
// satellites, labels, and panels of data anywhere along the edges. Above the clock only (448×175): it's dense.
// Everything here is the map's own; the ISS webcam's caption and its fallback map keep their settings on its page.
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { store, api, seed, clone, dirty, previewOnDisplay, playOnDisplay, toast } from "../core.js";
import { CaptionRows } from "./caption_rows.js";
import { InRotation } from "./webcams.js";

const SHAPE = "tall";
const SHAPES = { tall: "iss_map_tall" };
const ENTRY = () => ({ panel: "iss_map_tall", rules: [], dwell_sec: 60 });
const POS_NAMES = { tl: "Top left", tc: "Top middle", tr: "Top right", ml: "Middle left", mc: "Middle", mr: "Middle right",
  bl: "Bottom left", bc: "Bottom middle", br: "Bottom right" };
const PIECES = ["live", "place", "coords", "orbit", "below", "crew", "mission", "custom"];
const SECTIONS = { name: "Name", live: "LIVE badge", custom: "Your text", place: "What it's over", over: "What it's over",
  coords: "Coordinates (live)", orbit: "Next sunrise / sunset", below: "Local time below", crew: "People aboard", mission: "Mission" };
const CAPTION_CORNERS = ["tl", "tc", "tr", "bl", "bc", "br"];
const FONTS = [{ value: "tiny", label: "Tiny" }, { value: "small", label: "Small" }, { value: "medium", label: "Medium" }, { value: "large", label: "Large" }];
const TABS = [{ v: "panels", l: "Panels", i: "LayoutPanelTop" }, { v: "links", l: "Links", i: "Radio" }, { v: "labels", l: "Labels & flags", i: "Tag" },
  { v: "sats", l: "Satellites", i: "Satellite" }, { v: "layers", l: "Map layers", i: "Layers" }, { v: "view", l: "View", i: "ZoomIn" },
  { v: "caption", l: "Caption", i: "Captions" }, { v: "units", l: "Units", i: "Ruler" }];
const LAYER_ITEMS = [
  { k: "lights", title: "City lights", note: "NASA Black Marble on the night side", set: [["strength", "slider", 0.1, 1, 0.05, "%"]] },
  { k: "aurora", title: "Aurora forecast", note: "NOAA's live oval on the night side", set: [["strength", "slider", 0.1, 1, 0.05, "%"]] },
  { k: "night", title: "Night shading", note: "the dark side of the Earth", set: [["darkness", "slider", 0, 0.95, 0.05, "%"]] },
  { k: "terminator", title: "Day/night line", note: "a thin line where the Sun is on the horizon" },
  { k: "borders", title: "Country borders", note: "grey lines between countries" },
  { k: "grid", title: "Latitude/longitude grid", note: "faint lines; or just the equator, tropics and polar circles",
    set: [["step", "step", 10, 90, 10, "°"], ["lines", "seg", [["grid", "Grid"], ["special", "Equator & tropics"], ["both", "Both"]]]] },
  { k: "trail", title: "The ISS's trail", note: "where it's been, fading", set: [["minutes", "step", 5, 95, 5, " min"]] },
  { k: "sunlit", title: "Trail in sun and shadow", note: "warm in sunlight, another colour in Earth's shadow",
    set: [["shadow", "seg", [["blue", "Blue"], ["dim", "Dim grey"]]]] },
  { k: "ahead", title: "Track ahead", note: "where it goes next, dashed", set: [["minutes", "step", 5, 90, 5, " min"]] },
  { k: "footprint", title: "Who can see it", note: "the ring where the ISS is above the horizon", set: [["elevation", "step", 0, 45, 5, "° up"], ["tiangong", "check", "Tiangong's too"]] },
  { k: "tiangong", title: "Tiangong", note: "China's space station", set: [["trail", "check", "Its trail"]] },
  { k: "sunmoon", title: "Sun and Moon", note: "the points straight under them", set: [["lines", "check", "Faint lines from the ISS"]] },
  { k: "launches", title: "Launch pads", note: "the next launches' pads, pulsing, with countdowns", set: [["count", "step", 1, 6, 1, ""]] },
  { k: "signal", title: "AOS / LOS", note: "NASA's telemetry: links drawn only with the signal", set: [["tag", "check", "AOS/LOS beside the ISS"]] },
  { k: "russian", title: "Russian segment link", note: "estimated from geometry" },
  { k: "chinese", title: "Tiangong's link", note: "to a Chinese station or a Tianlian relay (estimated)" },
  { k: "flow", title: "Data moving along links", note: "the animation; each network's style is under Links" },
];
const KIND_NAMES = { iss: "The ISS", tiangong: "Tiangong", relays: "Relay satellites", ground: "Ground stations",
  sats: "Satellite layers (small ones)", launch: "Launch pads" };
const LINK_LABEL = { gps: "Links to the ISS's GPS receivers", goes: "Downlink to Wallops" };

// a first visit: the ISS webcam's caption (one line of it) and map settings, as the wall uses them until then
const seedSpaceMap = () => {
  const src = Object.values(store.saved?.modules?.webcams?.cams || {}).find((c) => c?.space) || {};
  const ov = clone(src.overlay || {});
  if (Array.isArray(ov.rows)) ov.rows = ov.rows.slice(0, 1);
  seed(["modules", "iss_map"], { label: "International Space Station", overlay: ov,
    map: { fixed: !!src.map?.fixed, ...(src.map?.extras ? { extras: clone(src.map.extras) } : {}) } });
};

const Preview = {
  props: { settings: Object, zone: Array },
  setup(props) {
    const src = ref("");
    const err = ref("");
    let t = null, busy = false, again = false, last = null, gone = false;
    const draw = async () => {
      if (busy) { again = true; return; }
      busy = true; again = false;
      try {
        const r = await fetch("/api/iss-map/preview.png", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ settings: props.settings, shape: SHAPE }) });
        if (!r.ok) {
          let why = r.statusText;
          try { why = (await r.json()).detail || why; } catch {}
          throw new Error(`The hub couldn't draw the map (${r.status}): ${why}`.slice(0, 240));
        }
        const url = URL.createObjectURL(await r.blob());
        if (gone) { URL.revokeObjectURL(url); return; }
        if (last) URL.revokeObjectURL(last);
        last = src.value = url;
        err.value = "";
      } catch (e) { err.value = e.message; } finally { busy = false; if (again && !gone) draw(); }
    };
    onMounted(() => { draw(); t = setInterval(draw, 1500); });
    onBeforeUnmount(() => { gone = true; clearInterval(t); if (last) URL.revokeObjectURL(last); });
    watch(() => props.settings, draw, { deep: true });
    return { src, err };
  },
  template: `<div class="led" :style="{maxWidth: Math.min(zone[0] * 3 + 22, 1366) + 'px'}">
    <img v-if="src" :src="src" :style="{aspectRatio: zone[0] + ' / ' + zone[1]}" alt="The Space Map as the wall draws it">
    <div v-else class="placeholder">{{ err || 'Drawing the map…' }}</div></div>
    <div v-if="err && src" class="callout" style="font-size:12.5px; margin-top:6px"><Icon name="TriangleAlert" :size="14"/><span>{{ err }}</span></div>`,
};

export const SpaceMap = {
  components: { Preview, CaptionRows, InRotation },
  setup() {
    seedSpaceMap();
    watch(() => store.draft.modules.iss_map, (v) => { if (!v) seedSpaceMap(); }, { flush: "sync" });
    const cam = computed(() => store.draft.modules.iss_map);
    const map = computed(() => cam.value.map || (cam.value.map = {}));
    const cat = ref(null);
    const tab = ref("panels");
    const zone = ref([448, 175]);
    onMounted(async () => {
      try { cat.value = await api("/api/iss-map/catalog"); } catch (e) { toast("Couldn't load the Space Map's options: " + e.message, "bad"); }
      try { zone.value = (await api("/api/webcams/status")).zones?.tall || zone.value; } catch {}
    });
    const found = computed(() => ["left", "right"].flatMap((side) => (store.draft.modules.layout_cycle?.[side] || [])
      .filter((e) => e.panel?.startsWith("iss_map")).map((e) => ({ side, e }))));
    const play = async (hold) => {
      if (dirty.value.length) await previewOnDisplay(120, { hook: false });
      playOnDisplay(ENTRY(), { side: "right", hold });
    };
    // the settings under map: read with defaults, written whole (a part at a time)
    const sub = (k) => map.value[k] || {};
    const put = (k, key, part, v) => {
      const top = map.value[k] || (map.value[k] = {});
      top[key] = { ...(top[key] || {}), [part]: v };
    };
    // --- panels
    const panels = computed(() => map.value.panels || cat.value?.default_panels || []);
    const own = () => { if (!map.value.panels) map.value.panels = clone(cat.value?.default_panels || []); return map.value.panels; };
    const sel = ref(null);
    const selPanel = computed(() => (sel.value != null ? panels.value[sel.value] : null));
    const P = (p, k) => p[k] ?? cat.value?.panel_defaults?.[k];
    const inPos = (pos) => panels.value.map((p, i) => ({ p, i })).filter(({ p }) => (p.pos || "tr") === pos);
    const addPanel = (pos) => {
      const list = own();
      list.push({ id: "p" + Date.now().toString(36), pos, title: "", fields: [] });
      sel.value = list.length - 1;
    };
    const setP = (k, v) => { own()[sel.value][k] = v; };
    const movePanel = (d) => {
      const list = own(), p = list[sel.value];
      const same = list.map((q, i) => ({ q, i })).filter(({ q }) => (q.pos || "tr") === (p.pos || "tr")).map(({ i }) => i);
      const at = same.indexOf(sel.value), to = same[at + d];
      if (to == null) return;
      [list[sel.value], list[to]] = [list[to], list[sel.value]];
      sel.value = to;
    };
    const removePanel = () => { own().splice(sel.value, 1); sel.value = null; };
    const dupPanel = () => { const list = own(); list.splice(sel.value + 1, 0, { ...clone(list[sel.value]), id: "p" + Date.now().toString(36) }); sel.value++; };
    const fieldQ = ref("");
    const fieldLabel = (k) => cat.value?.fields.find((f) => f.key === k)?.label || k;
    const toggleField = (k) => {
      const f = [...(selPanel.value.fields || [])];
      const i = f.indexOf(k);
      if (i >= 0) f.splice(i, 1); else f.push(k);
      setP("fields", f);
      if (i >= 0 && (selPanel.value.sparks || []).includes(k)) setP("sparks", selPanel.value.sparks.filter((x) => x !== k));
    };
    const moveField = (i, d) => {
      const f = [...selPanel.value.fields];
      if (i + d < 0 || i + d >= f.length) return;
      [f[i], f[i + d]] = [f[i + d], f[i]];
      setP("fields", f);
    };
    const groupFields = (g) => (cat.value?.fields || []).filter((f) => f.group === g
      && (!fieldQ.value || f.label.toLowerCase().includes(fieldQ.value.toLowerCase())));
    const sparkable = computed(() => (selPanel.value?.fields || []).filter((k) => (cat.value?.spark_fields || []).includes(k)));
    const toggleSpark = (k) => {
      const f = [...(selPanel.value.sparks || [])];
      const i = f.indexOf(k);
      if (i >= 0) f.splice(i, 1); else f.push(k);
      setP("sparks", f);
    };
    const hasCrew = computed(() => (selPanel.value?.fields || []).some((k) => ["crew", "crew_roles", "tg_crew"].includes(k)));
    const panelName = (p) => p.title || (p.fields || []).slice(0, 2).map(fieldLabel).join(", ") || "Empty panel";
    // --- links, labels, satellites, layers
    const link = (net) => ({ ...(cat.value?.link_defaults?.[net] || {}), ...(sub("links")[net] || {}) });
    const setLink = (net, k, v) => put("links", net, k, v);
    const lab = (kind) => ({ ...(cat.value?.label_defaults?.[kind] || {}), ...(sub("labels")[kind] || {}) });
    const setLab = (kind, k, v) => put("labels", kind, k, v);
    const sat = (k) => { const L = cat.value?.layers.find((l) => l.key === k) || {}; return { ...(cat.value?.sat_defaults || {}), colour: L.colour, ...(sub("sats")[k] || {}) }; };
    const setSat = (k, part, v) => put("sats", k, part, v);
    const ex = (k) => ({ ...(cat.value?.extras?.[k] || {}), ...((map.value.extras || {})[k] || {}) });
    const setEx = (k, part, v) => put("extras", k, part, v);
    const setTop = (k, v) => { map.value[k] = v; };
    const names = computed(() => map.value.names || {});
    const setName = (key, v) => {
      const n = { ...(map.value.names || {}) };
      if (v && v.trim()) n[key] = v.trim(); else delete n[key];
      map.value.names = n;
    };
    const nameQ = ref("");
    const objects = computed(() => (cat.value?.objects || []).filter((o) => !nameQ.value || o.key.toLowerCase().includes(nameQ.value.toLowerCase())).slice(0, 80));
    // --- view (above the clock)
    const view = computed(() => ({ ...(cat.value?.view || {}), ...((map.value.views || {}).tall || {}) }));
    const setView = (k, v) => { const vs = map.value.views || (map.value.views = {}); vs.tall = { ...(vs.tall || {}), [k]: v }; };
    // --- caption (one row by default; the second line's facts belong in panels now)
    const ov = computed(() => cam.value.overlays?.tall || cam.value.overlay || (cam.value.overlay = {}));
    const twoRows = computed(() => Array.isArray(ov.value.rows) && ov.value.rows.length > 1);
    const oneRow = () => {
      const moved = ov.value.rows.slice(1).flatMap((r) => (r.items || []).map((i) => i.k)).filter((k) => ["orbit", "below", "crew", "mission"].includes(k));
      ov.value.rows = ov.value.rows.slice(0, 1);
      const want = { orbit: "sun_next", below: "local_time", crew: "crew", mission: "expedition" };
      const list = own();
      const have = new Set(list.flatMap((p) => p.fields || []));
      const add = moved.map((k) => want[k]).filter((f) => !have.has(f));
      if (add.length) list.push({ id: "p" + Date.now().toString(36), pos: "br", title: "", fields: add });
      toast("The caption is one line now" + (add.length ? "; what was on its second line is in a panel, bottom right" : ""));
    };
    const units = computed(() => ({ ...(cat.value?.unit_defaults || {}), ...(map.value.units || {}) }));
    const setUnit = (k, v) => { map.value.units = { ...(map.value.units || {}), [k]: v }; };
    const pct = (v) => Math.round(v * 100) + "%";
    return { cam, map, cat, tab, zone, found, play, SHAPES, ENTRY, TABS, POS_NAMES, LAYER_ITEMS, LINK_LABEL, KIND_NAMES, PIECES, SECTIONS, CAPTION_CORNERS, FONTS,
      panels, sel, selPanel, P, inPos, sparkable, toggleSpark, addPanel, setP, movePanel, removePanel, dupPanel, fieldQ, fieldLabel, toggleField, moveField, groupFields, hasCrew, panelName,
      link, setLink, lab, setLab, sat, setSat, ex, setEx, setTop, names, setName, nameQ, objects, view, setView, ov, twoRows, oneRow, units, setUnit, pct };
  },
  template: `<div class="page" v-if="cam">
    <div class="page-head"><div class="grow"><h1>Space Map</h1>
      <div class="faint" style="font-size:13px">The ISS and everything around it above the clock: its links, other satellites, names and flags, and panels of live data wherever you want them</div></div></div>
    <div class="two-col">
      <div class="stack">
        <div class="seg sm-tabs" style="flex-wrap:wrap"><button v-for="t in TABS" :key="t.v" type="button" :class="{on: tab === t.v}" @click="tab = t.v"><Icon :name="t.i" :size="14"/>{{ t.l }}</button></div>
        <div v-if="!cat" class="card"><div class="card-body faint">Loading the options…</div></div>

        <!-- PANELS -->
        <template v-else-if="tab === 'panels'">
        <div class="card"><div class="card-head"><Icon name="LayoutPanelTop" :size="16"/><h3>Panels</h3><span class="spacer"></span>
          <span class="faint" style="font-size:12px">{{ panels.length }} panel{{ panels.length === 1 ? '' : 's' }}</span></div>
          <div class="card-body stack">
            <div class="faint" style="font-size:12.5px">Nine places along the edges. Each place stacks its panels in order (top places downwards, bottom places upwards, middle ones centred). A panel shows only the fields you give it — one number or a whole table.</div>
            <div class="sm-board">
              <div v-for="pos in cat.positions" :key="pos" class="sm-cell">
                <div class="sm-cell-head"><span>{{ POS_NAMES[pos] }}</span><button type="button" class="btn ghost icon-only sm" @click="addPanel(pos)" :title="'New panel: ' + POS_NAMES[pos]"><Icon name="Plus" :size="13"/></button></div>
                <button v-for="{p, i} in inPos(pos)" :key="p.id || i" type="button" class="sm-chip" :class="{on: sel === i, off: P(p, 'on') === false}" @click="sel = i">
                  {{ panelName(p) }}<small>{{ (p.fields || []).length }}</small></button>
              </div>
            </div>
          </div></div>
        <div class="card" v-if="selPanel"><div class="card-head"><Icon name="PanelTop" :size="16"/><h3>{{ panelName(selPanel) }}</h3><span class="spacer"></span>
            <button class="btn sm ghost" @click="movePanel(-1)" title="Earlier in its place"><Icon name="ArrowUp" :size="14"/></button>
            <button class="btn sm ghost" @click="movePanel(1)" title="Later in its place"><Icon name="ArrowDown" :size="14"/></button>
            <button class="btn sm ghost" @click="dupPanel"><Icon name="Copy" :size="14"/>Duplicate</button>
            <button class="btn sm ghost" @click="removePanel"><Icon name="Trash2" :size="14"/>Remove</button></div>
          <div class="card-body stack">
            <div class="row" style="gap:16px; align-items:flex-start; flex-wrap:wrap">
              <Field label="Shown"><Toggle :modelValue="P(selPanel, 'on') !== false" @update:modelValue="v => setP('on', v)"/></Field>
              <Field label="Place"><div class="sm-pos"><button v-for="pos in cat.positions" :key="pos" type="button" :class="{on: (selPanel.pos || 'tr') === pos}" @click="setP('pos', pos)" :title="POS_NAMES[pos]"></button></div></Field>
              <Field label="Title" hint="Optional, in the accent colour"><input class="input" maxlength="30" :value="selPanel.title || ''" @input="setP('title', $event.target.value)" style="width:160px"></Field>
              <Field label="Layout"><Seg :modelValue="P(selPanel, 'layout')" @update:modelValue="v => setP('layout', v)" :options="[{value:'rows', label:'Table'}, {value:'line', label:'One line'}]"/></Field>
              <Field label="Text"><Seg :modelValue="P(selPanel, 'font')" @update:modelValue="v => setP('font', v)" :options="[{value:'tiny', label:'Tiny'}, {value:'small', label:'Small'}]"/></Field>
            </div>
            <div class="row" style="gap:16px; align-items:flex-start; flex-wrap:wrap">
              <Field label="Labels" hint="CABIN 757 MMHG, or just 757 MMHG"><Toggle :modelValue="P(selPanel, 'labels') !== false" @update:modelValue="v => setP('labels', v)"/></Field>
              <Field label="Flags"><Toggle :modelValue="P(selPanel, 'flags') !== false" @update:modelValue="v => setP('flags', v)"/></Field>
              <Field label="Background"><div style="width:150px"><Slider :modelValue="P(selPanel, 'opacity')" @update:modelValue="v => setP('opacity', v)" :min="0" :max="1" :step="0.05" :format="pct"/></div></Field>
              <Field label="Text colour"><ColorPick :modelValue="P(selPanel, 'colour')" @update:modelValue="v => setP('colour', v)"/></Field>
              <Field label="Labels & title colour"><ColorPick :modelValue="P(selPanel, 'accent')" @update:modelValue="v => setP('accent', v)"/></Field>
            </div>
            <div class="row" style="gap:16px; align-items:flex-start; flex-wrap:wrap">
              <Field label="Show" hint="Only during a spacewalk: hidden the rest of the time, so it costs no room"><Seg :modelValue="P(selPanel, 'when')" @update:modelValue="v => setP('when', v)"
                :options="[{value:'always', label:'Always'}, {value:'eva', label:'Only during a spacewalk'}]"/></Field></div>
            <Field v-if="sparkable.length" label="Sparklines" :hint="P(selPanel, 'layout') === 'line' ? 'Sparklines show in the Table layout only' : 'A small chart of the recent past beside each reading you pick'">
              <div class="row" style="gap:6px; flex-wrap:wrap"><button v-for="k in sparkable" :key="k" type="button" class="isx-chip" :class="{on: (selPanel.sparks || []).includes(k)}" @click="toggleSpark(k)">{{ fieldLabel(k) }}</button></div></Field>
            <div class="row" style="gap:16px; align-items:flex-start; flex-wrap:wrap" v-if="(selPanel.sparks || []).length">
              <Field label="Covering" hint="How far back each sparkline goes"><Stepper :modelValue="P(selPanel, 'spark_min')" @update:modelValue="v => setP('spark_min', v)" :min="cat.spark_min[0]" :max="cat.spark_min[1]" :step="5" unit=" min"/></Field>
              <Field label="Width" hint="Narrower saves room"><Stepper :modelValue="P(selPanel, 'spark_w')" @update:modelValue="v => setP('spark_w', v)" :min="cat.spark_w[0]" :max="cat.spark_w[1]" :step="2" unit=" px"/></Field>
              <Field label="Colour"><ColorPick :modelValue="P(selPanel, 'spark_colour')" @update:modelValue="v => setP('spark_colour', v)"/></Field></div>
            <Field v-if="hasCrew" label="Crew names" hint="Role + last name uses the mission role (Commander, Flight Engineer…); the data has no military ranks">
              <Seg :modelValue="P(selPanel, 'crew_names')" @update:modelValue="v => setP('crew_names', v)"
                :options="[{value:'full', label:'Full names'}, {value:'last', label:'Last names'}, {value:'role', label:'CMDR. DUBROV'}, {value:'flags', label:'Flags and counts'}]"/></Field>
            <Field label="Fields, in order">
              <div v-if="!(selPanel.fields || []).length" class="faint" style="font-size:12.5px">None yet: pick some below.</div>
              <div class="sm-fields"><div v-for="(k, i) in selPanel.fields" :key="k" class="sm-field"><span>{{ fieldLabel(k) }}</span>
                <button type="button" class="btn ghost icon-only sm" @click="moveField(i, -1)"><Icon name="ChevronUp" :size="13"/></button>
                <button type="button" class="btn ghost icon-only sm" @click="moveField(i, 1)"><Icon name="ChevronDown" :size="13"/></button>
                <button type="button" class="btn ghost icon-only sm" @click="toggleField(k)"><Icon name="X" :size="13"/></button></div></div></Field>
            <Field label="Add fields"><input class="input" v-model="fieldQ" placeholder="Search fields" style="max-width:240px"></Field>
            <div v-for="g in cat.groups" :key="g" v-show="groupFields(g).length"><div class="faint" style="font-size:12px; margin:4px 0">{{ g }}</div>
              <div class="row" style="gap:6px; flex-wrap:wrap"><button v-for="f in groupFields(g)" :key="f.key" type="button" class="isx-chip" :class="{on: (selPanel.fields || []).includes(f.key)}" @click="toggleField(f.key)">{{ f.label }}</button></div></div>
          </div></div>
        </template>

        <!-- LINKS -->
        <template v-else-if="tab === 'links'">
        <div class="card"><div class="card-head"><Icon name="Radio" :size="16"/><h3>Links</h3></div>
          <div class="card-body stack">
            <div v-for="(label, net) in cat.nets" :key="net" class="sm-net"><b>{{ label }}</b>
              <div class="row" style="gap:14px; flex-wrap:wrap; align-items:flex-start">
                <Field :label="net === 'cn' ? 'Tiangong up / straight down' : 'ISS up to the relay / straight down'"><ColorPick :modelValue="link(net).up" @update:modelValue="v => setLink(net, 'up', v)"/></Field>
                <Field label="Relay down to the ground"><ColorPick :modelValue="link(net).down" @update:modelValue="v => setLink(net, 'down', v)"/></Field>
                <Field label="Line"><div style="width:140px"><Slider :modelValue="link(net).line" @update:modelValue="v => setLink(net, 'line', v)" :min="0" :max="1" :step="0.05" :format="pct"/></div></Field></div>
              <div class="row" style="gap:14px; flex-wrap:wrap; align-items:flex-start">
                <Field label="Moving data"><Seg :modelValue="link(net).style" @update:modelValue="v => setLink(net, 'style', v)"
                  :options="[{value:'dots', label:'Dots'}, {value:'dashes', label:'Dashes'}, {value:'comet', label:'Comets'}, {value:'pulse', label:'Pulse'}, {value:'still', label:'None'}]"/></Field>
                <Field label="Speed"><Seg :modelValue="link(net).speed" @update:modelValue="v => setLink(net, 'speed', v)" :options="[{value:'slow', label:'Slow'}, {value:'normal', label:'Normal'}, {value:'fast', label:'Fast'}]"/></Field>
                <Field label="Direction"><Seg :modelValue="link(net).direction" @update:modelValue="v => setLink(net, 'direction', v)" :options="[{value:'both', label:'Both'}, {value:'down', label:'Down'}, {value:'up', label:'Up'}]"/></Field></div></div>
            <div class="row" style="gap:16px; flex-wrap:wrap; align-items:flex-start">
              <Field label="Relay satellites" hint="TDRS, Luch, Tianlian"><Seg :modelValue="map.relays || 'always'" @update:modelValue="v => setTop('relays', v)" :options="[{value:'always', label:'Always'}, {value:'linked', label:'Only in a link'}]"/></Field>
              <Field label="Ground stations"><Seg :modelValue="map.ground || 'always'" @update:modelValue="v => setTop('ground', v)" :options="[{value:'always', label:'Always'}, {value:'linked', label:'Only in a link'}, {value:'hidden', label:'Hidden'}]"/></Field>
              <Field label="Icons"><Seg :modelValue="map.icons || 'auto'" @update:modelValue="v => setTop('icons', v)" :options="[{value:'auto', label:'Auto'}, {value:'small', label:'Small'}, {value:'big', label:'Big'}]"/></Field></div>
            <div class="faint" style="font-size:12px">NASA's link (and its partners': Canada, Europe and Japan all go through TDRS) is drawn only while NASA's telemetry has the signal. Russia's and China's are worked out from where things are.</div>
          </div></div>
        </template>

        <!-- LABELS -->
        <template v-else-if="tab === 'labels'">
        <div class="card"><div class="card-head"><Icon name="Tag" :size="16"/><h3>Labels & flags</h3></div>
          <div class="card-body stack">
            <div v-for="kind in cat.label_kinds" :key="kind" class="sm-net">
              <b>{{ KIND_NAMES[kind] }}</b>
              <div class="row" style="gap:14px; flex-wrap:wrap; align-items:center">
                <label class="isx-chk"><input type="checkbox" :checked="lab(kind).name" @change="setLab(kind, 'name', $event.target.checked)">Name</label>
                <label class="isx-chk" v-if="kind !== 'iss'"><input type="checkbox" :checked="lab(kind).flag" @change="setLab(kind, 'flag', $event.target.checked)">Flag of who runs it</label>
                <label class="isx-chk" v-if="kind === 'iss' || kind === 'tiangong'"><input type="checkbox" :checked="lab(kind).crew" @change="setLab(kind, 'crew', $event.target.checked)">How many aboard</label>
                <label class="isx-chk"><input type="checkbox" :checked="lab(kind).backdrop" @change="setLab(kind, 'backdrop', $event.target.checked)">Dark backing</label>
                <Seg :modelValue="lab(kind).pos" @update:modelValue="v => setLab(kind, 'pos', v)" :options="[{value:'auto', label:'Auto'}, {value:'right', label:'Right'}, {value:'left', label:'Left'}, {value:'above', label:'Above'}, {value:'below', label:'Below'}]"/>
                <ColorPick :modelValue="lab(kind).colour" @update:modelValue="v => setLab(kind, 'colour', v)"/></div></div>
            <div class="faint" style="font-size:12px">Auto puts each label where it covers least (other labels, icons, panels, the caption, the edges); the ISS's goes first. Minor labels with no clear room are left out.</div>
          </div></div>
        <div class="card"><div class="card-head"><Icon name="PenLine" :size="16"/><h3>Your own names</h3></div>
          <div class="card-body stack">
            <div class="faint" style="font-size:12.5px">Names come from CelesTrak (satellites) and the stations' own names; type one of yours to use instead. Empty: the usual name.</div>
            <input class="input" v-model="nameQ" placeholder="Search (TDRS, White Sands, GPS…)" style="max-width:280px">
            <div class="sm-names"><div v-for="o in objects" :key="o.key" class="sm-name"><span class="faint">{{ o.key }}</span>
              <input class="input" :value="names[o.key] || ''" :placeholder="o.short || o.key" maxlength="40" @change="setName(o.key, $event.target.value)"></div></div>
          </div></div>
        </template>

        <!-- SATELLITES -->
        <template v-else-if="tab === 'sats'">
        <div class="card"><div class="card-head"><Icon name="Satellite" :size="16"/><h3>Satellite layers</h3></div>
          <div class="card-body stack">
            <div class="faint" style="font-size:12.5px">From CelesTrak's current orbits, worked out on the hub every 10 minutes (switching one on fetches it on the next run). Thousands of satellites draw best as faint single pixels.</div>
            <div v-for="L in cat.layers" :key="L.key" class="isx-row" :class="{off: !sat(L.key).on}">
              <Toggle :modelValue="!!sat(L.key).on" @update:modelValue="v => setSat(L.key, 'on', v)"/>
              <Icon name="Satellite" :size="15"/>
              <div class="isx-txt"><b>{{ L.label }} <span class="faint" v-if="L.count != null">· {{ L.count.toLocaleString() }}</span></b><span class="faint">{{ L.note }}</span></div>
              <div class="isx-set" v-if="sat(L.key).on">
                <Seg :modelValue="sat(L.key).mark" @update:modelValue="v => setSat(L.key, 'mark', v)" :options="[{value:'auto', label:'Auto'}, {value:'icon', label:'Icons'}, {value:'dot', label:'Pixels'}]"/>
                <ColorPick :modelValue="sat(L.key).colour" @update:modelValue="v => setSat(L.key, 'colour', v)"/>
                <span class="faint">Brightness</span><Slider :modelValue="sat(L.key).brightness" @update:modelValue="v => setSat(L.key, 'brightness', v)" :min="0.1" :max="1" :step="0.05" :format="pct"/>
                <label class="isx-chk" v-if="L.mesh || LINK_LABEL[L.key]"><input type="checkbox" :checked="sat(L.key).links" @change="setSat(L.key, 'links', $event.target.checked)">{{ LINK_LABEL[L.key] || 'Laser links (trellis)' }}</label>
                <template v-if="L.mesh && sat(L.key).links"><span class="faint">Within</span><Stepper :modelValue="sat(L.key).near_km" @update:modelValue="v => setSat(L.key, 'near_km', v)" :min="0" :max="20000" :step="500" unit=" km"/><span class="faint">of the ISS (0: everywhere)</span></template>
                <Seg v-if="sat(L.key).links" :modelValue="sat(L.key).speed" @update:modelValue="v => setSat(L.key, 'speed', v)" :options="[{value:'slow', label:'Slow'}, {value:'normal', label:'Normal'}, {value:'fast', label:'Fast'}]"/>
                <label class="isx-chk" v-if="L.count == null || L.count <= 40"><input type="checkbox" :checked="sat(L.key).label" @change="setSat(L.key, 'label', $event.target.checked)">Names</label>
              </div></div>
          </div></div>
        </template>

        <!-- MAP LAYERS -->
        <template v-else-if="tab === 'layers'">
        <div class="card"><div class="card-head"><Icon name="Layers" :size="16"/><h3>Map layers</h3></div>
          <div class="card-body"><div class="isx">
            <div v-for="it in LAYER_ITEMS" :key="it.k" class="isx-row" :class="{off: !ex(it.k).on}">
              <Toggle :modelValue="!!ex(it.k).on" @update:modelValue="v => setEx(it.k, 'on', v)"/><span></span>
              <div class="isx-txt"><b>{{ it.title }}</b><span class="faint">{{ it.note }}</span></div>
              <div class="isx-set" v-if="ex(it.k).on && it.set">
                <template v-for="s in it.set" :key="s[0]">
                  <template v-if="s[1] === 'slider'"><Slider :modelValue="ex(it.k)[s[0]]" @update:modelValue="v => setEx(it.k, s[0], v)" :min="s[2]" :max="s[3]" :step="s[4]" :format="pct"/></template>
                  <Stepper v-else-if="s[1] === 'step'" :modelValue="ex(it.k)[s[0]]" @update:modelValue="v => setEx(it.k, s[0], v)" :min="s[2]" :max="s[3]" :step="s[4]" :unit="s[5]"/>
                  <Seg v-else-if="s[1] === 'seg'" :modelValue="ex(it.k)[s[0]]" @update:modelValue="v => setEx(it.k, s[0], v)" :options="s[2].map(o => ({value: o[0], label: o[1]}))"/>
                  <label v-else class="isx-chk"><input type="checkbox" :checked="ex(it.k)[s[0]]" @change="setEx(it.k, s[0], $event.target.checked)">{{ s[2] }}</label>
                </template></div></div></div></div></div>
        </template>

        <!-- VIEW -->
        <template v-else-if="tab === 'view'">
        <div class="card"><div class="card-head"><Icon name="ZoomIn" :size="16"/><h3>View</h3></div>
          <div class="card-body stack">
            <Seg :modelValue="map.fixed ? 'fixed' : 'follow'" @update:modelValue="v => setTop('fixed', v === 'fixed')" :options="[{ value: 'follow', label: 'Follow the ISS' }, { value: 'fixed', label: 'Fixed world' }]"/>
            <Field label="Zoom" hint="1×: the whole world across; zoomed in, the map follows the ISS up and down too"><div style="max-width:320px"><Slider :modelValue="view.zoom" @update:modelValue="v => setView('zoom', v)" :min="1" :max="4" :step="0.1" :format="v => v.toFixed(1) + '×'"/></div></Field>
            <Field label="Stretch" hint="Taller or flatter (rows per degree of latitude); the automatic view may flatten it to keep the ISS's path off the caption"><div style="max-width:320px"><Slider :modelValue="view.stretch" @update:modelValue="v => setView('stretch', v)" :min="0.5" :max="2" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></div></Field>
            <Field :label="map.fixed ? 'Centre longitude' : 'The ISS across the map'" :hint="map.fixed ? '0°: the whole world from the date line' : '0: in the middle; positive: the view sits east of it (the ISS left of centre)'">
              <div style="max-width:320px"><Slider :modelValue="view.lon" @update:modelValue="v => setView('lon', v)" :min="-180" :max="180" :step="5" :format="v => v + '°'"/></div></Field>
            <ToggleRow :modelValue="view.lat == null" @update:modelValue="v => setView('lat', v ? null : 20)" label="Automatic up and down" hint="The ISS's band of latitudes clear of the caption, and as far north as fits (at least 66°N)"/>
            <Field v-if="view.lat != null" label="Latitude in the middle"><div style="max-width:320px"><Slider :modelValue="view.lat" @update:modelValue="v => setView('lat', v)" :min="-80" :max="80" :step="1" :format="v => v + '°'"/></div></Field>
            <ToggleRow v-if="view.lat == null" :modelValue="view.clear !== false" @update:modelValue="v => setView('clear', v)" label="Keep the ISS's path off the caption"/>
          </div></div>
        </template>

        <!-- CAPTION -->
        <template v-else-if="tab === 'caption'">
        <div class="card"><div class="card-head"><Icon name="Captions" :size="16"/><h3>Caption</h3></div>
          <div class="card-body stack">
            <div v-if="twoRows" class="callout" style="font-size:12.5px"><Icon name="Rows3" :size="14"/><span>The caption has {{ ov.rows.length }} lines. The Space Map reads best with one;
              <button class="btn sm" style="margin-left:6px" @click="oneRow">Make it one line</button> (sunrise/sunset, local time, crew and mission move into a panel).</span></div>
            <Field label="Name"><input class="input" v-model="cam.label" style="max-width:320px"></Field>
            <Field label="Rows" hint="Drag pieces to reorder them, between rows, or to Not shown">
              <CaptionRows :cam="cam" :ov="ov" :labels="SECTIONS" :has-text="() => true" :only="PIECES"/></Field>
            <div class="row" style="gap:16px; flex-wrap:wrap; align-items:flex-start">
              <Field label="Position"><div class="wc-corners" style="grid-template-columns:22px 22px 22px"><button v-for="c in CAPTION_CORNERS" :key="c" type="button" :class="{on: ov.corner === c}" @click="ov.corner = c" :title="c"></button></div></Field>
              <Field label="Text size"><Seg v-model="ov.font" :options="FONTS"/></Field>
              <Field label="Clock"><Seg v-model="ov.time_format" :options="[{value:'24h', label:'24 h'}, {value:'12h', label:'12 h'}]"/></Field>
              <Field label="Coordinates"><Seg :modelValue="ov.coord_format || 'nsew'" @update:modelValue="v => ov.coord_format = v" :options="[{ value: 'nsew', label: '12.35°S' }, { value: 'signed', label: '-12.35°' }, { value: 'dms', label: '12°21′00″S' }]"/></Field></div>
            <div class="row" style="gap:16px; flex-wrap:wrap; align-items:flex-start">
              <Field label="Accent"><ColorPick v-model="ov.accent"/></Field>
              <Field label="Text"><ColorPick v-model="ov.text"/></Field>
              <Field label="Background"><ColorPick v-model="ov.bg"/></Field>
              <Field label="Background strength"><div style="width:150px"><Slider v-model="ov.opacity" :min="0" :max="1" :step="0.05" :format="pct"/></div></Field></div>
            <Field v-if="ov.rows?.some(r => r.items?.some(i => i.k === 'custom'))" label="Your text"><input class="input" v-model="ov.custom_text" style="max-width:320px"></Field>
            <div class="faint" style="font-size:12px">This is the Space Map's own caption; the ISS webcam's caption is unchanged on its page.</div>
          </div></div>
        </template>

        <!-- UNITS -->
        <template v-else-if="tab === 'units'">
        <div class="card"><div class="card-head"><Icon name="Ruler" :size="16"/><h3>Units</h3></div>
          <div class="card-body stack">
            <Field label="Clock"><Seg :modelValue="units.time" @update:modelValue="v => setUnit('time', v)" :options="[{value:'12h', label:'12 h'}, {value:'24h', label:'24 h'}]"/></Field>
            <Field label="Speed"><Seg :modelValue="units.speed" @update:modelValue="v => setUnit('speed', v)" :options="[{value:'kmh', label:'km/h'}, {value:'mph', label:'mph'}, {value:'kms', label:'km/s'}]"/></Field>
            <Field label="Distance"><Seg :modelValue="units.distance" @update:modelValue="v => setUnit('distance', v)" :options="[{value:'km', label:'km'}, {value:'mi', label:'miles'}]"/></Field>
            <Field label="Temperature"><Seg :modelValue="units.temp" @update:modelValue="v => setUnit('temp', v)" :options="[{value:'c', label:'°C'}, {value:'f', label:'°F'}]"/></Field>
          </div></div>
        </template>
      </div>

      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">LIVE</span><h3>On the wall · above the clock ({{ zone[0] }}×{{ zone[1] }})</h3></div>
          <div class="card-body stack">
            <Preview :settings="cam" :zone="zone"/>
            <div class="faint" style="font-size:12px">Covers the ticker; the clock and date stay. The Space Map is made for this size only.</div>
            <div class="row">
              <button class="btn sm" @click="play(false)"><Icon name="Play" :size="14"/>Play once on the wall</button>
              <button class="btn sm" @click="play(true)"><Icon name="Pause" :size="14"/>Hold on the wall</button></div>
            <InRotation :found="found" what="The Space Map" :shapes="SHAPES" :make="ENTRY"/>
          </div></div>
      </div>
    </div>
  </div>`,
};
