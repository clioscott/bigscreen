// DriveBC camera map: every highway camera in and around the screen's area, which way each one looks,
// what's on the roads, and which camera each event (and sample) shows. A small pan/zoom tile map, no library.
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { api, fail } from "../core.js";

const TILE = "/api/tiles/{layer}/{z}/{x}/{y}.png";          // Esri's dark grey canvas and its labels, via the hub's tile cache
const BEARING = { N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 };
const LOOK = { N: "north", NE: "northeast", E: "east", SE: "southeast", S: "south", SW: "southwest", W: "west", NW: "northwest" };
const COL = { event: "#ff8a1f", sample: "#c77dff", cam: "#7c8cff", off: "#5b6273", city: "#34c38f" };

const wx = (lon, z) => (lon + 180) / 360 * 256 * 2 ** z;
const wy = (lat, z) => { const s = Math.sin(lat * Math.PI / 180); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * 256 * 2 ** z; };
const lonOf = (x, z) => x / (256 * 2 ** z) * 360 - 180;
const latOf = (y, z) => { const n = Math.PI - 2 * Math.PI * y / (256 * 2 ** z); return 180 / Math.PI * Math.atan(Math.sinh(n)); };

export const CameraMap = {
  // addable: show "Add as a webcam" for the selected camera (Webcams page); added: camera ids already webcams
  // extra: the cities' own intersection cameras (several views per intersection, one marker each); focus: a bbox to show
  props: { addable: Boolean, added: { type: Array, default: () => [] }, extra: { type: Array, default: () => [] },
           focus: { type: Array, default: null } },
  emits: ["add", "open"],
  setup(props) {
    const st = ref({ cameras: [], events: [], samples: [], bbox: null });
    const loading = ref(true);
    const el = ref(null);
    const size = ref({ w: 900, h: 600 });
    const view = ref({ lon: -122.95, lat: 49.2, z: 11 });
    const sel = ref(null);
    const showSamples = ref(true);
    const showCity = ref(true);
    const selSpot = ref(null);                                 // a selected intersection (city cameras)
    const query = ref("");
    let ro;

    const load = async () => {
      try {
        st.value = await api("/api/bc/roads/cameras");
        if (props.focus) fit(props.focus);
        else if (st.value.bbox && !fitted) fit(st.value.bbox);
      } catch (e) { fail(e); }
      loading.value = false;
    };
    let fitted = false;
    const fit = ([lo0, la0, lo1, la1]) => {
      const { w, h } = size.value;
      let z = 14;
      while (z > 3 && (wx(lo1, z) - wx(lo0, z) > w - 20 || wy(la0, z) - wy(la1, z) > h - 20)) z--;
      view.value = { lon: (lo0 + lo1) / 2, lat: latOf((wy(la0, z) + wy(la1, z)) / 2, z), z };
      fitted = true;
    };
    onMounted(() => {
      ro = new ResizeObserver(() => { if (el.value) size.value = { w: el.value.clientWidth, h: el.value.clientHeight }; });
      if (el.value) { ro.observe(el.value); size.value = { w: el.value.clientWidth, h: el.value.clientHeight }; }
      load();
    });
    onBeforeUnmount(() => ro && ro.disconnect());
    watch(() => props.focus, (b) => { if (b) fit(b); });

    // the view: top-left world pixel, and screen position of a lon/lat
    const origin = computed(() => ({ x: wx(view.value.lon, view.value.z) - size.value.w / 2, y: wy(view.value.lat, view.value.z) - size.value.h / 2 }));
    const px = (lon, lat) => ({ x: wx(lon, view.value.z) - origin.value.x, y: wy(lat, view.value.z) - origin.value.y });
    const tiles = computed(() => {
      const z = view.value.z, n = 2 ** z, o = origin.value, out = [];
      for (let tx = Math.floor(o.x / 256); tx * 256 < o.x + size.value.w; tx++) {
        for (let ty = Math.max(0, Math.floor(o.y / 256)); ty * 256 < o.y + size.value.h && ty < n; ty++) {
          out.push({ key: `${z}/${tx}/${ty}`, x: tx * 256 - o.x, y: ty * 256 - o.y,
            src: TILE.replace("{z}", z).replace("{x}", ((tx % n) + n) % n).replace("{y}", ty) });
        }
      }
      return out;
    });

    // which cameras are in use
    const use = computed(() => {
      const m = {};
      for (const e of st.value.events) if (e.cam) (m[e.cam] = m[e.cam] || []).push({ ...e, what: "event" });
      if (showSamples.value) for (const e of st.value.samples) if (e.cam) (m[e.cam] = m[e.cam] || []).push({ ...e, what: "sample" });
      return m;
    });
    const colOf = (c) => {
      const u = use.value[c.id];
      if (u) return u.some((x) => x.what === "event") ? COL.event : COL.sample;
      return c.on ? COL.cam : COL.off;
    };
    const match = (c) => !query.value || `${c.name} ${c.caption} ${c.highway}`.toLowerCase().includes(query.value.toLowerCase());
    const cams = computed(() => {
      const zr = view.value.z;
      const r = zr >= 14 ? 26 : zr >= 13 ? 20 : zr >= 12 ? 15 : 11;
      return st.value.cameras.filter(match).map((c) => {
        const p = px(c.lon, c.lat);
        const b = BEARING[c.orientation];
        let cone = "";
        if (b !== undefined) {
          const a0 = (b - 28) * Math.PI / 180, a1 = (b + 28) * Math.PI / 180;
          cone = `M${p.x},${p.y} L${p.x + Math.sin(a0) * r},${p.y - Math.cos(a0) * r} A${r},${r} 0 0 1 ${p.x + Math.sin(a1) * r},${p.y - Math.cos(a1) * r} Z`;
        }
        return { c, p, cone, col: colOf(c), used: !!use.value[c.id] };
      }).filter(({ p }) => p.x > -40 && p.y > -40 && p.x < size.value.w + 40 && p.y < size.value.h + 40)
        .sort((a, b) => a.used - b.used);                       // cameras in use drawn on top
    });
    // the cities' cameras: one marker per intersection, all its views listed when picked
    const spots = computed(() => {
      const by = {};
      for (const c of props.extra) {
        if (c.lat == null || c.lon == null) continue;
        const k = `${(+c.lat).toFixed(5)},${(+c.lon).toFixed(5)}`;
        (by[k] = by[k] || { key: k, lat: +c.lat, lon: +c.lon, name: c.caption || c.name, credit: c.credit, views: [] }).views.push(c);
      }
      return Object.values(by);
    });
    const spotMarks = computed(() => {
      if (!showCity.value) return [];
      const q = query.value.toLowerCase();
      return spots.value.filter((s) => !q || s.name.toLowerCase().includes(q)).map((s) => ({ s, p: px(s.lon, s.lat) }))
        .filter(({ p }) => p.x > -20 && p.y > -20 && p.x < size.value.w + 20 && p.y < size.value.h + 20);
    });
    const pickSpot = (s) => { if (drag?.moved) return; selSpot.value = s; sel.value = null; };
    const word = (v) => (v.name.includes(" - ") ? v.name.split(" - ").pop() : "View");
    const lines = computed(() => [
      ...st.value.events.map((e) => ({ e, col: e.closed ? "#ff3b30" : "#ff8a1f" })),
      ...(showSamples.value ? st.value.samples.map((e) => ({ e, col: "#c77dff" })) : []),
    ].map(({ e, col }) => ({ e, col, pts: (e.points || []).map(([lo, la]) => px(lo, la)) })));
    const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");

    // panning and zooming
    let drag = null;
    const down = (ev) => { if (ev.button !== 0) return; drag = { x: ev.clientX, y: ev.clientY, o: { ...origin.value }, moved: false }; };
    const move = (ev) => {
      if (!drag) return;
      const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 3) {      // capture only once it is a drag, so a click still reaches a camera
        drag.moved = true;
        ev.currentTarget.setPointerCapture(ev.pointerId);
      }
      if (!drag.moved) return;
      const z = view.value.z;
      view.value = { z, lon: lonOf(drag.o.x - dx + size.value.w / 2, z), lat: latOf(drag.o.y - dy + size.value.h / 2, z) };
    };
    const up = () => { setTimeout(() => { drag = null; }, 0); };
    const zoomAt = (dz, sx, sy) => {
      const z = view.value.z, nz = Math.max(8, Math.min(17, z + dz));
      if (nz === z) return;
      const lon = lonOf(origin.value.x + sx, z), lat = latOf(origin.value.y + sy, z);      // keep the point under the cursor
      view.value = { z: nz, lon: lonOf(wx(lon, nz) - sx + size.value.w / 2, nz), lat: latOf(wy(lat, nz) - sy + size.value.h / 2, nz) };
    };
    let wheelAt = 0;
    const wheel = (ev) => {
      ev.preventDefault();
      if (Date.now() - wheelAt < 180) return;
      wheelAt = Date.now();
      const r = el.value.getBoundingClientRect();
      zoomAt(ev.deltaY < 0 ? 1 : -1, ev.clientX - r.left, ev.clientY - r.top);
    };
    const pick = (c) => { if (drag?.moved) return; sel.value = c; selSpot.value = null; };
    const goTo = (c) => {
      sel.value = c;
      const z = Math.max(view.value.z, 14);
      view.value = { z, lon: c.lon, lat: c.lat };
    };
    const byId = computed(() => Object.fromEntries(st.value.cameras.map((c) => [c.id, c])));
    const inUse = computed(() => Object.entries(use.value).map(([id, u]) => ({ cam: byId.value[id], u })).filter((x) => x.cam));
    const selUse = computed(() => (sel.value && use.value[sel.value.id]) || []);
    const imgT = ref(Date.now());
    const updated = (s) => { if (!s) return ""; const d = new Date(s); return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); };

    return { st, loading, el, tiles, cams, lines, path, down, move, up, wheel, zoomAt, size, sel, pick, goTo, inUse, selUse, showSamples,
      query, LOOK, COL, imgT, updated, load, fit, spots, spotMarks, selSpot, pickSpot, showCity, word };
  },
  template: `<div :class="addable ? '' : 'card'"><div class="card-head" v-if="!addable"><Icon name="Cctv" :size="15"/><h3>DriveBC cameras</h3>
      <span class="pill">{{ st.cameras.length }}</span>
      <input class="input" v-model="query" placeholder="Filter by name or highway" style="max-width:220px; height:28px; margin-left:auto">
      <label class="row faint" style="gap:6px; font-size:12.5px; cursor:pointer"><input type="checkbox" v-model="showSamples">Samples</label>
      <button class="btn sm ghost" @click="st.bbox && fit(st.bbox)" title="Whole area"><Icon name="Maximize2" :size="13"/></button></div>
    <div style="display:flex; min-height:0">
      <div ref="el" @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="up" @wheel="wheel"
           style="position:relative; flex:1; height:calc(100vh - 210px); min-height:420px; overflow:hidden; background:#0e1116; cursor:grab; touch-action:none; user-select:none">
        <img v-for="t in tiles" :key="t.key" :src="t.src.replace('{layer}', 'esri_dark')" draggable="false"
             :style="{position: 'absolute', left: t.x + 'px', top: t.y + 'px', width: '256px', height: '256px', pointerEvents: 'none'}" alt="">
        <img v-for="t in tiles" :key="'n' + t.key" :src="t.src.replace('{layer}', 'esri_dark_labels')" draggable="false"
             :style="{position: 'absolute', left: t.x + 'px', top: t.y + 'px', width: '256px', height: '256px', pointerEvents: 'none', opacity: .8}" alt="">
        <svg :width="size.w" :height="size.h" style="position:absolute; inset:0">
          <g v-for="(l, i) in lines" :key="'l' + i">
            <path v-if="l.pts.length > 1" :d="path(l.pts)" :stroke="l.col" stroke-width="5" fill="none" stroke-linecap="round" opacity=".85"/>
            <circle v-else-if="l.pts.length" :cx="l.pts[0].x" :cy="l.pts[0].y" r="6" :fill="l.col"/>
          </g>
          <g v-for="m in cams" :key="m.c.id" @click.stop="pick(m.c)" style="cursor:pointer">
            <path v-if="m.cone" :d="m.cone" :fill="m.col" :fill-opacity="sel && sel.id === m.c.id ? .7 : m.used ? .5 : .28" :stroke="m.col" stroke-opacity=".8" stroke-width="1"/>
            <circle :cx="m.p.x" :cy="m.p.y" :r="sel && sel.id === m.c.id ? 6 : m.used ? 5 : 3.5" :fill="m.col" stroke="#000" stroke-width="1.2"/>
            <circle v-if="sel && sel.id === m.c.id" :cx="m.p.x" :cy="m.p.y" r="10" fill="none" stroke="#fff" stroke-width="1.5"/>
            <title>{{ m.c.name }}{{ m.c.orientation ? ' · looking ' + LOOK[m.c.orientation] : '' }}</title>
          </g>
          <g v-for="m in spotMarks" :key="m.s.key" @click.stop="pickSpot(m.s)" style="cursor:pointer">
            <rect :x="m.p.x - 4" :y="m.p.y - 4" width="8" height="8" rx="2" :fill="COL.city" stroke="#000" stroke-width="1.2"/>
            <rect v-if="selSpot && selSpot.key === m.s.key" :x="m.p.x - 9" :y="m.p.y - 9" width="18" height="18" rx="4" fill="none" stroke="#fff" stroke-width="1.5"/>
            <title>{{ m.s.name }} · {{ m.s.views.length }} view{{ m.s.views.length === 1 ? '' : 's' }} · {{ m.s.credit }}</title>
          </g>
        </svg>
        <div style="position:absolute; left:10px; top:10px; display:flex; flex-direction:column; gap:4px" @pointerdown.stop>
          <button class="btn sm icon-only" @click="zoomAt(1, size.w / 2, size.h / 2)"><Icon name="Plus" :size="14"/></button>
          <button class="btn sm icon-only" @click="zoomAt(-1, size.w / 2, size.h / 2)"><Icon name="Minus" :size="14"/></button>
        </div>
        <div style="position:absolute; left:10px; bottom:8px; max-width:calc(100% - 20px); display:flex; flex-wrap:wrap; gap:4px 12px; font-size:12px; white-space:nowrap; background:rgba(0,0,0,.6); padding:4px 9px; border-radius:7px" @pointerdown.stop>
          <span><span class="dot" :style="{background: COL.event, display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%'}"></span> on the wall</span>
          <span v-if="showSamples"><span class="dot" :style="{background: COL.sample, display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%'}"></span> used by a sample</span>
          <span><span class="dot" :style="{background: COL.cam, display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%'}"></span> camera (cone = where it points)</span>
          <span><span class="dot" :style="{background: COL.off, display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%'}"></span> offline</span>
          <label v-if="spots.length" style="cursor:pointer"><input type="checkbox" v-model="showCity" style="vertical-align:-2px">
            <span :style="{background: COL.city, display: 'inline-block', width: '8px', height: '8px', borderRadius: '2px'}"></span> intersection cameras (City)</label>
        </div>
        <div style="position:absolute; right:6px; top:4px; max-width:calc(100% - 56px); font-size:10.5px; color:#9aa; background:rgba(0,0,0,.5); padding:1px 5px; border-radius:4px" @pointerdown.stop>Basemap © Esri, HERE, Garmin, © OpenStreetMap contributors · cameras: DriveBC{{ spots.length ? ', City of Vancouver, City of Richmond' : '' }}</div>
        <div v-if="loading" style="position:absolute; inset:0; display:grid; place-items:center" class="faint">Loading cameras…</div>
      </div>
      <div style="width:340px; flex:none; border-left:1px solid var(--line); overflow:auto; height:calc(100vh - 210px); min-height:420px">
        <div v-if="selSpot" class="card-body stack">
          <div><b>{{ selSpot.name }}</b><div class="faint" style="font-size:12.5px">{{ selSpot.credit }} · {{ selSpot.views.length }} view{{ selSpot.views.length === 1 ? '' : 's' }}</div></div>
          <div v-for="v in selSpot.views" :key="v.id" class="stack" style="gap:6px">
            <img :src="v.thumb" style="width:100%; border-radius:8px; display:block; background:#000; aspect-ratio:16/9; object-fit:cover" alt="" loading="lazy">
            <div class="row" style="gap:8px"><b style="font-size:13px; flex:1">{{ word(v) }}</b>
              <template v-if="addable">
                <button v-if="!added.includes(v.id)" class="btn sm primary" @click="$emit('add', v)"><Icon name="Plus" :size="13"/>Add</button>
                <button v-else class="btn sm" @click="$emit('open', v)"><Icon name="Check" :size="13"/>Added · open</button>
              </template></div>
          </div>
          <button class="btn sm ghost" @click="selSpot = null"><Icon name="X" :size="13"/>Close</button>
        </div>
        <div v-else-if="sel" class="card-body stack">
          <a :href="sel.image" target="_blank" rel="noopener"><img :src="sel.image + (sel.image.includes('?') ? '&' : '?') + 'v=' + imgT" style="width:100%; border-radius:8px; display:block" alt=""></a>
          <div><b>{{ sel.name }}</b><div class="faint" style="font-size:12.5px">{{ sel.caption }}</div></div>
          <div class="row" style="flex-wrap:wrap; gap:6px">
            <span class="pill" v-if="sel.orientation">Looking {{ LOOK[sel.orientation] || sel.orientation }}</span>
            <span class="pill" v-if="sel.highway">Hwy {{ sel.highway }}</span>
            <span class="pill" v-if="!sel.on" style="color:var(--bad)">Offline</span>
            <span class="pill" v-else-if="sel.delayed" style="color:var(--warn)">Delayed</span>
            <span class="faint" style="font-size:12px" v-if="sel.updated">Image {{ updated(sel.updated) }}</span>
          </div>
          <div v-for="(u, i) in selUse" :key="i" class="callout info" style="font-size:12.5px; display:block">
            {{ u.what === 'event' ? 'On the wall for' : 'Shown for the sample' }} <b>{{ u.label }}</b>{{ u.km != null ? ' · ' + u.km + ' km from it' : '' }}</div>
          <div class="row" v-if="addable">
            <button v-if="!added.includes(sel.id)" class="btn sm primary" @click="$emit('add', sel)"><Icon name="Plus" :size="13"/>Add as a webcam</button>
            <button v-else class="btn sm" @click="$emit('open', sel)"><Icon name="Check" :size="13"/>Added · open it</button></div>
          <button class="btn sm ghost" @click="sel = null"><Icon name="X" :size="13"/>Close</button>
        </div>
        <div v-else class="card-body stack">
          <div class="faint" style="font-size:12.5px">Click a camera to see its latest picture. The screen uses the nearest camera within 3 km of an event.</div>
          <div v-if="inUse.length"><b style="font-size:13px">Cameras in use</b></div>
          <div class="list">
            <div class="list-row" v-for="x in inUse" :key="x.cam.id" style="cursor:pointer" @click="goTo(x.cam)">
              <span class="dot" :style="{background: x.u.some(v => v.what === 'event') ? COL.event : COL.sample}"></span>
              <span style="flex:1; font-size:12.5px"><b>{{ x.cam.name }}</b><br><span class="faint">{{ x.u.map(v => v.label).join(' · ') }}</span></span>
            </div>
          </div>
          <div v-if="!inUse.length" class="faint" style="font-size:12.5px">No events on the roads right now.</div>
        </div>
      </div>
    </div></div>`,
};
