import { ref, computed, onMounted, onBeforeUnmount, watch } from "vue";
import { store, api, fail, toast, seed } from "../core.js";

const SCRIPTS = [{ value: "all", label: "All" }, { value: "latin", label: "English" }, { value: "japanese", label: "Japanese" }];
const STATUSES = [{ value: "all", label: "All" }, { value: "unreviewed", label: "To review" }, { value: "approved", label: "Approved" },
  { value: "dropped", label: "Dropped" }, { value: "candidate", label: "New candidates" }];
const COLOURS = [{ value: "ffffff", label: "White" }, { value: "ffc040", label: "Amber" }, { value: "50ff80", label: "Green" },
  { value: "ff5050", label: "Red" }, { value: "60c0ff", label: "Blue" }];
const SORTS = [{ value: "size", label: "Size" }, { value: "family", label: "Family" }, { value: "name", label: "Name" }];

export const Fonts = {
  setup() {
    const audit = seed(["hub", "fonts"], { status: {} });
    if (!audit.status) audit.status = {};
    const st = computed(() => store.draft.hub.fonts.status);
    const all = ref([]);
    const samples = ref({ latin: "", jp: "" });
    const latin = ref(""), jp = ref("");
    const fg = ref("ffffff");
    const zoom = ref(2);
    const script = ref("all"), status = ref("unreviewed"), sort = ref("size"), q = ref(""), usedOnly = ref(false);
    const minH = ref(0), maxH = ref(40);
    const compare = ref([]);
    const review = ref(-1);              // index into `shown` while reviewing one by one
    const mirror = ref(true);
    const load = async () => {
      try {
        const d = await api("/api/fonts");
        all.value = d.fonts;
        samples.value = d.samples;
        if (!latin.value) latin.value = d.samples.latin;
        if (!jp.value) jp.value = d.samples.jp;
      } catch (e) { fail(e); }
    };
    onMounted(load);

    const verdict = (f) => st.value[f.path] || (f.candidate ? "candidate" : "unreviewed");
    const shown = computed(() => {
      const needle = q.value.trim().toLowerCase();
      const out = all.value.filter((f) =>
        (script.value === "all" || f.script === script.value) &&
        (status.value === "all" || verdict(f) === status.value) &&
        (!usedOnly.value || f.used_by.length) &&
        f.height >= minH.value && f.height <= maxH.value &&
        (!needle || (f.path + " " + f.family + " " + f.origin + " " + f.tags.join(" ")).toLowerCase().includes(needle)));
      const key = { size: (f) => [f.height, f.family, f.name], family: (f) => [f.family, f.height, f.name], name: (f) => [f.name] }[sort.value];
      return out.sort((a, b) => { const ka = key(a), kb = key(b); for (let i = 0; i < ka.length; i++) { if (ka[i] < kb[i]) return -1; if (ka[i] > kb[i]) return 1; } return 0; });
    });
    const counts = computed(() => {
      const c = { approved: 0, dropped: 0, unreviewed: 0, candidate: 0 };
      for (const f of all.value) c[verdict(f)]++;
      return c;
    });
    const sampleFor = (f) => (f.script === "japanese" ? { text: f.ascii > 40 ? latin.value : "", jp: jp.value } : { text: latin.value, jp: "" });
    const img = (f, z) => {
      const s = sampleFor(f);
      return `/api/fonts/preview.png?path=${encodeURIComponent(f.path)}&text=${encodeURIComponent(s.text)}&jp=${encodeURIComponent(s.jp)}&scale=1&fg=${fg.value}`;
    };
    const set = (f, v) => {
      if (verdict(f) === v || v === "unreviewed") delete st.value[f.path];
      else st.value[f.path] = v;
    };
    const wall = async (paths) => {
      try {
        const one = paths.length === 1 ? all.value.find((f) => f.path === paths[0]) : null;
        const s = one ? sampleFor(one) : { text: latin.value, jp: jp.value };
        const r = await api("/api/fonts/wall", { json: { fonts: paths, text: s.text, jp: s.jp } });
        if (r && r.error) throw new Error(r.error);
      } catch (e) { fail(e); }
    };
    const back = async () => { try { await api("/api/display/override/clear", { json: { side: "right" } }); toast("Back to the rotation"); } catch (e) { fail(e); } };
    const toggleCompare = (f) => {
      const i = compare.value.indexOf(f.path);
      if (i >= 0) compare.value.splice(i, 1); else if (compare.value.length < 12) compare.value.push(f.path); else toast("12 fonts at most");
    };

    // one-by-one review: big preview, the font on the wall, A / D / arrow keys
    const current = computed(() => (review.value >= 0 ? shown.value[review.value] : null));
    const startReview = () => { if (!shown.value.length) return toast("Nothing matches the filters"); review.value = 0; };
    const step = (d) => { if (review.value < 0) return; review.value = Math.max(0, Math.min(shown.value.length - 1, review.value + d)); };
    const judge = (v) => {
      const f = current.value;
      if (!f) return;
      const before = shown.value.length;
      set(f, v);
      // the judged font may drop out of a status filter: stay on the same index then, else move on
      if (shown.value.length === before) step(1);
      if (review.value >= shown.value.length) review.value = shown.value.length - 1;
    };
    watch(current, (f) => { if (f && mirror.value) wall([f.path]); });
    const onKey = (e) => {
      if (review.value < 0 || ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
      if (e.key === "a") judge("approved");
      else if (e.key === "d") judge("dropped");
      else if (e.key === "ArrowRight" || e.key === " ") { e.preventDefault(); step(1); }
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "Escape") review.value = -1;
    };
    onMounted(() => window.addEventListener("keydown", onKey));
    onBeforeUnmount(() => window.removeEventListener("keydown", onKey));

    const statusClass = (f) => ({ approved: "ok", dropped: "bad", candidate: "warn" }[verdict(f)] || "");
    return { all, shown, counts, latin, jp, fg, zoom, script, status, sort, q, usedOnly, minH, maxH, compare, review, mirror, current,
      SCRIPTS, STATUSES, COLOURS, SORTS, verdict, img, set, wall, back, toggleCompare, startReview, step, judge, statusClass, samples };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Fonts</h1>
      <p>Every bitmap font on the wall, plus new candidates nothing uses yet. Preview them with your own text, show any of them on the wall, and mark each one <b>approved</b> or <b>dropped</b>. Save keeps the verdicts; the weather-alert and Japan EEW themes get redone with approved fonts only.</p></div>
      <div class="row" style="gap:6px; flex-wrap:wrap">
        <span class="pill" style="color:var(--ok, #4ade80)">{{ counts.approved }} approved</span>
        <span class="pill" style="color:var(--bad, #f87171)">{{ counts.dropped }} dropped</span>
        <span class="pill">{{ counts.unreviewed }} to review</span>
        <span class="pill" style="color:var(--warn, #fbbf24)">{{ counts.candidate }} candidates</span>
      </div></div>

    <div class="card"><div class="card-head"><Icon name="Type" :size="15"/><h3>Sample text &amp; filters</h3></div><div class="card-body stack">
      <Field label="English sample"><input class="input" v-model.lazy="latin" @keydown.enter="$event.target.blur()"></Field>
      <Field label="Japanese sample" hint="Shown for fonts with kana / kanji"><input class="input" v-model.lazy="jp" @keydown.enter="$event.target.blur()"></Field>
      <div class="row" style="flex-wrap:wrap; gap:14px">
        <Field label="Script"><Seg v-model="script" :options="SCRIPTS"/></Field>
        <Field label="Verdict"><Seg v-model="status" :options="STATUSES"/></Field>
        <Field label="Sort by"><Seg v-model="sort" :options="SORTS"/></Field>
      </div>
      <div class="row" style="flex-wrap:wrap; gap:14px; align-items:flex-end">
        <Field label="Colour"><Seg v-model="fg" :options="COLOURS"/></Field>
        <Field label="Zoom"><Seg v-model="zoom" :options="[{value:1,label:'1×'},{value:2,label:'2×'},{value:3,label:'3×'}]"/></Field>
        <Field label="Height (px)"><div class="row" style="gap:6px"><input class="input" type="number" v-model.number="minH" style="width:70px"> – <input class="input" type="number" v-model.number="maxH" style="width:70px"></div></Field>
        <Field label="Search"><input class="input" v-model="q" placeholder="name, family, tag…" style="width:200px"></Field>
        <ToggleRow v-model="usedOnly" label="Only fonts in use"/>
      </div>
      <div class="row" style="gap:8px; flex-wrap:wrap">
        <button class="btn primary" @click="startReview"><Icon name="ListChecks" :size="15"/>Review these {{ shown.length }} one by one</button>
        <button class="btn ghost" @click="back"><Icon name="Undo2" :size="15"/>Back to rotation</button>
      </div>
    </div></div>

    <div class="card" v-if="current" style="border-color:var(--accent, #7c83ff)">
      <div class="card-head"><Icon name="Eye" :size="15"/><h3>{{ current.family }} <span class="faint mono" style="font-size:12px">{{ current.path }}</span></h3>
        <span class="faint" style="font-size:12px">{{ review + 1 }} / {{ shown.length }}</span>
        <label class="row" style="gap:6px; font-size:12.5px"><input type="checkbox" v-model="mirror">on the wall</label>
        <button class="btn sm ghost" @click="review = -1">Close</button></div>
      <div class="card-body stack">
        <div style="background:#000; border-radius:8px; padding:8px; overflow:auto"><img :src="img(current)" :style="{width: 448 * 3 + 'px', maxWidth: '100%', imageRendering: 'pixelated', display: 'block'}" alt=""></div>
        <div class="faint" style="font-size:12.5px">{{ current.height }} px · {{ current.glyphs }} glyphs<template v-if="current.kanji"> · {{ current.kanji }} kanji</template> · {{ current.origin }} · {{ current.licence }}
          <template v-if="current.used_by.length"><br>Used by: {{ current.used_by.join(' · ') }}</template></div>
        <div class="row" style="gap:8px">
          <button class="btn" @click="step(-1)"><Icon name="ChevronLeft" :size="15"/>Prev</button>
          <button class="btn" :class="{primary: verdict(current) === 'approved'}" @click="judge('approved')"><Icon name="Check" :size="15"/>Approve <span class="faint">A</span></button>
          <button class="btn" :class="{danger: verdict(current) === 'dropped'}" @click="judge('dropped')"><Icon name="X" :size="15"/>Drop <span class="faint">D</span></button>
          <button class="btn" @click="step(1)">Skip<Icon name="ChevronRight" :size="15"/></button>
          <span class="pill">{{ verdict(current) }}</span>
        </div>
      </div></div>

    <div class="card" v-if="compare.length"><div class="card-head"><Icon name="Columns3" :size="15"/><h3>Compare on the wall</h3><span class="faint" style="font-size:12px">{{ compare.length }} / 12, stacked</span>
        <button class="btn sm primary" @click="wall(compare)"><Icon name="MonitorPlay" :size="13"/>Show stacked</button>
        <button class="btn sm ghost" @click="compare.splice(0)">Clear</button></div>
      <div class="card-body faint mono" style="font-size:11.5px">{{ compare.join('  ·  ') }}</div></div>

    <div class="stack">
      <div class="card" v-for="f in shown" :key="f.path">
        <div class="card-head" style="flex-wrap:wrap">
          <span class="dot" :class="statusClass(f)"></span>
          <h3 style="margin-right:6px">{{ f.family }}</h3>
          <span class="faint mono" style="font-size:11.5px">{{ f.name }}</span>
          <span class="pill">{{ f.height }} px</span>
          <span class="pill" v-if="f.bold">bold</span>
          <span class="pill" v-if="f.kanji">{{ f.kanji }} kanji</span>
          <span class="pill" v-if="f.jis">JIS-encoded</span>
          <span class="pill" v-for="t in f.tags.filter(t => t !== 'japanese')" :key="t">{{ t }}</span>
          <span class="grow"></span>
          <button class="btn sm" :class="{primary: verdict(f) === 'approved'}" @click="set(f, 'approved')"><Icon name="Check" :size="13"/>Approve</button>
          <button class="btn sm" :class="{danger: verdict(f) === 'dropped'}" @click="set(f, 'dropped')"><Icon name="X" :size="13"/>Drop</button>
          <button class="btn sm" :class="{primary: compare.includes(f.path)}" @click="toggleCompare(f)"><Icon name="Columns3" :size="13"/>Compare</button>
          <button class="btn sm" @click="wall([f.path])"><Icon name="MonitorPlay" :size="13"/>On wall</button>
        </div>
        <div class="card-body" style="padding-top:0">
          <div style="background:#000; border-radius:6px; padding:6px; overflow:auto"><img loading="lazy" :src="img(f)" :style="{width: 448 * zoom + 'px', maxWidth: '100%', imageRendering: 'pixelated', display: 'block'}" alt=""></div>
          <div class="faint" style="font-size:11.5px; margin-top:4px">{{ f.origin }}<template v-if="f.licence"> · {{ f.licence }}</template><template v-if="f.used_by.length"> · <b>Used by</b> {{ f.used_by.join(' · ') }}</template><template v-else-if="!f.candidate"> · not used by any screen</template></div>
        </div>
      </div>
      <div class="card"><div class="card-body faint" v-if="!shown.length">No fonts match these filters.</div></div>
    </div>
  </div>`,
};
