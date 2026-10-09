"use strict";
// Weather tab: map tracks (animations), map areas (geography + cartography), colour scales.
// Uses helpers from app.js: $, $$, clone, esc, api, toast, CFG, saveSection, ago.

let wm = null;            // draft of modules.weather_maps
let wmCatalog = null;
let wmState = { tracks: {}, packs: {} };
let wmEditing = null;     // {kind: "track"|"area", id}
const players = {};

function wmDirty() { return JSON.stringify(wm) !== JSON.stringify(CFG.modules.weather_maps || {}); }
function markWm() {
  const d = wmDirty();
  $("#wm-dirty").hidden = !d;
  $("#wm-save").disabled = !d;
  $("#wm-revert").disabled = !d;
}

async function enterWeatherTab() {
  if (!wmCatalog) {
    try { wmCatalog = await api("/api/maps/catalog"); } catch (e) { toast(e.message, true); return; }
  }
  if (!wm) loadWmDraft();
  await refreshWmState();
  renderWm();
}
function loadWmDraft() {
  wm = clone(CFG.modules.weather_maps || { areas: {}, tracks: {} });
  wm.areas = wm.areas || {};
  wm.tracks = wm.tracks || {};
  wm.palettes = wm.palettes || {};
  markWm();
}
async function refreshWmState() {
  try { wmState = await api("/api/maps/state"); } catch {}
}

$("#wm-save").addEventListener("click", async () => {
  try {
    await saveSection("weather_maps", wm, "weather maps edited");
    wm = clone(CFG.modules.weather_maps);
    markWm();
    toast("Saved. Re-rendering changed maps (takes up to a minute)");
    setTimeout(async () => { await refreshWmState(); renderWm(); }, 20000);
  } catch (e) { toast(e.message, true); }
});
$("#wm-revert").addEventListener("click", () => { loadWmDraft(); wmEditing = null; renderWm(); });

// ---------------------------------------------------------------------------------
function renderWm() {
  renderTracks();
  renderAreas();
  renderPalettes();
}

function trackStatus(tid) {
  const st = wmState.tracks[tid];
  const pk = wmState.packs[tid];
  if (!st) return '<span class="muted">not rendered yet</span>';
  const last = st.last || {};
  const when = st.tried ? ago(Date.now() / 1000 - st.tried) : "";
  return (last.ok ? `<span class="up">✓</span> ${esc(last.message)}` : `<span class="down">✕ ${esc(last.message || "")}</span>`)
    + ` <span class="muted">· ${when}${last.seconds ? ` · ${last.seconds}s` : ""}${pk && pk.times?.length ? ` · ${pk.times.length} frames` : ""}</span>`;
}

function renderTracks() {
  Object.values(players).forEach(clearInterval);
  const ids = Object.keys(wm.tracks);
  $("#wm-tracks").innerHTML = ids.map((tid) => {
    const t = wm.tracks[tid];
    const area = wm.areas[t.area];
    return `<div class="card track-card ${t.enabled === false ? "off" : ""}" data-tid="${esc(tid)}">
      <div class="track-preview ${t.size === "full" ? "full" : ""}"><img alt="" data-player="${esc(tid)}"></div>
      <h3>${esc(t.label || tid)} <span class="pill ${t.kind === "accumulation" ? "never" : "ok"}">${t.kind === "accumulation" ? "totals" : "loop"}</span></h3>
      <div class="muted">${esc(area ? area.label : t.area)} · ${t.size === "full" ? "full width" : "half"} ·
        ${t.kind === "accumulation" ? `${t.window_hours ?? 24} h window` : `${Math.round((t.window_min ?? 180) / 60 * 10) / 10} h loop`} · every ${t.step_min ?? 10} min</div>
      <div class="msg">${trackStatus(tid)}</div>
      <div class="row">
        <button class="small" data-edit-track>Edit</button>
        <button class="small" data-show-track>Show now</button>
        <button class="small" data-render-track>Re-render</button>
        <label class="small-check"><input type="checkbox" data-enable-track ${t.enabled !== false ? "checked" : ""}> on</label>
      </div>
    </div>`;
  }).join("") + `<div class="card add-card"><button id="wm-add-track">+ New map</button></div>`;

  $$("#wm-tracks [data-player]").forEach((img) => startPlayer(img, img.dataset.player));
  $$("#wm-tracks .track-card").forEach((card) => {
    const tid = card.dataset.tid;
    $("[data-edit-track]", card).addEventListener("click", () => { wmEditing = { kind: "track", id: tid }; renderTrackEditor(); });
    $("[data-render-track]", card).addEventListener("click", async () => {
      try { await api(`/api/maps/render/${encodeURIComponent(tid)}`, { method: "POST" }); toast("Rendering…"); } catch (e) { toast(e.message, true); }
      setTimeout(async () => { await refreshWmState(); renderTracks(); }, 15000);
    });
    $("[data-show-track]", card).addEventListener("click", async () => {
      const t = wm.tracks[tid];
      try {
        await api("/api/display/override", { json: { side: "right", panel: t.size === "full" ? "map_full" : "map", track: tid, duration_sec: 120 } });
        toast("Showing on the display for 2 minutes");
      } catch (e) { toast(e.message, true); }
    });
    $("[data-enable-track]", card).addEventListener("change", (e) => { wm.tracks[tid].enabled = e.target.checked; markWm(); });
  });
  $("#wm-add-track").addEventListener("click", () => {
    let n = 1; while (wm.tracks[`map${n}`]) n++;
    const firstArea = Object.keys(wm.areas)[0];
    wm.tracks[`map${n}`] = { label: `New map ${n}`, area: firstArea, size: "half", kind: "loop",
      basemap: { source: "esri_imagery", brightness: 0.5 }, layers: [{ type: "radar", opacity: 0.9 }],
      window_min: 180, step_min: 6, refresh_min: 6, fps: 8, hold_last_sec: 1.5, loops: 2 };
    wmEditing = { kind: "track", id: `map${n}` };
    markWm(); renderTracks(); renderTrackEditor();
  });
  if (wmEditing?.kind === "track") renderTrackEditor();
}

function startPlayer(img, tid) {
  const pk = wmState.packs[tid];
  if (!pk || !pk.n) { img.replaceWith(Object.assign(document.createElement("div"), { className: "muted pad", textContent: "no frames yet" })); return; }
  let i = 0;
  const bust = Math.round(pk.generated_at || 0);
  const holdFrames = Math.round((pk.hold_last_sec || 1.5) * (pk.fps || 8));
  const step = () => {
    const idx = Math.min(i, pk.n - 1);
    img.src = `/api/maps/track/${encodeURIComponent(tid)}/frame/${idx}.png?scale=2&v=${bust}`;
    i = (i + 1) % (pk.n + holdFrames);
  };
  step();
  players[tid] = setInterval(step, 1000 / Math.min(12, pk.fps || 8));
}

// --- track editor ---------------------------------------------------------------------
function num(v, d) { return v === undefined || v === null || v === "" ? d : v; }
function renderTrackEditor() {
  const tid = wmEditing.id;
  const t = wm.tracks[tid];
  if (!t) { $("#wm-editor").innerHTML = ""; return; }
  const bm = t.basemap || (t.basemap = {});
  const isAcc = t.kind === "accumulation";
  const areaOpts = Object.entries(wm.areas).map(([id, a]) => `<option value="${esc(id)}" ${id === t.area ? "selected" : ""}>${esc(a.label || id)}</option>`).join("");
  const bmOpts = wmCatalog.basemaps.map((b) => `<option value="${b.id}" ${b.id === (bm.source || "esri_imagery") ? "selected" : ""}>${esc(b.label)}</option>`).join("");
  $("#wm-editor").innerHTML = `<div class="card editor">
    <div class="toolbar"><h3>Edit map: ${esc(t.label || tid)}</h3><div class="spacer"></div>
      <button class="small danger" id="te-delete">Delete map</button><button class="small" id="te-close">Close</button></div>
    <div class="form inline">
      <label>Name <input data-k="label" value="${esc(t.label || "")}"></label>
      <label>Area <select data-k="area">${areaOpts}</select></label>
      <label>Size <select data-k="size"><option value="half" ${t.size !== "full" ? "selected" : ""}>Half (one side)</option><option value="full" ${t.size === "full" ? "selected" : ""}>Full width</option></select></label>
      <label>Type <select data-k="kind"><option value="loop" ${!isAcc ? "selected" : ""}>Animated loop</option><option value="accumulation" ${isAcc ? "selected" : ""}>Rain & snow totals</option></select></label>
    </div>
    <h4>Timing</h4>
    <div class="form inline">
      ${isAcc
        ? `<label>Totals window (hours, max 48) <input type="number" min="0.5" max="48" step="0.5" data-k="window_hours" value="${num(t.window_hours, 24)}"></label>
           <label>Snow colours on top from (cm) <input type="number" min="0" step="0.1" data-k="snow_on_top_cm" value="${num(t.snow_on_top_cm, 0.5)}"></label>`
        : `<label>Loop length (minutes) <input type="number" min="10" max="2880" data-k="window_min" value="${num(t.window_min, 180)}"></label>`}
      <label>Minutes between frames <input type="number" min="1" max="360" data-k="step_min" value="${num(t.step_min, 10)}"></label>
      <label>Rebuild every (minutes) <input type="number" min="2" max="240" data-k="refresh_min" value="${num(t.refresh_min, 10)}"></label>
      <label>Frames per second <input type="number" min="1" max="30" data-k="fps" value="${num(t.fps, 8)}"></label>
      <label>Hold last frame (s) <input type="number" min="0" max="20" step="0.5" data-k="hold_last_sec" value="${num(t.hold_last_sec, 1.5)}"></label>
      <label>Loops before moving on <input type="number" min="1" max="10" data-k="loops" value="${num(t.loops, 2)}"></label>
    </div>
    <h4>Basemap</h4>
    <div class="form inline">
      <label>Imagery <select data-bm="source">${bmOpts}</select></label>
      <label>Brightness <span class="row"><input type="range" min="0.05" max="1.2" step="0.05" data-bm="brightness" value="${num(bm.brightness, 0.5)}"></span></label>
      <label>Contrast <span class="row"><input type="range" min="0.5" max="1.6" step="0.05" data-bm="contrast" value="${num(bm.contrast, 1)}"></span></label>
      <label>Saturation <span class="row"><input type="range" min="0" max="1.6" step="0.05" data-bm="saturation" value="${num(bm.saturation, 1)}"></span></label>
      <label>Water tint <span class="row"><input type="checkbox" data-tint="water" ${bm.water ? "checked" : ""}><input type="color" data-bm="water" value="${bm.water || "#0a1a33"}"></span></label>
      <label>Land tint <span class="row"><input type="checkbox" data-tint="land" ${bm.land ? "checked" : ""}><input type="color" data-bm="land" value="${bm.land || "#141a12"}"></span></label>
      <label>Tint strength <input type="range" min="0.1" max="1" step="0.05" data-bm="tint_strength" value="${num(bm.tint_strength, 1)}"></label>
    </div>
    ${isAcc ? "" : `<h4>Layers <span class="muted">(drawn over the basemap, under coastlines/borders; first time-based layer sets the frame times)</span></h4>
    <div id="te-layers"></div><button class="small" id="te-add-radar">+ Radar</button> <button class="small" id="te-add-wms">+ GeoMet layer</button>`}
    <h4>On-screen</h4>
    <div class="form inline">
      <label><span><input type="checkbox" data-flag="timestamp" ${t.timestamp !== false ? "checked" : ""}> time label</span></label>
      <label><span><input type="checkbox" data-flag="markers" ${t.markers !== false ? "checked" : ""}> place markers</span></label>
      ${isAcc ? `<label><span><input type="checkbox" data-flag="legend" ${t.legend !== false ? "checked" : ""}> legend</span></label>` : ""}
    </div>
    <p class="muted">Area geography, coastlines and borders are edited under <b>Map areas</b>. Save, then the map re-renders within a minute.</p>
  </div>`;

  const ed = $("#wm-editor");
  $$("[data-k]", ed).forEach((el) => el.addEventListener("input", () => {
    const k = el.dataset.k;
    t[k] = el.type === "number" ? (el.value === "" ? undefined : +el.value) : el.value;
    markWm();
    if (k === "kind") { if (t.kind === "accumulation") { t.window_hours = t.window_hours || 24; t.step_min = t.step_min || 30; } renderTrackEditor(); }
    if (["label", "area", "size", "kind"].includes(k)) renderTracksHeaderOnly();
  }));
  $$("[data-bm]", ed).forEach((el) => el.addEventListener("input", () => {
    const k = el.dataset.bm;
    if ((k === "water" || k === "land") && !$(`[data-tint=${k}]`, ed).checked) return;
    bm[k] = el.type === "range" ? +el.value : el.value;
    markWm();
  }));
  $$("[data-tint]", ed).forEach((el) => el.addEventListener("change", () => {
    const k = el.dataset.tint;
    if (el.checked) bm[k] = $(`[data-bm=${k}]`, ed).value; else delete bm[k];
    markWm();
  }));
  $$("[data-flag]", ed).forEach((el) => el.addEventListener("change", () => { t[el.dataset.flag] = el.checked; markWm(); }));
  $("#te-close").addEventListener("click", () => { wmEditing = null; $("#wm-editor").innerHTML = ""; });
  $("#te-delete").addEventListener("click", (e) => {
    const b = e.target;
    if (!b.dataset.armed) { b.dataset.armed = 1; b.textContent = "Really delete?"; return; }
    delete wm.tracks[tid]; wmEditing = null; $("#wm-editor").innerHTML = ""; markWm(); renderTracks();
  });
  if (!isAcc) renderLayerRows(t);
}
function renderTracksHeaderOnly() { /* keep cards in sync without restarting players too often */ clearTimeout(renderTracksHeaderOnly._t); renderTracksHeaderOnly._t = setTimeout(renderTracks, 600); }

function renderLayerRows(t) {
  t.layers = t.layers || [];
  const opts = (cur) => wmCatalog.wms_layers.map((l) => `<option value="${esc(l.layer)}" ${l.layer === cur ? "selected" : ""}>${esc(l.label)}</option>`).join("")
    + (cur && !wmCatalog.wms_layers.some((l) => l.layer === cur) ? `<option value="${esc(cur)}" selected>${esc(cur)}</option>` : "");
  $("#te-layers").innerHTML = t.layers.map((l, i) => `<div class="cycle-row" data-i="${i}">
      ${l.type === "radar" ? `<b>Radar</b> <span class="muted">live rain/snow/mixed, TV colours</span>`
        : `<select data-l="layer">${opts(l.layer)}</select>
           <select data-l="time"><option value="past" ${l.time !== "forecast" && l.time !== "static" ? "selected" : ""}>past</option><option value="forecast" ${l.time === "forecast" ? "selected" : ""}>forecast</option><option value="static" ${l.time === "static" ? "selected" : ""}>static</option></select>`}
      opacity <input type="range" min="0.1" max="1" step="0.05" data-l="opacity" value="${num(l.opacity, l.type === "radar" ? 0.9 : 1)}">
      <button class="small" data-up ${i === 0 ? "disabled" : ""}>↑</button><button class="small" data-down ${i === t.layers.length - 1 ? "disabled" : ""}>↓</button>
      <button class="small danger" data-del>✕</button></div>`).join("") || '<div class="muted">Basemap only</div>';
  $$("#te-layers .cycle-row").forEach((row) => {
    const i = +row.dataset.i;
    $$("[data-l]", row).forEach((el) => el.addEventListener("input", () => {
      t.layers[i][el.dataset.l] = el.type === "range" ? +el.value : el.value;
      if (el.dataset.l === "layer") {
        const meta = wmCatalog.wms_layers.find((x) => x.layer === el.value);
        if (meta) { t.layers[i].time = meta.time; renderLayerRows(t); }
      }
      markWm();
    }));
    $("[data-up]", row).addEventListener("click", () => { t.layers.splice(i - 1, 0, t.layers.splice(i, 1)[0]); renderLayerRows(t); markWm(); });
    $("[data-down]", row).addEventListener("click", () => { t.layers.splice(i + 1, 0, t.layers.splice(i, 1)[0]); renderLayerRows(t); markWm(); });
    $("[data-del]", row).addEventListener("click", () => { t.layers.splice(i, 1); renderLayerRows(t); markWm(); });
  });
  $("#te-add-radar").onclick = () => { t.layers.push({ type: "radar", opacity: 0.9 }); renderLayerRows(t); markWm(); };
  $("#te-add-wms").onclick = () => { const l = wmCatalog.wms_layers[0]; t.layers.push({ type: "wms", layer: l.layer, time: l.time, opacity: 1, scale: 2 }); renderLayerRows(t); markWm(); };
}

// --- areas -------------------------------------------------------------------------------
function renderAreas() {
  $("#wm-areas").innerHTML = Object.entries(wm.areas).map(([aid, a]) => {
    const used = Object.values(wm.tracks).filter((t) => t.area === aid).map((t) => t.label);
    return `<div class="card area-card" data-aid="${esc(aid)}">
      <div class="toolbar"><h3>${esc(a.label || aid)}</h3><span class="muted">${used.length ? "used by " + esc(used.join(", ")) : "unused"}</span><div class="spacer"></div>
        <button class="small" data-edit-area>${wmEditing?.kind === "area" && wmEditing.id === aid ? "Close" : "Edit"}</button></div>
      <div class="muted">centre ${a.center[0]}, ${a.center[1]} · ${a.km_per_px} km per pixel</div>
      ${wmEditing?.kind === "area" && wmEditing.id === aid ? areaEditorHtml(aid, a) : ""}
    </div>`;
  }).join("") + `<div class="card add-card"><button id="wm-add-area">+ New area</button></div>`;
  $$("#wm-areas .area-card").forEach((card) => {
    const aid = card.dataset.aid;
    $("[data-edit-area]", card).addEventListener("click", () => {
      wmEditing = wmEditing?.kind === "area" && wmEditing.id === aid ? null : { kind: "area", id: aid };
      renderAreas();
    });
    if (wmEditing?.kind === "area" && wmEditing.id === aid) wireAreaEditor(card, aid);
  });
  $("#wm-add-area").addEventListener("click", () => {
    let n = 1; while (wm.areas[`area${n}`]) n++;
    wm.areas[`area${n}`] = { label: `New area ${n}`, center: [49.25, -123.1], km_per_px: 2, markers: [] };
    wmEditing = { kind: "area", id: `area${n}` }; markWm(); renderAreas();
  });
}

function kmToSlider(k) { return Math.round(Math.log10(k) * 100); }
function sliderToKm(v) { const k = Math.pow(10, v / 100); return k < 1 ? Math.round(k * 100) / 100 : k < 10 ? Math.round(k * 10) / 10 : Math.round(k); }

function areaEditorHtml(aid, a) {
  return `<div class="area-editor">
    <div class="form inline">
      <label>Name <input data-a="label" value="${esc(a.label || "")}"></label>
      <label>Centre latitude <input type="number" step="0.01" data-a="lat" value="${a.center[0]}"></label>
      <label>Centre longitude <input type="number" step="0.01" data-a="lon" value="${a.center[1]}"></label>
      <label>Scale: <span id="ae-kpp">${a.km_per_px}</span> km/pixel <input type="range" min="-100" max="160" data-a="kpp" value="${kmToSlider(a.km_per_px)}" style="width:220px"></label>
      <label>Drop islands/lakes smaller than (px) <input type="number" min="0" max="50" data-a="min_feature_px" value="${num(a.min_feature_px, 3)}"></label>
      <label>Preview size <select id="ae-size"><option value="half">half</option><option value="full">full</option></select></label>
    </div>
    <div class="area-preview"><img id="ae-img" alt=""><div id="ae-info" class="muted"></div></div>
    <h4>Coastlines, borders &amp; roads <span class="muted">(each layer picks its data resolution for this scale; greyed rows are outside their scale range)</span></h4>
    <div class="grid-wrap"><table class="grid" id="ae-layers"><thead><tr><th>On</th><th>Layer</th><th>Colour</th><th>Opacity</th><th>Width</th><th>Show between (km/px)</th><th>Source at this scale</th></tr></thead><tbody></tbody></table></div>
    <h4>Place markers</h4>
    <div id="ae-markers"></div><button class="small" id="ae-add-marker">+ Marker</button>
    <div class="row end"><button class="small danger" id="ae-delete">Delete area</button></div>
  </div>`;
}

let areaPreviewTimer = null;
function wireAreaEditor(card, aid) {
  const a = wm.areas[aid];
  const refresh = () => { clearTimeout(areaPreviewTimer); areaPreviewTimer = setTimeout(() => previewArea(aid), 450); };
  $$("[data-a]", card).forEach((el) => el.addEventListener("input", () => {
    const k = el.dataset.a;
    if (k === "label") a.label = el.value;
    if (k === "lat") a.center = [+el.value, a.center[1]];
    if (k === "lon") a.center = [a.center[0], +el.value];
    if (k === "kpp") { a.km_per_px = sliderToKm(+el.value); $("#ae-kpp").textContent = a.km_per_px; }
    if (k === "min_feature_px") a.min_feature_px = +el.value;
    markWm(); refresh();
  }));
  $("#ae-size").addEventListener("change", refresh);
  $("#ae-delete").addEventListener("click", (e) => {
    if (Object.values(wm.tracks).some((t) => t.area === aid)) { toast("This area is used by a map. Change those maps first.", true); return; }
    const b = e.target;
    if (!b.dataset.armed) { b.dataset.armed = 1; b.textContent = "Really delete?"; return; }
    delete wm.areas[aid]; wmEditing = null; markWm(); renderAreas();
  });
  renderMarkers(a, refresh);
  previewArea(aid);
}

function renderMarkers(a, refresh) {
  a.markers = a.markers || [];
  $("#ae-markers").innerHTML = a.markers.map((m, i) => `<div class="cycle-row" data-i="${i}">
    <input data-m="name" value="${esc(m.name || "")}" placeholder="Name" style="width:140px">
    lat <input type="number" step="0.001" data-m="lat" value="${m.lat}" style="width:100px">
    lon <input type="number" step="0.001" data-m="lon" value="${m.lon}" style="width:100px">
    <button class="small danger" data-del>✕</button></div>`).join("") || '<div class="muted">No markers</div>';
  $$("#ae-markers .cycle-row").forEach((row) => {
    const i = +row.dataset.i;
    $$("[data-m]", row).forEach((el) => el.addEventListener("input", () => {
      a.markers[i][el.dataset.m] = el.dataset.m === "name" ? el.value : +el.value; markWm(); refresh();
    }));
    $("[data-del]", row).addEventListener("click", () => { a.markers.splice(i, 1); renderMarkers(a, refresh); markWm(); refresh(); });
  });
  $("#ae-add-marker").onclick = () => { a.markers.push({ name: "", lat: a.center[0], lon: a.center[1] }); renderMarkers(a, refresh); markWm(); };
}

async function previewArea(aid) {
  const a = wm.areas[aid];
  const track = Object.values(wm.tracks).find((t) => t.area === aid);
  $("#ae-info").textContent = "rendering…";
  try {
    const r = await api("/api/maps/preview-area", { json: { area_id: aid, area: a, size: $("#ae-size")?.value || "half", basemap: track?.basemap } });
    if (!wmEditing || wmEditing.id !== aid) return;
    $("#ae-img").src = r.png;
    $("#ae-info").textContent = `${r.width_km} km wide · lon ${r.bbox[0]} → ${r.bbox[2]}, lat ${r.bbox[1]} → ${r.bbox[3]}`;
    renderAreaLayers(aid, r.layers);
  } catch (e) { $("#ae-info").textContent = e.message; }
}

function renderAreaLayers(aid, rows) {
  const a = wm.areas[aid];
  a.carto = a.carto || {};
  $("#ae-layers tbody").innerHTML = rows.map((r) => `<tr data-key="${r.key}" class="${r.in_range ? "" : "off"}">
    <td><input type="checkbox" data-c="enabled" ${r.enabled ? "checked" : ""}></td>
    <td>${esc(r.label)}</td>
    <td><input type="color" data-c="color" value="${r.color}"></td>
    <td><input type="range" min="0.1" max="1" step="0.05" data-c="opacity" value="${r.opacity}"></td>
    <td><input type="number" min="1" max="3" data-c="width" value="${r.width}" style="width:50px"></td>
    <td><input type="number" min="0" step="0.1" data-c="min_km_px" value="${r.min_km_px}" style="width:60px"> –
        <input type="number" min="0" step="0.1" data-c="max_km_px" value="${r.max_km_px >= 1e8 ? "" : r.max_km_px}" placeholder="any" style="width:60px"></td>
    <td class="muted">${r.in_range ? esc(String(r.source || "")) : "hidden at this scale"}</td></tr>`).join("");
  $$("#ae-layers tbody tr").forEach((tr) => {
    const key = tr.dataset.key;
    $$("[data-c]", tr).forEach((el) => el.addEventListener("input", () => {
      const o = a.carto[key] = a.carto[key] || {};
      const k = el.dataset.c;
      o[k] = el.type === "checkbox" ? el.checked : el.type === "color" ? el.value : (el.value === "" ? (k === "max_km_px" ? 1e9 : 0) : +el.value);
      markWm();
      clearTimeout(areaPreviewTimer); areaPreviewTimer = setTimeout(() => previewArea(aid), 450);
    }));
  });
}

// --- colour scales -----------------------------------------------------------------------
const PALETTE_LABELS = {
  rain_total: "Rain totals (mm)", snow_total: "Snow totals (cm)",
  rain_rate: "Radar: rain (mm/h)", snow_rate: "Radar: snow (cm/h)", mixed_rate: "Radar: mixed (mm/h)", fzra_rate: "Radar: freezing rain (mm/h)",
};
function renderPalettes() {
  const box = $("#wm-palettes");
  box.innerHTML = Object.entries(PALETTE_LABELS).map(([name, label]) => {
    const bands = wm.palettes[name] || wmCatalog.palettes[name];
    return `<div class="palette" data-p="${name}"><div class="toolbar"><b>${label}</b>${wm.palettes[name] ? ' <span class="dirty">custom</span>' : ""}<div class="spacer"></div>
        ${wm.palettes[name] ? '<button class="small" data-reset>Reset to default</button>' : ""}</div>
      <div class="bands">${bands.map(([t, c], i) => `<span class="band" data-i="${i}"><input type="color" value="${c}"><input type="number" min="0" step="0.05" value="${t}"></span>`).join("")}
        <button class="small" data-add>+</button><button class="small" data-remove ${bands.length <= 2 ? "disabled" : ""}>−</button></div></div>`;
  }).join("");
  $$(".palette", box).forEach((el) => {
    const name = el.dataset.p;
    const ensure = () => (wm.palettes[name] = wm.palettes[name] || clone(wmCatalog.palettes[name]));
    $$(".band", el).forEach((b) => {
      const i = +b.dataset.i;
      $("input[type=color]", b).addEventListener("input", (e) => { ensure()[i][1] = e.target.value; markWm(); });
      $("input[type=number]", b).addEventListener("input", (e) => { ensure()[i][0] = +e.target.value; markWm(); });
    });
    $("[data-add]", el).addEventListener("click", () => { const p = ensure(); const [t, c] = p[p.length - 1]; p.push([Math.round(t * 1.5 * 10) / 10, c]); markWm(); renderPalettes(); });
    $("[data-remove]", el).addEventListener("click", () => { ensure().pop(); markWm(); renderPalettes(); });
    $("[data-reset]", el)?.addEventListener("click", () => { delete wm.palettes[name]; markWm(); renderPalettes(); });
  });
}
