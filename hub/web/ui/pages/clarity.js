// Clarity lab: a few seconds of a webcam, rendered two ways, looping side by side on the wall (hub/clarity.py,
// display/modules/clip.py). Pick the webcam, drag the slice, choose what differs between A and B, build.
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast } from "../core.js";

const SHARPEN = [{ value: 0, label: "Off" }, { value: 0.1, label: "0.1" }, { value: 0.25, label: "0.25" }, { value: 0.5, label: "0.5" }];
const SCALING = [{ value: "current", label: "As now" }, { value: "linear", label: "Linear light" }];
const blank = () => ({ height: null, fps: 30, scaling: "current", sharpen: 0, dither: false });

export const Clarity = {
  setup() {
    const cams = computed(() => Object.entries(store.saved?.modules?.webcams?.cams || {})
      .filter(([, c]) => c?.source?.type === "youtube").map(([id, c]) => ({ id, label: c.label || id, url: c.source.url })));
    const cam = ref("");
    const formats = ref([]);
    const loadingFormats = ref(false);
    const info = ref({ state: {}, zone: [448, 175], half: [223, 175], seconds: [5, 10] });
    const crop = reactive({ x: 0.3, y: 0.3, w: 0.25 });
    const a = reactive(blank()), b = reactive({ ...blank(), fps: 60 });
    const seconds = ref(5);
    const blind = ref(false);
    const revealed = ref(false);
    const snapTick = ref(0);
    const snapOk = ref(false);
    let timer, snapRetry, gone = false;

    const camObj = computed(() => cams.value.find((c) => c.id === cam.value));
    const best = computed(() => formats.value[formats.value.length - 1] || { width: 1920, height: 1080, fps: 30 });
    const srcAspect = computed(() => best.value.width / best.value.height);
    const sliceAspect = computed(() => info.value.half[0] / info.value.half[1]);
    const maxW = computed(() => Math.min(1, sliceAspect.value / srcAspect.value));
    const cropH = computed(() => crop.w * srcAspect.value / sliceAspect.value);      // as a fraction of the picture's height
    const clamp = () => {
      crop.w = Math.min(maxW.value, Math.max(0.02, crop.w));
      crop.x = Math.min(1 - crop.w, Math.max(0, crop.x));
      crop.y = Math.min(1 - cropH.value, Math.max(0, crop.y));
    };
    const snapUrl = computed(() => camObj.value
      ? `/api/webcams/snapshot.jpg?type=youtube&url=${encodeURIComponent(camObj.value.url)}&t=${snapTick.value}` : "");
    const snapFailed = () => { snapOk.value = false; clearTimeout(snapRetry); snapRetry = setTimeout(() => snapTick.value++, 4000); };

    const fmt = (h) => formats.value.find((f) => f.height === h);
    const can60 = (v) => (fmt(v.height)?.fps || 0) >= 50;
    const perLed = (v) => { const f = fmt(v.height); return f ? (crop.w * f.width / info.value.half[0]) : 0; };
    const perLedText = (v) => { const k = perLed(v); return k ? `${k.toFixed(2)} source pixels per LED${k < 1 ? " (enlarged: fewer pixels than LEDs)" : ""}` : ""; };

    const loadFormats = async () => {
      formats.value = [];
      if (!cam.value) return;
      loadingFormats.value = true;
      try {
        const r = await api(`/api/clarity/formats?cam=${encodeURIComponent(cam.value)}`);
        formats.value = r.formats;
        const hs = r.formats.map((f) => f.height);
        const pick = (want) => (hs.includes(want) ? want : hs[hs.length - 1]);
        if (!hs.includes(a.height)) a.height = pick(1080);
        if (!hs.includes(b.height)) b.height = pick(1080);
        for (const v of [a, b]) if (v.fps === 60 && !can60(v)) v.fps = 30;
        clamp();
      } catch (e) { fail(e); }
      loadingFormats.value = false;
    };
    watch(cam, () => { snapOk.value = false; snapTick.value++; loadFormats(); });

    const loadState = async () => {
      try { info.value = await api("/api/clarity/state"); } catch (e) { /* the hub is restarting */ }
    };
    onMounted(async () => {
      await loadState();
      if (gone) return;
      if (!cam.value && cams.value.length) cam.value = info.value.state?.meta?.cam && cams.value.some((c) => c.id === info.value.state.meta.cam)
        ? info.value.state.meta.cam : cams.value[0].id;
      timer = setInterval(loadState, 1000);
    });
    onBeforeUnmount(() => { gone = true; clearInterval(timer); clearTimeout(snapRetry); });

    // dragging the slice on the picture
    const box = ref(null);
    let drag = null;
    const down = (e) => {
      const r = box.value.getBoundingClientRect();
      drag = { px: e.clientX, py: e.clientY, x: crop.x, y: crop.y, w: r.width, h: r.height };
      e.target.setPointerCapture?.(e.pointerId);
      e.preventDefault();
    };
    const move = (e) => {
      if (!drag) return;
      crop.x = drag.x + (e.clientX - drag.px) / drag.w;
      crop.y = drag.y + (e.clientY - drag.py) / drag.h;
      clamp();
    };
    const up = () => { drag = null; };
    const place = (e) => {                                   // a click on the picture centres the slice there
      if (e.target !== e.currentTarget && !e.target.classList.contains("clarity-img")) return;
      const r = box.value.getBoundingClientRect();
      crop.x = (e.clientX - r.left) / r.width - crop.w / 2;
      crop.y = (e.clientY - r.top) / r.height - cropH.value / 2;
      clamp();
    };
    const rectStyle = computed(() => ({ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.w * 100}%`, height: `${cropH.value * 100}%` }));

    const preset = (name) => {
      if (!formats.value.length) return;
      const top = best.value.height, base = formats.value.some((f) => f.height === 1080) ? 1080 : top;
      Object.assign(a, { ...blank(), height: base });
      Object.assign(b, { ...blank(), height: base });
      if (name === "fps") b.fps = can60(b) ? 60 : 30;
      if (name === "res") { a.height = formats.value.some((f) => f.height === 720) ? 720 : formats.value[0].height; b.height = top; }
      if (name === "linear") b.scaling = "linear";
      if (name === "sharpen") b.sharpen = 0.25;
      if (name === "dither") b.dither = true;
      if (name === "fps" && b.fps !== 60) toast("This stream has no 60 frames a second version", "bad");
    };

    const st = computed(() => info.value.state || {});
    const running = computed(() => !!st.value.running);
    const meta = computed(() => st.value.meta || null);
    const same = computed(() => JSON.stringify(a) === JSON.stringify(b));
    const build = async () => {
      revealed.value = false;
      try {
        await api("/api/clarity/build", { json: { cam: cam.value, crop: { ...crop }, seconds: seconds.value, a: { ...a }, b: { ...b }, blind: blind.value } });
        toast("Building the clip…");
        loadState();
      } catch (e) { fail(e); }
    };
    const act = async (what) => { try { await api(`/api/clarity/${what}`, { method: "POST" }); loadState(); } catch (e) { fail(e); } };
    const sideText = (side) => {
      const m = meta.value;
      if (!m) return "";
      if (m.blind && !revealed.value) return "hidden";
      const which = m[side];
      return `${which}: ${which === "A" ? m.a : m.b}`;
    };

    return { cams, cam, formats, loadingFormats, info, crop, a, b, seconds, blind, revealed, snapUrl, snapOk, snapFailed, maxW, clamp,
      can60, perLedText, box, down, move, up, place, rectStyle, preset, st, running, meta, same, build, act, sideText,
      SHARPEN, SCALING, versions: [["A", a], ["B", b]] };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Clarity lab</h1>
      <p>See what actually makes a webcam clearer on the wall. Pick a webcam and a slice of its picture; the hub records the same few
        seconds two ways and loops them side by side above the clock, A on the left and B on the right, until you stop it.</p></div></div>

    <div class="card">
      <div class="card-head"><Icon name="Video" :size="16"/><h3>1 · Webcam and slice</h3>
        <span class="spacer"></span>
        <select class="input" v-model="cam" style="width:auto; max-width: 320px"><option v-for="c in cams" :key="c.id" :value="c.id">{{ c.label }}</option></select></div>
      <div class="card-body stack">
        <div v-if="!cams.length" class="muted">No YouTube webcams yet: add one on the Webcams page.</div>
        <template v-else>
          <div class="clarity-pick" ref="box" @pointerdown="place" @pointermove="move" @pointerup="up" @pointercancel="up">
            <img class="clarity-img" :src="snapUrl" :key="snapUrl" draggable="false" @load="snapOk = true" @error="snapFailed">
            <div v-if="!snapOk" class="clarity-wait"><Icon name="LoaderCircle" :size="18" class="spin"/>Getting a picture from the webcam…</div>
            <div class="clarity-rect" :style="rectStyle" @pointerdown.stop="down"></div>
          </div>
          <Field label="Slice size" hint="Drag the box to move it, or click the picture. Smaller box: more zoom, fewer source pixels for each LED.">
            <Slider v-model="crop.w" :min="0.02" :max="maxW" :step="0.005" :format="(v) => Math.round(v * 100) + '% of the width'" @update:modelValue="clamp"/></Field>
        </template>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><Icon name="Columns2" :size="16"/><h3>2 · What to compare</h3><span class="spacer"></span>
        <span class="faint" style="font-size:12px">Start from:</span>
        <button class="btn sm" @click="preset('fps')">30 vs 60 frames</button>
        <button class="btn sm" @click="preset('res')">720p vs best</button>
        <button class="btn sm" @click="preset('linear')">Scaling</button>
        <button class="btn sm" @click="preset('sharpen')">Sharpening</button>
        <button class="btn sm" @click="preset('dither')">Dithering</button></div>
      <div class="card-body">
        <div v-if="loadingFormats" class="muted"><Icon name="LoaderCircle" :size="14" class="spin"/> Asking YouTube which resolutions this stream has…</div>
        <div class="grid cols-2" v-else>
          <div class="stack" v-for="[name, v] in versions" :key="name">
            <div class="row nowrap"><span class="pill accent">{{ name }}</span><b>{{ name === 'A' ? 'Left half' : 'Right half' }}</b></div>
            <Field label="Source resolution" :hint="perLedText(v)">
              <select class="input" v-model="v.height" @change="!can60(v) && (v.fps = 30)">
                <option v-for="f in formats" :key="f.height" :value="f.height">{{ f.height }}p ({{ f.width }}×{{ f.height }}, {{ f.fps }} frames a second)</option></select></Field>
            <Field label="Frames a second" :hint="can60(v) ? 'The wall plays webcams at 30 today.' : 'This resolution only comes at 30.'">
              <Seg v-model="v.fps" :options="[{ value: 30, label: '30' }].concat(can60(v) ? [{ value: 60, label: '60' }] : [])"/></Field>
            <Field label="Scaling down" hint="As now: averaged the way the wall does it. Linear light: averaged as light, so small bright things stay bright.">
              <Seg v-model="v.scaling" :options="SCALING"/></Field>
            <Field label="Sharpening" hint="The wall's default is 0.1."><Seg v-model="v.sharpen" :options="SHARPEN"/></Field>
            <ToggleRow v-model="v.dither" label="Dithering" hint="Shimmering noise that recovers shades the panels can't show, mostly in dark areas"/>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><Icon name="Play" :size="16"/><h3>3 · Build and watch</h3></div>
      <div class="card-body stack">
        <div class="row" style="gap:18px">
          <Field label="Clip length"><Seg v-model="seconds" :options="info.seconds.map((s) => ({ value: s, label: s + ' s' }))"/></Field>
          <ToggleRow v-model="blind" label="Blind" hint="Put A and B on random sides and hide which is which until you ask" style="flex:1; min-width: 260px"/>
        </div>
        <div v-if="same" class="callout" style="font-size:13px"><Icon name="Info" :size="16"/><span>A and B are the same right now: change something in one of them.</span></div>
        <div class="row">
          <button class="btn primary" :disabled="running || !cam || !formats.length || same" @click="build"><Icon :name="running ? 'LoaderCircle' : 'Clapperboard'" :size="15" :class="{spin: running}"/>{{ running ? 'Building…' : 'Build and show on the wall' }}</button>
          <button class="btn" :disabled="running || !meta" @click="act('play')"><Icon name="Play" :size="15"/>Show again</button>
          <button class="btn" @click="act('stop')"><Icon name="Undo2" :size="15"/>Back to rotation</button>
          <span class="spacer"></span>
          <button class="btn ghost" :disabled="running || !meta" @click="act('clear')"><Icon name="Trash2" :size="15"/>Delete the clip</button>
        </div>
        <div v-if="running || st.error || st.stage" class="stack" style="gap:6px">
          <div class="row nowrap"><span class="muted" style="flex:1">{{ st.stage }}</span><span class="faint mono" v-if="running">{{ Math.round(st.pct || 0) }}%</span></div>
          <div class="clarity-bar" v-if="running"><div :style="{ width: (st.pct || 0) + '%' }"></div></div>
          <div v-if="st.error" class="callout" style="font-size:13px"><Icon name="TriangleAlert" :size="16"/><span>{{ st.error }}</span></div>
        </div>
        <div v-if="meta && !running" class="grid cols-2">
          <div class="card pad"><div class="kpi"><span>Left half</span><b style="font-size:15px; font-family: inherit">{{ sideText('left') }}</b></div></div>
          <div class="card pad"><div class="kpi"><span>Right half</span><b style="font-size:15px; font-family: inherit">{{ sideText('right') }}</b></div></div>
        </div>
        <div v-if="meta && meta.blind && !revealed && !running" class="row">
          <button class="btn" @click="revealed = true"><Icon name="Eye" :size="15"/>Reveal which side is which</button></div>
        <div v-if="meta && !running" class="faint" style="font-size:12px">{{ meta.label }} · {{ meta.seconds }} s at {{ meta.fps }} frames a second · {{ meta.n }} frames. The clip stays on the wall for 30 minutes unless you go back to the rotation.</div>
      </div>
    </div>
  </div>`,
};
