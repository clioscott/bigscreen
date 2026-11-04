// US election results: the feed, and how the screen is on the wall (hub/jobs/election.py, display/modules/us_election.py).
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, clone, dirty, previewOnDisplay, playOnDisplay, rotationAction, ago, ELECTION_VIEWS } from "../core.js";

const PANEL = "us_election_tall";
const SIDE = "right";
const VIEWS = { cycle: "Cycle all three", senate: "Senate", house: "House", governor: "Governors" };
const HOLDS = { 1800: "30 min", 3600: "1 hour", 7200: "2 hours", 14400: "4 hours", 0: "Until stopped" };

export const Election = {
  setup() {
    const state = ref(null);
    const tick = ref(0);
    const running = ref(false);
    const holdFor = ref(14400);
    let t;
    const load = async () => {
      try {
        state.value = await api("/api/election/state");
        // the settings in use (defaults until edited), in saved and draft alike so opening the page isn't a change
        for (const root of [store.saved, store.draft]) {
          const m = root.modules.us_election || (root.modules.us_election = {});
          for (const k of ["enabled", "source", "refresh_interval_sec", "active_until", "view", "cycle_sec"]) {
            if (m[k] === undefined) m[k] = clone(state.value.settings[k] ?? { view: "cycle", cycle_sec: 20 }[k]);
          }
        }
      } catch (e) { fail(e); }
    };
    onMounted(async () => { await load(); t = setInterval(() => { tick.value++; load(); }, 20000); });
    onBeforeUnmount(() => clearInterval(t));

    const s = computed(() => store.draft.modules.us_election || {});
    const job = computed(() => store.status?.jobs?.election);
    const data = computed(() => state.value?.data || {});
    // the preview follows the view picked; "cycle" shows each office in turn
    const shown = computed(() => (s.value.view && s.value.view !== "cycle" ? s.value.view : ["senate", "house", "governor"][tick.value % 3]));
    const preview = computed(() => `/api/election/preview.png?view=${shown.value}&scale=3&t=${tick.value}`);
    const entry = () => ({ panel: PANEL, election_view: s.value.view || "cycle", cycle_sec: s.value.cycle_sec || 20,
      rules: [], advance: "cycles", cycles: 1, max_sec: 600 });

    // the rotation entry (right side): on / off, and kept in step with the view and seconds chosen here
    const rotIndex = computed(() => (store.draft.modules.layout_cycle?.[SIDE] || []).findIndex((e) => e.panel === PANEL));
    const inRotation = computed({
      get: () => rotIndex.value >= 0 && store.draft.modules.layout_cycle[SIDE][rotIndex.value].enabled !== false,
      set: (v) => {
        const list = store.draft.modules.layout_cycle[SIDE];
        if (rotIndex.value < 0) { if (v) list.push(entry()); }
        else if (v) delete list[rotIndex.value].enabled;
        else list[rotIndex.value].enabled = false;
        toast(v ? "In the rotation (one pass each turn). Save to keep it." : "Out of the rotation. Save to keep it.");
      },
    });
    const syncEntry = () => {
      const e = rotIndex.value >= 0 ? store.draft.modules.layout_cycle[SIDE][rotIndex.value] : null;
      if (e) { e.election_view = s.value.view || "cycle"; e.cycle_sec = s.value.cycle_sec || 20; }
    };
    const setView = (v) => { s.value.view = v; syncEntry(); tick.value++; };
    const setSec = (v) => { s.value.cycle_sec = v; syncEntry(); };

    const hold = async () => {
      if (dirty.value.length) await previewOnDisplay();
      playOnDisplay(entry(), { side: SIDE, hold: true, ttl: Number(holdFor.value) || undefined });
    };
    const once = async () => {
      if (dirty.value.length) await previewOnDisplay();
      playOnDisplay(entry(), { side: SIDE, hold: false });
    };
    const stop = () => rotationAction("stop", SIDE);
    const onWall = computed(() => ["left", "right"].some((sd) => store.display?.status?.panels?.[sd]?.playing?.entry?.panel === PANEL));
    const fetchNow = async () => {
      running.value = true;
      try { await api("/api/jobs/election/run", { method: "POST" }); toast("Fetching the latest results…"); }
      catch (e) { fail(e); }
      setTimeout(() => { tick.value++; load(); running.value = false; }, 6000);
    };
    const bop = (k) => data.value.bop?.[k];
    const fresh = computed(() => {
      const at = data.value.map_utc || data.value.updated_utc;
      return at ? ago(Date.now() / 1000 - at) : "never";
    });
    return { state, s, job, data, preview, shown, inRotation, setView, setSec, hold, once, stop, onWall, holdFor, fetchNow, running,
      bop, fresh, VIEWS, HOLDS, ELECTION_VIEWS, ago };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>US election</h1>
      <p>Live Senate, House and governor results on a state map, with seat counts and the key races, above the clock.
        Set where the results come from and how the screen is on the wall.</p></div>
      <button class="btn" :disabled="running" @click="fetchNow"><Icon :name="running ? 'LoaderCircle' : 'RefreshCw'" :size="15" :class="{spin: running}"/>Fetch now</button></div>
    <div class="two-col">
      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="Rss" :size="16"/><h3>Results feed</h3></div>
          <div class="card-body stack">
            <ToggleRow v-model="s.enabled" label="Fetch results" hint="Off: the job stops and the screen says the results are off"/>
            <Field label="Source"><div class="seg">
              <button v-for="(l, k) in state?.sources || {}" :key="k" type="button" :class="{on: s.source === k}" @click="s.source = k">{{ l }}</button></div></Field>
            <div class="faint" style="font-size:12.5px">
              <b>NBC News</b>: the numbers behind nbcnews.com's results pages: Associated Press counts and NBC Decision Desk calls,
              every state and House district. Free and keyless, but not a published API, so it could change without notice.<br>
              <b>civicAPI</b> (civicapi.org) is a free results API, used here only as a backup for the Senate and House seat counts: its
              per-state lookup was down on election night 2026. If NBC can't be read, the map keeps NBC's last numbers and the wall says so.</div>
            <div class="fields">
              <Field label="Fetch every"><Stepper v-model="s.refresh_interval_sec" :min="15" :max="600" :step="5" unit=" s"/></Field>
              <Field label="Stop fetching after"><input class="input" type="date" v-model="s.active_until"></Field>
            </div>
          </div></div>
        <div class="card"><div class="card-head"><Icon name="Activity" :size="16"/><h3>Last fetch</h3>
            <span class="spacer"></span>
            <span class="pill" :class="job?.health === 'ok' ? 'ok' : job?.health === 'failing' || job?.health === 'stale' ? 'bad' : ''">{{ job?.health || '…' }}</span></div>
          <div class="card-body stack" style="font-size:13px">
            <div>From <b>{{ data.source || '—' }}</b>, map updated {{ fresh }} ago.</div>
            <div v-if="job?.message" class="faint">{{ job.message }}</div>
            <div v-for="(k, i) in ['senate', 'house', 'governor']" :key="k" v-show="bop(k)" class="row">
              <span style="width:90px">{{ {senate: 'Senate', house: 'House', governor: 'Governors'}[k] }}</span>
              <span style="color:#3b82f6;font-weight:600">D {{ bop(k)?.d }}</span>
              <span style="color:#ef4444;font-weight:600">R {{ bop(k)?.r }}</span>
              <span v-if="bop(k)?.i" class="faint">I {{ bop(k).i }}</span>
              <span class="faint">{{ bop(k)?.called_only ? 'of ' + bop(k).total + ' races called' : bop(k)?.need + ' for majority' }}</span></div>
            <div v-for="e in data.errors || []" :key="e" class="faint" style="color:var(--bad)">{{ e }}</div>
          </div></div>
      </div>
      <div class="card sticky"><div class="card-head"><span class="live">LIVE</span><h3>On the wall</h3>
          <span class="spacer"></span><span v-if="onWall" class="pill ok">showing now</span></div>
        <div class="card-body stack">
          <div class="seg"><button v-for="(l, k) in VIEWS" :key="k" type="button" :class="{on: (s.view || 'cycle') === k}" @click="setView(k)">{{ l }}</button></div>
          <div class="led"><img :src="preview" alt=""></div>
          <Field v-if="(s.view || 'cycle') === 'cycle'" label="Seconds per office">
            <Stepper :model-value="s.cycle_sec || 20" @update:model-value="setSec" :min="5" :max="300" :step="5" unit=" s"/></Field>
          <Field v-else label="Seconds per pass">
            <Stepper :model-value="s.cycle_sec || 20" @update:model-value="setSec" :min="5" :max="300" :step="5" unit=" s"/></Field>
          <div class="row">
            <button class="btn sm primary" @click="hold"><Icon name="Pause" :size="14"/>Hold on the wall</button>
            <select class="input" v-model="holdFor" style="width:auto"><option v-for="(l, k) in HOLDS" :key="k" :value="Number(k)">{{ l }}</option></select>
            <button class="btn sm" @click="once"><Icon name="Play" :size="14"/>Play once</button>
            <span class="spacer"></span>
            <button class="btn sm" @click="stop" title="End a hold; the rotation carries on where it was"><Icon name="Square" :size="13"/>Back to rotation</button></div>
          <ToggleRow v-model="inRotation" label="In the rotation"
            :hint="'Plays on its turn: ' + ((s.view || 'cycle') === 'cycle' ? 'Senate, House and governors once each' : VIEWS[s.view] + ' for ' + (s.cycle_sec || 20) + ' s') + '. Save to keep it.'"/>
          <div class="faint" style="font-size:12.5px">Hold keeps it up (the view and seconds above) until the time runs out or Back to rotation.
            The preview is drawn from the last fetch{{ (s.view || 'cycle') === 'cycle' ? ', a different office every 20 s' : '' }}.</div>
        </div></div>
    </div>
  </div>`,
};
