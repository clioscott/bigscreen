// Clarity lab: a few seconds of a webcam looping on the wall while you change its picture settings (hub/clarity.py,
// display/modules/clip.py). Side by side: a slice you pick, A on the left and B on the right, each with its own
// settings. Full frame: the webcam's own above-the-clock framing with one set of settings. The loop is recorded
// once; every change re-renders it and the wall follows.
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast } from "../core.js";

const SCALING = [{ value: "current", label: "As now" }, { value: "linear", label: "Linear light" }];
const MODES = [{ value: "split", label: "Side by side (a slice, A and B)", icon: "Columns2" }, { value: "full", label: "Full frame (as the wall plays it)", icon: "RectangleHorizontal" }];
const blank = () => ({ height: null, fps: 30, scaling: "current", sharpen: 0.1, brightness: 1, contrast: 1, saturation: 1, dither: false });
const pct = (v) => Math.round(v * 100) + "%";

export const Clarity = {
  setup() {
    const cams = computed(() => Object.entries(store.saved?.modules?.webcams?.cams || {})
      .filter(([, c]) => c?.source?.type === "youtube").map(([id, c]) => ({ id, label: c.label || id, url: c.source.url })));
    const cam = ref("");
    const mode = ref("split");
    const formats = ref([]);
    const tallCrop = ref(null);
    const saved = ref({ colour: {}, default_sharpen: 0.1, default_height: 1080 });
    const loadingFormats = ref(false);
    const info = ref({ state: {}, zone: [448, 175], half: [223, 175], seconds: [5, 10], loop: null, now: 0 });
    const crop = reactive({ x: 0.3, y: 0.3, w: 0.25 });
    const a = reactive(blank()), b = reactive(blank());
    const seconds = ref(5);
    const blind = ref(false);
    const revealed = ref(false);
    const live = ref(true);
    const started = ref(false);          // a loop is up and being tuned: changes rebuild it
    const snapTick = ref(0);
    const snapOk = ref(false);
    let timer, snapRetry, debounce, pending = false, gone = false, wasRunning = false, restore = null;

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
    const outW = computed(() => (mode.value === "full" ? info.value.zone[0] : info.value.half[0]));
    const viewW = computed(() => (mode.value === "full" ? (tallCrop.value?.w ?? 1) : crop.w));
    const perLed = (v) => { const f = fmt(v.height); return f ? (viewW.value * f.width / outW.value) : 0; };
    const perLedText = (v) => { const k = perLed(v); return k ? `${k.toFixed(2)} source pixels for each LED${k < 1 ? " (fewer pixels than LEDs: enlarged)" : ""}` : ""; };

    // the webcam's saved settings as a version
    const fromSaved = () => {
      const hs = formats.value.map((f) => f.height);
      const q = saved.value.quality;
      let h = typeof q === "number" ? q : q === "max" ? hs[hs.length - 1] : saved.value.default_height;
      if (!hs.includes(h)) h = [...hs].reverse().find((x) => x <= h) ?? hs[hs.length - 1];
      const c = saved.value.colour || {};
      return { height: h, fps: 30, scaling: "current", sharpen: c.sharpen ?? saved.value.default_sharpen,
        brightness: c.brightness ?? 1, contrast: c.contrast ?? 1, saturation: c.saturation ?? 1, dither: false };
    };
    const reset = (v) => Object.assign(v, fromSaved());

    const loadFormats = async () => {
      const id = cam.value;
      formats.value = [];
      if (!id) return;
      loadingFormats.value = true;
      try {
        const r = await api(`/api/clarity/formats?cam=${encodeURIComponent(id)}`);
        if (id !== cam.value) return;                        // another webcam was picked meanwhile: not its answer
        formats.value = r.formats;
        tallCrop.value = r.tall_crop;
        saved.value = r.saved;
        reset(a); reset(b);
        if (restore && restore.cam === id) {                 // the loop that is up (after a reload): carry on tuning it
          const hs = r.formats.map((f) => f.height), s = restore.spec;
          if (hs.includes(s.a?.height) && hs.includes(s.b?.height)) {
            mode.value = s.mode === "full" ? "full" : "split";
            if (s.mode !== "full" && s.crop) Object.assign(crop, { x: s.crop.x, y: s.crop.y, w: s.crop.w });
            Object.assign(a, s.a); Object.assign(b, s.b);
            started.value = true;
          }
        }
        restore = null;
        clamp();
      } catch (e) { if (id === cam.value) fail(e); }
      if (id === cam.value) loadingFormats.value = false;
    };
    watch(cam, () => {
      started.value = false; pending = false; clearTimeout(debounce);
      snapOk.value = false; snapTick.value++; loadFormats();
    });

    const st = computed(() => info.value.state || {});
    const running = computed(() => !!st.value.running);
    const meta = computed(() => st.value.meta || null);
    const loadState = async () => {
      try { info.value = await api("/api/clarity/state"); } catch (e) { return; /* the hub is restarting */ }
      if (!running.value && pending) { pending = false; if (started.value) build(); }
      wasRunning = running.value;
    };
    onMounted(async () => {
      await loadState();
      if (gone) return;
      const last = info.value.loop?.cam;
      const m = info.value.state?.meta;
      if (m?.spec && m.cam && m.cam === last) restore = { cam: m.cam, spec: m.spec };
      if (!cam.value && cams.value.length) cam.value = last && cams.value.some((c) => c.id === last) ? last : cams.value[0].id;
      timer = setInterval(loadState, 700);
    });
    onBeforeUnmount(() => { gone = true; clearInterval(timer); clearTimeout(snapRetry); clearTimeout(debounce); });

    // dragging the slice on the picture
    const box = ref(null);
    let drag = null;
    const down = (e) => {
      if (mode.value !== "split") return;
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
      if (mode.value !== "split" || (e.target !== e.currentTarget && !e.target.classList.contains("clarity-img"))) return;
      const r = box.value.getBoundingClientRect();
      crop.x = (e.clientX - r.left) / r.width - crop.w / 2;
      crop.y = (e.clientY - r.top) / r.height - cropH.value / 2;
      clamp();
    };
    const rectStyle = computed(() => {
      const c = mode.value === "full" ? (tallCrop.value || { x: 0, y: 0, w: 1, h: 1 }) : { x: crop.x, y: crop.y, w: crop.w, h: cropH.value };
      return { left: `${c.x * 100}%`, top: `${c.y * 100}%`, width: `${c.w * 100}%`, height: `${c.h * 100}%` };
    });

    const build = async (newLoop = false) => {
      if (!cam.value || !formats.value.length) return;
      if (running.value) { pending = true; return; }
      if (newLoop) revealed.value = false;                   // (a new loop draws the blind sides again)
      try {
        await api("/api/clarity/build", { json: { cam: cam.value, mode: mode.value, crop: { ...crop }, seconds: seconds.value,
          a: { ...a }, b: { ...b }, blind: blind.value, new_loop: newLoop, play: true } });
        started.value = true;
        wasRunning = true;
        loadState();
      } catch (e) {
        if (String(e?.message || "").includes("already being built")) pending = true; else fail(e);
      }
    };
    // every change while a loop is up re-renders it (the same recorded seconds), a moment after you stop moving
    const sig = computed(() => JSON.stringify([mode.value, crop, a, b, blind.value]));
    watch(sig, () => {
      if (!started.value || !live.value) return;
      clearTimeout(debounce);
      debounce = setTimeout(() => { if (started.value) build(false); }, 600);
    });
    watch(blind, () => { revealed.value = false; });
    watch(seconds, () => { if (started.value) build(true); });          // another length is another recording
    const stop = async () => {
      started.value = false; pending = false; clearTimeout(debounce);
      try { await api("/api/clarity/stop", { method: "POST" }); } catch (e) { fail(e); }
    };
    const clear = async () => {
      started.value = false; pending = false; clearTimeout(debounce);
      try { await api("/api/clarity/clear", { method: "POST" }); loadState(); } catch (e) { fail(e); }
    };

    // keep a version as the webcam's own settings (in the draft: the Save bar keeps it)
    const keep = (v) => {
      const c = store.draft.modules?.webcams?.cams?.[cam.value];
      if (!c || !v.height) return;
      if (v.height === saved.value.default_height) delete c.quality; else c.quality = v.height;
      const col = { ...(c.colour || {}) };
      col.sharpen = v.sharpen;
      for (const k of ["brightness", "contrast", "saturation"]) { if (v[k] === 1) delete col[k]; else col[k] = v[k]; }
      c.colour = col;
      const lab = [v.fps === 60 && "60 frames a second", v.scaling === "linear" && "linear-light scaling", v.dither && "dithering"].filter(Boolean);
      toast(`Set on ${camObj.value?.label}: resolution, sharpness and colour. Press Save to keep.`
        + (lab.length ? ` Not carried over (the wall can't do them yet): ${lab.join(", ")}.` : ""), "ok", 7000);
    };

    const sideText = (side) => {
      const m = meta.value;
      if (!m) return "";
      if (m.blind && !revealed.value) return "hidden";
      const which = m[side];
      return `${which}: ${which === "A" ? m.a : m.b}`;
    };
    const loopText = computed(() => {
      const l = info.value.loop;
      if (!l?.recorded) return "";
      const d = new Date(l.recorded * 1000);
      return `${l.seconds} s recorded at ${d.toLocaleTimeString()}`;
    });
    const versions = computed(() => (mode.value === "full" ? [["A", a, "The picture"]] : [["A", a, "Left half"], ["B", b, "Right half"]]));

    return { cams, cam, mode, formats, loadingFormats, info, crop, a, b, seconds, blind, revealed, live, started, snapUrl, snapOk,
      snapFailed, maxW, clamp, can60, perLedText, box, down, move, up, place, rectStyle, st, running, meta, build, stop, clear,
      keep, reset, sideText, loopText, versions, SCALING, MODES, pct };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Clarity lab</h1>
      <p>Record a few seconds of a webcam, loop them on the wall above the clock, and change the picture's settings while you watch.
        The loop stays the same; only what you change is redone, so you can judge each setting on the same moving picture.</p></div></div>

    <div class="card">
      <div class="card-head"><Icon name="Video" :size="16"/><h3>1 · Webcam and view</h3>
        <select class="input" v-model="cam" style="width:auto; max-width: 300px"><option v-for="c in cams" :key="c.id" :value="c.id">{{ c.label }}</option></select></div>
      <div class="card-body stack">
        <div v-if="!cams.length" class="muted">No YouTube webcams yet: add one on the Webcams page.</div>
        <template v-else>
          <Seg v-model="mode" :options="MODES"/>
          <div class="clarity-pick" :class="{fixed: mode === 'full'}" ref="box" @pointerdown="place" @pointermove="move" @pointerup="up" @pointercancel="up">
            <img class="clarity-img" :src="snapUrl" :key="snapUrl" draggable="false" @load="snapOk = true" @error="snapFailed">
            <div v-if="!snapOk" class="clarity-wait"><Icon name="LoaderCircle" :size="18" class="spin"/>Getting a picture from the webcam…</div>
            <div class="clarity-rect" :style="rectStyle" @pointerdown.stop="down"></div>
          </div>
          <Field v-if="mode === 'split'" label="Slice size" hint="Drag the box to move it, or click the picture. A smaller box is more zoom. Both halves of the wall show this slice.">
            <Slider v-model="crop.w" :min="0.02" :max="maxW" :step="0.005" :format="(v) => Math.round(v * 100) + '% of the width'" @update:modelValue="clamp"/></Field>
          <div v-else class="faint" style="font-size:12px">The box is this webcam's own above-the-clock framing (set on the Webcams page). The wall shows exactly that, full width. The caption isn't drawn here.</div>
        </template>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><Icon name="Play" :size="16"/><h3>2 · The loop</h3><span class="faint" style="font-size:12px" v-if="loopText">{{ loopText }}</span></div>
      <div class="card-body stack">
        <div class="row" style="gap:14px">
          <button class="btn primary" :disabled="running || !cam || !formats.length" @click="build(true)"><Icon :name="running ? 'LoaderCircle' : 'Clapperboard'" :size="15" :class="{spin: running}"/>{{ started ? 'Record a new loop' : 'Record a loop and show it on the wall' }}</button>
          <Seg v-model="seconds" :options="info.seconds.map((s) => ({ value: s, label: s + ' s' }))"/>
          <button class="btn" @click="stop"><Icon name="Undo2" :size="15"/>Back to rotation</button>
          <span class="spacer"></span>
          <button class="btn ghost" :disabled="running" @click="clear"><Icon name="Trash2" :size="15"/>Delete</button>
        </div>
        <ToggleRow v-model="live" label="The wall follows my changes" hint="On: a moment after you change anything below (or move the slice), the loop is rendered again and swaps in. Off: change several things, then press Update."/>
        <div class="row" v-if="!live"><button class="btn" :disabled="running || !started" @click="build(false)"><Icon name="RefreshCw" :size="15"/>Update the wall</button></div>
        <div v-if="running || st.error" class="stack" style="gap:6px">
          <div class="row nowrap" v-if="running"><span class="muted" style="flex:1">{{ st.stage }}</span><span class="faint mono">{{ Math.round(st.pct || 0) }}%</span></div>
          <div class="clarity-bar" v-if="running"><div :style="{ width: (st.pct || 0) + '%' }"></div></div>
          <div v-if="st.error" class="callout" style="font-size:13px"><Icon name="TriangleAlert" :size="16"/><span>{{ st.error }}</span></div>
        </div>
        <div v-if="!started" class="faint" style="font-size:12px">Recording takes about 10-20 seconds. After that, sharpness and colour changes show in a few seconds; a new resolution or slice takes a little longer the first time.</div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><Icon name="SlidersHorizontal" :size="16"/><h3>3 · Settings{{ mode === 'split' ? ' for each half' : '' }}</h3>
        <ToggleRow v-if="mode === 'split'" v-model="blind" label="Blind" hint="Random sides, hidden until you ask" style="min-width: 250px"/></div>
      <div class="card-body">
        <div v-if="loadingFormats" class="muted"><Icon name="LoaderCircle" :size="14" class="spin"/> Asking YouTube which resolutions this stream has…</div>
        <div class="grid cols-2" v-else>
          <div class="stack" v-for="[name, v, title] in versions" :key="name">
            <div class="row nowrap"><span class="pill accent" v-if="mode === 'split'">{{ name }}</span><b style="flex:1">{{ title }}</b>
              <button class="btn sm ghost" @click="reset(v)" title="Back to what this webcam uses now"><Icon name="RotateCcw" :size="13"/>As saved</button>
              <button class="btn sm" @click="keep(v)" title="Make these the webcam's own settings (in the draft: press Save to keep)"><Icon name="Check" :size="13"/>Use for this webcam</button></div>
            <Field label="Source resolution" :hint="perLedText(v)">
              <select class="input" v-model="v.height" @change="!can60(v) && (v.fps = 30)">
                <option v-for="f in formats" :key="f.height" :value="f.height">{{ f.height }}p ({{ f.width }}×{{ f.height }})</option></select></Field>
            <Field label="Sharpness"><Slider v-model="v.sharpen" :min="0" :max="2" :step="0.05" :format="pct"/></Field>
            <Field label="Brightness"><Slider v-model="v.brightness" :min="0.4" :max="2" :step="0.05" :format="pct"/></Field>
            <Field label="Contrast"><Slider v-model="v.contrast" :min="0.5" :max="2" :step="0.05" :format="pct"/></Field>
            <Field label="Saturation"><Slider v-model="v.saturation" :min="0" :max="2" :step="0.05" :format="pct"/></Field>
            <div class="section-title" style="margin-top:6px"><Icon name="FlaskConical" :size="13"/>Lab only (the wall can't do these yet)</div>
            <Field label="Scaling down" hint="Linear light averages as light, so small bright things stay bright.">
              <Seg v-model="v.scaling" :options="SCALING"/></Field>
            <Field v-if="can60(v)" label="Frames a second" hint="The wall plays webcams at 30.">
              <Seg v-model="v.fps" :options="[{ value: 30, label: '30' }, { value: 60, label: '60' }]"/></Field>
            <ToggleRow v-model="v.dither" label="Dithering" hint="Fine moving noise that recovers shades the panels can't show, mostly in dark areas"/>
          </div>
        </div>
        <div v-if="meta && mode === 'split' && started && !running" class="row" style="margin-top:14px; gap:14px">
          <span class="pill">Left: {{ sideText('left') }}</span><span class="pill">Right: {{ sideText('right') }}</span>
          <button v-if="meta.blind && !revealed" class="btn sm" @click="revealed = true"><Icon name="Eye" :size="13"/>Reveal</button>
        </div>
      </div>
    </div>
  </div>`,
};
