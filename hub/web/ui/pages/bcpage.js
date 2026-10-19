// Shared page for the BC alert screens (Alert Ready, DriveBC, earthquakes, tsunami, wildfire).
// Each page passes its own config: title, intro, settings fields and how to show its live items.
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast, ago, seed, clone, go } from "../core.js";

export function makeBCPage(P) {
  return {
    setup() {
      const tab = ref((P.tabs || [])[0]?.id || "screen");
      seed(["modules", P.section], { enabled: true });
      const s = computed(() => store.draft.modules[P.section]);
      const st = ref({ settings: {}, defaults: {}, themes: {}, samples: {}, data: { items: [] }, job: null });
      const load = async () => { try { st.value = await api(`/api/bc/${P.src}`); } catch (e) { /* hub restarting */ } };
      const val = (key) => (s.value[key] !== undefined ? s.value[key] : st.value.defaults?.[key]);
      const set = (key, v) => { s.value[key] = v; };
      const toggleIn = (key, item) => {
        const cur = clone(val(key) || []);
        const i = cur.indexOf(item);
        if (i >= 0) cur.splice(i, 1); else cur.push(item);
        set(key, cur);
      };

      // preview: a sample (or the real items) in the chosen theme, stepping through time
      const view = ref(null);          // hovered theme
      const pinned = ref(null);        // theme chosen with its Full / Half buttons (previewed until another is chosen)
      const theme = computed(() => view.value || pinned.value || val("theme") || Object.keys(st.value.themes)[0] || "");
      const realTheme = computed(() => theme.value === "tour" ? Object.keys(st.value.themes)[0] || "" : theme.value);
      const sample = ref("");
      const real = ref(false);
      const t = ref(1);
      const playing = ref(true);
      let tick, poll;
      onMounted(() => {
        load();
        poll = setInterval(() => { load(); loadFull(); }, 8000);   // (pages change when the job finds a sample's camera)
        tick = setInterval(() => {
          if (!playing.value) return;
          t.value = (t.value + 0.5) % (half.value ? Math.max(4, halfTl.value.total || 30) : fullOn.value ? Math.max(4, fullTl.value.total) : 30);
        }, 500);
      });
      onBeforeUnmount(() => { clearInterval(tick); clearInterval(poll); });
      const items = computed(() => st.value.data?.items || []);
      const smp = computed(() => sample.value || Object.keys(st.value.samples)[0] || "");
      const look = computed(() => encodeURIComponent(JSON.stringify(Object.fromEntries((P.fields || []).map((f) => [f.key, val(f.key)])))));
      const half = ref(false);
      // the half-width version's pages (summary, text, map…) and where the preview clock is in them
      const halfTl = ref({ pages: [], total: 0 });
      const loadHalf = async () => {
        try { halfTl.value = await api(`/api/bc/${P.src}/half_pages?theme=${realTheme.value}&sample=${smp.value}`); } catch (e) { /* hub restarting */ }
      };
      const halfStarts = computed(() => { let a = 0; return halfTl.value.pages.map((p) => { const s0 = a; a += p.hold; return s0; }); });
      const halfPage = computed(() => { let i = 0; halfStarts.value.forEach((s0, j) => { if (t.value >= s0) i = j; }); return i; });
      const halfAt = computed(() => Math.max(0.5, t.value - (halfStarts.value[halfPage.value] || 0) + 0.5));
      const jumpHalf = (i) => { t.value = (halfStarts.value[i] || 0) + 0.05; };
      // the full-wall pages of a sample's first showing (the theme, then e.g. the nearest camera), when the screen has them
      const fullTl = ref({ pages: [], total: 0 });
      const loadFull = async () => {
        if (!P.fullPages) return;
        try { fullTl.value = await api(`/api/bc/${P.src}/full_pages?theme=${realTheme.value}&sample=${smp.value}`); } catch (e) { /* hub restarting */ }
      };
      const fullOn = computed(() => !!P.fullPages && !half.value && !real.value && fullTl.value.pages.length > 1);
      const fullStarts = computed(() => { let a = 0; return fullTl.value.pages.map((p) => { const s0 = a; a += p.hold; return s0; }); });
      const fullPage = computed(() => { let i = 0; fullStarts.value.forEach((s0, j) => { if (t.value >= s0) i = j; }); return i; });
      const fullAt = computed(() => Math.max(0.5, t.value - (fullStarts.value[fullPage.value] || 0) + 0.5));
      const jumpFull = (i) => { t.value = (fullStarts.value[i] || 0) + 0.05; };
      watch([realTheme, smp], loadFull, { immediate: true });
      const preview = computed(() => half.value
        ? `/api/bc/${P.src}/half.png?theme=${realTheme.value}&sample=${smp.value}&page=${halfPage.value}&at=${halfAt.value.toFixed(1)}&scale=2`
        : fullOn.value
          ? `/api/bc/${P.src}/preview.png?theme=${realTheme.value}&sample=${smp.value}&page=${fullPage.value}&at=${fullAt.value.toFixed(1)}&scale=2&s=${look.value}`
          : `/api/bc/${P.src}/preview.png?theme=${theme.value}&sample=${smp.value}&real=${real.value ? 1 : 0}&at=${t.value.toFixed(1)}&scale=2&s=${look.value}`);
      const pick = (id) => { set("theme", id); view.value = null; pinned.value = null; };
      const show = (id, h) => { pinned.value = id; view.value = null; half.value = h; t.value = h ? 0 : 1; };
      watch(view, (v, old) => { if (fullOn.value && v && v !== old) t.value = 1; });   // hovering a theme starts at its first page
      watch([half, realTheme, smp], () => { if (half.value) loadHalf(); });
      const testWall = async (opts = {}) => {
        try {
          await api(`/api/bc/${P.src}/test`, { json: { theme: opts.theme ?? theme.value, sample: smp.value, real: opts.real ?? real.value, half: half.value } });
          toast(opts.real ?? real.value ? "Playing what's in effect on the wall once" : `Playing "${st.value.samples[smp.value]}" on the wall once`);
        } catch (e) { fail(e); }
      };
      const stop = async () => { try { await api("/api/display/override/clear", { json: { side: "right" } }); toast("Back to the rotation"); } catch (e) { fail(e); } };
      const runNow = async () => { try { await api(`/api/bc/${P.src}/run`, { method: "POST" }); toast("Fetching now"); setTimeout(load, 4000); } catch (e) { fail(e); } };

      // rotation: is there an entry for this screen?
      const lc = computed(() => store.draft.modules.layout_cycle || {});
      const inRotation = computed(() => ["left", "right"].some((side) => (lc.value[side] || []).some((e) => e.panel === P.panel)));
      const halfInRotation = computed(() => (lc.value.right || []).some((e) => e.panel === P.panel + "_half"));
      const addToRotation = () => {
        const right = store.draft.modules.layout_cycle.right = store.draft.modules.layout_cycle.right || [];
        if (!inRotation.value) {
          const at = right.findIndex((e) => e.panel === "weather_alert");
          right.splice(at + 1, 0, { panel: P.panel, rules: [{ if: P.rule, phase: "full" }], priority: "next", advance: "cycles", cycles: 1, max_sec: 600 });
        }
        if (!halfInRotation.value) {
          const m = right.findIndex((e) => e.panel?.startsWith("map"));
          right.splice(m >= 0 ? m : right.length, 0, { panel: P.panel + "_half", rules: [{ if: P.rule, phase: "half" }], advance: "cycles", cycles: 1, max_sec: 300 });
        }
        toast("Added to the rotation — Save to keep it");
      };
      const health = computed(() => st.value.job?.health || "never");
      const openTrafficMap = () => { store.trafficTab = "map"; go("webcams"); };
      return { openTrafficMap, P, s, st, val, set, toggleIn, theme, pick, view, sample, smp, real, t, playing, preview, testWall, stop, runNow,
        items, inRotation, halfInRotation, addToRotation, health, ago, half, halfPage, pinned, show, realTheme, halfTl, jumpHalf,
        tab, fullTl, fullOn, fullPage, jumpFull };
    },
    template: `<div class="page">
      <div class="page-head"><div class="grow"><h1>{{ P.title }}</h1><p>{{ P.intro }}</p></div>
        <Seg v-if="P.tabs" v-model="tab" :options="P.tabs.map(x => ({value: x.id, label: x.label}))"/>
        <Toggle :modelValue="val('enabled') !== false" @update:modelValue="v => set('enabled', v)"/></div>

      <div v-if="tab === 'cameras'" class="card"><div class="card-body row" style="gap:12px">
        <Icon name="MapPin" :size="18"/><span class="grow">The DriveBC camera map has moved to <b>Webcams → Traffic cams</b>, where any camera
          can also be added to the rotation. It still shows which camera each road event uses.</span>
        <button class="btn primary" @click="openTrafficMap"><Icon name="Video" :size="14"/>Open the camera map</button></div></div>
      <div class="two-col" v-else>
        <div class="stack sticky">
          <div class="card"><div class="card-head"><span class="live">PREVIEW</span>
              <h3>{{ st.themes[realTheme]?.label }}{{ half ? ' (half width)' : '' }} · {{ real && !half ? 'what is in effect' : st.samples[smp] }}</h3>
              <Seg :modelValue="half" @update:modelValue="v => { half = v; t = v ? 0 : 1; }" :options="[{value: false, label: 'Full wall'}, {value: true, label: 'Half width'}]"/>
              <button class="btn sm ghost icon-only" @click="playing = !playing" :title="playing ? 'Pause' : 'Play'"><Icon :name="playing ? 'Pause' : 'Play'" :size="14"/></button></div>
            <div class="card-body stack">
              <div style="background:#000; border-radius:8px; overflow:hidden"><img :src="preview" style="width:100%; image-rendering:pixelated; display:block" alt=""></div>
              <div class="scrub" v-if="half && halfTl.pages.length">
                <div class="scrub-track">
                  <button v-for="(p, i) in halfTl.pages" :key="i" class="scrub-seg" :class="{on: halfPage === i}" :style="{flex: p.hold}" @click="jumpHalf(i)"
                    :title="p.name + ' · holds ' + p.hold + ' s'">{{ p.name }}</button></div>
                <span class="mono faint" style="font-size:12px; width:70px; text-align:right">{{ t.toFixed(0) }} / {{ Math.round(halfTl.total) }} s</span>
              </div>
              <div class="scrub" v-if="fullOn">
                <div class="scrub-track">
                  <button v-for="(p, i) in fullTl.pages" :key="i" class="scrub-seg" :class="{on: fullPage === i}" :style="{flex: p.hold}" @click="jumpFull(i)"
                    :title="p.name + ' · holds ' + p.hold + ' s'">{{ i ? p.name : 'Main · ' + p.name }}</button></div>
                <span class="mono faint" style="font-size:12px; width:70px; text-align:right">{{ t.toFixed(0) }} / {{ Math.round(fullTl.total) }} s</span>
              </div>
              <div v-if="fullOn" class="faint" style="font-size:12px">A new event's first showing: the theme, then the extra pages. Later showings are the theme alone.</div>
              <div v-if="half" class="faint" style="font-size:12px">The right half of the wall, under the clock bar — what an alert turns into once it has had its full-wall showings.</div>
              <div class="row" style="flex-wrap:wrap; gap:6px">
                <button v-for="(lab, id) in st.samples" :key="id" class="btn sm" :class="{primary: !real && smp === id}" @click="sample = id; real = false; t = 1">{{ lab }}</button>
                <button class="btn sm" :class="{primary: real}" :disabled="!items.length" @click="real = true; t = 1" :title="items.length ? '' : 'Nothing in effect right now'">
                  <Icon name="Radio" :size="13"/>In effect ({{ items.length }})</button>
              </div>
              <div class="row">
                <button class="btn primary" @click="testWall()"><Icon name="MonitorPlay" :size="15"/>Test on the wall</button>
                <button class="btn ghost" @click="stop"><Icon name="Undo2" :size="15"/>Back to rotation</button>
                <span class="faint mono" style="font-size:12px">+{{ t.toFixed(1) }} s</span>
              </div>
            </div></div>

          <div class="card"><div class="card-head"><Icon name="Palette" :size="15"/><h3>Theme</h3>
              <button class="btn sm" @click="testWall({ theme: 'tour' })"><Icon name="MonitorPlay" :size="13"/>Tour all on the wall</button></div>
            <div class="list">
              <div class="list-row" v-for="(th, id) in st.themes" :key="id" style="cursor:pointer" :style="theme === id ? {background: 'var(--accent-soft, rgba(255,255,255,.06))'} : {}"
                   @click="pick(id)" @mouseenter="view = id" @mouseleave="view = null">
                <span class="dot" :class="(val('theme')) === id ? 'ok' : ''"></span>
                <span style="flex:1"><b>{{ th.label }}</b> <span class="faint" style="font-size:12px">— {{ th.hint }}</span></span>
                <span class="pill" v-if="val('theme') === id">in use</span>
                <div class="seg" @click.stop style="flex:none; padding:2px">
                  <button :class="{on: pinned === id && !half}" @click="show(id, false)" style="height:22px; padding:0 9px; font-size:12px" title="Preview the full-wall version">Full</button>
                  <button :class="{on: pinned === id && half}" @click="show(id, true)" style="height:22px; padding:0 9px; font-size:12px" title="Preview the half-width version">Half</button></div>
              </div>
              <div class="list-row" style="cursor:pointer" @click="pick('tour')"><span class="dot" :class="val('theme') === 'tour' ? 'ok' : ''"></span>
                <span style="flex:1"><b>Rotate themes</b> <span class="faint" style="font-size:12px">— a different theme each time the screen comes round</span></span></div>
            </div>
            <div class="card-body faint" style="font-size:12.5px">Hover a theme to preview it, or use its Full / Half buttons to keep that version in the preview; click a theme to use it
              (Save to keep). Each theme has its own half-width version too. Samples are labelled SAMPLE on the wall.</div>
          </div>
        </div>

        <div class="stack">
          <div class="card"><div class="card-head"><Icon name="Radio" :size="15"/><h3>Feed</h3>
              <button class="btn sm" @click="runNow"><Icon name="RefreshCw" :size="13"/>Fetch now</button></div>
            <div class="list">
              <div class="list-row" v-for="src in P.sources" :key="src"><span class="dot" :class="health === 'ok' ? 'ok' : health === 'running' ? '' : 'bad'"></span><span style="flex:1">{{ src }}</span></div>
              <div class="list-row" v-if="st.listener"><span class="dot" :class="st.listener.connected ? 'ok' : 'bad'"></span>
                <span style="flex:1">Live stream {{ st.listener.connected || 'reconnecting' }}</span>
                <span class="faint" style="font-size:12px">heartbeat {{ ago(st.listener.last_heartbeat ? Date.now() / 1000 - st.listener.last_heartbeat : null) }} · {{ st.listener.received }} received</span></div>
              <div class="list-row"><span class="faint" style="font-size:12.5px; flex:1">{{ st.job?.message || 'not run yet' }}</span>
                <span class="faint" style="font-size:12px">{{ st.job?.last_success ? ago(Date.now() / 1000 - st.job.last_success) : '' }}</span></div>
            </div>
            <div class="card-body" v-if="st.job?.error"><div class="callout" style="font-size:12px; white-space:pre-wrap">{{ st.job.error.slice(-400) }}</div></div>
          </div>

          <div class="card"><div class="card-head"><Icon name="Siren" :size="15"/><h3>On the wall now</h3><span class="pill">{{ items.length }}</span></div>
            <div class="list" v-if="items.length">
              <div class="list-row" v-for="it in items" :key="it.id">
                <span class="pill" :style="P.chip(it).style">{{ P.chip(it).text }}</span>
                <span style="flex:1">{{ P.line(it) }}</span>
              </div></div>
            <div class="card-body faint" v-else>{{ P.empty }}</div>
          </div>

          <div class="card"><div class="card-head"><Icon name="SlidersHorizontal" :size="15"/><h3>Settings</h3></div><div class="card-body stack">
            <template v-for="f in P.fields" :key="f.key">
              <ToggleRow v-if="f.type === 'toggle'" :modelValue="!!val(f.key)" @update:modelValue="v => set(f.key, v)" :label="f.label" :hint="f.hint"/>
              <Field v-else-if="f.type === 'seg'" :label="f.label" :hint="f.hint"><Seg :modelValue="val(f.key)" @update:modelValue="v => set(f.key, v)" :options="f.options"/></Field>
              <Field v-else-if="f.type === 'slider'" :label="f.label" :hint="f.hint"><Slider :modelValue="val(f.key)" @update:modelValue="v => set(f.key, v)" :min="f.min" :max="f.max" :step="f.step" :unit="f.unit || ''"/></Field>
              <Field v-else-if="f.type === 'multi'" :label="f.label" :hint="f.hint"><div class="chips">
                <span v-for="(lab, id) in (f.optionsFrom ? st[f.optionsFrom] : f.options)" :key="id" class="chip" :class="{off: !(val(f.key) || []).includes(id)}" style="cursor:pointer; padding-right:11px" @click="toggleIn(f.key, id)">{{ lab }}</span></div></Field>
            </template>
            <Field label="Each item stays on the wall for at least"><Slider :modelValue="val('dwell_sec')" @update:modelValue="v => set('dwell_sec', v)" :min="10" :max="90" :step="5" unit=" s"/></Field>
            <div class="faint" style="font-size:12.5px">{{ P.note }}</div>
          </div></div>

          <div class="card"><div class="card-head"><Icon name="Repeat" :size="15"/><h3>Rotation</h3></div><div class="card-body stack">
            <div v-if="inRotation && halfInRotation" class="row"><span class="dot ok"></span>In the rotation: a new alert jumps the queue full-wall, comes round full-wall
              while it's fresh, then plays in the half-width version on the right (settings on the Rotation page).</div>
            <div v-else-if="inRotation" class="row"><span class="dot ok"></span><span style="flex:1">Full-wall entry in the rotation; the half-width version isn't yet.</span>
              <button class="btn sm primary" @click="addToRotation"><Icon name="Plus" :size="13"/>Add the half-width version</button></div>
            <div v-else class="row"><span class="dot bad"></span><span style="flex:1">Not in the rotation yet — it will only appear for takeovers.</span>
              <button class="btn sm primary" @click="addToRotation"><Icon name="Plus" :size="13"/>Add to rotation</button></div>
          </div></div>
        </div>
      </div>
    </div>`,
  };
}
