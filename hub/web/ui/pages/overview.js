import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { store, api, ago, dur, fail, toast, go, PANEL_TYPES, refreshStatus } from "../core.js";

// Live mirror of the LED panels (polls the display's frame PNG through the hub).
export const LiveView = {
  props: { scale: { type: Number, default: 3 }, interval: { type: Number, default: 400 } },
  setup(props) {
    const src = ref("");
    const err = ref("");
    let timer, last, alive = true;
    const loop = async () => {
      if (!alive) return;
      try {
        const r = await fetch(`/api/display/frame.png?scale=${props.scale}&t=${Date.now()}`);
        if (r.ok) {
          const url = URL.createObjectURL(await r.blob());
          src.value = url;
          if (last) URL.revokeObjectURL(last);
          last = url;
          err.value = "";
        } else err.value = r.status === 404 ? "The live view needs the v2 display." : "Display unreachable.";
      } catch { err.value = "Display unreachable."; }
      timer = setTimeout(loop, props.interval);
    };
    onMounted(loop);
    onBeforeUnmount(() => { alive = false; clearTimeout(timer); if (last) URL.revokeObjectURL(last); });
    return { src, err };
  },
  template: `<div class="led"><img v-if="src && !err" :src="src" alt="Live display"><div v-else class="placeholder">{{ err || 'Connecting to the display…' }}</div></div>`,
};

const JOB_ICONS = { finance: "TrendingUp", news: "Newspaper", calendar: "CalendarDays", weather: "CloudSun", maps: "Radar" };

export const Overview = {
  components: { LiveView },
  setup() {
    const jobs = computed(() => Object.entries(store.status?.jobs || {}));
    const disp = computed(() => store.display?.status);
    const now = computed(() => store.status?.now || Date.now() / 1000);
    const showing = (side) => {
      const p = disp.value?.panels?.[side];
      if (!p) return null;
      const t = PANEL_TYPES[p.panel] || { label: p.panel, icon: "Square" };
      return { ...t, name: p.panel };
    };
    const health = (h) => ({ ok: "ok", running: "warn", never: "warn", stale: "bad", failing: "bad" }[h] || "");
    const runJob = async (name) => {
      try { await api(`/api/jobs/${name}/run`, { method: "POST" }); toast("Started"); setTimeout(refreshStatus, 1500); } catch (e) { fail(e); }
    };
    const every = (s) => (s >= 120 ? `${Math.round(s / 60)} min` : `${s} s`);
    return { store, jobs, disp, showing, health, runJob, ago, dur, every, now, JOB_ICONS, go };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Overview</h1><p>What's on the display right now and how every data feed is doing.</p></div></div>

    <div class="card">
      <div class="card-head"><span class="live">LIVE</span><h3></h3>
        <template v-if="disp"><span class="pill">{{ disp.fps }} fps</span><span class="pill">draw {{ disp.draw_ms_avg }} ms</span>
        <span class="pill">brightness {{ disp.brightness }}</span><span class="pill" v-if="disp.preview">preview active</span></template>
      </div>
      <div class="card-body"><LiveView :scale="3"/>
        <div class="row" style="margin-top:12px" v-if="disp">
          <template v-for="side in ['left','right']" :key="side">
            <span class="pill accent" v-if="showing(side)"><Icon :name="showing(side).icon" :size="13"/>{{ side }}: {{ showing(side).label }}</span>
          </template>
          <span class="spacer"></span>
          <button class="btn sm" @click="go('rotation')"><Icon name="Repeat" :size="14"/>Edit rotation</button>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-title"><Icon name="Activity" :size="14"/>Data feeds</div>
      <div class="grid cols-3">
        <div class="card pad stack" v-for="[name, j] in jobs" :key="name">
          <div class="row nowrap"><Icon :name="JOB_ICONS[name] || 'Activity'" :size="18" style="color: var(--accent)"/><h3 style="flex:1">{{ j.label }}</h3>
            <span class="pill" :class="health(j.health)"><span class="dot" :class="health(j.health)"></span>{{ j.health }}</span></div>
          <div class="muted" style="font-size:13px; min-height: 20px">{{ j.message || '—' }}</div>
          <div class="row between faint" style="font-size:12px">
            <span>Updated {{ ago(j.age) }} · every {{ every(j.interval) }}</span>
            <button class="btn sm ghost" :disabled="j.running" @click="runJob(name)"><Icon :name="j.running ? 'LoaderCircle' : 'RefreshCw'" :size="13" :class="{spin: j.running}"/>{{ j.running ? 'Running' : 'Run now' }}</button>
          </div>
          <div v-if="j.last_ok === false && j.error" class="callout" style="font-size:12px; white-space: pre-wrap; max-height: 120px; overflow:auto">{{ j.error.split('\\n').slice(-5).join('\\n') }}</div>
        </div>
      </div>
    </div>

    <div class="grid cols-3">
      <div class="card pad"><div class="kpi"><b>{{ disp ? disp.fps : '—' }}</b><span>frames per second on the panels</span></div></div>
      <div class="card pad"><div class="kpi"><b>{{ disp ? disp.draw_ms_p95 + ' ms' : '—' }}</b><span>95th-percentile draw time</span></div></div>
      <div class="card pad"><div class="kpi"><b>{{ store.status?.sync?.ok ? 'OK' : 'Check' }}</b><span>data sync to the Pi · {{ store.status?.sync?.at?.slice(11) || '—' }}</span></div></div>
    </div>
  </div>`,
};
