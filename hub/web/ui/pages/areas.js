import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, go } from "../core.js";


async function previewOf(id, a, size = "half", basemap) {
  const track = Object.values(store.draft.modules.weather_maps.tracks).find((t) => t.area === id);
  return api("/api/maps/preview-area", { json: { area_id: id, area: a, size, basemap: basemap || track?.basemap || { source: "esri_imagery", brightness: 1 } } });
}

export const Areas = {
  setup() {
    const areas = computed(() => store.draft.modules.weather_maps.areas);
    const previews = ref({});
    const usedBy = (id) => Object.values(store.draft.modules.weather_maps.tracks).filter((t) => t.area === id).map((t) => t.label);
    onMounted(async () => {
      for (const [id, a] of Object.entries(areas.value)) {
        try { previews.value = { ...previews.value, [id]: await previewOf(id, a) }; } catch {}
      }
    });
    const create = () => {
      let n = 1;
      while (areas.value[`area${n}`]) n++;
      areas.value[`area${n}`] = { label: `New area ${n}`, short_label: "", center: [49.25, -123.1], km_per_px: 1, carto: {} };
      go(`areas/area${n}`);
    };
    return { areas, previews, usedBy, create, go };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Map areas</h1>
      <p>The geography behind every weather map: where it's centred, how far it zooms, and which coastlines, borders, rivers and highways are drawn.</p></div>
      <button class="btn primary" @click="create"><Icon name="Plus" :size="15"/>New area</button></div>
    <div class="grid cols-2">
      <div class="card" v-for="(a, id) in areas" :key="id" style="cursor:pointer" @click="go('areas/' + id)">
        <div style="padding:12px 12px 0"><div class="thumb"><img v-if="previews[id]" :src="previews[id].png" alt="">
          <div class="overlay-chips"><span class="pill">{{ a.short_label || id }}</span></div></div></div>
        <div class="card-body stack" style="gap:6px">
          <div class="row"><h3 style="flex:1">{{ a.label }}</h3><span class="pill" v-if="a.fit">fitted to corners</span></div>
          <div class="muted" style="font-size:13px" v-if="previews[id]">{{ previews[id].width_km }} × {{ previews[id].height_km }} km · {{ previews[id].km_per_px }} km per pixel</div>
          <div class="faint" style="font-size:12px">{{ usedBy(id).length ? 'Used by ' + usedBy(id).join(', ') : 'Not used by any map yet' }}</div>
        </div>
      </div>
    </div>
  </div>`,
};

export const AreaEditor = {
  setup() {
    const id = store.route.params[0];
    const a = computed(() => store.draft.modules.weather_maps.areas[id]);
    const carto = computed(() => (a.value.carto = a.value.carto || {}));
    const size = ref("half");
    const preview = ref(null);
    const busy = ref(false);
    let t;
    const refresh = () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        busy.value = true;
        try { preview.value = await previewOf(id, a.value, size.value); } catch (e) { preview.value = { error: e.message }; }
        busy.value = false;
      }, 450);
    };
    watch(() => [JSON.stringify(a.value), size.value], refresh, { immediate: true });
    onBeforeUnmount(() => clearTimeout(t));

    // shape: centre + scale, or fitted to two corners
    const mode = computed({
      get: () => (a.value.fit ? "fit" : "centre"),
      set: (m) => {
        if (m === "fit" && !a.value.fit) {
          const b = preview.value?.bbox;   // [w, s, e, n]
          a.value.fit = b ? { nw: [b[3], b[0]], se: [b[1], b[2]] } : { nw: [49.4, -123.3], se: [49.0, -122.5] };
        }
        if (m === "centre" && a.value.fit) {
          a.value.center = preview.value?.center || [49.2, -123.0];
          a.value.km_per_px = preview.value?.km_per_px || 1;
          delete a.value.fit;
        }
      },
    });
    const logKm = computed({
      get: () => Math.log10(a.value.km_per_px || 1),
      set: (v) => { const k = Math.pow(10, v); a.value.km_per_px = k < 1 ? Math.round(k * 100) / 100 : k < 10 ? Math.round(k * 10) / 10 : Math.round(k); },
    });

    // cartography rows come from the preview (they include "in range at this scale" + source)
    const layerRows = computed(() => preview.value?.layers || []);
    const setLayer = (key, k, v) => { carto.value[key] = { ...(carto.value[key] || {}), [k]: v }; };

    const used = computed(() => Object.values(store.draft.modules.weather_maps.tracks).filter((t) => t.area === id));
    const del = () => {
      if (used.value.length) { toast("This area is used by a map — point those maps elsewhere first", "bad"); return; }
      delete store.draft.modules.weather_maps.areas[id]; go("areas");
    };
    const kmRange = (v) => (v >= 1e8 ? "" : v);
    return { id, a, carto, size, preview, busy, mode, logKm, layerRows, setLayer, store, del, used, go, kmRange };
  },
  template: `<div class="page" v-if="a">
    <div class="page-head">
      <button class="btn ghost icon-only" @click="go('areas')"><Icon name="ArrowLeft" :size="18"/></button>
      <div class="grow row nowrap"><input class="input" style="font-size:20px; font-weight:650; height:42px; max-width:420px" v-model="a.label">
        <input class="input" style="max-width:140px" v-model="a.short_label" placeholder="Short label"></div>
    </div>
    <div class="two-col">
      <div class="stack sticky">
        <div class="card"><div class="card-head"><Icon name="Map" :size="15"/><h3>Preview</h3><Icon v-if="busy" name="LoaderCircle" class="spin" :size="15"/>
          <Seg v-model="size" :options="[{value:'half', label:'Half'}, {value:'full', label:'Full'}]"/></div>
          <div class="card-body"><div class="thumb" :class="{full: size === 'full'}"><img v-if="preview?.png" :src="preview.png" alt=""></div>
            <div class="faint" style="font-size:12px; margin-top:8px" v-if="preview && !preview.error">
              {{ preview.width_km }} × {{ preview.height_km }} km · {{ preview.km_per_px }} km per pixel ·
              lat {{ preview.bbox[1] }} → {{ preview.bbox[3] }}, lon {{ preview.bbox[0] }} → {{ preview.bbox[2] }}</div>
            <div class="callout" v-if="preview?.error">{{ preview.error }}</div>
</div></div>
      </div>

      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="Crosshair" :size="15"/><h3>Extent</h3></div><div class="card-body stack">
          <Seg v-model="mode" :options="[{value:'centre', label:'Centre & zoom', icon:'Crosshair'}, {value:'fit', label:'Fit two corners', icon:'Maximize2'}]"/>
          <template v-if="mode === 'centre'">
            <div class="fields">
              <Field label="Centre latitude"><input class="input mono" type="number" step="0.01" :value="a.center[0]" @input="a.center = [+$event.target.value, a.center[1]]"></Field>
              <Field label="Centre longitude"><input class="input mono" type="number" step="0.01" :value="a.center[1]" @input="a.center = [a.center[0], +$event.target.value]"></Field>
            </div>
            <Field label="Zoom" :hint="'Smaller = more zoomed in. ' + a.km_per_px + ' km per LED pixel'">
              <Slider v-model="logKm" :min="-1" :max="1.7" :step="0.01" :format="() => a.km_per_px + ' km/px'"/></Field>
          </template>
          <template v-else>
            <div class="faint" style="font-size:12.5px">The view is centred between the two corners and zoomed so both fit; whichever side is the tighter fit touches the edges exactly and the other is extended.</div>
            <div class="fields">
              <Field label="North-west corner lat"><input class="input mono" type="number" step="0.0001" v-model.number="a.fit.nw[0]"></Field>
              <Field label="North-west corner lon"><input class="input mono" type="number" step="0.0001" v-model.number="a.fit.nw[1]"></Field>
              <Field label="South-east corner lat"><input class="input mono" type="number" step="0.0001" v-model.number="a.fit.se[0]"></Field>
              <Field label="South-east corner lon"><input class="input mono" type="number" step="0.0001" v-model.number="a.fit.se[1]"></Field>
            </div>
          </template>
        </div></div>

        <div class="card"><div class="card-head"><Icon name="Waves" :size="15"/><h3>Map lines</h3>
          <span class="faint" style="font-size:12px">all 1 px, matched to this zoom</span></div>
          <div class="scroll-x"><table class="table">
            <thead><tr><th>On</th><th>Layer</th><th>Colour</th><th>Opacity</th><th>Shown between (km/px)</th><th>At this zoom</th></tr></thead>
            <tbody><tr v-for="r in layerRows" :key="r.key" :class="{dim: !r.in_range}">
              <td><Toggle :modelValue="r.enabled" @update:modelValue="v => setLayer(r.key, 'enabled', v)"/></td>
              <td><b style="font-weight:560">{{ r.label }}</b><div class="faint" style="font-size:11.5px">{{ r.under ? 'below radar' : 'above radar' }}</div></td>
              <td><ColorPick :modelValue="r.color" @update:modelValue="v => setLayer(r.key, 'color', v)"/></td>
              <td style="min-width:140px"><Slider :modelValue="r.opacity" @update:modelValue="v => setLayer(r.key, 'opacity', v)" :min="0.1" :max="1" :step="0.05" :format="v => Math.round(v * 100) + '%'"/></td>
              <td class="row nowrap"><input class="input mono" style="width:68px" type="number" step="0.1" min="0" :value="r.min_km_px" @change="setLayer(r.key, 'min_km_px', +$event.target.value)">
                <span class="faint">–</span><input class="input mono" style="width:68px" type="number" step="0.1" min="0" :value="kmRange(r.max_km_px)" placeholder="any"
                @change="setLayer(r.key, 'max_km_px', $event.target.value === '' ? 1e9 : +$event.target.value)"></td>
              <td style="font-size:12px"><span v-if="r.in_range" class="pill ok">shown</span><span v-else class="pill">hidden</span>
                <div class="faint" v-if="r.in_range">{{ r.source }}</div></td>
            </tr></tbody></table></div>
          <div class="card-body"><Field label="Drop islands, lakes and slivers smaller than" hint="Keeps 1 px shorelines clean at this zoom">
            <Stepper :modelValue="a.min_feature_px ?? 3" @update:modelValue="v => a.min_feature_px = v" :min="0" :max="50" unit="px"/></Field></div>
        </div>

        <div class="row end"><ConfirmButton label="Delete this area" @confirm="del"/></div>
      </div>
    </div>
  </div>
  <div class="page" v-else><div class="empty">That area doesn't exist. <a href="#/areas">Back to areas</a></div></div>`,
};
