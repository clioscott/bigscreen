import { ref, computed } from "vue";
import { store, toast, seed, PANEL_TYPES, WEATHER_SCREENS, RULES, ALERT_LEVELS, HAZARDS, DAYS, ruleText, areaLabel, entryTitle,
  playOnDisplay, rotationGoto, PHASES, ALERT_RULES } from "../core.js";
import { NowPlaying } from "../nowplaying.js";

function entrySub(e) {
  const bits = [];
  if (e.panel.startsWith("map")) {
    const tr = store.draft.modules.weather_maps?.tracks?.[e.track];
    if (tr) bits.push(areaLabel(tr.area, true));
  }
  if (PANEL_TYPES[e.panel]?.full) bits.push(["weather_alert", "webcam_wall", "bridges_wall"].includes(e.panel) ? "whole wall" : "full width");
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
const sameEntry = (a, b) => a && b && (a._shape_of || a.panel) === (b._shape_of || b.panel) &&   // a shaped play matches its entry
  (a.track || null) === (b.track || null) && (a.weather_screen || null) === (b.weather_screen || null)
  && (a.cam || null) === (b.cam || null);

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
    return { setIf, toggleIn, hazards, RULES, ALERT_LEVELS, HAZARDS, DAYS, WITHIN, PHASES, ALERT_RULES };
  },
  template: `<div class="rule">
    <select class="input" :value="r.not ? 'not' : 'only'" @change="r.not = $event.target.value === 'not' || undefined">
      <option value="only">Only when</option><option value="not">Not when</option></select>
    <select class="input" :value="r.if" @change="setIf($event.target.value)">
      <option v-for="(t, id) in RULES" :key="id" :value="id">{{ t.label }}</option></select>
    <select v-if="ALERT_RULES.includes(r.if)" class="input" v-model="r.phase" title="Which stage of an alert's life on the wall">
      <option :value="undefined">any stage</option><option v-for="(l, id) in PHASES" :key="id" :value="id">{{ l }}</option></select>
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

// an entry's shapes: the same screen at another size (webcam / webcam_full / webcam_wall, map / map_full, …)
const baseOf = (id) => id.replace(/_(full|wall|half)$/, "");
const shapesOf = (id) => Object.keys(PANEL_TYPES).filter((p) => baseOf(p) === baseOf(id));
const shapeName = (id) => (id.endsWith("_wall") ? "whole wall" : id.endsWith("_half") ? "half width"
  : PANEL_TYPES[id]?.full ? (id.endsWith("_full") ? "full width" : "full screen") : "half width");
// "Half width when it's 07:00–17:00 and it's Mon–Fri", "Half width once its alerts are past their full-wall showings"
const clause = (r) => (r.not && r.phase === "full" ? ["once", "its alerts are past their full-wall showings"]
  : [r.not ? "unless" : "when", ruleText(r).replace(/^(not )?when /, "")]);
const variantText = (v) => {
  const s = shapeName(v.panel);
  let last = "";
  const when = (v.rules || []).map(clause).map(([c, t]) => { const out = c === last ? t : `${c} ${t}`; last = c; return out; });
  return s[0].toUpperCase() + s.slice(1) + (when.length ? " " + when.join(" and ") : " always");
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
      if (pt.sub === "cam" && !e.cam) e.cam = Object.keys(store.draft.modules.webcams?.cams || {})[0];
      if (pt.sub !== "track") delete e.track;
      if (pt.sub !== "weather_screen") delete e.weather_screen;
      if (pt.sub !== "cam") delete e.cam;
      if (!pt.passes) { delete e.advance; delete e.cycles; delete e.max_sec; e.dwell_sec = e.dwell_sec ?? 30; }
      if (e.variants) {                                     // keep only other sizes of the new panel
        e.variants = e.variants.filter((v) => baseOf(v.panel) === baseOf(p) && v.panel !== p);
        if (!e.variants.length) delete e.variants;
      }
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
    const cams = computed(() => store.draft.modules.webcams?.cams || {});
    // other shapes at certain times (display/rotation.py "variants"): the first whose rules hold wins
    const shapes = computed(() => shapesOf(props.e.panel));
    const variants = computed(() => props.e.variants || []);
    const addVariant = () => {
      const other = shapes.value.find((p) => p !== props.e.panel);
      if (!other) return;
      if (!props.e.variants) props.e.variants = [];
      props.e.variants.push({ panel: other, rules: [{ if: "hours", from: "07:00", to: "17:00" }, { if: "days", days: [0, 1, 2, 3, 4] }] });
    };
    const removeVariant = (i) => { props.e.variants.splice(i, 1); if (!props.e.variants.length) delete props.e.variants; };
    const addVariantRule = (v) => (v.rules = v.rules || []).push({ if: "hours", ...RULES.hours.make() });
    return { t, tracks, cams, setPanel, mode, passes, defaultLoops, rules, addRule, priority, enabled, PANEL_TYPES, WEATHER_SCREENS,
      shapes, variants, addVariant, removeVariant, addVariantRule, shapeName };
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
      <Field v-if="t.sub === 'cam'" label="Webcam"><div class="row nowrap"><select class="input" v-model="e.cam">
        <option v-for="(c, id) in cams" :key="id" :value="id">{{ c.label || id }}</option></select>
        <a :href="'#/webcams/' + (e.cam || '')" class="btn sm ghost" title="Crop, colour and caption"><Icon name="Pencil" :size="14"/></a></div></Field>
    </div>
    <div class="field"><div class="label">When it comes up</div>
      <div class="rule-list">
        <RuleRow v-for="(r, i) in rules" :key="i" :r="r" @remove="rules.splice(i, 1)"/>
        <div class="row"><button class="btn sm" @click="addRule"><Icon name="Plus" :size="14"/>Add a rule</button>
          <span class="faint" style="font-size:12.5px">{{ rules.length ? 'All rules must hold. They are checked when the next screen is picked; a screen that starts always plays to its end.' : 'No rules: it comes up every time round.' }}</span></div>
      </div>
      <ToggleRow v-model="priority" label="Jump the queue" hint="When its rules start holding, it plays next (after the current screen finishes), then the rotation carries on where it was."/>
    </div>
    <div class="field" v-if="shapes.length > 1"><div class="label">Size at certain times</div>
      <div class="rule-list">
        <div v-for="(v, i) in variants" :key="i" class="card-body" style="border:1px solid var(--line); border-radius:8px; padding:8px 10px">
          <div class="row" style="gap:8px; margin-bottom:6px"><span>Plays</span>
            <select class="input" v-model="v.panel" style="max-width:180px"><option v-for="p in shapes" :key="p" :value="p" :disabled="p === e.panel || variants.some((w, j) => j !== i && w.panel === p)">{{ shapeName(p) }}</option></select>
            <span class="faint">while all of these hold:</span><span class="spacer"></span>
            <button class="btn sm ghost icon-only" title="Remove" @click="removeVariant(i)"><Icon name="X" :size="14"/></button></div>
          <RuleRow v-for="(r, k) in v.rules" :key="k" :r="r" @remove="v.rules.splice(k, 1)"/>
          <button class="btn sm ghost" @click="addVariantRule(v)"><Icon name="Plus" :size="13"/>Add a rule</button>
        </div>
        <div class="row"><button class="btn sm" @click="addVariant"><Icon name="Plus" :size="14"/>Play at another size at certain times</button>
          <span class="faint" style="font-size:12.5px">{{ variants.length ? 'Otherwise it plays ' + shapeName(e.panel) + '. Decided when the screen starts.' : 'Always plays ' + shapeName(e.panel) + '.' }}</span></div>
      </div>
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
    const life = seed(["modules", "alert_lifecycle"], { full_showings: 3, full_minutes: 30, half: true });
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
    // ⇥ jumps the rotation there; only for entries the wall has (an unsaved new one isn't in its list yet)
    const onWall = (side, i) => !!disp.value[side]?.cycle?.some((x) => sameEntry(x, cycle.value[side][i]));
    const jumpTitle = (side, i) => !disp.value[side]?.cycle ? "Jump here (the wall isn't answering)"
      : !onWall(side, i) ? "Save first: the wall doesn't have this screen yet"
      : "Jump here: play it now, then the rotation carries on from it"
        + (cycle.value[side][i].enabled === false ? " (it's off: plays this once)" : waiting(side, i) ? " (its rules don't hold: plays this once)" : "");
    const jump = (side, i) => {
      const { variants, rules, ...own } = cycle.value[side][i];
      return rotationGoto(side, i, own);
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
    // ▶ shows the entry at its own size (an alert entry with nothing new would otherwise show an empty half panel)
    const play = (side, e, hold) => { const { variants, ...own } = e; return playOnDisplay(own, { side, hold }); };
    return { cycle, open, isCurrent, waiting, onWall, jumpTitle, jump, add, remove, moveTo, play, entryTitle, entrySub, advanceText, ruleText, variantText, PANEL_TYPES,
      life: computed(() => store.draft.modules.alert_lifecycle) };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Rotation</h1>
      <p>What each half of the wall cycles through, and when. Every screen plays to its end; rules decide which screens come up.
        ⇥ jumps the rotation to a screen and carries on from there; ▶ plays a screen once and returns to where the rotation was;
        ⏸ holds it on the wall.</p></div></div>
    <NowPlaying/>
    <div class="card" style="margin-bottom:16px"><div class="card-head"><Icon name="Siren" :size="16"/><h3>Alerts on the wall</h3></div>
      <div class="card-body stack">
        <div class="faint" style="font-size:12.5px">Every alert screen (weather, Alert Ready, DriveBC, earthquakes, tsunami, wildfire) follows the same life:
          a new alert jumps the queue and plays full-wall with all its pages (maps, more information); then its full-wall screen comes round
          in the rotation like any other; after the showings or the time below it moves to the half-width version on the right, so the
          ticker and clock stay. Urgent ones (early warnings, tsunami warnings, evacuation orders…) still take over at once.</div>
        <ToggleRow :modelValue="life.half !== false" @update:modelValue="v => life.half = v" label="Move alerts to the half-width version"
          hint="Off: alerts stay full-wall for as long as they're in effect"/>
        <div class="row" v-if="life.half !== false" style="gap:16px">
          <Field label="Full-wall for"><Stepper :modelValue="life.full_showings" @update:modelValue="v => life.full_showings = v" :min="1" :max="20" unit=" showings"/></Field>
          <Field label="or at most"><Stepper :modelValue="life.full_minutes" @update:modelValue="v => life.full_minutes = v" :min="5" :max="720" :step="5" unit=" min"/></Field>
        </div>
      </div></div>
    <div class="lanes">
      <div class="card" v-for="side in ['left','right']" :key="side">
        <div class="card-head"><Icon :name="side === 'left' ? 'PanelLeft' : 'PanelRight'" :size="17" style="color:var(--accent)"/>
          <h3>{{ side === 'left' ? 'Left half' : 'Right half' }}</h3>
          <button class="btn sm" @click="add(side)"><Icon name="Plus" :size="14"/>Add</button></div>
        <div class="lane-items" v-sortable="{ list: cycle[side], handle: '.handle', onChange: () => open = null }">
          <div v-for="(e, i) in cycle[side]" :key="side + i + e.panel + (e.track || '') + (e.weather_screen || '') + (e.cam || '')">
            <div class="slot" :class="{current: isCurrent(side, i), off: e.enabled === false, waiting: waiting(side, i)}">
              <span class="handle"><Icon name="GripVertical" :size="16"/></span>
              <span class="kind"><Icon :name="PANEL_TYPES[e.panel]?.icon || 'Square'" :size="18"/></span>
              <div class="meta" style="cursor:pointer" @click="open = open === side + ':' + i ? null : side + ':' + i">
                <b>{{ entryTitle(e) }}</b>
                <span>{{ [entrySub(e), e.enabled === false ? 'off' : waiting(side, i) ? 'skipped right now: rules don\\'t hold' : ''].filter(Boolean).join(' · ') || (e.rules?.length ? '' : 'every time round') }}</span>
                <div class="rule-summary" v-if="e.rules?.length"><span class="pill" v-for="(r, k) in e.rules" :key="k">{{ ruleText(r) }}</span></div>
                <div class="rule-summary" v-if="e.variants?.length"><span class="pill accent" v-for="(v, k) in e.variants" :key="'v' + k" title="Plays at this size while these hold; otherwise its own size"><Icon name="Maximize2" :size="12"/>{{ variantText(v) }}</span></div>
              </div>
              <span class="pill" :class="{accent: e.advance === 'cycles'}"><Icon :name="e.advance === 'cycles' ? 'Repeat' : 'Timer'" :size="12"/>{{ advanceText(e) }}</span>
              <span class="live" v-if="isCurrent(side, i)" title="On screen now"></span>
              <button class="btn sm ghost icon-only" :title="jumpTitle(side, i)" :disabled="!onWall(side, i)" @click="jump(side, i)"><Icon name="ArrowRightToLine" :size="14"/></button>
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
