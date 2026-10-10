import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, ago, seed, refreshDisplay } from "../core.js";

const TYPES = [
  { id: "warning", label: "Warnings", hint: "Severe weather is happening or about to", colour: "#d70000" },
  { id: "watch", label: "Watches", hint: "Conditions favour severe weather", colour: "#ff7800" },
  { id: "advisory", label: "Advisories", hint: "Less severe but still a nuisance (frost, fog…)", colour: "#ebbe00" },
  { id: "statement", label: "Special weather statements", hint: "Heads-up about unusual weather", colour: "#5f5f78" },
];
const ENTRY = { panel: "weather_alert", when: "weather_alert_active", advance: "cycles", cycles: 1, max_sec: 900 };

export const Alerts = {
  setup() {
    seed(["modules", "weather_alerts"], { enabled: true, theme: "ebs", types: ["warning", "watch", "advisory", "statement"],
      scroll_px_per_sec: 18, refresh_interval_sec: 120 });
    const a = computed(() => store.draft.modules.weather_alerts);
    const data = ref({});
    const load = async () => { try { data.value = await api("/api/alerts"); } catch (e) { /* hub restarting */ } };
    let poll;
    onMounted(() => { load(); poll = setInterval(load, 30000); });
    onBeforeUnmount(() => { clearInterval(poll); clearInterval(anim); });

    // live preview of the selected theme: step through time so scrolling / crawling / typing is visible
    const t = ref(0);
    const anim = setInterval(() => { t.value = (t.value + 0.5) % 40; }, 500);
    const q = computed(() => {
      const s = a.value;
      const p = new URLSearchParams({ flash: s.flash === true ? "on" : s.flash === false ? "off" : "default",
        title: s.title || "", speed: s.scroll_px_per_sec || 18, real: data.value.alerts?.length ? 1 : 0 });
      return p.toString();
    });
    const thumb = (id) => `/api/alerts/preview/${id}.png?at=4&scale=1&${q.value}`;
    const live = computed(() => `/api/alerts/preview/${a.value.theme || "ebs"}.png?at=${t.value}&scale=2&${q.value}`);

    const types = computed(() => a.value.types || TYPES.map((x) => x.id));
    const toggleType = (id, on) => {
      const s = new Set(types.value);
      on ? s.add(id) : s.delete(id);
      a.value.types = TYPES.map((x) => x.id).filter((x) => s.has(x));
    };
    const areas = computed(() => Object.entries(store.draft.modules.weather_maps?.areas || {}));
    const areaId = computed({
      get: () => a.value.area_id || (store.draft.modules.weather_maps?.areas?.core_metro ? "core_metro" : ""),
      set: (v) => { a.value.area_id = v; },
    });

    const right = computed(() => (store.draft.modules.layout_cycle.right = store.draft.modules.layout_cycle.right || []));
    const inRotation = computed(() => ["left", "right"].some((s) => (store.draft.modules.layout_cycle[s] || []).some((e) => e.panel === "weather_alert")));
    const entry = computed(() => ["left", "right"].map((s) => (store.draft.modules.layout_cycle[s] || []).find((e) => e.panel === "weather_alert")).find(Boolean));
    const addToRotation = () => { right.value.unshift({ ...ENTRY }); toast("Added to the start of the right-half rotation — save to apply"); };

    const show = async (test) => {
      try {
        await api("/api/display/override", { json: { side: "right", panel: "weather_alert", duration_sec: test ? 90 : 300, ...(test ? { test: true } : {}) } });
        toast(test ? "Showing a test alert on the wall for 90 s" : "Showing the current alerts on the wall");
        setTimeout(refreshDisplay, 500);
      } catch (e) { fail(e); }
    };
    const stop = async () => { try { await api("/api/display/override/clear", { json: { side: "right" } }); toast("Back to the rotation"); } catch (e) { fail(e); } };
    const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "");
    const typeColour = (k) => TYPES.find((x) => x.id === k)?.colour || "#5f5f78";
    return { a, data, thumb, live, TYPES, types, toggleType, areas, areaId, inRotation, entry, addToRotation, show, stop, when, typeColour, ago };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Weather alerts</h1>
      <p>When Environment Canada has an alert in effect for your area, the wall switches to a full-screen emergency-broadcast style bulletin at the start of the rotation, before the weather maps.</p></div>
      <Toggle v-model="a.enabled"/></div>

    <div class="two-col">
      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">PREVIEW</span><h3>{{ data.themes?.[a.theme || 'ebs']?.label }}</h3>
          <span class="pill" v-if="!data.alerts?.length">sample alert</span></div>
          <div class="card-body"><div class="led-frame"><img :src="live" style="width:100%; image-rendering:pixelated; display:block" alt=""></div>
            <div class="row" style="margin-top:12px">
              <button class="btn" @click="show(true)"><Icon name="FlaskConical" :size="15"/>Show a test alert on the wall</button>
              <button class="btn" v-if="data.alerts?.length" @click="show(false)"><Icon name="Siren" :size="15"/>Show current alerts</button>
              <button class="btn ghost" @click="stop"><Icon name="Undo2" :size="15"/>Back to rotation</button>
            </div></div></div>

        <div class="card"><div class="card-head"><Icon name="Siren" :size="16" style="color:var(--bad)"/><h3>In effect now</h3>
          <span class="faint" style="font-size:12px" v-if="data.updated_utc">{{ data.area }} · checked {{ ago(Date.now() / 1000 - data.updated_utc) }}</span></div>
          <div class="list" v-if="data.alerts?.length">
            <div class="list-row" v-for="(x, i) in data.alerts" :key="i" style="align-items:flex-start">
              <span class="pill" :style="{background: typeColour(x.type) + '33', color: typeColour(x.type), borderColor: typeColour(x.type) + '66'}">{{ x.type }}</span>
              <div style="flex:1; min-width:0"><b style="text-transform:capitalize">{{ x.name }}</b>
                <div class="faint" style="font-size:12px">{{ (x.regions || []).join(' · ') }}</div>
                <div class="faint" style="font-size:12px">issued {{ when(x.published) }}<template v-if="x.expires"> · until {{ when(x.expires) }}</template></div></div>
            </div></div>
          <div class="card-body faint" v-else><Icon name="CircleCheck" :size="15" style="color:var(--ok); vertical-align:-3px"/> No alerts for {{ data.area || 'this area' }} right now — the alert screen stays out of the rotation.</div>
        </div>
      </div>

      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="Palette" :size="15"/><h3>Theme</h3></div>
          <div class="card-body"><div class="theme-grid">
            <button v-for="(th, id) in data.themes" :key="id" class="theme-card" :class="{on: (a.theme || 'ebs') === id}" @click="a.theme = id">
              <img :src="thumb(id)" alt=""><span>{{ th.label }}</span>
              <small class="faint">{{ {scroll: 'scrolling text', crawl: 'big crawl', type: 'typewriter'}[th.mode] }}{{ th.flash ? ' · flashing header' : '' }}</small>
            </button></div></div></div>

        <div class="card"><div class="card-head"><Icon name="SlidersHorizontal" :size="15"/><h3>Look & timing</h3></div><div class="card-body stack">
          <Field label="Header text" :hint="'Leave empty for the theme\\'s own: ' + (data.themes?.[a.theme || 'ebs']?.title || '<TYPE> IN EFFECT')">
            <input class="input" v-model.trim="a.title" placeholder="Theme default"></Field>
          <Field label="Flashing header"><Seg :modelValue="a.flash === true ? 'on' : a.flash === false ? 'off' : 'default'"
            @update:modelValue="v => v === 'default' ? delete a.flash : a.flash = (v === 'on')"
            :options="[{value:'default', label:'Theme default'}, {value:'on', label:'On'}, {value:'off', label:'Off'}]"/></Field>
          <Field label="Text speed" hint="Scrolling speed; the crawl and typewriter themes scale from this">
            <Slider v-model="a.scroll_px_per_sec" :min="6" :max="60" :step="1" unit=" px/s"/></Field>
        </div></div>

        <div class="card"><div class="card-head"><Icon name="Filter" :size="15"/><h3>Which alerts</h3></div><div class="card-body stack">
          <Field label="Area" hint="Alerts for any forecast region touching this map area">
            <select class="input" v-model="areaId"><option v-for="[id, ar] in areas" :key="id" :value="id">{{ ar.label }}</option></select></Field>
          <ToggleRow v-for="x in TYPES" :key="x.id" :modelValue="types.includes(x.id)" @update:modelValue="v => toggleType(x.id, v)" :label="x.label" :hint="x.hint"/>
        </div></div>

        <div class="card"><div class="card-head"><Icon name="Repeat" :size="15"/><h3>In the rotation</h3></div><div class="card-body stack">
          <template v-if="entry">
            <div class="muted" style="font-size:13px"><Icon name="CircleCheck" :size="14" style="color:var(--ok); vertical-align:-2px"/> Shown at the start of the rotation whenever an alert is in effect.</div>
            <Field label="Passes through all alerts per visit"><Stepper v-model="entry.cycles" :min="1" :max="5" unit="×"/></Field>
          </template>
          <template v-else>
            <div class="callout"><Icon name="Info" :size="15"/>The alert screen isn't in the rotation yet.</div>
            <div><button class="btn primary" @click="addToRotation"><Icon name="Plus" :size="15"/>Add before the weather maps</button></div>
          </template>
        </div></div>
      </div>
    </div>
  </div>`,
};
