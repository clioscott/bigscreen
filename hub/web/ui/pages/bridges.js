// Bridges now: a grid of the latest DriveBC stills from the bridges (hub/jobs/bridges.py, display/modules/bridges.py).
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, seed, clone, dirty, previewOnDisplay, playOnDisplay, ago } from "../core.js";

const SHAPES = {
  full: { label: "Full width", panel: "bridges_full", side: "right", icon: "RectangleHorizontal" },
  wall: { label: "Whole wall", panel: "bridges_wall", side: "right", icon: "Maximize" },
  right: { label: "One side", panel: "bridges", side: "right", icon: "PanelRight" },
};

export const Bridges = {
  setup() {
    const state = ref(null);
    const cameras = ref([]);
    const shape = ref("full");
    const page = ref(0);
    const tick = ref(0);
    const running = ref(false);
    let t;
    const load = async () => {
      try {
        state.value = await api("/api/bridges/state");
        seed(["modules", "bridges"], { cams: clone(state.value.cams) });       // the defaults, until edited
        if (!store.draft.modules.bridges.cams) store.draft.modules.bridges.cams = clone(state.value.cams);
      } catch (e) { fail(e); }
    };
    onMounted(async () => {
      await load();
      api("/api/bridges/cameras").then((c) => (cameras.value = c)).catch(() => {});
      t = setInterval(() => { tick.value++; load(); }, 30000);
    });
    onBeforeUnmount(() => clearInterval(t));
    const cams = computed(() => store.draft.modules.bridges?.cams || []);
    // what the last fetch found for the bridge in this row (the job keys them by position)
    const found = (i, b) => { const c = state.value?.data?.cams?.[i]; return c && c.label === b.label ? c : null; };
    const camName = (id) => cameras.value.find((c) => c.id === id)?.name || `#${id}`;
    const preview = computed(() => `/api/bridges/preview.png?shape=${shape.value}&page=${page.value}&scale=3&t=${tick.value}`);
    const pages = computed(() => (shape.value === "right" ? Math.ceil(cams.value.length / 4) : 1));
    const add = () => cams.value.push({ label: "New bridge", ids: [] });
    const move = (i, d) => { const a = cams.value; const j = i + d; if (j < 0 || j >= a.length) return; a.splice(j, 0, a.splice(i, 1)[0]); };
    const setCam = (b, id) => { b.ids = [Number(id), ...(b.ids || []).filter((x) => x !== Number(id))]; };
    const refresh = async () => {
      running.value = true;
      try { await api("/api/jobs/bridges/run", { method: "POST" }); toast("Fetching the latest images…"); setTimeout(() => { tick.value++; load(); running.value = false; }, 8000); }
      catch (e) { fail(e); running.value = false; }
    };
    const entry = () => ({ panel: SHAPES[shape.value].panel, rules: [], advance: "cycles", cycles: 1, max_sec: 120 });
    const play = async (hold) => {
      if (dirty.value.length) await previewOnDisplay();
      playOnDisplay(entry(), { side: SHAPES[shape.value].side, hold });
    };
    const addToRotation = () => {
      store.draft.modules.layout_cycle[SHAPES[shape.value].side].push(entry());
      toast(`Added to the ${SHAPES[shape.value].side} rotation (one pass). Save to keep it.`);
    };
    const used = computed(() => ["left", "right"].flatMap((s) => (store.draft.modules.layout_cycle[s] || [])
      .filter((e) => e.panel?.startsWith("bridges")).map((e) => `${s} · ${e.panel.replace("bridges_", "").replace("bridges", "side")}`)));
    return { state, cameras, cams, found, camName, shape, page, pages, preview, add, move, setCam, refresh, running, play, addToRotation, used, SHAPES, ago };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Bridges now</h1>
      <p>The latest DriveBC camera image from each bridge, in a grid. DriveBC's cameras are stills that refresh every 3–15 minutes
        (there's no public live video of these bridges); each tile shows how old its image is.</p></div>
      <button class="btn" :disabled="running" @click="refresh"><Icon :name="running ? 'LoaderCircle' : 'RefreshCw'" :size="15" :class="{spin: running}"/>Fetch now</button></div>
    <div class="two-col">
      <div class="card"><div class="card-head"><Icon name="Construction" :size="16"/><h3>Bridges</h3>
          <span class="spacer"></span><button class="btn sm" @click="add" :disabled="cams.length >= 12"><Icon name="Plus" :size="14"/>Add</button></div>
        <div class="list-row" v-for="(b, i) in cams" :key="i" style="gap:8px">
          <span class="faint mono" style="width:18px">{{ i + 1 }}</span>
          <input class="input" v-model="b.label" style="width:120px" maxlength="24">
          <select class="input grow" :value="b.ids?.[0]" @change="setCam(b, $event.target.value)" style="min-width:0">
            <option v-if="!b.ids?.length" :value="undefined">Pick a DriveBC camera…</option>
            <option v-if="b.ids?.length && !cameras.find(c => c.id === b.ids[0])" :value="b.ids[0]">#{{ b.ids[0] }}</option>
            <option v-for="c in cameras" :key="c.id" :value="c.id">{{ c.name }}{{ c.on ? '' : ' (off)' }}</option></select>
          <span class="pill" :class="found(i, b)?.on ? (found(i, b).cam !== b.ids?.[0] ? 'warn' : 'ok') : found(i, b) ? 'bad' : ''"
            :title="found(i, b)?.cam && found(i, b).cam !== b.ids?.[0] ? 'Using ' + camName(found(i, b).cam) + ': the chosen camera is off' : found(i, b)?.caption">
            {{ found(i, b) ? (found(i, b).on ? (found(i, b).cam !== b.ids?.[0] ? 'fallback' : ago(Date.now() / 1000 - found(i, b).updated)) : found(i, b).why) : 'not fetched yet' }}</span>
          <button class="btn sm ghost icon-only" title="Up" @click="move(i, -1)"><Icon name="ChevronUp" :size="14"/></button>
          <button class="btn sm ghost icon-only" title="Down" @click="move(i, 1)"><Icon name="ChevronDown" :size="14"/></button>
          <button class="btn sm ghost icon-only" title="Remove" @click="cams.splice(i, 1)"><Icon name="X" :size="14"/></button>
        </div>
        <div class="card-body faint" style="font-size:12.5px">The first camera is used while it's on; the defaults also list a fallback or two.
          Changes show on the wall after you save and the next fetch (every 2 min, or Fetch now).</div>
      </div>
      <div class="card sticky"><div class="card-head"><span class="live">LIVE</span><h3>On the wall</h3></div>
        <div class="card-body stack">
          <div class="seg"><button v-for="(s, k) in SHAPES" :key="k" type="button" :class="{on: shape === k}" @click="shape = k; page = 0"><Icon :name="s.icon" :size="14"/>{{ s.label }}</button></div>
          <div class="led"><img :src="preview" alt=""></div>
          <div class="row" v-if="pages > 1"><span class="faint">Page</span>
            <button v-for="p in pages" :key="p" class="btn sm" :class="{primary: page === p - 1}" @click="page = p - 1">{{ p }}</button></div>
          <div class="row">
            <button class="btn sm" @click="play(false)"><Icon name="Play" :size="14"/>Play once</button>
            <button class="btn sm" @click="play(true)"><Icon name="Pause" :size="14"/>Hold</button>
            <span class="spacer"></span>
            <button class="btn sm primary" @click="addToRotation"><Icon name="ListPlus" :size="14"/>Add to the rotation</button></div>
          <div class="faint" style="font-size:12.5px">{{ used.length ? 'In the rotation: ' + used.join(', ') : 'Not in the rotation yet.' }}
            <span v-if="state?.data?.generated_at"> Last fetch {{ ago(Date.now() / 1000 - state.data.generated_at) }}.</span></div>
        </div></div>
    </div>
  </div>`,
};
