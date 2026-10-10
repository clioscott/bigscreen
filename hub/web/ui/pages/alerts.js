import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, ago, seed, refreshDisplay, clone } from "../core.js";

const TYPES = [
  { id: "warning", label: "Warnings", hint: "Severe weather is happening or about to", colour: "#dc1414" },
  { id: "watch", label: "Watches", hint: "Conditions favour severe weather", colour: "#ff7d00" },
  { id: "advisory", label: "Advisories", hint: "Less severe but still a nuisance (frost, fog…)", colour: "#ebbe00" },
  { id: "statement", label: "Special weather statements", hint: "Heads-up about unusual weather", colour: "#6e7896" },
];
const ENTRY = { panel: "weather_alert", when: "weather_alert_active", advance: "cycles", cycles: 1, max_sec: 900 };
const DETAIL = [
  { value: "all", label: "Everything", hint: "The whole ECCC text (minus boilerplate) and every forecast region" },
  { value: "essentials", label: "Essentials", hint: "What's happening, what to expect and what to do, in larger text" },
];
const DENSITY = [
  { value: "dense", label: "Dense", hint: "Smaller text, as much of the message as possible" },
  { value: "balanced", label: "Balanced", hint: "The full message at the largest size that fits" },
  { value: "minimal", label: "Minimal", hint: "Big text: what's happening, what to do, and where" },
];
const LOOK_KEYS = ["header", "palette", "family", "structure", "border", "reveal", "reveal_speed", "title_font", "title_scale",
  "body_font", "reading_wpm", "min_hold_sec", "detail", "upper", "pulse", "density", "sample"];

export const Alerts = {
  setup() {
    seed(["modules", "weather_alerts"], { enabled: true, theme: "t01", types: ["warning", "watch", "advisory", "statement"],
      reading_wpm: 200, min_hold_sec: 12, reveal_speed: 1, detail: "all", refresh_interval_sec: 120 });
    const a = computed(() => store.draft.modules.weather_alerts);
    // first-version theme names → their closest new theme (in saved and draft alike, so it isn't an "unsaved change")
    const LEGACY = { ebs: "t01", eas: "t19", alert_ready: "t21", bulletin: "t02", severity: "t06", terminal: "t23" };
    for (const root of [store.saved, store.draft]) {
      const wa = root.modules.weather_alerts;
      if (wa && !/^t\d\d$/.test(wa.theme || "")) wa.theme = LEGACY[wa.theme] || "t01";
    }
    const cat = ref({ themes: [], structures: {}, borders: {}, reveals: {}, palettes: {}, families: {}, fonts: [] });
    const load = async () => { try { cat.value = await api("/api/alerts"); } catch (e) { /* hub restarting */ } };

    // unsaved look settings travel to the preview renderer as ?s=
    const look = computed(() => Object.fromEntries(LOOK_KEYS.filter((k) => a.value[k] !== undefined && a.value[k] !== "").map((k) => [k, a.value[k]])));
    const qs = computed(() => `s=${encodeURIComponent(JSON.stringify(look.value))}&real=${cat.value.alerts?.length ? 1 : 0}`);

    // live preview: plays the real page timeline; the scrubber jumps to a page
    const tl = ref({ total: 30, pages: [] });
    const t = ref(0);
    const playing = ref(true);
    const loadTl = async () => { try { tl.value = await api(`/api/alerts/timeline?${qs.value}`); } catch (e) { /* ignore */ } };
    let tlTimer;
    watch(qs, () => { clearTimeout(tlTimer); tlTimer = setTimeout(loadTl, 300); });
    // "play all": step through every theme in the preview, each for tourSec seconds
    const tour = ref(null);              // { i, t } while touring
    const tourSec = ref(9);
    const ids = computed(() => (cat.value.themes || []).map((x) => x.id));
    const tourTheme = computed(() => (tour.value ? cat.value.themes[tour.value.i] : null));
    const startTour = (from) => {
      const i = Math.max(0, from === undefined ? ids.value.indexOf(a.value.theme) : from);
      tour.value = { i: i < 0 ? 0 : i, t: 0 };
      playing.value = true;
    };
    const stepTour = (d) => { if (tour.value) tour.value = { i: (tour.value.i + d + ids.value.length) % ids.value.length, t: 0 }; };
    const stopTour = () => { tour.value = null; };
    const useTour = () => { if (tourTheme.value) { a.value.theme = tourTheme.value.id; toast(`Theme set to ${tourTheme.value.name} — save to apply`); } };
    const live = computed(() => tour.value
      ? `/api/alerts/preview/${tourTheme.value.id}.png?at=${tour.value.t.toFixed(2)}&scale=2&s=${encodeURIComponent(JSON.stringify({ reveal_speed: 2.2 }))}`
      : `/api/alerts/preview/${a.value.theme || "t01"}.png?at=${t.value.toFixed(2)}&scale=2&${qs.value}`);
    const tourWall = async () => {
      const from = Math.max(0, ids.value.indexOf(a.value.theme));
      const n = ids.value.length - from;
      try {
        await api("/api/display/override", { json: { side: "right", panel: "weather_alert", test: true, tour: true,
          tour_sec: tourSec.value, tour_from: from, duration_sec: Math.round(n * tourSec.value + 3) } });
        toast(`Touring ${n} themes on the wall, ${tourSec.value} s each (about ${Math.round(n * tourSec.value / 60)} min). "Back to rotation" stops it.`);
      } catch (e) { fail(e); }
    };
    const shown = ref("");
    const next = ref("");
    watch(live, (u) => { next.value = u; }, { immediate: true });
    const onLoaded = () => { shown.value = next.value; };
    let tick, tileTick;
    onMounted(() => {
      load().then(loadTl);
      tileTick = setInterval(() => { tileT.value += 1; }, 450);
      tick = setInterval(() => {
        if (!playing.value) return;
        if (tour.value) {
          const nt = tour.value.t + 0.25;
          if (nt >= tourSec.value) {
            if (tour.value.i + 1 >= ids.value.length) tour.value = null;
            else tour.value = { i: tour.value.i + 1, t: 0 };
          } else tour.value = { ...tour.value, t: nt };
          return;
        }
        t.value = (t.value + 0.25) % Math.max(4, tl.value.total || 30);
      }, 250);
    });
    onBeforeUnmount(() => { clearInterval(tick); clearInterval(tileTick); clearTimeout(tlTimer); });
    const page = computed(() => [...(tl.value.pages || [])].reverse().find((p) => t.value >= p.start) || tl.value.pages?.[0]);
    const jump = (p) => { t.value = p.start + 0.05; };

    // gallery
    const layoutFilter = ref("");
    const thumbsOn = ref("headline");
    const themes = computed(() => (cat.value.themes || []).filter((x) => !layoutFilter.value || x.structure === layoutFilter.value));
    const thumbQs = computed(() => `s=${encodeURIComponent(JSON.stringify(a.value.header ? { header: a.value.header } : {}))}`);
    const thumb = (id) => `/api/alerts/preview/${id}.png?at=${thumbsOn.value === "headline" ? 30 : 0.8}&scale=1&${thumbQs.value}`;
    const current = computed(() => (cat.value.themes || []).find((x) => x.id === a.value.theme));

    const types = computed(() => a.value.types || TYPES.map((x) => x.id));
    const toggleType = (id, on) => {
      const s = new Set(types.value);
      on ? s.add(id) : s.delete(id);
      a.value.types = TYPES.map((x) => x.id).filter((x) => s.has(x));
    };
    const hazards = computed(() => a.value.hazards || (cat.value.hazards || []).map((x) => x.id));
    const toggleHazard = (id, on) => {
      const all = (cat.value.hazards || []).map((x) => x.id);
      const cur = new Set(hazards.value);
      on ? cur.add(id) : cur.delete(id);
      a.value.hazards = all.filter((x) => cur.has(x));
    };
    const areas = computed(() => Object.entries(store.draft.modules.weather_maps?.areas || {}));
    const areaId = computed({
      get: () => a.value.area_id || (store.draft.modules.weather_maps?.areas?.metro ? "metro" : "core_metro"),
      set: (v) => { a.value.area_id = v; },
    });
    const setOpt = (k, v) => { if (v === "" || v === null || v === undefined) delete a.value[k]; else a.value[k] = v; };
    const resetLook = () => { for (const k of LOOK_KEYS) delete a.value[k]; a.value.reading_wpm = 200; a.value.min_hold_sec = 12; a.value.reveal_speed = 1; a.value.detail = "all"; };

    const entry = computed(() => ["left", "right"].map((s) => (store.draft.modules.layout_cycle[s] || []).find((e) => e.panel === "weather_alert")).find(Boolean));
    const addToRotation = () => {
      const r = (store.draft.modules.layout_cycle.right = store.draft.modules.layout_cycle.right || []);
      r.unshift({ ...ENTRY });
      toast("Added to the start of the right-half rotation — save to apply");
    };
    const show = async (test) => {
      try {
        await api("/api/display/override", { json: { side: "right", panel: "weather_alert", duration_sec: test ? 120 : 300, ...(test ? { test: true } : {}) } });
        toast(test ? "Showing a test alert on the wall for 2 min (save first to see unsaved changes there)" : "Showing the current alerts on the wall");
        setTimeout(refreshDisplay, 500);
      } catch (e) { fail(e); }
    };
    const onWall = ref(false);            // the wall is showing this page's settings
    const previewSample = ref(false);      // an alert type was picked: show its sample, even if real alerts exist
    let wallTest = null;
    const pickSample = (id) => { previewSample.value = true; a.value.sample = id; };
    let liveTimer;
    const pushLive = async () => {
      const settings = clone(a.value);
      for (const k of LOOK_KEYS) if (settings[k] === undefined || settings[k] === "") settings[k] = null;   // unset beats saved
      try {
        await api("/api/display/preview", { json: { overlay: { modules: { weather_alerts: settings } }, ttl_sec: 4 * 3600 } });
        const test = previewSample.value || !cat.value.alerts?.length;
        if (!onWall.value || wallTest !== test) {
          await api("/api/display/override", { json: { side: "right", panel: "weather_alert", duration_sec: 4 * 3600,
            ...(test ? { test: true } : {}) } });
          onWall.value = true;
          wallTest = test;
        }
      } catch (e) { fail(e); }
    };
    let armed = false;                   // only changes the user makes, not the initial load
    setTimeout(() => { armed = true; }, 1500);
    watch(() => JSON.stringify(a.value), () => {
      if (!armed || tour.value) return;
      clearTimeout(liveTimer);
      liveTimer = setTimeout(pushLive, 350);
    });
    const stop = async () => {
      try {
        await api("/api/display/override/clear", { json: { side: "right" } });
        await api("/api/display/preview/clear", { method: "POST" });
        onWall.value = false;
        previewSample.value = false;
        wallTest = null;
        toast("Back to the rotation");
      } catch (e) { fail(e); }
    };
    const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "");
    const typeColour = (k) => TYPES.find((x) => x.id === k)?.colour || "#6e7896";
    const fontName = (f) => f.replace("alert/", "").replace(".bdf", "");
    // animated option tiles: the current theme with one border (or reveal) swapped in
    const tileT = ref(0);
    const optImg = (over, at) => `/api/alerts/preview/${a.value.theme || "t01"}.png?at=${at.toFixed(2)}&scale=1&s=${encodeURIComponent(JSON.stringify({ ...look.value, ...over }))}`;
    const borderAt = computed(() => 20 + (tileT.value % 8) * 0.5);           // after the reveal: border motion only
    const revealAt = computed(() => (tileT.value % 12) * 0.3);               // replays the reveal every few seconds
    const themeOf = computed(() => (cat.value.themes || []).find((x) => x.id === a.value.theme) || {});
    const opts = (dict) => [{ value: "", label: "Theme" }, ...Object.entries(dict || {}).map(([value, label]) => ({ value, label }))];
    return { pickSample, previewSample, hazards, toggleHazard, onWall, pushLive, DENSITY, optImg, borderAt, revealAt, themeOf, tour, tourSec, tourTheme, startTour, stepTour, stopTour, useTour, tourWall, ids, a, cat, live, shown, onLoaded, tl, t, playing, page, jump, layoutFilter, thumbsOn, themes, thumb, current,
      TYPES, DETAIL, opts, types, toggleType, areas, areaId, setOpt, resetLook, entry, addToRotation, show, stop,
      when, typeColour, ago, fontName };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Weather alerts</h1>
      <p>When Environment Canada has an alert in effect for your area, the wall switches to a full-screen bulletin at the start of the rotation. Each alert fills one dense screen with nearly all of Environment Canada's text (amounts, what to expect and what to do highlighted), framed by a bright animated border, and holds long enough to read.</p></div>
      <Toggle v-model="a.enabled"/></div>

    <div class="card density-card"><div class="card-body row" style="gap:14px; flex-wrap:wrap">
      <div><div class="section-title" style="margin:0">Text density</div>
        <div class="faint" style="font-size:12px">{{ DENSITY.find(d => d.value === (a.density || 'balanced')).hint }} — applies to every design</div></div>
      <Seg :modelValue="a.density || 'balanced'" @update:modelValue="v => a.density = v" :options="DENSITY"/>
      <span class="spacer"></span>
      <template v-if="onWall"><span class="live">ON THE WALL</span><span class="faint" style="font-size:12px">changes show instantly</span>
        <button class="btn sm" @click="stop"><Icon name="Undo2" :size="14"/>Back to rotation</button></template>
      <button v-else class="btn sm" @click="pushLive"><Icon name="MonitorPlay" :size="14"/>Show on the wall</button>
    </div></div>

    <div class="card"><div class="card-head"><Icon name="Siren" :size="16" style="color:var(--bad)"/><h3>Alert type</h3>
      <span class="faint" style="font-size:12px">click a type to preview its sample · the switch decides whether that kind of alert appears on the wall</span></div>
      <div class="card-body"><div class="hz-grid">
        <div v-for="hz in cat.hazards" :key="hz.id" class="hz-card" :class="{on: (a.sample || 'rain') === hz.id, off: !hazards.includes(hz.id)}"
          @click="pickSample(hz.id)" :title="hz.hint">
          <img :src="'/api/alerts/icon/' + hz.id + '.png?scale=3'" alt="">
          <div class="hz-meta"><b>{{ hz.label }}</b>
            <small>{{ cat.samples?.[hz.id]?.name }} <span class="pill" style="height:17px; font-size:10px">{{ cat.samples?.[hz.id]?.type }}</span></small></div>
          <span @click.stop><Toggle :modelValue="hazards.includes(hz.id)" @update:modelValue="v => toggleHazard(hz.id, v)"/></span>
        </div>
      </div></div></div>

    <div class="two-col">
      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">PREVIEW</span><h3>{{ current?.name }} <span class="faint" style="font-weight:500">· {{ current?.description }}</span></h3>
          <span class="pill" v-if="!cat.alerts?.length">sample alert</span></div>
          <div class="card-body stack">
            <div style="position:relative; background:#000; border-radius:8px; overflow:hidden">
              <img :src="shown || live" style="width:100%; image-rendering:pixelated; display:block" alt="">
              <img :src="live" @load="onLoaded" style="display:none" alt="">
            </div>
            <div class="tourbar" v-if="tour">
              <span class="pill accent">{{ tour.i + 1 }} / {{ ids.length }}</span>
              <b>{{ tourTheme?.name }}</b><span class="faint ellipsis" style="flex:1; font-size:12px">{{ tourTheme?.description }}</span>
              <button class="btn sm ghost icon-only" @click="stepTour(-1)" title="Previous theme"><Icon name="SkipBack" :size="14"/></button>
              <button class="btn sm ghost icon-only" @click="playing = !playing" :title="playing ? 'Pause' : 'Play'"><Icon :name="playing ? 'Pause' : 'Play'" :size="14"/></button>
              <button class="btn sm ghost icon-only" @click="stepTour(1)" title="Next theme"><Icon name="SkipForward" :size="14"/></button>
              <button class="btn sm primary" @click="useTour"><Icon name="Check" :size="14"/>Use this</button>
              <button class="btn sm ghost" @click="stopTour"><Icon name="X" :size="14"/>Stop</button>
            </div>
            <div class="scrub" v-else>
              <button class="btn sm ghost icon-only" @click="playing = !playing" :title="playing ? 'Pause' : 'Play'"><Icon :name="playing ? 'Pause' : 'Play'" :size="14"/></button>
              <div class="scrub-track">
                <button v-for="(p, i) in tl.pages" :key="i" class="scrub-seg" :class="{on: page === p}"
                  :style="{flex: p.hold + p.reveal}" @click="jump(p)" :title="p.label + ' · reveal ' + p.reveal + ' s, holds ' + p.hold + ' s · text ' + p.font_px + ' px'">{{ p.label }} · {{ p.font_px }}px</button>
              </div>
              <span class="mono faint" style="font-size:12px; width:70px; text-align:right">{{ t.toFixed(0) }} / {{ Math.round(tl.total) }} s</span>
            </div>
            <div class="row">
              <button class="btn primary" @click="startTour(0)" v-if="!tour"><Icon name="Clapperboard" :size="15"/>Play all themes here</button>
              <button class="btn" @click="tourWall"><Icon name="MonitorPlay" :size="15"/>Tour all on the wall</button>
              <span class="row nowrap faint" style="font-size:12px">each<Stepper v-model="tourSec" :min="4" :max="30" unit="s"/></span>
            </div>
            <div class="row">
              <button class="btn" @click="show(true)"><Icon name="FlaskConical" :size="15"/>Test on the wall</button>
              <button class="btn" v-if="cat.alerts?.length" @click="show(false)"><Icon name="Siren" :size="15"/>Show current alerts</button>
              <button class="btn ghost" @click="stop"><Icon name="Undo2" :size="15"/>Back to rotation</button>
            </div>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="Siren" :size="16" style="color:var(--bad)"/><h3>In effect now</h3>
          <span class="faint" style="font-size:12px" v-if="cat.updated_utc">{{ cat.area }} · checked {{ ago(Date.now() / 1000 - cat.updated_utc) }}</span></div>
          <div class="list" v-if="cat.alerts?.length">
            <div class="list-row" v-for="(x, i) in cat.alerts" :key="i" style="align-items:flex-start">
              <span class="pill" :style="{background: typeColour(x.type) + '33', color: typeColour(x.type), borderColor: typeColour(x.type) + '66'}">{{ x.type }}</span>
              <div style="flex:1; min-width:0"><b style="text-transform:capitalize">{{ x.name }}</b>
                <span class="pill" :class="x.home ? 'bad' : ''" style="margin-left:6px">{{ x.home ? 'includes ' + (a.home_city || 'Richmond') : 'nearby' }}</span>
                <div class="faint" style="font-size:12px">{{ (x.regions || []).join(' · ') }}</div>
                <div class="faint" style="font-size:12px">issued {{ when(x.published) }}<template v-if="x.expires"> · until {{ when(x.expires) }}</template></div></div>
            </div></div>
          <div class="card-body faint" v-else><Icon name="CircleCheck" :size="15" style="color:var(--ok); vertical-align:-3px"/> No alerts for {{ cat.area || 'this area' }} right now — the alert screen stays out of the rotation.</div>
        </div>
      </div>

      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="LayoutGrid" :size="15"/><h3>Theme</h3><span class="pill">{{ cat.themes?.length }} designs</span>
          <Seg v-model="thumbsOn" :options="[{value:'headline', label:'Finished'}, {value:'page', label:'Revealing'}]"/></div>
          <div class="card-body stack">
            <div class="row" style="flex-wrap:wrap; gap:6px">
              <button class="btn sm" :class="{primary: !layoutFilter}" @click="layoutFilter = ''">All</button>
              <button v-for="(lab, id) in cat.structures" :key="id" class="btn sm" :class="{primary: layoutFilter === id}" @click="layoutFilter = id">{{ lab }}</button>
            </div>
            <div class="theme-grid dense">
              <button v-for="th in themes" :key="th.id" class="theme-card" :class="{on: a.theme === th.id, touring: tourTheme && tourTheme.id === th.id}" @click="tour ? startTour(ids.indexOf(th.id)) : (a.theme = th.id)">
                <img :src="thumb(th.id)" loading="lazy" alt="">
                <span>{{ th.name }}</span><small class="faint">{{ th.description }}</small>
              </button></div></div></div>

        <div class="card"><div class="card-head"><Icon name="Frame" :size="15"/><h3>Border</h3>
          <span class="faint" style="font-size:12px">mix any border with any theme</span></div>
          <div class="card-body"><div class="opt-grid">
            <button class="opt-card" :class="{on: !a.border}" @click="setOpt('border', '')">
              <img :src="optImg({}, borderAt)" alt=""><span>Theme's own · {{ cat.borders?.[themeOf.border] }}</span></button>
            <button v-for="(lab, id) in cat.borders" :key="id" class="opt-card" :class="{on: a.border === id}" @click="setOpt('border', id)">
              <img :src="optImg({ border: id }, borderAt)" alt="" loading="lazy"><span>{{ lab }}</span></button>
          </div></div></div>

        <div class="card"><div class="card-head"><Icon name="Sparkles" :size="15"/><h3>Reveal animation</h3>
          <span class="faint" style="font-size:12px">how the text first appears</span></div>
          <div class="card-body stack"><div class="opt-grid">
            <button class="opt-card" :class="{on: !a.reveal}" @click="setOpt('reveal', '')">
              <img :src="optImg({}, revealAt)" alt=""><span>Theme's own · {{ cat.reveals?.[themeOf.reveal] }}</span></button>
            <button v-for="(lab, id) in cat.reveals" :key="id" class="opt-card" :class="{on: a.reveal === id}" @click="setOpt('reveal', id)">
              <img :src="optImg({ reveal: id, reveal_speed: 1.6 }, revealAt)" alt="" loading="lazy"><span>{{ lab }}</span></button>
          </div>
          <div class="fields">
            <Field label="Key facts pulse" hint="Amounts glow every couple of seconds after the reveal">
              <Seg :modelValue="a.pulse === true ? 'on' : a.pulse === false ? 'off' : ''" @update:modelValue="v => setOpt('pulse', v === '' ? '' : v === 'on')"
                :options="[{value:'', label:'Theme'}, {value:'on', label:'On'}, {value:'off', label:'Off'}]"/></Field>

          </div></div></div>

        <div class="card"><div class="card-head"><Icon name="BookOpenText" :size="15"/><h3>Reading & timing</h3>
          <button class="btn sm ghost" @click="resetLook"><Icon name="RotateCcw" :size="13"/>Defaults</button></div><div class="card-body stack">
          <Field label="How much to show"><Seg :modelValue="a.detail || 'all'" @update:modelValue="v => a.detail = v" :options="DETAIL"/></Field>
          <div class="faint" style="font-size:12.5px; margin-top:-6px">{{ DETAIL.find(d => d.value === (a.detail || 'all')).hint }}. Text uses the largest size of the font family that fits on one screen.</div>
          <Field label="Reading speed" hint="Each alert holds for its words at this speed">
            <Slider :modelValue="a.reading_wpm || 200" @update:modelValue="v => a.reading_wpm = v" :min="100" :max="400" :step="10" unit=" wpm"/></Field>
          <Field label="Shortest hold"><Slider :modelValue="a.min_hold_sec || 12" @update:modelValue="v => a.min_hold_sec = v" :min="4" :max="60" :step="1" unit=" s"/></Field>
          <Field label="Reveal speed" hint="How fast the text types, wipes, scans or decodes in">
            <Slider :modelValue="a.reveal_speed || 1" @update:modelValue="v => a.reveal_speed = v" :min="0.3" :max="3" :step="0.1" :format="v => v.toFixed(1) + '×'"/></Field>
          <div class="muted" style="font-size:13px">One pass through {{ cat.alerts?.length ? 'the current alerts' : 'the sample alert' }} takes about <b>{{ Math.round(tl.total) }} s</b> ({{ tl.pages?.length }} pages).</div>
        </div></div>

        <div class="card"><div class="card-head"><Icon name="Type" :size="15"/><h3>Text & type</h3></div><div class="card-body stack">
          <Field label="Header text" :hint="'Shown by layouts with a header. Theme default: ' + (current?.header || 'WEATHER ALERT')">
            <input class="input" :value="a.header || ''" @input="setOpt('header', $event.target.value.trim())" placeholder="Theme default"></Field>
          <Field label="Capitals"><Seg :modelValue="a.upper === true ? 'on' : a.upper === false ? 'off' : ''" @update:modelValue="v => setOpt('upper', v === '' ? '' : v === 'on')"
            :options="[{value:'', label:'Theme'}, {value:'on', label:'ALL CAPS'}, {value:'off', label:'Normal'}]"/></Field>
          <Field label="Colour palette">
            <div class="palette-row">
              <button class="pal" :class="{on: !a.palette}" @click="setOpt('palette', '')"><span class="faint">Theme</span></button>
              <button v-for="(pl, id) in cat.palettes" :key="id" class="pal" :class="{on: a.palette === id}" @click="setOpt('palette', id)" :title="pl.name">
                <i v-for="(c, k) in pl.swatch" :key="k" :style="{background: c}"></i><span>{{ pl.name }}</span></button>
            </div></Field>
          <div class="fields">
            <Field label="Structure"><select class="input" :value="a.structure || ''" @change="setOpt('structure', $event.target.value)">
              <option v-for="o in opts(cat.structures)" :key="o.value" :value="o.value">{{ o.label }}</option></select></Field>
            <Field label="Font family"><select class="input" :value="a.family || ''" @change="setOpt('family', $event.target.value)">
              <option v-for="o in opts(cat.families)" :key="o.value" :value="o.value">{{ o.label }}</option></select></Field>
            <Field label="Title font"><div class="row nowrap"><select class="input" :value="a.title_font || ''" @change="setOpt('title_font', $event.target.value)">
              <option value="">Family default</option><option v-for="f in cat.fonts" :key="f" :value="f">{{ fontName(f) }}</option></select>
              <Stepper v-if="a.title_font" :modelValue="a.title_scale || 1" @update:modelValue="v => a.title_scale = v" :min="1" :max="4" unit="×"/></div></Field>
            <Field label="Body font" hint="Tried first; the family's sizes are the fallback"><select class="input" :value="a.body_font || ''" @change="setOpt('body_font', $event.target.value)">
              <option value="">Family default</option><option v-for="f in cat.fonts" :key="f" :value="f">{{ fontName(f) }}</option></select></Field>
          </div>
        </div></div>

        <div class="card"><div class="card-head"><Icon name="Filter" :size="15"/><h3>Which alerts</h3></div><div class="card-body stack">
          <Field label="Home city" hint="Alerts that include it come first and it's highlighted; others are labelled NEARBY with a calmer border">
            <input class="input" :value="a.home_city || 'Richmond'" @change="a.home_city = $event.target.value.trim() || 'Richmond'"></Field>
          <Field label="Area" hint="Alerts for any forecast region touching this map area (Lower Mainland catches nearby alerts too)">
            <select class="input" v-model="areaId"><option v-for="[id, ar] in areas" :key="id" :value="id">{{ ar.label }}</option></select></Field>
          <ToggleRow v-for="x in TYPES" :key="x.id" :modelValue="types.includes(x.id)" @update:modelValue="v => toggleType(x.id, v)" :label="x.label" :hint="x.hint"/>

        </div></div>

        <div class="card"><div class="card-head"><Icon name="Repeat" :size="15"/><h3>In the rotation</h3></div><div class="card-body stack">
          <template v-if="entry">
            <div class="muted" style="font-size:13px"><Icon name="CircleCheck" :size="14" style="color:var(--ok); vertical-align:-2px"/> Shown at the start of the rotation whenever an alert is in effect.</div>
            <Field label="Passes through all alerts per visit"><Stepper v-model="entry.cycles" :min="1" :max="5" unit="×"/></Field>
          </template>
          <template v-else>
            <div class="callout"><Icon name="Info" :size="15"/>The alert screen isn't in the rotation yet.</div>
            <div><button class="btn primary" @click="addToRotation"><Icon name="Plus" :size="15"/>Add before the weather maps</button></div>
          </template>
        </div></div>
      </div>
    </div>
  </div>`,
};
