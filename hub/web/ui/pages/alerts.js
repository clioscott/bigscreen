import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, ago, seed, refreshDisplay, clone } from "../core.js";

const TYPES = [
  { id: "warning", label: "Warnings", hint: "Severe weather is happening or about to" },
  { id: "watch", label: "Watches", hint: "Conditions favour severe weather" },
  { id: "advisory", label: "Advisories", hint: "Less severe but still a nuisance (frost, fog…)" },
  { id: "statement", label: "Special weather statements", hint: "Heads-up about unusual weather" },
];
const ENTRY = { panel: "weather_alert", when: "weather_alert_active", advance: "cycles", cycles: 1, max_sec: 900 };
const DETAIL = [
  { value: "all", label: "Everything", hint: "The whole ECCC text (minus boilerplate)" },
  { value: "essentials", label: "Essentials", hint: "What's happening, what to expect and what to do" },
];
const DENSITY = [
  { value: "dense", label: "Dense", hint: "Smaller text, as much of the message as possible" },
  { value: "balanced", label: "Balanced", hint: "The whole message at the largest size that fits" },
  { value: "minimal", label: "Minimal", hint: "Big text: what's happening, what to do, and where" },
];
const TABS = [
  { value: "design", label: "Design", icon: "Palette" },
  { value: "review", label: "Review & test on the wall", icon: "ListChecks" },
  { value: "alerts", label: "Alerts & rotation", icon: "Siren" },
];
const VERDICTS = [{ value: "", label: "All" }, { value: "unreviewed", label: "To review" }, { value: "approved", label: "Approved" },
  { value: "dropped", label: "Dropped" }];
// one sample per alert level: what a review "per level" steps through
const PER_LEVEL = ["rain", "thunder", "fog", "smoke"];
// settings that change how a theme looks; they travel to previews and the wall as overrides
const LOOK_KEYS = ["header", "palette", "family", "structure", "border", "reveal", "reveal_speed", "title_font",
  "body_font", "reading_wpm", "min_hold_sec", "detail", "upper", "pulse", "density", "sample"];
const STYLE_KEYS = ["header", "palette", "family", "structure", "border", "reveal", "title_font", "body_font", "upper", "pulse"];

export const Alerts = {
  setup() {
    seed(["modules", "weather_alerts"], { enabled: true, theme: "t01", types: ["warning", "watch", "advisory", "statement"],
      reading_wpm: 200, min_hold_sec: 12, reveal_speed: 1, detail: "all", refresh_interval_sec: 120 });
    const a = computed(() => store.draft.modules.weather_alerts);
    for (const root of [store.saved, store.draft]) {          // ids from older theme sets → the first theme
      const wa = root.modules.weather_alerts;
      if (wa && !/^t\d\d$/.test(wa.theme || "")) wa.theme = "t01";
    }
    seed(["modules", "weather_alerts", "theme_status"], {});     // in saved and draft alike: not an unsaved change
    const verdicts = computed(() => a.value.theme_status || {});
    const verdict = (id) => verdicts.value[id] || "unreviewed";

    let saved = "design";
    try { saved = localStorage.getItem("alertsTab") || "design"; } catch (e) { /* private window */ }
    const tab = ref(TABS.some((x) => x.value === saved) ? saved : "design");
    watch(tab, (v) => { try { localStorage.setItem("alertsTab", v); } catch (e) { /* ignore */ } });

    const cat = ref({ themes: [], structures: {}, borders: {}, reveals: {}, palettes: {}, families: {}, fonts: [], hazards: [], samples: {}, levels: {} });
    const load = async () => { try { cat.value = await api("/api/alerts"); } catch (e) { /* hub restarting */ } };
    const themeById = (id) => (cat.value.themes || []).find((x) => x.id === id) || {};
    const current = computed(() => themeById(a.value.theme));
    const hazardOf = (id) => (cat.value.hazards || []).find((h) => h.id === id) || {};
    const levelColour = (k) => cat.value.levels?.[k] || "#6e7896";
    const sampleLabel = (id) => cat.value.samples?.[id]?.name || id;
    const sampleLevel = (id) => cat.value.samples?.[id]?.type || "";

    // ------------------------------------------------------------------ design: live preview
    const useReal = ref(false);                     // preview the alerts in effect instead of a sample
    const look = computed(() => Object.fromEntries(LOOK_KEYS.filter((k) => a.value[k] !== undefined && a.value[k] !== "").map((k) => [k, a.value[k]])));
    const qs = computed(() => `s=${encodeURIComponent(JSON.stringify(look.value))}&real=${useReal.value ? 1 : 0}`);
    const tl = ref({ total: 30, pages: [] });
    const t = ref(0);
    const playing = ref(true);
    const loadTl = async () => { try { tl.value = await api(`/api/alerts/timeline?${qs.value}`); } catch (e) { /* ignore */ } };
    let tlTimer;
    watch(qs, () => { clearTimeout(tlTimer); tlTimer = setTimeout(loadTl, 300); });
    const live = computed(() => `/api/alerts/preview/${a.value.theme || "t01"}.png?at=${t.value.toFixed(2)}&scale=2&${qs.value}`);
    const page = computed(() => [...(tl.value.pages || [])].reverse().find((p) => t.value >= p.start) || tl.value.pages?.[0]);
    const jump = (p) => { t.value = p.start + 0.05; };
    const pickSample = (id) => { a.value.sample = id; useReal.value = false; };

    // ------------------------------------------------------------------ design: gallery
    const layoutFilter = ref("");
    const verdictFilter = ref("");
    const themes = computed(() => (cat.value.themes || []).filter((x) => (!layoutFilter.value || x.structure === layoutFilter.value) &&
      (!verdictFilter.value || verdict(x.id) === verdictFilter.value)));
    const thumbQs = computed(() => `s=${encodeURIComponent(JSON.stringify({ sample: a.value.sample || "rain", density: a.value.density || "balanced" }))}`);
    const thumb = (id) => `/api/alerts/preview/${id}.png?at=30&scale=1&${thumbQs.value}`;
    const counts = computed(() => {
      const c = { approved: 0, dropped: 0, unreviewed: 0 };
      for (const th of cat.value.themes || []) c[verdict(th.id)]++;
      return c;
    });
    const approvedIds = computed(() => (cat.value.themes || []).map((x) => x.id).filter((id) => verdict(id) === "approved"));
    const setVerdict = (id, v) => {
      const st = a.value.theme_status;
      if (!v || v === "unreviewed" || st[id] === v) delete st[id]; else st[id] = v;
    };
    const setOpt = (k, v) => { if (v === "" || v === null || v === undefined) delete a.value[k]; else a.value[k] = v; };
    const customised = computed(() => STYLE_KEYS.filter((k) => a.value[k] !== undefined && a.value[k] !== ""));
    const resetStyle = () => { for (const k of STYLE_KEYS) delete a.value[k]; };
    const fineTab = ref("look");
    const tileT = ref(0);
    const optImg = (over, at) => `/api/alerts/preview/${a.value.theme || "t01"}.png?at=${at.toFixed(2)}&scale=1&s=${encodeURIComponent(JSON.stringify({ ...look.value, ...over }))}`;
    const borderAt = computed(() => 20 + (tileT.value % 8) * 0.5);
    const revealAt = computed(() => (tileT.value % 12) * 0.3);
    const opts = (dict) => [{ value: "", label: "Theme's own" }, ...Object.entries(dict || {}).map(([value, label]) => ({ value, label }))];
    const fontName = (f) => f.replace(/^(alert|candidates)\//, "").replace(".bdf", "");

    // ------------------------------------------------------------------ the wall
    const wall = ref("");                // "" | "live" (this page's settings) | "review" | "tour" | "real"
    const nullLook = () => Object.fromEntries(LOOK_KEYS.map((k) => [k, null]));
    const overlay = async (settings, ttl = 4 * 3600) => api("/api/display/preview", { json: { overlay: { modules: { weather_alerts: settings } }, ttl_sec: ttl } });
    const pin = async (extra, sec = 4 * 3600) => api("/api/display/override", { json: { side: "right", panel: "weather_alert", duration_sec: sec, ...extra } });
    let wallTestState = null;
    const showLive = async () => {
      try {
        const s = clone(a.value);
        for (const k of LOOK_KEYS) if (s[k] === undefined || s[k] === "") s[k] = null;     // unset beats saved
        await overlay({ ...s, theme_mode: "fixed", review_label: null });
        if (wall.value !== "live" || wallTestState !== !useReal.value) {
          await pin(useReal.value && cat.value.alerts?.length ? {} : { test: true });
          wallTestState = !useReal.value;
        }
        wall.value = "live";
      } catch (e) { fail(e); }
    };
    let liveTimer;
    watch(() => JSON.stringify(a.value) + useReal.value, () => {
      if (wall.value !== "live") return;           // only follows edits while "live on the wall" is on
      clearTimeout(liveTimer);
      liveTimer = setTimeout(showLive, 350);
    });
    const showReal = async () => {
      try {
        await api("/api/display/preview/clear", { method: "POST" });
        await pin({}, 300);
        wall.value = "real";
        toast("Showing the alerts in effect on the wall for 5 min");
        setTimeout(refreshDisplay, 500);
      } catch (e) { fail(e); }
    };
    const backToRotation = async () => {
      try {
        await api("/api/display/override/clear", { json: { side: "right" } });
        await api("/api/display/preview/clear", { method: "POST" });
        wall.value = "";
        wallTestState = null;
        toast("Back to the rotation");
      } catch (e) { fail(e); }
    };

    // ------------------------------------------------------------------ review: themes × test alerts, approve / drop
    const revThemes = ref("unreviewed");
    const revLayout = ref("");
    const revAlerts = ref([...PER_LEVEL]);
    const revOrder = ref("theme");       // "theme": each theme through the alerts · "alert": each alert through the themes
    const revThemeList = computed(() => (cat.value.themes || []).filter((x) => (!revLayout.value || x.structure === revLayout.value) &&
      (!revThemes.value || verdict(x.id) === revThemes.value)));
    const items = computed(() => {
      const ths = revThemeList.value.map((x) => x.id);
      const als = revAlerts.value.length ? revAlerts.value : [a.value.sample || "rain"];
      return revOrder.value === "theme" ? ths.flatMap((th) => als.map((s) => [th, s])) : als.flatMap((s) => ths.map((th) => [th, s]));
    });
    const toggleRevAlert = (id) => {
      const i = revAlerts.value.indexOf(id);
      if (i >= 0) revAlerts.value.splice(i, 1); else revAlerts.value.push(id);
    };
    const queue = ref([]);               // frozen when a review starts, so verdicts don't reshuffle it
    const idx = ref(-1);
    const item = computed(() => (idx.value >= 0 ? queue.value[idx.value] : null));
    const mirror = ref(true);
    const auto = ref(false);
    const autoSec = ref(8);
    const rt = ref(0);                   // seconds into the current item (browser preview clock)
    const revImg = computed(() => item.value
      ? `/api/alerts/preview/${item.value[0]}.png?at=${rt.value.toFixed(2)}&scale=2&s=${encodeURIComponent(JSON.stringify({ sample: item.value[1], density: a.value.density || "balanced", detail: a.value.detail || "all", reveal_speed: 1.6 }))}`
      : "");
    const revLabel = computed(() => {
      if (!item.value) return "";
      const th = themeById(item.value[0]);
      return `REVIEW ${idx.value + 1}/${queue.value.length}  ${(th.name || "").toUpperCase()}  ·  ${sampleLabel(item.value[1]).toUpperCase()}`;
    });
    let pushTimer;
    const pushReview = () => {
      clearTimeout(pushTimer);
      pushTimer = setTimeout(async () => {
        if (!item.value || !mirror.value) return;
        try {
          await overlay({ ...nullLook(), theme: item.value[0], sample: item.value[1], theme_mode: "fixed",
            density: a.value.density || null, detail: a.value.detail || null, review_label: revLabel.value }, 2 * 3600);
          if (wall.value !== "review") { await pin({ test: true }, 2 * 3600); wall.value = "review"; }
        } catch (e) { fail(e); }
      }, 150);
    };
    watch(item, () => { rt.value = 0; pushReview(); });
    watch(mirror, (on) => { if (on) pushReview(); });
    const startReview = (from = 0) => {
      if (!items.value.length) return toast("Nothing to review with these choices");
      queue.value = items.value.slice();
      idx.value = Math.min(from, queue.value.length - 1);
      pushReview();
    };
    const stopReview = () => { idx.value = -1; auto.value = false; };
    const step = (d) => { if (idx.value >= 0) idx.value = Math.max(0, Math.min(queue.value.length - 1, idx.value + d)); };
    const nextTheme = () => {          // first item of the next theme (or next alert group) in the queue
      if (!item.value) return;
      const key = revOrder.value === "theme" ? 0 : 1;
      const cur = item.value[key];
      let j = idx.value + 1;
      while (j < queue.value.length && queue.value[j][key] === cur) j++;
      if (j < queue.value.length) idx.value = j; else { toast("That was the last one"); auto.value = false; }
    };
    const judge = (v) => {
      if (!item.value) return;
      setVerdict(item.value[0], v);
      if (revOrder.value === "theme") nextTheme(); else step(1);
    };
    const onKey = (e) => {
      if (tab.value !== "review" || idx.value < 0 || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
      if (e.key === "a") judge("approved");
      else if (e.key === "d") judge("dropped");
      else if (e.key === "ArrowRight" || e.key === " ") { e.preventDefault(); step(1); }
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "ArrowDown") { e.preventDefault(); nextTheme(); }
      else if (e.key === "Escape") stopReview();
    };

    // unattended: the wall plays a list of (theme, test alert) on its own
    const tourSec = ref(9);
    const tourWall = async (list, what) => {
      if (!list.length) return toast("Nothing to tour");
      try {
        await api("/api/display/preview/clear", { method: "POST" });
        await pin({ test: true, tour: true, tour_items: list, tour_sec: tourSec.value }, Math.round(list.length * tourSec.value + 3));
        wall.value = "tour";
        stopReview();
        toast(`The wall is touring ${list.length} ${what}, ${tourSec.value} s each (about ${Math.max(1, Math.round(list.length * tourSec.value / 60))} min)`);
      } catch (e) { fail(e); }
    };
    const allSamples = computed(() => Object.keys(cat.value.samples || {}));

    // ------------------------------------------------------------------ alerts & rotation
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
    const entry = computed(() => ["left", "right"].map((s) => (store.draft.modules.layout_cycle[s] || []).find((e) => e.panel === "weather_alert")).find(Boolean));
    const addToRotation = () => {
      const r = (store.draft.modules.layout_cycle.right = store.draft.modules.layout_cycle.right || []);
      r.unshift({ ...ENTRY });
      toast("Added to the start of the right-half rotation — save to apply");
    };
    const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "");

    // ------------------------------------------------------------------ clocks
    let tick, tileTick;
    onMounted(() => {
      load().then(loadTl);
      window.addEventListener("keydown", onKey);
      tileTick = setInterval(() => { tileT.value += 1; }, 450);
      tick = setInterval(() => {
        if (idx.value >= 0) {
          rt.value += 0.5;
          if (auto.value && rt.value >= autoSec.value) {
            if (idx.value + 1 < queue.value.length) idx.value += 1; else auto.value = false;
          } else if (rt.value > 24) rt.value = 0;
        }
        if (playing.value && tab.value === "design") t.value = (t.value + 0.5) % Math.max(4, tl.value.total || 30);
      }, 500);
    });
    onBeforeUnmount(() => { clearInterval(tick); clearInterval(tileTick); clearTimeout(tlTimer); clearTimeout(liveTimer); window.removeEventListener("keydown", onKey); });

    return { TABS, TYPES, DETAIL, DENSITY, VERDICTS, PER_LEVEL, tab, a, cat, current, hazardOf, levelColour, sampleLabel, sampleLevel,
      useReal, tl, t, playing, live, page, jump, pickSample,
      layoutFilter, verdictFilter, themes, thumb, counts, approvedIds, verdict, setVerdict, setOpt, customised, resetStyle, fineTab,
      optImg, borderAt, revealAt, opts, fontName,
      wall, showLive, showReal, backToRotation,
      revThemes, revLayout, revAlerts, revOrder, revThemeList, items, toggleRevAlert, queue, idx, item, mirror, auto, autoSec, rt, revImg,
      startReview, stopReview, step, nextTheme, judge, tourSec, tourWall, allSamples, themeById,
      types, toggleType, hazards, toggleHazard, areas, areaId, entry, addToRotation, when, ago };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Weather alerts</h1>
      <p>When Environment Canada has an alert in effect for your area, the wall shows it full screen at the start of the rotation: the whole message on one screen, in colours that match the weather, with a label box for its level.</p></div>
      <Toggle v-model="a.enabled"/></div>

    <!-- status: what's in effect, what the wall is doing -->
    <div class="card"><div class="card-body row" style="gap:12px">
      <template v-if="cat.alerts?.length">
        <Icon name="Siren" :size="16" style="color:var(--bad)"/>
        <b>{{ cat.alerts.length }} in effect</b>
        <span v-for="(x, i) in cat.alerts" :key="i" class="pill" :style="{color: levelColour(x.type), borderColor: levelColour(x.type) + '66'}" style="text-transform:capitalize">{{ x.name }}</span>
      </template>
      <template v-else><Icon name="CircleCheck" :size="16" style="color:var(--ok)"/><span class="muted">No alerts for {{ cat.area || 'your area' }} right now</span></template>
      <span class="faint" style="font-size:12px" v-if="cat.updated_utc">· checked {{ ago(Date.now() / 1000 - cat.updated_utc) }}</span>
      <span class="spacer" style="flex:1"></span>
      <template v-if="wall"><span class="live">ON THE WALL</span>
        <span class="faint" style="font-size:12px">{{ {live: 'your settings, updating as you edit', review: 'the review', tour: 'a tour', real: 'the alerts in effect'}[wall] }}</span>
        <button class="btn sm" @click="backToRotation"><Icon name="Undo2" :size="14"/>Back to rotation</button></template>
      <template v-else>
        <button class="btn sm" v-if="cat.alerts?.length" @click="showReal"><Icon name="Siren" :size="14"/>Show alerts in effect</button>
        <button class="btn sm" @click="showLive"><Icon name="MonitorPlay" :size="14"/>Show this design on the wall</button></template>
    </div></div>

    <div class="row" style="gap:8px; margin:4px 0 14px" role="tablist">
      <button v-for="x in TABS" :key="x.value" role="tab" :aria-selected="tab === x.value" class="btn" :class="{primary: tab === x.value}" @click="tab = x.value">
        <Icon :name="x.icon" :size="15"/>{{ x.label }}
        <span v-if="x.value === 'review'" class="pill" :style="tab === x.value ? {marginLeft: '4px', background: 'rgba(0,0,0,.28)', color: '#fff', borderColor: 'transparent'} : {marginLeft: '4px'}">{{ counts.unreviewed }} to review</span></button>
    </div>

    <!-- ================================================================ DESIGN -->
    <div class="two-col" v-show="tab === 'design'">
      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">PREVIEW</span>
          <h3>{{ current.name }} <span class="faint" style="font-weight:500">· {{ current.description }}</span></h3>
          <span class="pill" :class="{ok: verdict(a.theme) === 'approved', bad: verdict(a.theme) === 'dropped'}">{{ verdict(a.theme) === 'unreviewed' ? 'not reviewed' : verdict(a.theme) }}</span></div>
          <div class="card-body stack">
            <div style="background:#000; border-radius:8px; overflow:hidden"><img :src="live" style="width:100%; image-rendering:pixelated; display:block" alt=""></div>
            <div class="scrub">
              <button class="btn sm ghost icon-only" @click="playing = !playing" :title="playing ? 'Pause' : 'Play'"><Icon :name="playing ? 'Pause' : 'Play'" :size="14"/></button>
              <div class="scrub-track">
                <button v-for="(p, i) in tl.pages" :key="i" class="scrub-seg" :class="{on: page === p}" :style="{flex: p.hold + p.reveal}" @click="jump(p)"
                  :title="p.label + ' · holds ' + p.hold + ' s · text ' + p.font_px + ' px'">{{ p.label }} · {{ p.font_px }}px</button>
              </div>
              <span class="mono faint" style="font-size:12px; width:70px; text-align:right">{{ t.toFixed(0) }} / {{ Math.round(tl.total) }} s</span>
            </div>
            <Field label="Preview with">
              <div class="row" style="gap:6px">
                <button v-if="cat.alerts?.length" class="btn sm" :class="{primary: useReal}" @click="useReal = true"><Icon name="Siren" :size="13"/>Alerts in effect</button>
                <button v-for="h in cat.hazards" :key="h.id" class="btn sm" :class="{primary: !useReal && (a.sample || 'rain') === h.id}" @click="pickSample(h.id)"
                  :title="sampleLabel(h.id) + ' (' + sampleLevel(h.id) + ')'">
                  <i :style="{display:'inline-block', width:'9px', height:'9px', borderRadius:'2px', background: h.colour}"></i>{{ h.label.split(' ')[0] }}
                  <span class="lvl" :style="{background: levelColour(sampleLevel(h.id))}">{{ sampleLevel(h.id) }}</span></button>
              </div></Field>
            <Field label="Text density" :hint="DENSITY.find(d => d.value === (a.density || 'balanced')).hint + ' — applies to every theme'">
              <Seg :modelValue="a.density || 'balanced'" @update:modelValue="v => a.density = v" :options="DENSITY"/></Field>
          </div></div>
      </div>

      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="LayoutGrid" :size="15"/><h3>Theme</h3>
          <span class="pill ok">{{ counts.approved }} approved</span><span class="pill bad">{{ counts.dropped }} dropped</span></div>
          <div class="card-body stack">
            <Field label="On the wall, use" :hint="a.theme_mode === 'rotate' ? (approvedIds.length ? 'Each time alerts come round, the next of your ' + approvedIds.length + ' approved themes' : 'No approved themes yet — the chosen theme is used until you approve some') : 'The theme picked below, every time'">
              <Seg :modelValue="a.theme_mode || 'fixed'" @update:modelValue="v => a.theme_mode = v"
                :options="[{value:'fixed', label:'This theme'}, {value:'rotate', label:'Rotate approved themes'}]"/></Field>
            <div class="row" style="gap:10px">
              <Seg v-model="verdictFilter" :options="VERDICTS"/>
              <select class="input" v-model="layoutFilter" style="width:auto"><option value="">Every layout</option>
                <option v-for="(lab, id) in cat.structures" :key="id" :value="id">{{ lab }}</option></select>
            </div>
            <div class="theme-grid dense">
              <button v-for="th in themes" :key="th.id" class="theme-card" :class="{on: a.theme === th.id}" @click="a.theme = th.id" style="position:relative">
                <img :src="thumb(th.id)" loading="lazy" alt="">
                <span>{{ th.name }}</span><small class="faint">{{ th.description }}</small>
                <span v-if="verdict(th.id) !== 'unreviewed'" class="pill" :class="verdict(th.id) === 'approved' ? 'ok' : 'bad'"
                  style="position:absolute; top:10px; right:10px; height:18px; padding:0 6px">{{ verdict(th.id) === 'approved' ? '✓' : '✗' }}</span>
              </button>
              <div v-if="!themes.length" class="faint" style="padding:8px">No themes match.</div>
            </div>
            <div class="row">
              <button class="btn sm" :class="{primary: verdict(a.theme) === 'approved'}" @click="setVerdict(a.theme, 'approved')"><Icon name="Check" :size="13"/>Approve {{ current.name }}</button>
              <button class="btn sm" :class="{danger: verdict(a.theme) === 'dropped'}" @click="setVerdict(a.theme, 'dropped')"><Icon name="X" :size="13"/>Drop</button>
              <span class="faint" style="font-size:12px">or review them one by one on the wall in the next tab</span>
            </div>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="SlidersHorizontal" :size="15"/><h3>Fine-tune this theme</h3>
          <span class="pill accent" v-if="customised.length">{{ customised.length }} changed</span>
          <button class="btn sm ghost" @click="resetStyle" :disabled="!customised.length"><Icon name="RotateCcw" :size="13"/>Theme's own</button></div>
          <div class="card-body stack">
            <Seg v-model="fineTab" :options="[{value:'look', label:'Colours & type'}, {value:'motion', label:'Border & reveal'}, {value:'text', label:'Wording'}]"/>
            <template v-if="fineTab === 'look'">
              <div class="faint" style="font-size:12.5px">The main colours always follow the weather (rain blue, snow white…); a palette sets the ground and text colours around them.</div>
              <Field label="Palette"><div class="palette-row">
                <button class="pal" :class="{on: !a.palette}" @click="setOpt('palette', '')"><span class="faint">Theme's own</span></button>
                <button v-for="(pl, id) in cat.palettes" :key="id" class="pal" :class="{on: a.palette === id}" @click="setOpt('palette', id)" :title="pl.name">
                  <i v-for="(c, k) in pl.swatch" :key="k" :style="{background: c}"></i><span>{{ pl.name }}</span></button>
              </div></Field>
              <div class="fields">
                <Field label="Layout"><select class="input" :value="a.structure || ''" @change="setOpt('structure', $event.target.value)">
                  <option v-for="o in opts(cat.structures)" :key="o.value" :value="o.value">{{ o.label }}</option></select></Field>
                <Field label="Font family"><select class="input" :value="a.family || ''" @change="setOpt('family', $event.target.value)">
                  <option v-for="o in opts(cat.families)" :key="o.value" :value="o.value">{{ o.label }}</option></select></Field>
                <Field label="Title font" hint="Approved fonts, native size"><select class="input" :value="a.title_font || ''" @change="setOpt('title_font', $event.target.value)">
                  <option value="">Family's own</option><option v-for="f in cat.fonts" :key="f" :value="f">{{ fontName(f) }}</option></select></Field>
                <Field label="Body font" hint="Tried first; smaller sizes step in to fit"><select class="input" :value="a.body_font || ''" @change="setOpt('body_font', $event.target.value)">
                  <option value="">Family's own</option><option v-for="f in cat.fonts" :key="f" :value="f">{{ fontName(f) }}</option></select></Field>
              </div>
            </template>
            <template v-if="fineTab === 'motion'">
              <Field label="Border"><div class="opt-grid">
                <button class="opt-card" :class="{on: !a.border}" @click="setOpt('border', '')"><img :src="optImg({}, borderAt)" alt=""><span>Theme's own</span></button>
                <button v-for="(lab, id) in cat.borders" :key="id" class="opt-card" :class="{on: a.border === id}" @click="setOpt('border', id)">
                  <img :src="optImg({ border: id }, borderAt)" alt="" loading="lazy"><span>{{ lab }}</span></button>
              </div></Field>
              <Field label="Reveal (how the text first appears)"><div class="opt-grid">
                <button class="opt-card" :class="{on: !a.reveal}" @click="setOpt('reveal', '')"><img :src="optImg({}, revealAt)" alt=""><span>Theme's own</span></button>
                <button v-for="(lab, id) in cat.reveals" :key="id" class="opt-card" :class="{on: a.reveal === id}" @click="setOpt('reveal', id)">
                  <img :src="optImg({ reveal: id, reveal_speed: 1.6 }, revealAt)" alt="" loading="lazy"><span>{{ lab }}</span></button>
              </div></Field>
              <Field label="Amounts pulse" hint="Key amounts glow every couple of seconds">
                <Seg :modelValue="a.pulse === true ? 'on' : a.pulse === false ? 'off' : ''" @update:modelValue="v => setOpt('pulse', v === '' ? '' : v === 'on')"
                  :options="[{value:'', label:'Theme'}, {value:'on', label:'On'}, {value:'off', label:'Off'}]"/></Field>
            </template>
            <template v-if="fineTab === 'text'">
              <Field label="Header text" :hint="'Used by the newspaper masthead. Theme default: ' + (current.header || 'WEATHER ALERT')">
                <input class="input" :value="a.header || ''" @input="setOpt('header', $event.target.value.trim())" placeholder="Theme default"></Field>
              <Field label="Capitals"><Seg :modelValue="a.upper === true ? 'on' : a.upper === false ? 'off' : ''" @update:modelValue="v => setOpt('upper', v === '' ? '' : v === 'on')"
                :options="[{value:'', label:'Theme'}, {value:'on', label:'ALL CAPS'}, {value:'off', label:'Normal'}]"/></Field>
              <Field label="How much of the message"><Seg :modelValue="a.detail || 'all'" @update:modelValue="v => a.detail = v" :options="DETAIL"/></Field>
            </template>
          </div></div>
      </div>
    </div>

    <!-- ================================================================ REVIEW -->
    <div class="stack" v-show="tab === 'review'">
      <div class="card" v-if="idx < 0"><div class="card-head"><Icon name="ListChecks" :size="15"/><h3>Review themes on the wall</h3>
        <span class="pill ok">{{ counts.approved }} approved</span><span class="pill bad">{{ counts.dropped }} dropped</span><span class="pill">{{ counts.unreviewed }} to review</span></div>
        <div class="card-body stack">
          <div class="faint" style="font-size:12.5px">Step through themes with test alerts; each one plays on the wall (with a caption saying what it is) and here. Approve or drop with the buttons or the <b>A</b> / <b>D</b> keys — then Save to keep the verdicts. Approved themes can take turns on the wall (Design tab → Rotate approved themes).</div>
          <div class="row" style="gap:14px; align-items:flex-end">
            <Field label="Themes"><Seg v-model="revThemes" :options="VERDICTS"/></Field>
            <Field label="Layout"><select class="input" v-model="revLayout" style="width:auto"><option value="">Every layout</option>
              <option v-for="(lab, id) in cat.structures" :key="id" :value="id">{{ lab }}</option></select></Field>
          </div>
          <Field label="Test alerts">
            <div class="row" style="gap:6px; margin-bottom:6px">
              <button class="btn sm" @click="revAlerts = [...PER_LEVEL]">One per level</button>
              <button class="btn sm" @click="revAlerts = Object.keys(cat.samples || {})">Every weather type</button>
              <button class="btn sm" @click="revAlerts = [a.sample || 'rain']">Just {{ hazardOf(a.sample || 'rain').label }}</button>
            </div>
            <div class="row" style="gap:6px">
              <button v-for="h in cat.hazards" :key="h.id" class="btn sm" :class="{primary: revAlerts.includes(h.id)}" @click="toggleRevAlert(h.id)">
                <i :style="{display:'inline-block', width:'9px', height:'9px', borderRadius:'2px', background: h.colour}"></i>{{ sampleLabel(h.id) }}
                  <span class="lvl" :style="{background: levelColour(sampleLevel(h.id))}">{{ sampleLevel(h.id) }}</span></button>
            </div></Field>
          <Field label="Order"><Seg v-model="revOrder" :options="[{value:'theme', label:'Theme by theme'}, {value:'alert', label:'Alert by alert'}]"/></Field>
          <div class="row">
            <button class="btn primary start-review" @click="startReview(0)" :disabled="!items.length"><Icon name="Play" :size="15"/>Review {{ revThemeList.length }} themes × {{ revAlerts.length || 1 }} alerts ({{ items.length }} screens)</button>
            <label class="row" style="gap:6px; font-size:12.5px"><input type="checkbox" v-model="mirror">mirror on the wall</label>
          </div>
        </div></div>

      <div class="card" v-else style="border-color:var(--accent)">
        <div class="card-head"><Icon name="Eye" :size="15"/>
          <h3>{{ themeById(item[0]).name }} <span class="faint" style="font-weight:500">· {{ themeById(item[0]).description }}</span></h3>
          <span class="pill" :class="{ok: verdict(item[0]) === 'approved', bad: verdict(item[0]) === 'dropped'}">{{ verdict(item[0]) === 'unreviewed' ? 'not reviewed' : verdict(item[0]) }}</span>
          <span class="faint mono" style="font-size:12px">{{ idx + 1 }} / {{ queue.length }}</span>
          <button class="btn sm ghost" @click="stopReview"><Icon name="X" :size="13"/>Close</button></div>
        <div class="card-body stack">
          <div style="background:#000; border-radius:8px; overflow:hidden"><img :src="revImg" style="width:100%; image-rendering:pixelated; display:block" alt=""></div>
          <div class="row" style="gap:8px">
            <span class="pill" :style="{color: levelColour(sampleLevel(item[1]))}"><i :style="{display:'inline-block', width:'9px', height:'9px', borderRadius:'2px', background: hazardOf(item[1]).colour}"></i>{{ sampleLabel(item[1]) }} · {{ sampleLevel(item[1]) }}</span>
            <div style="flex:1; height:4px; background:var(--surface-3); border-radius:2px; overflow:hidden"><div :style="{width: (100 * (idx + 1) / queue.length) + '%', height:'100%', background:'var(--accent)'}"></div></div>
          </div>
          <div class="row" style="gap:8px">
            <button class="btn" @click="step(-1)" :disabled="idx === 0"><Icon name="ChevronLeft" :size="15"/>Prev</button>
            <button class="btn" :class="{primary: verdict(item[0]) === 'approved'}" @click="judge('approved')"><Icon name="Check" :size="15"/>Approve <span class="faint">A</span></button>
            <button class="btn" :class="{danger: verdict(item[0]) === 'dropped'}" @click="judge('dropped')"><Icon name="X" :size="15"/>Drop <span class="faint">D</span></button>
            <button class="btn" @click="step(1)" :disabled="idx + 1 >= queue.length">Next<Icon name="ChevronRight" :size="15"/></button>
            <button class="btn ghost" @click="nextTheme" v-if="revOrder === 'theme'">Skip theme<Icon name="ChevronsRight" :size="15"/></button>
            <span class="spacer" style="flex:1"></span>
            <button class="btn sm" :class="{primary: auto}" @click="auto = !auto"><Icon :name="auto ? 'Pause' : 'Play'" :size="13"/>{{ auto ? 'Auto' : 'Auto-advance' }}</button>
            <Stepper v-model="autoSec" :min="4" :max="30" unit="s"/>
            <label class="row" style="gap:6px; font-size:12.5px"><input type="checkbox" v-model="mirror">on the wall</label>
          </div>
          <div class="faint" style="font-size:12px">Keys: <b>A</b> approve · <b>D</b> drop (both jump to the next theme) · <b>←</b> / <b>→</b> or space step · <b>↓</b> skip theme · <b>Esc</b> close. Verdicts are part of the page's changes — Save to keep them.</div>
        </div></div>

      <div class="card"><div class="card-head"><Icon name="Clapperboard" :size="15"/><h3>Unattended tours on the wall</h3>
        <span class="row nowrap faint" style="font-size:12px">each<Stepper v-model="tourSec" :min="4" :max="30" unit="s"/></span></div>
        <div class="card-body stack">
          <div class="faint" style="font-size:12.5px">The wall plays these by itself, each with a caption and a progress line, then goes back to the rotation.</div>
          <div class="row">
            <button class="btn" @click="tourWall(items, 'screens (the review selection above)')"><Icon name="ListVideo" :size="15"/>The review selection ({{ items.length }})</button>
            <button class="btn" @click="tourWall(allSamples.map(s => [a.theme, s]), 'weather types in ' + current.name)"><Icon name="CloudRainWind" :size="15"/>Every weather type in {{ current.name }}</button>
            <button class="btn" @click="tourWall(PER_LEVEL.map(s => [a.theme, s]), 'alert levels in ' + current.name)"><Icon name="Layers" :size="15"/>Each level in {{ current.name }}</button>
            <button class="btn" @click="tourWall(approvedIds.map(id => [id, a.sample || 'rain']), 'approved themes')" :disabled="!approvedIds.length"><Icon name="BadgeCheck" :size="15"/>Approved themes ({{ approvedIds.length }})</button>
            <button class="btn" @click="tourWall((cat.themes || []).map(x => [x.id, a.sample || 'rain']), 'themes')"><Icon name="LayoutGrid" :size="15"/>All {{ cat.themes?.length }} themes</button>
          </div>
          <div class="row">
            <button class="btn" v-if="cat.alerts?.length" @click="showReal"><Icon name="Siren" :size="15"/>Show the alerts in effect</button>
            <button class="btn ghost" @click="backToRotation"><Icon name="Undo2" :size="15"/>Back to rotation</button>
          </div>
        </div></div>
    </div>

    <!-- ================================================================ ALERTS & ROTATION -->
    <div class="two-col" v-show="tab === 'alerts'">
      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="Filter" :size="15"/><h3>Which alerts appear</h3></div><div class="card-body stack">
          <div class="fields">
            <Field label="Area" hint="Alerts for any forecast region touching this map area">
              <select class="input" v-model="areaId"><option v-for="[id, ar] in areas" :key="id" :value="id">{{ ar.label }}</option></select></Field>
            <Field label="Home city" hint="Its alerts come first; its region is tinted">
              <input class="input" :value="a.home_city || 'Richmond'" @change="a.home_city = $event.target.value.trim() || 'Richmond'"></Field>
          </div>
          <div class="section-title" style="margin:4px 0 0">Levels</div>
          <ToggleRow v-for="x in TYPES" :key="x.id" :modelValue="types.includes(x.id)" @update:modelValue="v => toggleType(x.id, v)" :hint="x.hint">
            <template #label><span class="pill" :style="{background: levelColour(x.id), color:'#000', borderColor:'transparent'}">{{ x.id.toUpperCase() }}</span> {{ x.label }}</template></ToggleRow>
          <div class="section-title" style="margin:4px 0 0">Weather types</div>
          <div class="hz-grid">
            <div v-for="hz in cat.hazards" :key="hz.id" class="hz-card" :class="{off: !hazards.includes(hz.id)}" :title="hz.hint">
              <img :src="'/api/alerts/icon/' + hz.id + '.png?scale=3&colour=' + (hz.colour || '#ffffff').slice(1)" alt="">
              <div class="hz-meta"><b>{{ hz.label }}</b><small>{{ hz.hint }}</small></div>
              <Toggle :modelValue="hazards.includes(hz.id)" @update:modelValue="v => toggleHazard(hz.id, v)"/>
            </div></div>
        </div></div>
      </div>
      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="Timer" :size="15"/><h3>Timing</h3></div><div class="card-body stack">
          <Field label="Reading speed" hint="Each alert holds for its words at this speed">
            <Slider :modelValue="a.reading_wpm || 200" @update:modelValue="v => a.reading_wpm = v" :min="100" :max="400" :step="10" unit=" wpm"/></Field>
          <Field label="Shortest hold"><Slider :modelValue="a.min_hold_sec || 12" @update:modelValue="v => a.min_hold_sec = v" :min="4" :max="60" :step="1" unit=" s"/></Field>
          <Field label="Reveal speed" hint="How fast the text types, wipes, scans or decodes in">
            <Slider :modelValue="a.reveal_speed || 1" @update:modelValue="v => a.reveal_speed = v" :min="0.3" :max="3" :step="0.1" :format="v => v.toFixed(1) + '×'"/></Field>
          <ToggleRow :modelValue="a.show_map !== false" @update:modelValue="v => a.show_map = v" label="Map screen after each alert" hint="The affected zones on a map, zoomed to fit them"/>
          <div class="muted" style="font-size:13px">One pass through {{ useReal && cat.alerts?.length ? 'the current alerts' : 'the sample alert' }} takes about <b>{{ Math.round(tl.total) }} s</b>.</div>
        </div></div>
        <div class="card"><div class="card-head"><Icon name="Repeat" :size="15"/><h3>In the rotation</h3></div><div class="card-body stack">
          <template v-if="entry">
            <div class="muted" style="font-size:13px"><Icon name="CircleCheck" :size="14" style="color:var(--ok); vertical-align:-2px"/> Shown at the start of the rotation whenever an alert is in effect.</div>
            <Field label="Passes through all alerts per visit"><Stepper v-model="entry.cycles" :min="1" :max="5" unit="×"/></Field>
          </template>
          <template v-else>
            <div class="callout"><Icon name="Info" :size="15"/>The alert screen isn't in the rotation yet.</div>
            <div><button class="btn primary" @click="addToRotation"><Icon name="Plus" :size="15"/>Add to the rotation</button></div>
          </template>
        </div></div>
        <div class="card"><div class="card-head"><Icon name="Siren" :size="16" style="color:var(--bad)"/><h3>In effect now</h3></div>
          <div class="list" v-if="cat.alerts?.length">
            <div class="list-row" v-for="(x, i) in cat.alerts" :key="i" style="align-items:flex-start">
              <span class="pill" :style="{background: levelColour(x.type), color: '#000', borderColor: 'transparent'}">{{ x.type }}</span>
              <div style="flex:1; min-width:0"><b style="text-transform:capitalize">{{ x.name }}</b>
                <div class="faint" style="font-size:12px">{{ (x.regions || []).join(' · ') }}</div>
                <div class="faint" style="font-size:12px">issued {{ when(x.published) }}<template v-if="x.expires"> · until {{ when(x.expires) }}</template></div></div>
            </div></div>
          <div class="card-body faint" v-else>None right now — the alert screen stays out of the rotation.</div>
        </div>
      </div>
    </div>
  </div>`,
};
