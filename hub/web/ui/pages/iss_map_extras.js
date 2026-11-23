// The ISS full map's extras (display/modules/issmap.py EXTRAS): each on or off with its own settings, kept in
// cams.<id>.map.extras (only what differs from the defaults is needed, but the whole of each extra is written).
export const EXTRA_DEFAULTS = {
  lights: { on: true, strength: 0.8 },
  aurora: { on: true, strength: 0.8 },
  ahead: { on: true, minutes: 45 },
  sunlit: { on: true, shadow: "blue" },
  tiangong: { on: true, trail: true, label: true },
  signal: { on: true, tag: true },
  russian: { on: true },
  flow: { on: true, speed: "normal", direction: "both" },
  telemetry: { on: true, corner: "tr", rows: ["cabin_p", "cabin_t", "o2", "co2", "gyros", "water"] },
  docked: { on: true, corner: "tr", days: true },
};
const ROWS = [
  { value: "cabin_p", label: "Cabin pressure" }, { value: "cabin_t", label: "Cabin temperature" },
  { value: "o2", label: "Oxygen" }, { value: "co2", label: "CO₂" }, { value: "gyros", label: "Gyroscopes online" },
  { value: "ku", label: "Ku-band transmitting" }, { value: "water", label: "Clean water tank" },
  { value: "arrays", label: "Solar array angles" },
];
const ITEMS = [
  { k: "lights", icon: "Lightbulb", title: "City lights on the night side", note: "NASA Black Marble, faded in where it's dark" },
  { k: "aurora", icon: "Sparkles", title: "Aurora forecast", note: "NOAA's live aurora oval, faint green on the night side" },
  { k: "ahead", icon: "MoveRight", title: "Track ahead", note: "where the ISS goes next, dashed and fading" },
  { k: "sunlit", icon: "SunMoon", title: "Trail in sun and shadow", note: "warm in sunlight, another colour in Earth's shadow" },
  { k: "tiangong", icon: "Satellite", title: "Tiangong", note: "China's space station, with its own faint trail" },
  { k: "signal", icon: "Radio", title: "Signal: AOS / LOS", note: "NASA's own telemetry: the link to TDRS is drawn only while mission control has the signal" },
  { k: "russian", icon: "RadioTower", title: "Russian segment link", note: "orange: straight down over Russia, else through a Luch-5 relay (estimated from geometry)" },
  { k: "flow", icon: "Activity", title: "Data moving along the links", note: "dots travel ISS ↔ relay ↔ ground, only while the link is up" },
  { k: "telemetry", icon: "Gauge", title: "Station telemetry", note: "live readings from NASA ISS Live" },
  { k: "docked", icon: "Ship", title: "Docked spacecraft", note: "with flags and days docked" },
];

export const IssMapExtras = {
  props: { cam: Object },
  setup(props) {
    const get = (k) => ({ ...EXTRA_DEFAULTS[k], ...((props.cam.map?.extras || {})[k] || {}) });
    const set = (k, key, v) => {
      const map = props.cam.map || (props.cam.map = {});
      const ex = map.extras || (map.extras = {});
      ex[k] = { ...get(k), [key]: v };
    };
    const toggleRow = (r) => {
      const rows = [...get("telemetry").rows];
      const i = rows.indexOf(r);
      if (i >= 0) rows.splice(i, 1); else rows.splice(ROWS.findIndex((x) => x.value === r), 0, r);
      set("telemetry", "rows", ROWS.map((x) => x.value).filter((v) => rows.includes(v)));
    };
    const pct = (v) => Math.round(v * 100) + "%";
    return { ITEMS, ROWS, get, set, toggleRow, pct };
  },
  template: `<div class="isx">
    <div v-for="it in ITEMS" :key="it.k" class="isx-row" :class="{off: !get(it.k).on}">
      <Toggle :modelValue="get(it.k).on" @update:modelValue="v => set(it.k, 'on', v)" :title="it.title"/>
      <Icon :name="it.icon" :size="15"/>
      <div class="isx-txt"><b>{{ it.title }}</b><span class="faint">{{ it.note }}</span></div>
      <div class="isx-set" v-if="get(it.k).on">
        <template v-if="it.k === 'lights' || it.k === 'aurora'">
          <span class="faint">Strength</span><Slider :modelValue="get(it.k).strength" @update:modelValue="v => set(it.k, 'strength', v)" :min="0.1" :max="1" :step="0.05" :format="pct"/></template>
        <template v-else-if="it.k === 'ahead'">
          <Stepper :modelValue="get('ahead').minutes" @update:modelValue="v => set('ahead', 'minutes', v)" :min="5" :max="90" :step="5" unit=" min"/></template>
        <template v-else-if="it.k === 'sunlit'">
          <span class="faint">Shadow</span><Seg :modelValue="get('sunlit').shadow" @update:modelValue="v => set('sunlit', 'shadow', v)"
            :options="[{ value: 'blue', label: 'Blue' }, { value: 'dim', label: 'Dim grey' }]"/></template>
        <template v-else-if="it.k === 'tiangong'">
          <label class="isx-chk"><input type="checkbox" :checked="get('tiangong').trail" @change="set('tiangong', 'trail', $event.target.checked)">Trail</label>
          <label class="isx-chk"><input type="checkbox" :checked="get('tiangong').label" @change="set('tiangong', 'label', $event.target.checked)">Name (full width)</label></template>
        <template v-else-if="it.k === 'signal'">
          <label class="isx-chk"><input type="checkbox" :checked="get('signal').tag" @change="set('signal', 'tag', $event.target.checked)">AOS/LOS beside the ISS</label></template>
        <template v-else-if="it.k === 'flow'">
          <Seg :modelValue="get('flow').direction" @update:modelValue="v => set('flow', 'direction', v)"
            :options="[{ value: 'both', label: 'Up & down' }, { value: 'down', label: 'Down' }, { value: 'up', label: 'Up' }]"/>
          <Seg :modelValue="get('flow').speed" @update:modelValue="v => set('flow', 'speed', v)"
            :options="[{ value: 'slow', label: 'Slow' }, { value: 'normal', label: 'Normal' }, { value: 'fast', label: 'Fast' }]"/></template>
        <template v-else-if="it.k === 'telemetry' || it.k === 'docked'">
          <Seg :modelValue="get(it.k).corner" @update:modelValue="v => set(it.k, 'corner', v)"
            :options="[{ value: 'tl', label: 'Top left' }, { value: 'tr', label: 'Top right' }]"/>
          <label v-if="it.k === 'docked'" class="isx-chk"><input type="checkbox" :checked="get('docked').days" @change="set('docked', 'days', $event.target.checked)">Days docked</label></template>
      </div>
      <div class="isx-rows" v-if="it.k === 'telemetry' && get('telemetry').on">
        <button v-for="r in ROWS" :key="r.value" type="button" class="isx-chip" :class="{on: get('telemetry').rows.includes(r.value)}"
          @click="toggleRow(r.value)">{{ r.label }}</button></div>
    </div>
    <div class="faint" style="font-size:12px">The panels and names show only where there's room (from 280 px wide); full-width zones get the bigger icons.</div>
  </div>`,
};
