import { ref, computed } from "vue";
import { store, toast, PANEL_TYPES, WEATHER_SCREENS, RULES, ALERT_LEVELS, HAZARDS, DAYS, ruleText, areaLabel, entryTitle,
  playOnDisplay } from "../core.js";
import { NowPlaying } from "../nowplaying.js";

function entrySub(e) {
  const bits = [];
  if (e.panel.startsWith("map")) {
    const tr = store.draft.modules.weather_maps?.tracks?.[e.track];
    if (tr) bits.push(areaLabel(tr.area, true));
  }
  if (PANEL_TYPES[e.panel]?.full) bits.push(e.panel === "weather_alert" ? "whole wall" : "full width");
  if (e.priority === "next") bits.push("jumps the queue");
  return bits.join(" · ");
}
function advanceText(e) {
  const t = PANEL_TYPES[e.panel] || {};
  if (e.advance === "cycles" && t.passes) {
    const n = e.cycles ?? (e.panel.startsWith("map") ? store.draft.modules.weather_maps?.tracks?.[e.track]?.loops ?? 2 : 1);
    return e.panel === "headlines" ? (n === 1 ? "all categories once" : `all categories ×${n}`) : `${n} ${n === 1 ? "pass" : "passes"}`;
  }
  return `${e.dwell_sec ?? 30} s`;
}
const sameEntry = (a, b) => a && b && a.panel === b.panel && (a.track || null) === (b.track || null) && (a.weather_screen || null) === (b.weather_screen || null);

const WITHIN = [{ value: 0, label: "right now" }, { value: 1, label: "within 1 h" }, { value: 3, label: "within 3 h" },
  { value: 6, label: "within 6 h" }, { value: 12, label: "within 12 h" }, { value: 24, label: "within 24 h" }];

const RuleRow = {
  props: { r: Object },
  emits: ["remove"],
  setup(props) {
    const setIf = (v) => {
      const r = props.r;
      for (const k of Object.keys(r)) if (k !== "not") delete r[k];
      Object.assign(r, { if: v }, RULES[v].make());
    };
    const toggleIn = (list, v) => { const i = list.indexOf(v); if (i >= 0) list.splice(i, 1); else list.push(v); list.sort(); };
    const hazards = computed(() => props.r.hazards || (props.r.hazards = []));
    return { setIf, toggleIn, hazards, RULES, ALERT_LEVELS, HAZARDS, DAYS, WITHIN };
  },
  template: `<div class="rule">
    <select class="input" :value="r.not ? 'not' : 'only'" @change="r.not = $event.target.value === 'not' || undefined">
      <option value="only">Only when</option><option value="not">Not when</option></select>
    <select class="input" :value="r.if" @change="setIf($event.target.value)">
      <option v-for="(t, id) in RULES" :key="id" :value="id">{{ t.label }}</option></select>
    <template v-if="r.if === 'alert'">
      <select class="input" v-model="r.min_level"><option :value="undefined">any level</option>
        <option v-for="(l, id) in ALERT_LEVELS" :key="id" :value="id">{{ l.toLowerCase() }} or worse</option></select>
      <label class="row nowrap faint" style="gap:5px; font-size:12.5px"><input type="checkbox" v-model="r.home"> names home</label>
      <div class="row" style="gap:4px; flex-basis:100%">
        <span class="faint" style="font-size:12px">Weather types (none = any):</span>
        <button v-for="(l, id) in HAZARDS" :key="id" type="button" class="chip" :class="{off: !hazards.includes(id)}"
          style="height:24px; padding:0 9px" @click="toggleIn(hazards, id)">{{ l }}</button></div>
    </template>
    <template v-else-if="r.if === 'precip'">
      <select class="input" v-model.number="r.within_h"><option v-for="w in WITHIN" :key="w.value" :value="w.value">{{ w.label }}</option></select>
      <template v-if="r.within_h > 0"><span class="faint">chance ≥</span><Stepper v-model="r.pop" :min="10" :max="100" :step="10" unit="%"/></template>
    </template>
    <template v-else-if="r.if === 'hours'">
      <input class="input" type="time" v-model="r.from"><span class="faint">to</span><input class="input" type="time" v-model="r.to">
    </template>
    <template v-else-if="r.if === 'days'">
      <div class="day-pick"><button v-for="(d, i) in DAYS" :key="i" type="button" :class="{on: (r.days || []).includes(i)}"
        @click="toggleIn(r.days || (r.days = []), i)">{{ d }}</button></div>
    </template>
    <template v-else-if="r.if === 'fresh'">
      <span class="faint">under</span><Stepper v-model="r.max_age_min" :min="5" :max="1440" :step="5" unit=" min"/><span class="faint">old</span>
    </template>
    <span class="grow"></span>
    <button class="btn sm ghost icon-only" title="Remove this rule" @click="$emit('remove')"><Icon name="X" :size="14"/></button>
  </div>`,
};

const SlotEditor = {
  components: { RuleRow },
  props: { e: Object, side: String },
  setup(props) {
    const t = computed(() => PANEL_TYPES[props.e.panel] || {});
    const tracks = computed(() => Object.entries(store.draft.modules.weather_maps?.tracks || {})
      .filter(([, tr]) => (tr.size === "full") === !!t.value.full));
    const setPanel = (p) => {
      const e = props.e;
      e.panel = p;
      const pt = PANEL_TYPES[p];
      if (pt.sub === "track" && !e.track) e.track = tracks.value[0]?.[0] || Object.keys(store.draft.modules.weather_maps?.tracks || {})[0];
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
    const passes = computed(() => props.e.cycles ?? (props.e.panel.startsWith("map") ? null : 1));
    const defaultLoops = computed(() => store.draft.modules.weather_maps?.tracks?.[props.e.track]?.loops ?? 2);
    const rules = computed(() => props.e.rules || (props.e.rules = []));
    const addRule = () => rules.value.push({ if: "calendar_events", ...RULES.calendar_events.make() });
    const priority = computed({ get: () => props.e.priority === "next", set: (v) => { props.e.priority = v ? "next" : ""; } });
    const enabled = computed({ get: () => props.e.enabled !== false, set: (v) => { if (v) delete props.e.enabled; else props.e.enabled = false; } });
    return { t, tracks, setPanel, mode, passes, defaultLoops, rules, addRule, priority, enabled, PANEL_TYPES, WEATHER_SCREENS };
  },
  template: `<div class="slot-editor">
    <div class="field"><div class="label">Panel</div>
      <div class="type-grid"><button v-for="(pt, id) in PANEL_TYPES" :key="id" type="button" class="type-card" :class="{on: e.panel === id}"
        :disabled="id === 'calendar' && side === 'right'" @click="setPanel(id)"><Icon :name="pt.icon" :size="16"/><span style="font-size:12.5px">{{ pt.label }}</span></button></div></div>
    <div class="fields" v-if="t.sub">
      <Field v-if="t.sub === 'track'" label="Map"><select class="input" v-model="e.track">
        <option v-for="[id, tr] in tracks" :key="id" :value="id">{{ tr.label }}</option></select></Field>
      <Field v-if="t.sub === 'weather_screen'" label="Screen"><select class="input" v-model="e.weather_screen">
        <option v-for="(l, id) in WEATHER_SCREENS" :key="id" :value="id">{{ l }}</option></select></Field>
    </div>
    <div class="field"><div class="label">When it comes up</div>
      <div class="rule-list">
        <RuleRow v-for="(r, i) in rules" :key="i" :r="r" @remove="rules.splice(i, 1)"/>
        <div class="row"><button class="btn sm" @click="addRule"><Icon name="Plus" :size="14"/>Add a rule</button>
          <span class="faint" style="font-size:12.5px">{{ rules.length ? 'All rules must hold. They are checked when the next screen is picked; a screen that starts always plays to its end.' : 'No rules: it comes up every time round.' }}</span></div>
      </div>
      <ToggleRow v-model="priority" label="Jump the queue" hint="When its rules start holding, it plays next (after the current screen finishes), then the rotation carries on where it was."/>
    </div>
    <div class="field"><div class="label">Move on to the next screen</div>
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
      <div class="hint" v-if="t.full">While it plays, the other side is paused (its time doesn't run out unseen).</div>
    </div>
    <ToggleRow v-model="enabled" label="In the rotation" hint="Off keeps it here (and playable with ▶) but the rotation skips it."/>
  </div>`,
};

export const Rotation = {
  components: { SlotEditor, NowPlaying },
  setup() {
    const cycle = computed(() => store.draft.modules.layout_cycle);
    const open = ref(null);   // "left:3"
    const disp = computed(() => store.display?.status?.panels || {});
    const isCurrent = (side, i) => {
      const p = disp.value[side];
      return !!p && !p.preview && p.playing && sameEntry(p.playing.entry, cycle.value[side][i]);
    };
    // rules evaluated on the display just now (matched by entry, since the draft may be reordered/unsaved)
    const waiting = (side, i) => {
      const p = disp.value[side];
      const e = cycle.value[side][i];
      if (!p?.cycle || !p.eligible) return false;
      const j = p.cycle.findIndex((x) => sameEntry(x, e));
      return j >= 0 && p.eligible[j] === false && e.enabled !== false;
    };
    const add = (side) => {
      cycle.value[side].push({ panel: "headlines", rules: [], advance: "cycles", cycles: 1, max_sec: 1800 });
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
    const play = (side, e, hold) => playOnDisplay(e, { side, hold });
    return { cycle, open, isCurrent, waiting, add, remove, moveTo, play, entryTitle, entrySub, advanceText, ruleText, PANEL_TYPES };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Rotation</h1>
      <p>What each half of the wall cycles through, and when. Every screen plays to its end; rules decide which screens come up.
        ▶ plays a screen once and returns to the rotation; ⏸ holds it on the wall.</p></div></div>
    <NowPlaying/>
    <div class="lanes">
      <div class="card" v-for="side in ['left','right']" :key="side">
        <div class="card-head"><Icon :name="side === 'left' ? 'PanelLeft' : 'PanelRight'" :size="17" style="color:var(--accent)"/>
          <h3>{{ side === 'left' ? 'Left half' : 'Right half' }}</h3>
          <button class="btn sm" @click="add(side)"><Icon name="Plus" :size="14"/>Add</button></div>
        <div class="lane-items" v-sortable="{ list: cycle[side], handle: '.handle', onChange: () => open = null }">
          <div v-for="(e, i) in cycle[side]" :key="side + i + e.panel + (e.track || '') + (e.weather_screen || '')">
            <div class="slot" :class="{current: isCurrent(side, i), off: e.enabled === false, waiting: waiting(side, i)}">
              <span class="handle"><Icon name="GripVertical" :size="16"/></span>
              <span class="kind"><Icon :name="PANEL_TYPES[e.panel]?.icon || 'Square'" :size="18"/></span>
              <div class="meta" style="cursor:pointer" @click="open = open === side + ':' + i ? null : side + ':' + i">
                <b>{{ entryTitle(e) }}</b>
                <span>{{ [entrySub(e), e.enabled === false ? 'off' : waiting(side, i) ? 'skipped right now: rules don\\'t hold' : ''].filter(Boolean).join(' · ') || 'every time round' }}</span>
                <div class="rule-summary" v-if="e.rules?.length"><span class="pill" v-for="(r, k) in e.rules" :key="k">{{ ruleText(r) }}</span></div>
              </div>
              <span class="pill" :class="{accent: e.advance === 'cycles'}"><Icon :name="e.advance === 'cycles' ? 'Repeat' : 'Timer'" :size="12"/>{{ advanceText(e) }}</span>
              <span class="live" v-if="isCurrent(side, i)" title="On screen now"></span>
              <button class="btn sm ghost icon-only" title="Play once now, then back to the rotation" @click="play(side, e, false)"><Icon name="Play" :size="14"/></button>
              <button class="btn sm ghost icon-only" title="Hold it on the wall" @click="play(side, e, true)"><Icon name="Pause" :size="14"/></button>
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
