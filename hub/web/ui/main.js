import { createApp, computed, ref, onMounted, onBeforeUnmount } from "vue";
import { store, loadAll, dirty, discard, save, previewOnDisplay, endPreview, go } from "./core.js";
import { common, vSortable } from "./widgets.js";
import { Overview } from "./pages/overview.js";
import { Rotation } from "./pages/rotation.js";
import { Ticker } from "./pages/ticker.js";
import { Headlines } from "./pages/headlines.js";
import { Maps, MapEditor } from "./pages/maps.js";
import { Areas, AreaEditor } from "./pages/areas.js";
import { Colours } from "./pages/colours.js";
import { DisplaySettings } from "./pages/display.js";
import { System } from "./pages/system.js";
import { Consoles } from "./pages/consoles.js";
import { Alerts } from "./pages/alerts.js";
import { Quake } from "./pages/quake.js";
import { Fonts } from "./pages/fonts.js";

const NAV = [
  { id: "overview", label: "Overview", icon: "LayoutDashboard" },
  { id: "rotation", label: "Rotation", icon: "Repeat" },
  { sep: true },
  { id: "ticker", label: "Stock ticker", icon: "TrendingUp" },
  { id: "headlines", label: "Headlines", icon: "Newspaper" },
  { id: "maps", label: "Weather maps", icon: "Radar" },
  { id: "alerts", label: "Weather alerts", icon: "Siren" },
  { id: "quake", label: "Japan EEW", icon: "Activity" },
  { id: "areas", label: "Map areas", icon: "MapPinned" },
  { id: "colours", label: "Colour scales", icon: "Palette" },
  { id: "fonts", label: "Fonts", icon: "Type" },
  { sep: true },
  { id: "display", label: "Display", icon: "MonitorCog" },
  { id: "consoles", label: "Live consoles", icon: "SquareTerminal" },
  { id: "system", label: "System", icon: "Server" },
];

const App = {
  components: { ...common, Overview, Rotation, Ticker, Headlines, Maps, MapEditor, Areas, AreaEditor, Colours, DisplaySettings, System, Consoles, Alerts, Quake, Fonts },
  setup() {
    const ready = ref(false);
    const err = ref("");
    const now = ref(Date.now());
    let t;
    onMounted(async () => {
      try { await loadAll(); ready.value = true; } catch (e) { err.value = e.message; }
      t = setInterval(() => (now.value = Date.now()), 1000);
    });
    onBeforeUnmount(() => clearInterval(t));
    window.addEventListener("beforeunload", (e) => { if (dirty.value.length) { e.preventDefault(); e.returnValue = ""; } });

    const page = computed(() => {
      const r = store.route.name;
      if (r === "maps" && store.route.params[0]) return "MapEditor";
      if (r === "areas" && store.route.params[0]) return "AreaEditor";
      return { overview: "Overview", rotation: "Rotation", ticker: "Ticker", headlines: "Headlines", maps: "Maps", areas: "Areas",
        colours: "Colours", display: "DisplaySettings", system: "System", consoles: "Consoles", alerts: "Alerts", quake: "Quake", fonts: "Fonts" }[r] || "Overview";
    });
    const feedsBad = computed(() => Object.values(store.status?.jobs || {}).filter((j) => ["stale", "failing"].includes(j.health)).length);
    const disp = computed(() => store.display?.status);
    // jobs running right now, with progress (from /api/status, polled every few seconds)
    const busy = computed(() => Object.entries(store.status?.jobs || {}).filter(([, j]) => j.running).map(([name, j]) => {
      const p = j.progress || {};
      const pct = p.total ? Math.round((100 * (p.done || 0)) / p.total)
        : j.duration && p.started ? Math.min(95, Math.round((100 * (now.value / 1000 - p.started)) / j.duration)) : null;
      return { name, label: j.label, step: p.label || "", pct };
    }));
    const previewLeft = computed(() => store.preview ? Math.max(0, Math.round((store.preview.until - now.value) / 1000)) : 0);
    const dirtyLabel = computed(() => dirty.value.map((d) => d.label).join(", "));
    return { store, NAV, ready, err, page, go, feedsBad, busy, disp, dirty, dirtyLabel, discard, save, previewOnDisplay, endPreview, previewLeft };
  },
  template: `
  <div class="boot" v-if="!ready">{{ err ? 'Could not reach the hub: ' + err : 'Loading BigScreen…' }}</div>
  <div class="shell" v-else>
    <aside class="sidebar">
      <div class="brand"><div class="brand-mark"><span></span></div><div><b>BigScreen</b><small>control panel</small></div></div>
      <template v-for="n in NAV" :key="n.id || Math.random()">
        <div v-if="n.sep" class="nav-sep"></div>
        <div v-else class="nav-item" :class="{active: store.route.name === n.id}" @click="go(n.id)">
          <Icon :name="n.icon" :size="17"/>{{ n.label }}
          <span v-if="n.id === 'overview' && feedsBad" class="pill bad badge">{{ feedsBad }}</span>
        </div>
      </template>
      <div class="side-tasks" v-if="busy.length" @click="go('consoles')" title="Open the live consoles">
        <div class="side-task" v-for="b in busy" :key="b.name">
          <div class="row nowrap" style="gap:6px"><Icon name="LoaderCircle" :size="12" class="spin"/><span class="ellipsis" style="flex:1">{{ b.label }}<span class="faint" v-if="b.step"> · {{ b.step }}</span></span><span class="mono faint" v-if="b.pct !== null">{{ b.pct }}%</span></div>
          <div class="bar" :class="{indeterminate: b.pct === null}"><span :style="{width: (b.pct ?? 30) + '%'}"></span></div>
        </div>
      </div>
      <div class="sidebar-foot">
        <div class="row"><span class="faint">Display</span>
          <span v-if="disp" class="row nowrap"><span class="dot ok"></span><span class="mono">{{ disp.fps }} fps</span></span>
          <span v-else class="row nowrap"><span class="dot bad"></span>offline</span></div>
        <div class="row" style="margin-top:6px"><span class="faint">Config</span><span class="mono">r{{ store.saved?.revision }}</span></div>
      </div>
    </aside>
    <main class="main"><component :is="page" :key="store.route.name + '/' + (store.route.params[0] || '')"/></main>

    <div class="preview-banner" v-if="store.preview">
      <Icon name="Eye" :size="16"/><span>Previewing unsaved changes on the display · <b class="mono">{{ Math.floor(previewLeft / 60) }}:{{ String(previewLeft % 60).padStart(2, '0') }}</b></span>
      <button class="btn sm primary" @click="endPreview(true)"><Icon name="Check" :size="14"/>Keep & save</button>
      <button class="btn sm" @click="endPreview(false)"><Icon name="Undo2" :size="14"/>Revert</button>
    </div>

    <div class="savebar" v-if="dirty.length">
      <Icon name="CircleDot" :size="16" style="color: var(--warn)"/>
      <span class="what">Unsaved changes: <b>{{ dirtyLabel }}</b></span>
      <button class="btn sm ghost" @click="discard"><Icon name="RotateCcw" :size="14"/>Discard</button>
      <button class="btn sm" @click="previewOnDisplay()" title="Show these changes on the LED display for 2 minutes without saving"><Icon name="Eye" :size="14"/>Preview on display</button>
      <button class="btn sm primary" :disabled="store.saving" @click="save()"><Icon :name="store.saving ? 'LoaderCircle' : 'Save'" :size="14" :class="{spin: store.saving}"/>Save</button>
    </div>

    <div class="toasts"><div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.kind">
      <Icon :name="t.kind === 'bad' ? 'CircleX' : 'CircleCheck'" :size="16"/><span>{{ t.msg }}</span></div></div>
  </div>`,
};

const app = createApp(App);
for (const [name, comp] of Object.entries(common)) app.component(name, comp);   // shared widgets everywhere
app.directive("sortable", vSortable);
app.mount("#app");
