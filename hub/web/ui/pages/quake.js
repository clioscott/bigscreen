import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, ago, seed } from "../core.js";

const MIN_CLASSES = ["3", "4", "5-", "5+", "6-"].map((v) => ({ value: v, label: v.replace("-", "弱").replace("+", "強") }));
const TOKYO_CLASSES = ["1", "2", "3", "4", "5-"].map((v) => ({ value: v, label: v.replace("-", "弱").replace("+", "強") }));

export const Quake = {
  setup() {
    seed(["modules", "japan_eew"], { enabled: false, min_class: "4", tokyo_class: "3", forecasts: true, show_training: false, duration_sec: 150, theme: "monitor" });
    const q = computed(() => store.draft.modules.japan_eew);
    const st = ref({ connected: {}, last_message: {}, events: [], errors: [], scenarios: {}, themes: {} });
    const view = ref(null);                       // theme shown in the preview (defaults to the saved one)
    const touring = ref(false);
    const shown = computed(() => view.value || q.value.theme || "monitor");
    const load = async () => { try { st.value = await api("/api/quake"); } catch (e) { /* hub restarting */ } };
    const scenario = ref("tokyo_bay");
    const t = ref(0);
    let poll, tick;
    onMounted(() => {
      load();
      poll = setInterval(load, 5000);
      tick = setInterval(() => {                                                // 0.5 s per frame step, 1 s of quake time
        t.value = (t.value + 1) % 120;
        if (touring.value && t.value % 16 === 0) {                                 // tour: next theme every 8 s
          const ids = Object.keys(st.value.themes);
          view.value = ids[(ids.indexOf(shown.value) + 1) % ids.length];
        }
      }, 500);
    });
    onBeforeUnmount(() => { clearInterval(poll); clearInterval(tick); });
    const preview = computed(() => `/api/quake/preview.png?name=${scenario.value}&at=${t.value}&scale=2&theme=${shown.value}`);
    const pick = (id) => { q.value.theme = id; view.value = null; touring.value = false; };
    const tourHere = () => { touring.value = !touring.value; if (touring.value) { view.value = Object.keys(st.value.themes)[0]; t.value = 0; } else view.value = null; };
    const testWall = async (theme) => {
      const th = theme || shown.value;
      try {
        await api(`/api/quake/test/${scenario.value}?theme=${th}`, { method: "POST" });
        toast(th === "tour" ? `Touring every theme on the wall (12 s each)` : `Playing "${st.value.scenarios[scenario.value]}" on the wall in ${st.value.themes[th]?.label || th}`);
        setTimeout(load, 1500);
      } catch (e) { fail(e); }
    };
    const stop = async () => { try { await api("/api/display/override/clear", { json: { side: "right" } }); toast("Back to the rotation"); } catch (e) { fail(e); } };
    const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");
    const shindo = (c) => String(c || "0").replace("-", "弱").replace("+", "強");
    const SHINDO_COL = { "0": "#6e6e78", "1": "#82a5d2", "2": "#2882eb", "3": "#28c864", "4": "#ffd700", "5-": "#ff9600", "5+": "#ff6400", "6-": "#ff281e", "6+": "#c80028", "7": "#a0006e" };
    return { q, st, scenario, preview, t, testWall, stop, when, ago, shindo, SHINDO_COL, MIN_CLASSES, TOKYO_CLASSES, store, shown, pick, tourHere, touring };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Japan earthquake early warning</h1>
      <p>Optional. Listens live to the Japan Meteorological Agency's earthquake early warnings (via Wolfx) and intensity reports (via P2PQuake). When a quake passes your thresholds — with extra attention to Tokyo and Yokohama — the whole wall switches to an EEW screen in the theme you pick below: every strong-motion sensor (NIED Kyoshin Monitor) as a live dot, the P and S waves spreading out, and every major city as it is reached, with a shindo icon on the map, its predicted intensity, live reading and S-wave countdown.</p></div>
      <Toggle v-model="q.enabled"/></div>

    <div class="two-col">
      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">PREVIEW</span><h3>{{ st.themes[shown]?.label }} · {{ st.scenarios[scenario] }}</h3><span class="pill">simulated sensors</span></div>
          <div class="card-body stack">
            <div style="background:#000; border-radius:8px; overflow:hidden"><img :src="preview" style="width:100%; image-rendering:pixelated; display:block" alt=""></div>
            <div class="row" style="flex-wrap:wrap; gap:6px">
              <button v-for="(lab, id) in st.scenarios" :key="id" class="btn sm" :class="{primary: scenario === id}" @click="scenario = id; t = 0">{{ lab }}</button>
            </div>
            <div class="row">
              <button class="btn primary" @click="testWall()"><Icon name="MonitorPlay" :size="15"/>Test on the wall</button>
              <button class="btn ghost" @click="stop"><Icon name="Undo2" :size="15"/>Back to rotation</button>
              <span class="faint mono" style="font-size:12px">+{{ t }} s</span>
            </div>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="Palette" :size="15"/><h3>Theme</h3>
            <button class="btn sm" :class="{primary: touring}" @click="tourHere"><Icon name="Play" :size="13"/>{{ touring ? 'Stop tour' : 'Play all themes here' }}</button>
            <button class="btn sm" @click="testWall('tour')"><Icon name="MonitorPlay" :size="13"/>Tour all on the wall</button></div>
          <div class="list">
            <div class="list-row" v-for="(th, id) in st.themes" :key="id" style="cursor:pointer" :style="shown === id ? {background: 'var(--accent-soft, rgba(255,255,255,.06))'} : {}" @click="pick(id)">
              <span class="dot" :class="(q.theme || 'monitor') === id ? 'ok' : ''"></span>
              <span style="flex:1"><b>{{ th.label }}</b> <span class="faint" style="font-size:12px">— {{ th.hint }}</span><br><span class="faint mono" style="font-size:11px">{{ th.type }}</span></span>
              <span class="pill" v-if="(q.theme || 'monitor') === id">in use</span>
            </div>
          </div>
          <div class="card-body faint" style="font-size:12.5px">Click a theme to preview it and make it the one used for real earthquakes (Save to keep it). "Test on the wall" plays the theme in the preview without saving.</div>
        </div>
      </div>

      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="Radio" :size="15"/><h3>Live feeds</h3></div>
          <div class="list">
            <div class="list-row" v-for="(lab, id) in {wolfx: 'Wolfx — JMA earthquake early warnings', p2pquake: 'P2PQuake — JMA intensity reports'}" :key="id">
              <span class="dot" :class="st.connected[id] ? 'ok' : 'bad'"></span><span style="flex:1">{{ lab }}</span>
              <span class="faint" style="font-size:12px">{{ st.connected[id] ? 'connected' : 'reconnecting' }}<template v-if="st.last_message[id]"> · last message {{ ago(Date.now() / 1000 - st.last_message[id]) }}</template></span>
            </div>
            <div class="list-row"><span class="dot" :class="st.obs_at ? 'ok' : ''"></span><span style="flex:1">NIED Kyoshin Monitor — live sensor readings</span>
              <span class="faint" style="font-size:12px">{{ st.obs_at ? 'last read ' + ago(Date.now() / 1000 - st.obs_at) : 'read once a second while an event is on the wall' }}</span></div>
          </div>
          <div class="card-body" v-if="st.errors?.length"><div class="callout" style="font-size:12px">{{ st.errors[0] }}</div></div>
        </div>

        <div class="card"><div class="card-head"><Icon name="SlidersHorizontal" :size="15"/><h3>When to take over the wall</h3></div><div class="card-body stack">
          <Field label="Any quake with a maximum intensity of at least" hint="JMA shindo (seismic intensity) anywhere in Japan">
            <Seg :modelValue="q.min_class || '4'" @update:modelValue="v => q.min_class = v" :options="MIN_CLASSES"/></Field>
          <Field label="…or a predicted intensity in Tokyo or Yokohama of at least" hint="Estimated from magnitude, depth and distance">
            <Seg :modelValue="q.tokyo_class || '3'" @update:modelValue="v => q.tokyo_class = v" :options="TOKYO_CLASSES"/></Field>
          <ToggleRow :modelValue="q.forecasts !== false" @update:modelValue="v => q.forecasts = v" label="Include forecasts (予報)" hint="Off = only EEW warnings (警報), which JMA issues for expected intensity 5弱 or more"/>
          <ToggleRow :modelValue="!!q.show_training" @update:modelValue="v => q.show_training = v" label="Show JMA training messages"/>
          <Field label="Keep it on the wall for"><Slider :modelValue="q.duration_sec || 150" @update:modelValue="v => q.duration_sec = v" :min="60" :max="400" :step="10" unit=" s"/></Field>
          <div class="faint" style="font-size:12.5px">EEW warnings (警報) always take over, whatever the thresholds. Japan EEW sits above everything else on the wall, local weather alerts included.</div>
        </div></div>

        <div class="card"><div class="card-head"><Icon name="History" :size="15"/><h3>Recent</h3><span class="faint" style="font-size:12px">received since the hub started</span></div>
          <div class="list" v-if="st.events?.length">
            <div class="list-row" v-for="e in st.events" :key="e.id">
              <span class="pill" :style="{background: SHINDO_COL[e.max] + '33', color: SHINDO_COL[e.max], borderColor: SHINDO_COL[e.max] + '66'}">震度{{ shindo(e.max) }}</span>
              <span style="flex:1">{{ e.hypocenter }} · M{{ (+e.mag).toFixed(1) }} · {{ e.depth }} km <span class="faint">{{ e.warn ? '警報' : e.source === 'p2p' ? '震度情報' : '予報' }}{{ e.test ? ' · test' : '' }}</span></span>
              <span class="faint" style="font-size:12px">{{ when(e.origin) }}</span>
            </div></div>
          <div class="card-body faint" v-else>Nothing yet — JMA issues EEW forecasts for even small quakes, so something usually shows up within a day.</div>
        </div>
      </div>
    </div>
  </div>`,
};
