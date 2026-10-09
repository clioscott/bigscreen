import { ref, computed, watch, onBeforeUnmount } from "vue";
import { store, api, fail, toast, go, ago, dur, areaLabel, refreshMaps, clone, seed } from "../core.js";

const STEP_OPTIONS = [5, 6, 10, 15, 20, 30, 60, 120];

function trackSummary(t) {
  if (t.kind === "accumulation") return `Precipitation (Accumulated) · ${t.window_hours ?? 24} h`;
  const h = (t.window_min ?? 180) / 60;
  const what = (t.layers || []).some((l) => l.type === "radar") ? "Precipitation (Live)" : "Live loop";
  return `${what} · ${h % 1 ? h.toFixed(1) : h} h`;
}
function hasRadar(t) { return t.kind === "accumulation" || (t.layers || []).some((l) => l.type === "radar"); }

async function showNow(tid, t) {
  try {
    await api("/api/display/override", { json: { side: "right", panel: t.size === "full" ? "map_full" : "map", track: tid, duration_sec: 120 } });
    toast("On the display for 2 minutes");
  } catch (e) { fail(e); }
}
async function rerender(tid) {
  try { await api(`/api/maps/render/${encodeURIComponent(tid)}`, { method: "POST" }); toast("Re-rendering — usually under a minute"); setTimeout(refreshMaps, 20000); }
  catch (e) { fail(e); }
}

export const Maps = {
  setup() {
    const wm = computed(() => store.draft.modules.weather_maps);
    const status = (tid) => store.maps?.tracks?.[tid]?.last;
    const createMap = () => {
      let n = 1;
      while (wm.value.tracks[`map${n}`]) n++;
      const area = Object.keys(wm.value.areas)[0];
      wm.value.tracks[`map${n}`] = { label: `New map ${n}`, area, size: "half", kind: "loop", enabled: true,
        basemap: { source: "esri_imagery", brightness: 1, contrast: 1, saturation: 1 }, layers: [{ type: "radar" }],
        window_min: 180, step_min: 6, refresh_min: 6, fps: 8, hold_last_sec: 1.5, loops: 2 };
      go(`maps/map${n}`);
    };
    return { wm, status, createMap, go, trackSummary, areaLabel, showNow, rerender, ago, store };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Weather maps</h1>
      <p>Animated maps rendered on the server from Environment Canada data, then played back by the display. Add them to the rotation as "Weather map" panels.</p></div>
      <button class="btn primary" @click="createMap"><Icon name="Plus" :size="15"/>New map</button></div>
    <div class="grid cols-3">
      <div class="card" v-for="(t, tid) in wm.tracks" :key="tid" :style="t.enabled === false ? 'opacity:.55' : ''">
        <div style="padding:12px 12px 0; cursor:pointer" @click="go('maps/' + tid)">
          <AnimThumb :track="tid" :full="t.size === 'full'">
            <div class="overlay-chips"><span class="pill">{{ areaLabel(t.area, true) }}</span><span class="pill" v-if="t.size === 'full'">full</span></div>
          </AnimThumb></div>
        <div class="card-body stack" style="gap:8px">
          <div class="row nowrap"><h3 style="flex:1">{{ t.label || tid }}</h3>
            <span class="pill" :class="t.kind === 'accumulation' ? 'warn' : 'accent'"><Icon :name="t.kind === 'accumulation' ? 'CloudRain' : 'Repeat'" :size="12"/>{{ t.kind === 'accumulation' ? 'Accum' : 'Live' }}</span></div>
          <div class="muted" style="font-size:13px">{{ trackSummary(t) }} · plays {{ t.loops ?? 2 }}× per visit</div>
          <div style="font-size:12px" v-if="status(tid)">
            <span :class="status(tid).ok ? 'faint' : ''" :style="status(tid).ok ? '' : 'color: var(--bad)'">{{ status(tid).message }}</span>
            <span class="faint"> · {{ ago(Date.now()/1000 - (store.maps.tracks[tid]?.tried || 0)) }}</span></div>
          <div class="row">
            <button class="btn sm" @click="go('maps/' + tid)"><Icon name="Pencil" :size="13"/>Edit</button>
            <button class="btn sm" @click="showNow(tid, t)"><Icon name="Play" :size="13"/>Show now</button>
            <button class="btn sm ghost" @click="rerender(tid)"><Icon name="RefreshCw" :size="13"/></button>
            <span class="spacer"></span><Toggle :modelValue="t.enabled !== false" @update:modelValue="v => t.enabled = v"/></div>
        </div>
      </div>
    </div>
  </div>`,
};

export const MapEditor = {
  setup() {
    const tid = store.route.params[0];
    const wm = computed(() => store.draft.modules.weather_maps);
    const t = computed(() => wm.value.tracks[tid]);
    const areas = computed(() => Object.entries(wm.value.areas));
    const tpath = ["modules", "weather_maps", "tracks", tid];
    if (t.value) { seed([...tpath, "basemap"], {}); seed([...tpath, "basemap", "daynight"], { enabled: false, night_brightness: 0.25, twilight_deg: 6 }); }
    const bm = computed(() => t.value.basemap);
    const dn = computed(() => bm.value.daynight);
    const catalog = computed(() => store.catalog || { basemaps: [], wms_layers: [] });
    const isAcc = computed(() => t.value.kind === "accumulation");
    // totals need their own window; give a fresh totals map the same 24 h the renderer assumes
    watch(() => t.value?.kind, (k) => { if (k === "accumulation" && t.value.window_hours == null) t.value.window_hours = 24; });
    const usesRadar = computed(() => hasRadar(t.value));

    const hours = computed({
      get: () => (isAcc.value ? t.value.window_hours ?? 24 : (t.value.window_min ?? 180) / 60),
      set: (v) => { if (isAcc.value) t.value.window_hours = v; else t.value.window_min = Math.round(v * 60); },
    });
    const frames = computed(() => Math.max(1, Math.floor((hours.value * 60) / (t.value.step_min || 10)) + 1));
    const loopSec = computed(() => frames.value / (t.value.fps || 8) + (t.value.hold_last_sec ?? 1.5));
    const archiveNote = computed(() => (store.status?.jobs?.maps?.detail?.archive || []).find((s) => s.startsWith(t.value.area + ":")));

    // live basemap/cartography preview of the (unsaved) settings
    const preview = ref("");
    const previewInfo = ref(null);
    let pt;
    const refreshPreview = () => {
      clearTimeout(pt);
      pt = setTimeout(async () => {
        if (!t.value) return;
        const a = wm.value.areas[t.value.area];
        if (!a) return;
        try {
          const r = await api("/api/maps/preview-area", { json: { area_id: t.value.area, area: a, size: t.value.size || "half", basemap: bm.value } });
          preview.value = r.png; previewInfo.value = r;
        } catch (e) { previewInfo.value = { error: e.message }; }
      }, 400);
    };
    watch(() => [t.value?.area, t.value?.size, JSON.stringify(t.value?.basemap)], refreshPreview, { immediate: true });
    onBeforeUnmount(() => clearTimeout(pt));

    const addLayer = (type) => {
      t.value.layers = t.value.layers || [];
      if (type === "radar") t.value.layers.push({ type: "radar" });
      else { const l = catalog.value.wms_layers[0]; t.value.layers.push({ type: "wms", layer: l.layer, time: l.time, opacity: 1, scale: 2 }); }
    };
    const pickWms = (l, name) => {
      l.layer = name;
      const meta = catalog.value.wms_layers.find((x) => x.layer === name);
      if (meta) l.time = meta.time;
    };
    const wmsLabel = (name) => catalog.value.wms_layers.find((x) => x.layer === name)?.label || name;
    const del = () => { delete wm.value.tracks[tid]; go("maps"); toast("Map removed — save to apply"); };
    const duplicate = () => {
      let n = 2;
      while (wm.value.tracks[`${tid}_${n}`]) n++;
      wm.value.tracks[`${tid}_${n}`] = { ...clone(t.value), label: `${t.value.label} (copy)` };
      go(`maps/${tid}_${n}`);
    };
    return { tid, t, wm, areas, bm, dn, catalog, isAcc, usesRadar, hours, frames, loopSec, archiveNote, preview, previewInfo, addLayer, pickWms, wmsLabel, del, duplicate,
      go, dur, STEP_OPTIONS, showNow, rerender, areaLabel, store };
  },
  template: `<div class="page" v-if="t">
    <div class="page-head">
      <button class="btn ghost icon-only" @click="go('maps')"><Icon name="ArrowLeft" :size="18"/></button>
      <div class="grow"><input class="input" style="font-size:20px; font-weight:650; height:42px; max-width:520px" v-model="t.label"></div>
      <Toggle :modelValue="t.enabled !== false" @update:modelValue="v => t.enabled = v"/>
      <button class="btn" @click="showNow(tid, t)"><Icon name="Play" :size="15"/>Show now</button>
      <button class="btn" @click="rerender(tid)"><Icon name="RefreshCw" :size="15"/>Re-render</button>
      <button class="btn ghost" @click="duplicate"><Icon name="Copy" :size="15"/></button>
    </div>

    <div class="two-col">
      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">ON DISPLAY</span><h3></h3><span class="faint" style="font-size:12px">last render</span></div>
          <div class="card-body"><AnimThumb :track="tid" :full="t.size === 'full'" :scale="3"/></div></div>
        <div class="card"><div class="card-head"><Icon name="Layers" :size="15"/><h3>Basemap & map lines</h3><span class="faint" style="font-size:12px">live, unsaved</span></div>
          <div class="card-body">
            <div class="thumb" :class="{full: t.size === 'full'}"><img v-if="preview" :src="preview" alt=""></div>
            <div class="faint" style="font-size:12px; margin-top:8px" v-if="previewInfo && !previewInfo.error">
              {{ previewInfo.width_km }} × {{ previewInfo.height_km }} km · {{ previewInfo.km_per_px }} km per pixel ·
              <a href="#" @click.prevent="go('areas/' + t.area)">edit {{ areaLabel(t.area) }}</a></div>
            <div class="callout" v-if="previewInfo?.error">{{ previewInfo.error }}</div>
          </div></div>
      </div>

      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="MapPin" :size="15"/><h3>Where</h3></div><div class="card-body stack">
          <div class="fields">
            <Field label="Map area"><select class="input" v-model="t.area"><option v-for="[id, a] in areas" :key="id" :value="id">{{ a.label }}{{ a.short_label ? ' (' + a.short_label + ')' : '' }}{{ a.scale_label ? ' · ' + a.scale_label : '' }}</option></select></Field>
            <Field label="Size on the display"><Seg v-model="t.size" :options="[{value:'half', label:'Half', icon:'PanelRight'}, {value:'full', label:'Full width', icon:'Columns2'}]"/></Field>
          </div></div></div>

        <div class="card"><div class="card-head"><Icon name="Sparkles" :size="15"/><h3>What it shows</h3></div><div class="card-body stack">
          <Seg v-model="t.kind" :options="[{value:'loop', label:'Live loop', icon:'Repeat'}, {value:'accumulation', label:'Precipitation (Accumulated)', icon:'CloudRain'}]"/>
          <template v-if="!isAcc">
            <div class="faint" style="font-size:12.5px">Satellite and forecast layers are drawn under the coastlines and borders. Radar is drawn on top of everything, opaque at full colour.</div>
            <div class="stack" style="gap:8px" v-sortable="{ list: t.layers }">
              <div class="slot" v-for="(l, i) in t.layers" :key="i">
                <span class="handle"><Icon name="GripVertical" :size="16"/></span>
                <span class="kind"><Icon :name="l.type === 'radar' ? 'Radar' : 'Satellite'" :size="17"/></span>
                <div class="meta" v-if="l.type === 'radar'"><b>Precipitation (Live)</b><span>Radar: rain, snow, mixed & freezing rain in TV colours</span></div>
                <div class="meta stack" style="gap:6px" v-else>
                  <select class="input" :value="l.layer" @change="pickWms(l, $event.target.value)">
                    <option v-for="w in catalog.wms_layers" :key="w.layer" :value="w.layer">{{ w.label }}</option></select>
                  <div class="row"><Seg v-model="l.time" :options="[{value:'past', label:'Past'}, {value:'forecast', label:'Forecast'}, {value:'static', label:'Static'}]"/>
                    <span class="faint" style="font-size:12px">opacity</span><div style="flex:1; min-width:120px"><Slider v-model="l.opacity" :min="0.1" :max="1" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></div></div>
                </div>
                <button class="btn sm ghost icon-only" @click="t.layers.splice(i, 1)"><Icon name="Trash2" :size="14"/></button>
              </div>
            </div>
            <div class="row"><button class="btn sm" @click="addLayer('radar')"><Icon name="Plus" :size="14"/>Precipitation (Live)</button>
              <button class="btn sm" @click="addLayer('wms')"><Icon name="Plus" :size="14"/>Satellite / forecast layer</button></div>
          </template>
          <template v-else>
            <div class="faint" style="font-size:12.5px">Each frame shows how much rain and snow has fallen since the start of the window, so heavy, lasting rain builds from green to red. Snow colours take over once a spot's snow passes the threshold below.</div>
            <Field label="Snow colours on top from"><Stepper v-model="t.snow_on_top_cm" :min="0" :max="20" :step="0.1" unit="cm"/></Field>
          </template>
        </div></div>

        <div class="card"><div class="card-head"><Icon name="Timer" :size="15"/><h3>History & playback</h3></div><div class="card-body stack">
          <Field :label="isAcc ? 'Accumulate the last' : 'Show the last'" :hint="usesRadar ? 'Radar history builds up on the server (Environment Canada keeps only 3 h).' : ''">
            <Slider v-model="hours" :min="0.5" :max="48" :step="0.5" :format="v => (v % 1 ? v.toFixed(1) : v) + ' h'"/></Field>
          <div class="callout info" v-if="archiveNote" style="font-size:12.5px"><Icon name="Info" :size="15"/>{{ archiveNote }}</div>
          <div class="fields">
            <Field label="One frame every"><select class="input" v-model.number="t.step_min"><option v-for="m in STEP_OPTIONS" :key="m" :value="m">{{ dur(m * 60) }}</option></select></Field>
            <Field label="Playback speed"><Stepper v-model="t.fps" :min="1" :max="30" unit="fps"/></Field>
            <Field label="Hold the last frame"><Stepper v-model="t.hold_last_sec" :min="0" :max="20" :step="0.5" unit="s"/></Field>
            <Field label="Loops before moving on"><Stepper v-model="t.loops" :min="1" :max="10" unit="×"/></Field>
            <Field label="Rebuild every"><Stepper v-model="t.refresh_min" :min="2" :max="240" unit="min"/></Field>
          </div>
          <div class="muted" style="font-size:13px">≈ <b>{{ frames }}</b> frames · one loop ≈ <b>{{ dur(loopSec) }}</b> · on screen ≈ <b>{{ dur(loopSec * (t.loops || 2)) }}</b> per visit
            <span class="faint">(rotation entries set to "after complete passes" use this loop count)</span></div>
        </div></div>

        <div class="card"><div class="card-head"><Icon name="Image" :size="15"/><h3>Basemap</h3></div><div class="card-body stack">
          <div class="basemap-grid"><button v-for="b in catalog.basemaps" :key="b.id" class="type-card" :class="{on: (bm.source || 'esri_imagery') === b.id}" @click="bm.source = b.id"><span style="font-size:12.5px">{{ b.label }}</span></button></div>
          <div class="fields">
            <Field label="Brightness"><Slider v-model="bm.brightness" :min="0.1" :max="1.4" :step="0.05" :format="v => Math.round((v ?? 1) * 100) + '%'"/></Field>
            <Field label="Contrast"><Slider v-model="bm.contrast" :min="0.5" :max="1.6" :step="0.05" :format="v => Math.round((v ?? 1) * 100) + '%'"/></Field>
            <Field label="Saturation"><Slider v-model="bm.saturation" :min="0" :max="1.6" :step="0.05" :format="v => Math.round((v ?? 1) * 100) + '%'"/></Field>
          </div>
          <div class="fields">
            <Field label="Water tint"><div class="row"><Toggle :modelValue="!!bm.water" @update:modelValue="v => v ? bm.water = '#0a1a33' : delete bm.water"/><ColorPick v-if="bm.water" v-model="bm.water"/></div></Field>
            <Field label="Land tint"><div class="row"><Toggle :modelValue="!!bm.land" @update:modelValue="v => v ? bm.land = '#141a12' : delete bm.land"/><ColorPick v-if="bm.land" v-model="bm.land"/></div></Field>
            <Field label="Tint strength" v-if="bm.water || bm.land"><Slider v-model="bm.tint_strength" :min="0.1" :max="1" :step="0.05" :format="v => Math.round((v ?? 1) * 100) + '%'"/></Field>
          </div>
          <ToggleRow v-model="dn.enabled" label="Day / night shading" hint="Darkens the night side of the basemap, frame by frame, from the real sun position. Radar and totals are never darkened."/>
          <div class="fields" v-if="dn.enabled">
            <Field label="Night brightness"><Slider v-model="dn.night_brightness" :min="0.05" :max="0.8" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></Field>
            <Field label="Twilight width"><Stepper v-model="dn.twilight_deg" :min="1" :max="18" unit="°"/></Field>
          </div>
        </div></div>

        <div class="card"><div class="card-head"><Icon name="Type" :size="15"/><h3>On screen</h3></div><div class="card-body stack">
          <ToggleRow :modelValue="t.timestamp !== false" @update:modelValue="v => t.timestamp = v" label="Time label" hint="Bottom-left: map name and the frame's time"/>
          <ToggleRow v-if="usesRadar" :modelValue="t.legend !== false" @update:modelValue="v => t.legend = v" label="Colour legend" hint="Top-left: rain and snow colour bars"/>
        </div></div>

        <div class="row end"><ConfirmButton label="Delete this map" @confirm="del"/></div>
      </div>
    </div>
  </div>
  <div class="page" v-else><div class="empty">That map doesn't exist. <a href="#/maps">Back to maps</a></div></div>
  <div class="page" v-else><div class="empty"><Icon name="MapPinOff" :size="28"/><h3>No map called “{{ tid }}”</h3>
    <button class="btn" @click="go('maps')"><Icon name="ArrowLeft" :size="14"/>All weather maps</button></div></div>`,
};
