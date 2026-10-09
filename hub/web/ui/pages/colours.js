import { computed } from "vue";
import { store, clone } from "../core.js";

const SCALES = [
  { id: "rain_total", label: "Accumulated · rain", unit: "mm", icon: "CloudRain", note: "Precipitation (Accumulated): rain over the map's window" },
  { id: "snow_total", label: "Accumulated · snow", unit: "cm", icon: "Snowflake", note: "Drawn over rain once past the map's snow threshold" },
  { id: "rain_rate", label: "Live · rain", unit: "mm/h", icon: "Radar", note: "Precipitation (Live): radar rain rate" },
  { id: "snow_rate", label: "Live · snow", unit: "cm/h", icon: "Snowflake" },
  { id: "mixed_rate", label: "Live · mixed rain & snow", unit: "mm/h", icon: "CloudRain" },
  { id: "fzra_rate", label: "Live · freezing rain", unit: "mm/h", icon: "TriangleAlert" },
];

export const Colours = {
  setup() {
    const wm = computed(() => store.draft.modules.weather_maps);
    const bands = (id) => (wm.value.palettes?.[id]) || store.catalog?.palettes?.[id] || [];
    const custom = (id) => !!wm.value.palettes?.[id];
    const ensure = (id) => {
      wm.value.palettes = wm.value.palettes || {};
      if (!wm.value.palettes[id]) wm.value.palettes[id] = clone(store.catalog.palettes[id]);
      return wm.value.palettes[id];
    };
    const set = (id, i, k, v) => { ensure(id)[i][k] = v; };
    const add = (id) => { const p = ensure(id); const [t, c] = p[p.length - 1]; p.push([Math.round(t * 1.5 * 10) / 10 || 1, c]); };
    const remove = (id, i) => { const p = ensure(id); if (p.length > 2) p.splice(i, 1); };
    const reset = (id) => { delete wm.value.palettes[id]; };
    return { SCALES, bands, custom, set, add, remove, reset };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Colour scales</h1>
      <p>How rain, snow and radar intensities are coloured on the maps. A pixel takes the colour of the highest band it reaches; radar and totals are always drawn opaque at full colour.</p></div></div>
    <div class="grid cols-2">
      <div class="card" v-for="s in SCALES" :key="s.id">
        <div class="card-head"><Icon :name="s.icon" :size="16" style="color:var(--accent)"/><h3>{{ s.label }}</h3>
          <span class="pill accent" v-if="custom(s.id)">custom</span>
          <button class="btn sm ghost" v-if="custom(s.id)" @click="reset(s.id)"><Icon name="RotateCcw" :size="13"/>Default</button></div>
        <div class="card-body stack">
          <div class="gradient"><span v-for="(b, i) in bands(s.id)" :key="i" :style="{background: b[1]}" :title="'≥ ' + b[0] + ' ' + s.unit"></span></div>
          <div class="faint" style="font-size:12px" v-if="s.note">{{ s.note }}</div>
          <div class="stack" style="gap:6px">
            <div class="band-row" v-for="(b, i) in bands(s.id)" :key="i">
              <span class="color"><input type="color" :value="b[1]" @input="set(s.id, i, 1, $event.target.value)"></span>
              <div class="row nowrap"><span class="faint" style="font-size:12px; width:22px">≥</span>
                <input class="input mono" type="number" min="0" step="0.05" :value="b[0]" @change="set(s.id, i, 0, +$event.target.value)">
                <span class="faint" style="font-size:12px; width:44px">{{ s.unit }}</span></div>
              <button class="btn sm ghost icon-only" :disabled="bands(s.id).length <= 2" @click="remove(s.id, i)"><Icon name="Minus" :size="14"/></button>
            </div>
          </div>
          <div><button class="btn sm" @click="add(s.id)"><Icon name="Plus" :size="14"/>Add band</button></div>
        </div>
      </div>
    </div>
  </div>`,
};
