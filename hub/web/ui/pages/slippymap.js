// A small pan/zoom web-mercator tile map with no library: the view, tiles for it, and pointer/wheel handling.
// Used by the DriveBC camera map and the live webcam map (their markers are SVG on top of the tiles).
import { ref, computed, onMounted, onBeforeUnmount } from "vue";

export const TILE = "/api/tiles/{layer}/{z}/{x}/{y}.png";   // Esri's dark grey canvas and its labels, via the hub's tile cache

export const wx = (lon, z) => (lon + 180) / 360 * 256 * 2 ** z;
export const wy = (lat, z) => { const s = Math.sin(lat * Math.PI / 180); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * 256 * 2 ** z; };
export const lonOf = (x, z) => x / (256 * 2 ** z) * 360 - 180;
export const latOf = (y, z) => { const n = Math.PI - 2 * Math.PI * y / (256 * 2 ** z); return 180 / Math.PI * Math.atan(Math.sinh(n)); };

// el: a ref to the map's element; view: its starting {lon, lat, z}
export function useSlippyMap(el, view0, { minZ = 2, maxZ = 16 } = {}) {
  const size = ref({ w: 900, h: 600 });
  const view = ref({ ...view0 });
  let ro;
  onMounted(() => {
    ro = new ResizeObserver(() => { if (el.value) size.value = { w: el.value.clientWidth, h: el.value.clientHeight }; });
    if (el.value) { ro.observe(el.value); size.value = { w: el.value.clientWidth, h: el.value.clientHeight }; }
  });
  onBeforeUnmount(() => ro && ro.disconnect());

  // the view: top-left world pixel, and screen position of a lon/lat
  const origin = computed(() => ({ x: wx(view.value.lon, view.value.z) - size.value.w / 2, y: wy(view.value.lat, view.value.z) - size.value.h / 2 }));
  const px = (lon, lat) => ({ x: wx(lon, view.value.z) - origin.value.x, y: wy(lat, view.value.z) - origin.value.y });
  const onScreen = (p, m = 20) => p.x > -m && p.y > -m && p.x < size.value.w + m && p.y < size.value.h + m;
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
  // the largest zoom (up to cap) that shows the box [lon0, lat0, lon1, lat1]
  const fit = ([lo0, la0, lo1, la1], cap = 14) => {
    const { w, h } = size.value;
    let z = Math.min(cap, maxZ);
    while (z > minZ && (wx(lo1, z) - wx(lo0, z) > w - 20 || wy(la0, z) - wy(la1, z) > h - 20)) z--;
    view.value = { lon: (lo0 + lo1) / 2, lat: latOf((wy(la0, z) + wy(la1, z)) / 2, z), z };
  };

  // panning and zooming
  let drag = null;
  const dragged = () => !!drag?.moved;                       // a click at the end of a drag isn't a pick
  const down = (ev) => { if (ev.button !== 0) return; drag = { x: ev.clientX, y: ev.clientY, o: { ...origin.value }, moved: false }; };
  const move = (ev) => {
    if (!drag) return;
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 3) {      // capture only once it is a drag, so a click still reaches a marker
      drag.moved = true;
      ev.currentTarget.setPointerCapture(ev.pointerId);
    }
    if (!drag.moved) return;
    const z = view.value.z;
    view.value = { z, lon: lonOf(drag.o.x - dx + size.value.w / 2, z), lat: latOf(drag.o.y - dy + size.value.h / 2, z) };
  };
  const up = () => { setTimeout(() => { drag = null; }, 0); };
  const zoomAt = (dz, sx, sy) => {
    const z = view.value.z, nz = Math.max(minZ, Math.min(maxZ, z + dz));
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
  return { size, view, origin, px, onScreen, tiles, fit, down, move, up, wheel, zoomAt, dragged };
}
