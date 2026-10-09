import { ref, onMounted } from "vue";
import { store, api, fail, toast, ago, clone } from "../core.js";

export const System = {
  setup() {
    const backups = ref([]);
    const load = async () => { try { backups.value = await api("/api/config/backups"); } catch (e) { fail(e); } };
    onMounted(load);
    const restore = async (b) => {
      try {
        const r = await api("/api/config/restore", { json: { file: b.file } });
        store.saved = r.config; store.draft = clone(r.config);
        toast(`Restored revision ${b.revision}`); load();
      } catch (e) { fail(e); }
    };
    const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "");
    return { store, backups, restore, when, ago };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>System</h1><p>Hub, sync and configuration history.</p></div></div>
    <div class="grid cols-3">
      <div class="card pad stack"><div class="row"><Icon name="Server" :size="16" style="color:var(--accent)"/><h3>Hub</h3></div>
        <div class="muted" style="font-size:13px">Config revision <b class="mono">r{{ store.saved?.revision }}</b></div>
        <div class="muted" style="font-size:13px">Data sync to the Pi:
          <span :class="store.status?.sync?.ok ? 'pill ok' : 'pill bad'">{{ store.status?.sync?.ok ? 'ok' : 'failing' }}</span> {{ store.status?.sync?.at?.replace('T', ' ') }}</div>
        <div class="callout" v-if="store.status?.sync?.error" style="font-size:12px">{{ store.status.sync.error }}</div></div>
      <div class="card pad stack"><div class="row"><Icon name="Monitor" :size="16" style="color:var(--accent)"/><h3>Display</h3></div>
        <template v-if="store.display?.status"><div class="muted" style="font-size:13px">Renderer {{ store.display.version }} · up {{ Math.round(store.display.status.uptime_s / 3600) }} h</div>
          <div class="muted" style="font-size:13px">{{ store.display.status.fps }} fps · draw {{ store.display.status.draw_ms_avg }} ms · {{ store.display.status.recent_errors }} recent errors</div></template>
        <div v-else class="pill bad">unreachable</div></div>
      <div class="card pad stack"><div class="row"><Icon name="Info" :size="16" style="color:var(--accent)"/><h3>Data</h3></div>
        <div class="muted" style="font-size:13px">Weather and maps: Environment and Climate Change Canada (MSC GeoMet). Map lines: OpenStreetMap, Natural Earth. Imagery: Esri.</div></div>
    </div>
    <div class="card"><div class="card-head"><Icon name="History" :size="16"/><h3>Configuration history</h3><span class="faint" style="font-size:12px">every save is kept (last 100)</span></div>
      <div class="list">
        <div class="list-row" v-for="b in backups" :key="b.file">
          <b class="mono" style="width:56px">r{{ b.revision }}</b><span style="flex:1">{{ b.reason || '—' }}</span>
          <span class="faint" style="font-size:12px">{{ when(b.saved_at) }}</span>
          <button class="btn sm" :disabled="b.revision === store.saved?.revision" @click="restore(b)"><Icon name="Undo2" :size="13"/>Restore</button>
        </div>
      </div></div>
  </div>`,
};
