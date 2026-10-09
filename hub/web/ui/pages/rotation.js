import { ref, computed } from "vue";
import { store, api, fail, toast, PANEL_TYPES, WEATHER_SCREENS, CONDITIONS, areaLabel, go } from "../core.js";

function entryTitle(e) {
  const t = PANEL_TYPES[e.panel];
  if (!t) return e.panel;
  if (e.panel.startsWith("map")) {
    const tr = store.draft.modules.weather_maps?.tracks?.[e.track];
    return tr ? tr.label : `Map: ${e.track || "?"}`;
  }
  if (e.panel.startsWith("weather")) return WEATHER_SCREENS[e.weather_screen || "current"] || t.label;
  return t.label;
}
function entrySub(e) {
  const t = PANEL_TYPES[e.panel] || {};
  const bits = [];
  if (e.panel.startsWith("map")) {
    const tr = store.draft.modules.weather_maps?.tracks?.[e.track];
    if (tr) bits.push(areaLabel(tr.area, true));
  }
  if (t.full) bits.push("full width");
  if ((e.when || "always") !== "always") bits.push(CONDITIONS[e.when]?.replace(/^When /, "when ") || e.when);
  return bits.join(" · ");
}
function advanceText(e) {
  const t = PANEL_TYPES[e.panel] || {};
  if (e.advance === "cycles" && t.passes) {
    const n = e.cycles ?? (e.panel.startsWith("map") ? store.draft.modules.weather_maps?.tracks?.[e.track]?.loops ?? 2 : 1);
    return e.panel === "headlines" ? (n === 1 ? "all categories once" : `all categories ×${n}`) : `${n} loop${n === 1 ? "" : "s"}`;
  }
  return `${e.dwell_sec ?? 30} s`;
}

const SlotEditor = {
  props: { e: Object, side: String },
  setup(props) {
    const t = computed(() => PANEL_TYPES[props.e.panel] || {});
    const tracks = computed(() => Object.entries(store.draft.modules.weather_maps?.tracks || {})
      .filter(([, tr]) => (tr.size === "full") === !!t.value.full));
    const setPanel = (p) => {
      const e = props.e;
      e.panel = p;
      const pt = PANEL_TYPES[p];
      if (pt.sub === "track" && !e.track) e.track = Object.keys(store.draft.modules.weather_maps?.tracks || {})[0];
      if (pt.sub === "weather_screen" && !e.weather_screen) e.weather_screen = "current";
      if (pt.sub !== "track") delete e.track;
      if (pt.sub !== "weather_screen") delete e.weather_screen;
      if (!pt.passes) { delete e.advance; delete e.cycles; delete e.max_sec; e.dwell_sec = e.dwell_sec ?? 30; }
    };
    const mode = computed({
      get: () => (props.e.advance === "cycles" ? "cycles" : "time"),
      set: (v) => {
        const e = props.e;
        if (v === "cycles") { e.advance = "cycles"; e.max_sec = e.max_sec ?? (e.panel === "headlines" ? 1800 : 600); delete e.dwell_sec; }
        else { delete e.advance; delete e.cycles; delete e.max_sec; e.dwell_sec = e.dwell_sec ?? 60; }
      },
    });
    const passes = computed({
      get: () => props.e.cycles ?? (props.e.panel.startsWith("map") ? null : 1),
      set: (v) => { props.e.cycles = v; },
    });
    const defaultLoops = computed(() => store.draft.modules.weather_maps?.tracks?.[props.e.track]?.loops ?? 2);
    return { t, tracks, setPanel, mode, passes, defaultLoops, PANEL_TYPES, WEATHER_SCREENS, CONDITIONS };
  },
  template: `<div class="slot-editor">
    <div class="field"><div class="label">Panel</div>
      <div class="type-grid"><button v-for="(pt, id) in PANEL_TYPES" :key="id" type="button" class="type-card" :class="{on: e.panel === id}"
        :disabled="id === 'calendar' && side === 'right'" @click="setPanel(id)"><Icon :name="pt.icon" :size="16"/><span style="font-size:12.5px">{{ pt.label }}</span></button></div></div>
    <div class="fields">
      <Field v-if="t.sub === 'track'" label="Map"><select class="input" v-model="e.track">
        <option v-for="[id, tr] in tracks" :key="id" :value="id">{{ tr.label }}</option></select></Field>
      <Field v-if="t.sub === 'weather_screen'" label="Screen"><select class="input" v-model="e.weather_screen">
        <option v-for="(l, id) in WEATHER_SCREENS" :key="id" :value="id">{{ l }}</option></select></Field>
      <Field label="Show it"><select class="input" v-model="e.when">
        <option v-for="(l, id) in CONDITIONS" :key="id" :value="id">{{ l }}</option></select></Field>
    </div>
    <div class="field"><div class="label">Move on to the next panel</div>
      <div class="row">
        <Seg v-if="t.passes" v-model="mode" :options="[{value:'cycles', label:'After complete passes', icon:'Repeat'}, {value:'time', label:'After a set time', icon:'Timer'}]"/>
        <template v-if="mode === 'cycles' && t.passes">
          <Stepper v-if="passes !== null" v-model="e.cycles" :min="1" :max="20"/>
          <button v-else class="btn sm" @click="e.cycles = defaultLoops">Use map's setting ({{ defaultLoops }} loops) · change</button>
          <span class="faint">× {{ t.passWord }}</span>
          <span class="spacer"></span>
          <span class="faint">safety cap</span><Stepper :modelValue="Math.round((e.max_sec || 600) / 60)" @update:modelValue="v => e.max_sec = v * 60" :min="1" :max="120" unit="min"/>
        </template>
        <template v-else><Stepper v-model="e.dwell_sec" :min="3" :max="3600" :step="5" unit="s"/></template>
      </div>
      <div class="hint" v-if="e.panel === 'headlines' && mode === 'cycles'">Stays until every headline category has scrolled through, however long that takes.</div>
    </div>
  </div>`,
};

export const Rotation = {
  components: { SlotEditor },
  setup() {
    const cycle = computed(() => store.draft.modules.layout_cycle);
    const open = ref(null);   // "left:3"
    const disp = computed(() => store.display?.status?.panels || {});
    const isCurrent = (side, i) => {
      const p = disp.value[side];
      return p && p.current_idx === i && !p.override && cycle.value[side][i]?.panel === p.panel;
    };
    const add = (side) => {
      const e = { panel: "headlines", when: "always", advance: "cycles", cycles: 1, max_sec: 1800 };
      cycle.value[side].push(e);
      open.value = `${side}:${cycle.value[side].length - 1}`;
    };
    const remove = (side, i) => { cycle.value[side].splice(i, 1); open.value = null; };
    const moveTo = (side, i) => {
      const other = side === "left" ? "right" : "left";
      const [e] = cycle.value[side].splice(i, 1);
      if (e.panel === "calendar" && other === "right") { cycle.value[side].splice(i, 0, e); toast("The calendar can only show on the left", "bad"); return; }
      cycle.value[other].push(e);
      open.value = null;
    };
    const showNow = async (side, e) => {
      const body = { side, panel: e.panel, duration_sec: 120 };
      if (e.track) body.track = e.track;
      if (e.weather_screen) body.weather_screen = e.weather_screen;
      try { await api("/api/display/override", { json: body }); toast("On the display for 2 minutes"); } catch (err) { fail(err); }
    };
    const backToRotation = async () => {
      try { await api("/api/display/override/clear", { method: "POST", json: {} }); toast("Back to the normal rotation"); } catch (e) { fail(e); }
    };
    return { cycle, open, isCurrent, add, remove, moveTo, showNow, backToRotation, entryTitle, entrySub, advanceText, PANEL_TYPES, disp };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Rotation</h1>
      <p>What each half of the display cycles through. Drag to reorder; click an entry to change what it shows, when, and how long it stays.</p></div>
      <button class="btn" @click="backToRotation"><Icon name="Undo2" :size="15"/>Clear "show now"</button></div>
    <div class="lanes">
      <div class="card" v-for="side in ['left','right']" :key="side">
        <div class="card-head"><Icon :name="side === 'left' ? 'PanelLeft' : 'PanelRight'" :size="17" style="color:var(--accent)"/>
          <h3>{{ side === 'left' ? 'Left half' : 'Right half' }}</h3>
          <span class="pill accent" v-if="disp[side]?.override">showing: {{ PANEL_TYPES[disp[side].override.panel]?.label || disp[side].override.panel }}</span>
          <button class="btn sm" @click="add(side)"><Icon name="Plus" :size="14"/>Add</button></div>
        <div class="lane-items" v-sortable="{ list: cycle[side], handle: '.handle', onChange: () => open = null }">
          <div v-for="(e, i) in cycle[side]" :key="side + i + e.panel + (e.track || '') + (e.weather_screen || '')">
            <div class="slot" :class="{current: isCurrent(side, i)}">
              <span class="handle"><Icon name="GripVertical" :size="16"/></span>
              <span class="kind"><Icon :name="PANEL_TYPES[e.panel]?.icon || 'Square'" :size="18"/></span>
              <div class="meta" style="cursor:pointer" @click="open = open === side + ':' + i ? null : side + ':' + i">
                <b>{{ entryTitle(e) }}</b><span>{{ entrySub(e) || 'always' }}</span></div>
              <span class="pill" :class="{accent: e.advance === 'cycles'}"><Icon :name="e.advance === 'cycles' ? 'Repeat' : 'Timer'" :size="12"/>{{ advanceText(e) }}</span>
              <span class="live" v-if="isCurrent(side, i)" title="On screen now"></span>
              <button class="btn sm ghost icon-only" title="Show now" @click="showNow(side, e)"><Icon name="Play" :size="14"/></button>
              <button class="btn sm ghost icon-only" :title="'Move to the ' + (side === 'left' ? 'right' : 'left')" @click="moveTo(side, i)"><Icon name="ArrowLeftRight" :size="14"/></button>
              <button class="btn sm ghost icon-only" title="Edit" @click="open = open === side + ':' + i ? null : side + ':' + i"><Icon :name="open === side + ':' + i ? 'ChevronDown' : 'Pencil'" :size="14"/></button>
            </div>
            <div v-if="open === side + ':' + i" style="margin-top:8px">
              <SlotEditor :e="e" :side="side"/>
              <div class="row end" style="margin-top:8px"><ConfirmButton small label="Remove" @confirm="remove(side, i)"/></div>
            </div>
          </div>
          <div class="empty" v-if="!cycle[side].length">Nothing here yet.</div>
        </div>
      </div>
    </div>
  </div>`,
};
