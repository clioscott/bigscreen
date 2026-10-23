// Time zone map: the US and Canada by UTC offset (hub/jobs/timezones.py, display/modules/tzmap.py).
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, clone, dirty, seed, previewOnDisplay, playOnDisplay, ago } from "../core.js";

const SHAPES = {
  wall: { label: "Whole wall", panel: "timezones_wall", side: "right", icon: "Maximize" },
  full: { label: "Full width", panel: "timezones_full", side: "right", icon: "RectangleHorizontal" },
  right: { label: "One side", panel: "timezones", side: "right", icon: "PanelRight" },
};
const offText = (m) => `UTC${m >= 0 ? "+" : "−"}${Math.floor(Math.abs(m) / 60)}${Math.abs(m) % 60 ? ":" + String(Math.abs(m) % 60).padStart(2, "0") : ""}`;
const when = (t) => new Date(t * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export const Timezones = {
  setup() {
    const state = ref(null);
    const shape = ref("wall");
    const page = ref(0);
    const asOf = ref("now");           // "now" | "after:<i>" (just after change i) | "custom"
    const custom = ref("");
    const tick = ref(0);
    const running = ref(false);
    let t;
    seed(["modules", "timezones"], {});
    const tz = computed(() => store.draft.modules.timezones);
    const load = async () => { try { state.value = await api("/api/timezones/state"); } catch (e) { fail(e); } };
    onMounted(() => { load(); t = setInterval(() => { tick.value++; load(); }, 30000); });
    onBeforeUnmount(() => clearInterval(t));

    const d = computed(() => state.value?.defaults || {});
    const val = (k) => tz.value[k] ?? d.value[k];
    const set = (k, v) => { tz.value[k] = v; };
    // dictionaries keep only what differs from the defaults
    const dictVal = (k, key) => (tz.value[k] || {})[key] ?? "";
    // (a place may be set to "" to leave a default one out of the legend)
    const dictSet = (k, key, v) => {
      const o = tz.value[k] || (tz.value[k] = {});
      const def = (d.value[k] || {})[key];
      if (v === def || (v === "" && (k !== "places" || !def))) delete o[key]; else o[key] = v;
      if (!Object.keys(o).length) delete tz.value[k];
    };
    const cities = computed(() => tz.value.cities || d.value.cities || []);
    // zone times on the map, one switch per country and offset (all on unless listed in map_label_hide)
    const labelShown = (cc, off) => !(val("map_label_hide") || []).includes(`${cc}:${off}`);
    const toggleLabel = (cc, off) => {
      const key = `${cc}:${off}`, hide = new Set(val("map_label_hide") || []);
      if (hide.has(key)) hide.delete(key); else hide.add(key);
      set("map_label_hide", [...hide]);
    };
    const setListed = (id, on) => {
      const hide = new Set(val("list_hide") || []);
      if (on) hide.delete(id); else hide.add(id);
      set("list_hide", [...hide]);
    };
    const editCities = () => { if (!tz.value.cities) tz.value.cities = clone(d.value.cities); return tz.value.cities; };
    const addCity = () => editCities().push({ name: "New city", lat: 49.0, lon: -100.0, on: true });

    // every offset the map shows now or after a coming change, for colours, with its names in each country
    // across the seasons (Canada's UTC−4 is Ontario/Quebec's Eastern in summer, the Atlantic's in winter)
    const offsets = computed(() => {
      const seen = new Map();
      for (const g of [...(state.value?.groups || []), ...(state.value?.changes || []).flatMap((c) => c.groups)]) {
        const o = seen.get(g.off) || seen.set(g.off, {}).get(g.off);
        const names = new Set((o[g.cc] || "").split(" / ").filter(Boolean));
        for (const n of g.name.split("/")) names.add(n);
        o[g.cc] = [...names].join(" / ");
      }
      return [...seen.entries()].sort((a, b) => a[0] - b[0]).map(([off, names]) => ({ off, names }));
    });
    // an offset nobody picked a colour for gets the same hue as on the wall (tzmap.color_of)
    const hue = (off) => {
      const h = (((off + 720) * 7) % 360) / 60, f = h - Math.floor(h), v = 255, p = Math.round(255 * 0.4);
      const q = Math.round(255 * (1 - 0.6 * f)), t = Math.round(255 * (1 - 0.6 * (1 - f)));
      const rgb = [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]][Math.floor(h) % 6];
      return "#" + rgb.map((x) => x.toString(16).padStart(2, "0")).join("");
    };
    const colorOf = (off) => (tz.value.colors || {})[off] || (d.value.colors || {})[off] || hue(off);

    // the moment previewed / tested: now, just after a coming change, or a date of your own
    const asOfTs = computed(() => {
      if (asOf.value.startsWith("after:")) return state.value?.changes?.[Number(asOf.value.slice(6))]?.end + 3600;
      if (asOf.value === "custom" && custom.value) return new Date(custom.value).getTime() / 1000;
      return null;
    });
    const pagesAt = computed(() => {
      const tt = asOfTs.value ?? Date.now() / 1000;
      const days = Number(val("warn_days") || 0);
      const nxt = (state.value?.changes || []).find((c) => c.start > tt);
      if (!nxt || days <= 0 || tt < nxt.start - days * 86400) return ["Now"];
      return val("warn_pages") === "upcoming" ? ["Upcoming"] : ["Upcoming", "Now"];
    });
    const look = computed(() => encodeURIComponent(JSON.stringify(tz.value)));
    const preview = computed(() => `/api/timezones/preview.png?shape=${shape.value}&page=${page.value}&scale=3&as_of=${asOfTs.value || 0}&s=${look.value}&t=${tick.value}`);

    const entry = (test) => {
      const e = { panel: SHAPES[shape.value].panel, rules: [], advance: "cycles", cycles: 1, max_sec: 180 };
      if (test && asOfTs.value) e.as_of = asOfTs.value;
      return e;
    };
    const play = async (hold) => {
      if (dirty.value.length) await previewOnDisplay();
      playOnDisplay(entry(true), { side: SHAPES[shape.value].side, hold });
    };
    const addToRotation = (onlyNearChange) => {
      const e = entry(false);
      if (onlyNearChange) e.rules = [{ if: "timechange", days_after: 1 }];
      store.draft.modules.layout_cycle[SHAPES[shape.value].side].push(e);
      toast(`Added to the ${SHAPES[shape.value].side} rotation${onlyNearChange ? ", around clock changes only" : ""}. Save to keep it.`);
    };
    const used = computed(() => ["left", "right"].flatMap((s) => (store.draft.modules.layout_cycle[s] || [])
      .filter((e) => e.panel?.startsWith("timezones")).map((e) => `${s} · ${e.panel.replace("timezones_", "").replace("timezones", "side")}`)));
    const rebuild = async () => {
      running.value = true;
      try { await api("/api/jobs/timezones/run", { method: "POST" }); toast("Checking the rules and rebuilding the map…"); setTimeout(() => { tick.value++; load(); running.value = false; }, 10000); }
      catch (e) { fail(e); running.value = false; }
    };
    const zoneName = (z) => z.replace(/^(America|Pacific)\//, "").replace(/_/g, " ");

    // what each zone's map label says, a line each (map_label_lines; unset: the time, plus the older offset switch)
    const lines = computed(() => tz.value.map_label_lines
      || [{ kind: "time" }, ...(val("map_label_offsets") ? [{ kind: "home" }] : [])]);
    const editLines = () => {
      if (!tz.value.map_label_lines) { tz.value.map_label_lines = clone(lines.value); delete tz.value.map_label_offsets; }
      return tz.value.map_label_lines;
    };
    const setLine = (i, k, v) => { const ln = editLines()[i]; if (v === "" || v == null) delete ln[k]; else ln[k] = v; };
    const moveLine = (i, by) => { const ls = editLines(), [ln] = ls.splice(i, 1); ls.splice(i + by, 0, ln); };
    const addLine = (kind) => editLines().push({ kind, ...(kind === "zone" ? { zone: "Europe/London", prefix: "LON " } : {}) });
    // a zone's UTC offset now, from the browser's own rules (null: no such zone)
    const zoneOff = (z) => {
      try {
        const p = new Intl.DateTimeFormat("en-US", { timeZone: z, timeZoneName: "longOffset" }).formatToParts(new Date())
          .find((x) => x.type === "timeZoneName").value.match(/([+-])(\d+):?(\d+)?/);
        return p ? (p[1] === "-" ? -1 : 1) * (+p[2] * 60 + +(p[3] || 0)) : 0;
      } catch { return null; }
    };
    const delta = (d) => (d > 0 ? "+" : "-") + Math.floor(Math.abs(d) / 60) + (Math.abs(d) % 60 ? ":" + String(Math.abs(d) % 60).padStart(2, "0") : "h");
    const utcText = (off, style) => {
      const s = off >= 0 ? "+" : "-", h = Math.floor(Math.abs(off) / 60), m = Math.abs(off) % 60, p2 = (n) => String(n).padStart(2, "0");
      return style === "hhmm" ? `${s}${p2(h)}:${p2(m)}` : style === "compact" ? `${s}${p2(h)}${p2(m)}` : `${s}${h}${m ? ":" + p2(m) : ""}`;
    };
    // how a line reads on Newfoundland's label (UTC−2:30 in summer, −3:30 in winter: shows the minutes too)
    const example = (ln) => {
      const off = zoneOff("America/St_Johns") ?? -150, home = zoneOff(val("home_zone") || "America/Vancouver") ?? -420;
      const pre = ln.prefix || "", d = new Date(Date.now() + off * 60000);
      switch (ln.kind) {
        case "time": return (d.getUTCHours() % 12 || 12) + ":" + String(d.getUTCMinutes()).padStart(2, "0") + (ln.style === "ampm" ? (d.getUTCHours() < 12 ? "AM" : "PM") : "");
        case "home": return pre + delta(off - home);
        case "utc": return (ln.prefix ?? "UTC") + utcText(off, ln.style);
        case "zone": { const r = zoneOff(ln.zone || ""); return r == null ? "(unknown zone)" : pre + (r === off ? ln.same ?? "same" : delta(off - r)); }
        case "name": return pre + (ln.style === "abbr" ? "NDT" : "Newfoundland");
        case "day": { const w = d.toLocaleString("en-US", { weekday: "short", timeZone: "UTC" }).toUpperCase(), m = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" }).toUpperCase();
          return pre + ({ date: `${m} ${d.getUTCDate()}`, both: `${w} ${d.getUTCDate()}` }[ln.style] || w); }
      }
      return "";
    };
    const LINE_KINDS = { time: "Time", home: "Offset from home", utc: "Offset from UTC / GMT", zone: "Offset from another zone",
      name: "Zone name", day: "Day / date" };
    const LINE_STYLES = {
      time: [{ value: "", label: "7:42" }, { value: "ampm", label: "7:42PM" }],
      utc: [{ value: "", label: "-8 / +5:30" }, { value: "hhmm", label: "-08:00" }, { value: "compact", label: "-0800" }],
      name: [{ value: "", label: "Pacific" }, { value: "abbr", label: "PDT" }],
      day: [{ value: "", label: "THU" }, { value: "date", label: "OCT 23" }, { value: "both", label: "THU 23" }],
    };
    const lineColour = (ln) => ln.color || (ln.kind === "time" ? "#ffffff" : "zone");
    return { lines, editLines, setLine, moveLine, addLine, example, zoneOff, lineColour, LINE_KINDS, LINE_STYLES, labelShown, toggleLabel, setListed, d, state, shape, page, asOf, custom, tz, val, set, dictVal, dictSet, cities, editCities, addCity, offsets, colorOf,
      pagesAt, preview, play, addToRotation, used, rebuild, running, SHAPES, ago, when, offText, zoneName };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Time zone map</h1>
      <p>The US and Canada shaded by UTC offset, with the time in each, from the newest tz database (as of tzdata 2026e, BC, Alberta,
        the NWT and Manitoba stop changing their clocks on Nov 1, 2026, so the map no longer lines up with the old Pacific / Mountain / Central bands). Before every clock change
        the wall also shows the map as it will be, labelled as upcoming; it switches when the clocks change.</p></div>
      <button class="btn" :disabled="running" @click="rebuild"><Icon :name="running ? 'LoaderCircle' : 'RefreshCw'" :size="15" :class="{spin: running}"/>Check rules now</button></div>
    <div class="two-col">
      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="CalendarClock" :size="16"/><h3>Clock changes</h3></div>
          <div class="card-body stack">
            <div v-if="!state?.changes?.length" class="faint">No clock changes in the next year{{ state ? '' : ' (loading…)' }}.</div>
            <div v-for="(c, i) in state?.changes || []" :key="c.start" class="stack" style="gap:4px">
              <div class="row"><b>{{ c.words }}</b><span class="faint">{{ when(c.start) }} – {{ when(c.end) }}</span>
                <span class="spacer"></span><button class="btn sm" @click="asOf = 'after:' + i; page = 0"><Icon name="Eye" :size="14"/>Preview after</button></div>
              <div class="faint" style="font-size:12.5px">{{ c.changed.length }} of {{ state.zones.filter(z => z.cc !== 'MX').length }} Canadian and US zones change offset.
                <span v-if="c.new.length">New rules this year: <b>{{ c.new.map(zoneName).join(", ") }}</b>.</span></div>
              <div class="row" style="flex-wrap:wrap; gap:6px">
                <span v-for="g in c.groups" :key="g.cc + g.off" class="pill" :style="{borderColor: colorOf(g.off)}"><b>{{ g.cc }}</b>&nbsp;{{ offText(g.off) }} {{ g.name }}<span class="faint" v-if="g.places.length">&nbsp;· {{ g.places.join(" ") }}</span></span></div>
            </div>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="Type" :size="16"/><h3>Text</h3></div>
          <div class="card-body stack">
            <div class="row" style="flex-wrap:wrap">
              <Field label="Title"><input class="input" :value="val('title')" @input="set('title', $event.target.value)" maxlength="30"></Field>
              <Field label="Upcoming label"><input class="input" :value="val('upcoming_label')" @input="set('upcoming_label', $event.target.value)" maxlength="30"></Field>
              <Field label="Now label"><input class="input" :value="val('now_label')" @input="set('now_label', $event.target.value)" maxlength="30"></Field>
              <Field label="Test label"><input class="input" :value="val('test_label')" @input="set('test_label', $event.target.value)" maxlength="30"></Field></div>
            <template v-if="val('show_list')">
            <div class="row" style="flex-wrap:wrap">
              <Field v-for="cc in ['CA', 'US']" :key="cc" :label="(cc === 'CA' ? 'Canada' : 'US') + ' heading (wide / narrow)'">
                <div class="row nowrap" style="gap:4px">
                  <input class="input" style="width:130px" :placeholder="d.country_labels?.[cc]" :value="dictVal('country_labels', cc)" @input="dictSet('country_labels', cc, $event.target.value)" maxlength="30">
                  <input class="input" style="width:80px" :placeholder="d.country_short?.[cc]" :value="dictVal('country_short', cc)" @input="dictSet('country_short', cc, $event.target.value)" maxlength="12"></div></Field></div>
            </template>
            <div class="row" style="flex-wrap:wrap">
              <Field label="Clock"><Seg :modelValue="val('time_format')" @update:modelValue="v => set('time_format', v)" :options="[{value:'12h', label:'12 h'}, {value:'24h', label:'24 h'}]"/></Field></div>
            <ToggleRow :modelValue="val('show_list')" @update:modelValue="v => set('show_list', v)" label="Show the list beside the map"
              hint="Off: the map fills the panel and its labels carry the times (see Cities)"/>
            <template v-if="val('show_list')">
            <Field label="Legend rows" hint="Canada and the US in columns side by side, so their times line up">
              <Seg :modelValue="val('legend_rows')" @update:modelValue="v => set('legend_rows', v)"
                :options="[{value:'offset', label:'UTC offsets (like the map)'}, {value:'traditional', label:'Old zones (Pacific, Mountain…)'}]"/></Field>
            <div class="row" style="flex-wrap:wrap; gap:6px" v-if="val('legend_rows') !== 'offset'">
              <span v-for="(n, off) in {'-480': 'PACIFIC', '-420': 'MOUNTAIN', '-360': 'CENTRAL', '-300': 'EASTERN', '-240': 'ATLANTIC'}" :key="off" class="row nowrap" style="gap:4px">
                <input class="input" style="width:100px" :placeholder="n" :value="dictVal('row_names', off)" @input="dictSet('row_names', off, $event.target.value)" maxlength="12"></span></div>
            <Field v-if="val('legend_rows') === 'traditional'" label="Rule tags" hint="On each time in the list: places that still change their clocks, and places that don't (highlighted)">
              <div class="row nowrap" style="gap:4px">
                <input class="input" style="width:110px" :placeholder="d.rule_labels?.dst" :value="dictVal('rule_labels', 'dst')" @input="dictSet('rule_labels', 'dst', $event.target.value)" maxlength="12">
                <input class="input" style="width:110px" :placeholder="d.rule_labels?.fixed" :value="dictVal('rule_labels', 'fixed')" @input="dictSet('rule_labels', 'fixed', $event.target.value)" maxlength="12"></div></Field>
            <Field label="Each time is called" hint="From the zones' tz abbreviations, so each country's own naming: after Nov 1 BC is Mountain (MST) beside the US's Pacific (PST)">
              <Seg :modelValue="val('label_style')" @update:modelValue="v => set('label_style', v)"
                :options="[{value:'name', label:'Mountain, Pacific…'}, {value:'abbr', label:'MST, PST…'}]"/></Field>
            </template>
            <div class="row" style="flex-wrap:wrap">
              <Field label="Home" hint="Its time is highlighted; the map's offsets count from it">
                <select class="input" :value="val('home_zone')" @change="set('home_zone', $event.target.value)">
                  <option v-for="z in (state?.zones || []).filter(z => z.cc !== 'MX')" :key="z.id" :value="z.id">{{ zoneName(z.id) }}</option></select></Field>
              <ToggleRow :modelValue="val('highlight_home')" @update:modelValue="v => set('highlight_home', v)" label="Highlight home's time" hint="Its zone time on the map, and in the list"/></div>
            <template v-if="val('show_list')">
            <ToggleRow :modelValue="val('show_utc')" @update:modelValue="v => set('show_utc', v)" label="UTC offset in the legend"/>
            <ToggleRow :modelValue="val('show_places')" @update:modelValue="v => set('show_places', v)" label="Places in the legend" hint="BC, AB, US Pacific…; places with new rules this year are coloured"/>
            </template>
            <details><summary class="faint">Zone names (by abbreviation)</summary>
              <div class="row" style="flex-wrap:wrap; gap:6px; margin-top:8px">
                <span v-for="(n, ab) in state?.defaults?.names || {}" :key="ab" class="row nowrap" style="gap:4px"><span class="mono faint" style="width:40px">{{ ab }}</span>
                  <input class="input" style="width:110px" :placeholder="n" :value="dictVal('names', ab)" @input="dictSet('names', ab, $event.target.value)" maxlength="30"></span></div>
              <div class="faint" style="font-size:12.5px; margin-top:6px">An offset's name comes from its zones' abbreviations (Vancouver becomes MST on Nov 1, so BC is "Mountain"); name an offset yourself under Colours.</div></details>
            <details><summary class="faint">Zones: in the list, and their places</summary>
              <div class="stack" style="gap:4px; margin-top:8px">
                <div v-for="z in state?.zones || []" :key="z.id" class="row nowrap" style="gap:8px">
                  <Toggle v-if="z.cc !== 'MX'" :modelValue="!(val('list_hide') || []).includes(z.id)" @update:modelValue="v => setListed(z.id, v)" title="In the list (it's on the map either way)"/>
                  <span v-else class="faint" style="width:36px" title="Mexico is on the map, never in the list">map</span>
                  <span style="width:170px" class="mono">{{ zoneName(z.id) }}</span><span class="faint" style="width:110px">{{ z.abbr }} {{ offText(z.off) }}</span>
                  <input class="input grow" :placeholder="state.defaults.places[z.id] || '(none)'" :value="dictVal('places', z.id)" @input="dictSet('places', z.id, $event.target.value)" maxlength="30">
                  <button v-if="state.defaults.places[z.id] && tz.places?.[z.id] !== ''" class="btn sm ghost icon-only" title="Leave out of the legend" @click="dictSet('places', z.id, '')"><Icon name="EyeOff" :size="14"/></button>
                  <button v-if="tz.places?.[z.id] === ''" class="btn sm ghost icon-only" title="Show the default again" @click="dictSet('places', z.id, state.defaults.places[z.id])"><Icon name="Eye" :size="14"/></button></div></div></details>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="Palette" :size="16"/><h3>Colours</h3></div>
          <div class="card-body stack">
            <div v-for="o in offsets" :key="o.off" class="row nowrap" style="gap:10px">
              <span class="mono" style="width:80px">{{ offText(o.off) }}</span>
              <ColorPick :modelValue="colorOf(o.off)" @update:modelValue="v => dictSet('colors', String(o.off), v)"/>
              <input v-for="(n, cc) in o.names" :key="cc" class="input grow" style="min-width:0" :title="'Name in ' + ({CA: 'Canada', US: 'the US', MX: 'Mexico'}[cc] || cc)"
                :placeholder="cc + ': ' + n" :value="dictVal('offset_names', cc + ':' + o.off)" @input="dictSet('offset_names', cc + ':' + o.off, $event.target.value)" maxlength="30"></div>
            <div class="faint" style="font-size:12.5px">Colours go by offset, so areas with the same clock time match across the border; the names are per country.</div>
            <Field label="Area brightness"><Slider :modelValue="val('fill')" @update:modelValue="v => set('fill', v)" :min="0.15" :max="1" :step="0.05"/></Field>
            <Field label="Canada, brighter than the US by" hint="1× = the same; the border is drawn as a heavy line either way">
              <Slider :modelValue="val('canada_boost')" @update:modelValue="v => set('canada_boost', v)" :min="0.5" :max="2" :step="0.05" :format="v => v.toFixed(2) + '×'"/></Field>
            <ToggleRow :modelValue="val('highlight_new')" @update:modelValue="v => set('highlight_new', v)" label="Hatch zones with new rules" hint="On the upcoming map: zones whose offset differs from a year before; their times in the list get hatched marks too"/>
          </div></div>

        <div class="card"><div class="card-head"><Icon name="MapPin" :size="16"/><h3>Cities</h3><span class="spacer"></span>
            <button class="btn sm" @click="addCity" :disabled="cities.length >= 60"><Icon name="Plus" :size="14"/>Add</button></div>
          <div class="list-row" v-for="(c, i) in cities" :key="i" style="gap:8px">
            <Toggle :modelValue="c.on" @update:modelValue="v => editCities()[i].on = v"/>
            <input class="input" :value="c.name" @input="editCities()[i].name = $event.target.value" style="width:130px" maxlength="24">
            <input class="input mono" type="number" step="0.01" :value="c.lat" @change="editCities()[i].lat = Number($event.target.value)" style="width:90px" title="Latitude">
            <input class="input mono" type="number" step="0.01" :value="c.lon" @change="editCities()[i].lon = Number($event.target.value)" style="width:100px" title="Longitude">
            <span class="spacer"></span><button class="btn sm ghost icon-only" title="Remove" @click="editCities().splice(i, 1)"><Icon name="X" :size="14"/></button></div>
          <div class="card-body stack">
            <ToggleRow :modelValue="val('map_labels') === 'on'" @update:modelValue="v => set('map_labels', v ? 'on' : 'off')"
              label="Each zone's time in the middle of it" hint="Once for each offset in Canada and once in the US; separate from the cities below"/>
            <div v-if="val('map_labels') === 'on'" class="stack" style="gap:6px">
              <div class="faint" style="font-size:12.5px">What each label says, top to bottom. Times use the map font, every other line the small one under it. Examples are Newfoundland's.</div>
              <div v-for="(ln, i) in lines" :key="i" class="row" style="flex-wrap:wrap; gap:6px; padding:6px 0; border-top:1px solid var(--line, #333)">
                <Toggle :modelValue="ln.on !== false" @update:modelValue="v => setLine(i, 'on', v ? null : false)"/>
                <b style="min-width:150px">{{ LINE_KINDS[ln.kind] || ln.kind }}</b>
                <span class="mono" :style="{color: lineColour(ln) === 'zone' ? 'var(--accent)' : lineColour(ln)}" style="min-width:90px">{{ example(ln) }}</span>
                <span class="spacer"></span>
                <button class="btn sm ghost icon-only" title="Up" :disabled="i === 0" @click="moveLine(i, -1)"><Icon name="ArrowUp" :size="14"/></button>
                <button class="btn sm ghost icon-only" title="Down" :disabled="i === lines.length - 1" @click="moveLine(i, 1)"><Icon name="ArrowDown" :size="14"/></button>
                <button class="btn sm ghost icon-only" title="Remove" @click="editLines().splice(i, 1)"><Icon name="X" :size="14"/></button>
                <div class="row" style="flex-basis:100%; flex-wrap:wrap; gap:6px; padding-left:44px">
                  <Seg v-if="LINE_STYLES[ln.kind]" :modelValue="ln.style || ''" @update:modelValue="v => setLine(i, 'style', v)" :options="LINE_STYLES[ln.kind]"/>
                  <input v-if="ln.kind === 'zone'" class="input mono" style="width:170px" placeholder="Europe/London" :value="ln.zone"
                    :class="{bad: zoneOff(ln.zone || '') == null}" @change="setLine(i, 'zone', $event.target.value.trim())" title="A tz database zone">
                  <input v-if="ln.kind !== 'time'" class="input" style="width:80px" maxlength="12" :placeholder="ln.kind === 'utc' ? 'UTC' : 'Prefix'"
                    :value="ln.kind === 'utc' ? (ln.prefix ?? 'UTC') : ln.prefix" @input="setLine(i, 'prefix', $event.target.value)" title="Text before it (UTC, GMT, LON …)">
                  <input v-if="ln.kind === 'home'" class="input" style="width:70px" maxlength="12" placeholder="home" :value="ln.home"
                    @input="setLine(i, 'home', $event.target.value)" :title="'Home\'s own label (' + zoneName(val('home_zone') || '') + ')'">
                  <input v-if="ln.kind === 'home' || ln.kind === 'zone'" class="input" style="width:70px" maxlength="12" placeholder="same" :value="ln.same"
                    @input="setLine(i, 'same', $event.target.value)" title="A zone at the same time">
                  <Seg :modelValue="lineColour(ln) === 'zone' ? 'zone' : 'custom'" :options="[{value:'zone', label:'Zone colour'}, {value:'custom', label:'Custom'}]"
                    @update:modelValue="v => setLine(i, 'color', v === 'zone' ? 'zone' : '#ffffff')"/>
                  <template v-if="lineColour(ln) !== 'zone'">
                    <ColorPick :modelValue="lineColour(ln)" @update:modelValue="v => setLine(i, 'color', v)"/>
                    <input class="input mono" style="width:84px" maxlength="7" :value="lineColour(ln)" title="#rrggbb"
                      @change="/^#[0-9a-f]{6}$/i.test($event.target.value) && setLine(i, 'color', $event.target.value.toLowerCase())"></template>
                </div></div>
              <div class="row" style="flex-wrap:wrap; gap:6px" v-if="lines.length < 6"><span class="faint">Add</span>
                <button v-for="(n, k) in LINE_KINDS" :key="k" class="btn sm" @click="addLine(k)"><Icon name="Plus" :size="14"/>{{ n }}</button></div>
              <div class="faint" style="font-size:12.5px">Offset from home: +1h, -1h, +4:30 from {{ zoneName(val('home_zone') || '') }}. More lines make each label taller, so small zones may lose some of the map.</div>
            </div>
            <div v-if="val('map_labels') === 'on'" class="row" style="flex-wrap:wrap; gap:6px">
              <template v-for="o in offsets" :key="o.off">
                <button v-for="(n, cc) in o.names" :key="cc" type="button" class="btn sm" :class="{primary: labelShown(cc, o.off)}"
                  :title="labelShown(cc, o.off) ? 'Shown: click to leave it off the map' : 'Left off: click to show it'"
                  @click="toggleLabel(cc, o.off)"><b>{{ cc }}</b>&nbsp;{{ offText(o.off) }} {{ n }}</button></template></div>
            <Field label="Each city's time">
              <Seg :modelValue="{true: 'after', false: 'off'}[val('city_times')] || val('city_times') || 'off'" @update:modelValue="v => set('city_times', v)"
                :options="[{value:'off', label:'None'}, {value:'after', label:'After its name'}, {value:'below', label:'On a new line'}]"/></Field>
            <div class="faint" style="font-size:12.5px">All off by default (names take room on the map). Cities are drawn in this order wherever their names fit; one that would overlap another is left as a dot.</div></div>
        </div>

        <div class="card"><div class="card-head"><Icon name="ShieldCheck" :size="16"/><h3>Rules</h3></div>
          <div class="card-body stack" style="font-size:13px">
            <div>Time zone rules: <b>tzdata {{ state?.rules?.version || '?' }}</b> ({{ state?.rules?.source }}), checked {{ ago(Date.now() / 1000 - (state?.rules?.checked_at || 0)) }}.
              Boundaries: timezone-boundary-builder <b>{{ state?.boundaries || '?' }}</b>.</div>
            <div v-if="state?.rules?.error" class="pill bad">{{ state.rules.error }}</div>
            <div class="faint">The hub checks for a newer tz database (and newer boundaries) every 6 hours; a new version takes effect on the map straight away.</div>
            <div v-for="u in state?.rule_updates || []" :key="u.at" class="stack" style="gap:2px">
              <div><b v-if="u.to">tzdata {{ u.from }} → {{ u.to }}</b><b v-else>Boundaries {{ u.boundaries_from }} → {{ u.boundaries_to }}</b> <span class="faint">{{ when(u.at) }}</span></div>
              <div v-if="u.to && !u.changes.length" class="faint">No US or Canada offsets changed.</div>
              <div v-for="ch in u.changes" :key="ch.zone" class="faint">{{ zoneName(ch.zone) }}: {{ ch.what === 'changed' ? offText(ch.old) + ' → ' + offText(ch.new) + ' from ' + when(ch.at) : ch.what }}</div></div>
          </div></div>
      </div>

      <div class="card sticky"><div class="card-head"><span class="live">LIVE</span><h3>On the wall</h3></div>
        <div class="card-body stack">
          <div class="seg"><button v-for="(s, k) in SHAPES" :key="k" type="button" :class="{on: shape === k}" @click="shape = k"><Icon :name="s.icon" :size="14"/>{{ s.label }}</button></div>
          <div class="row" style="flex-wrap:wrap"><span class="faint">As of</span>
            <select class="input" v-model="asOf" @change="page = 0">
              <option value="now">Now</option>
              <option v-for="(c, i) in state?.changes || []" :key="i" :value="'after:' + i">Just after {{ c.words }}</option>
              <option value="custom">A date…</option></select>
            <input v-if="asOf === 'custom'" class="input" type="datetime-local" v-model="custom"></div>
          <div class="led"><img :src="preview" alt=""></div>
          <div class="row" v-if="pagesAt.length > 1"><span class="faint">Page</span>
            <button v-for="(p, i) in pagesAt" :key="i" class="btn sm" :class="{primary: page === i}" @click="page = i">{{ p }}</button></div>
          <div class="row" style="flex-wrap:wrap">
            <Field label="Warn before a change"><Stepper :modelValue="val('warn_days')" @update:modelValue="v => set('warn_days', v)" :min="0" :max="60" unit=" days"/></Field>
            <Field label="While warning, show"><Seg :modelValue="val('warn_pages')" @update:modelValue="v => set('warn_pages', v)" :options="[{value:'both', label:'Upcoming + now'}, {value:'upcoming', label:'Upcoming only'}]"/></Field>
            <Field label="Each page"><Stepper :modelValue="val('page_sec')" @update:modelValue="v => set('page_sec', v)" :min="3" :max="120" unit=" s"/></Field></div>
          <div class="row">
            <button class="btn sm" @click="play(false)"><Icon name="Play" :size="14"/>{{ asOf === 'now' ? 'Play once' : 'Test once' }}</button>
            <button class="btn sm" @click="play(true)"><Icon name="Pause" :size="14"/>Hold</button>
            <span class="spacer"></span>
            <button class="btn sm" @click="addToRotation(true)" title="Only while a change is coming, and the day after"><Icon name="CalendarClock" :size="14"/>Rotation, near changes</button>
            <button class="btn sm primary" @click="addToRotation(false)"><Icon name="ListPlus" :size="14"/>Add to the rotation</button></div>
          <div class="faint" style="font-size:12.5px">{{ asOf === 'now' ? '' : 'Testing shows the real time of day with the offsets of the chosen moment, labelled ' + val('test_label') + '. ' }}
            {{ used.length ? 'In the rotation: ' + used.join(', ') : 'Not in the rotation yet.' }}
            <span v-if="state?.generated_at"> Map data {{ ago(Date.now() / 1000 - state.generated_at) }}.</span></div>
        </div></div>
    </div>
  </div>`,
};
