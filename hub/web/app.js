"use strict";
// BigScreen control panel. Plain JS, no build step.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const clone = (o) => JSON.parse(JSON.stringify(o));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let CFG = null;          // last saved config from the hub
let ticker = null;       // draft of modules.finance
let news = null;         // draft of modules.headlines

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
    init.method = init.method || "POST";
  }
  const r = await fetch(path, init);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
  return data;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = bad ? "bad" : "";
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), bad ? 6000 : 2500);
}

function ago(sec) {
  if (sec == null) return "never";
  if (sec < 90) return `${Math.round(sec)}s ago`;
  if (sec < 5400) return `${Math.round(sec / 60)} min ago`;
  if (sec < 172800) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} days ago`;
}

// ---- tabs ---------------------------------------------------------------
$$("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((s) => (s.hidden = s.id !== `tab-${name}`));
  try { localStorage.setItem("tab", name); } catch {}
  history.replaceState(null, "", `#${name}`);
  if (name === "ticker") refreshQuotes();
  if (name === "headlines") refreshHeadlines();
  if (name === "display" && typeof enterDisplayTab === "function") enterDisplayTab();
  if (name === "weather" && typeof enterWeatherTab === "function") enterWeatherTab();
}

// ---- config save helper -----------------------------------------------------
async function saveSection(section, draft, reason) {
  const next = clone(CFG);
  next.modules[section] = clone(draft);
  const res = await api("/api/config", { method: "PUT", json: { config: next, reason } });
  CFG = res.config;
  updateBanner(res.display_in_sync);
  toast("Saved");
  return res;
}

function updateBanner(inSync) {
  $("#display-banner").hidden = inSync !== false;
}

$("#apply-btn").addEventListener("click", async () => {
  const b = $("#apply-btn");
  b.disabled = true;
  b.textContent = "Applying…";
  try {
    const r = await api("/api/display/apply", { method: "POST" });
    if (r.ok) { toast("Display updated. It restarts and shows a loading screen for a few seconds."); updateBanner(true); }
    else toast("Apply failed: " + r.steps.filter((s) => !s.ok).map((s) => `${s.step}: ${s.error}`).join("; "), true);
  } catch (e) { toast(e.message, true); }
  b.disabled = false;
  b.textContent = "Apply to display";
});

// ---- status -----------------------------------------------------------------
async function refreshStatus() {
  let st;
  try { st = await api("/api/status"); } catch (e) { $("#jobs").innerHTML = `<div class="card">Hub unreachable: ${esc(e.message)}</div>`; return; }
  updateBanner(st.display.in_sync);
  $("#jobs").innerHTML = Object.entries(st.jobs).map(([name, j]) => `
    <div class="card">
      <h3>${esc(j.label)} <span class="pill ${j.health}">${j.health}</span></h3>
      <div class="muted">Last success ${ago(j.age)} · every ${Math.round(j.interval / 60) >= 2 ? Math.round(j.interval / 60) + " min" : j.interval + " s"}${j.duration ? ` · took ${j.duration}s` : ""}</div>
      <div class="msg">${esc(j.message || "")}</div>
      ${j.last_ok === false && j.error ? `<pre>${esc(j.error.split("\n").slice(-8).join("\n"))}</pre>` : ""}
      <button class="small" data-run="${name}" ${j.running ? "disabled" : ""}>${j.running ? "Running…" : "Run now"}</button>
    </div>`).join("");
  $$("[data-run]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true; b.textContent = "Running…";
    await api(`/api/jobs/${b.dataset.run}/run`, { method: "POST" });
    setTimeout(refreshStatus, 1500);
  }));
  const s = st.sync;
  $("#display-info").innerHTML = `
    <div>Data sync to Pi: ${s.at ? (s.ok ? `<span class="up">ok</span> at ${esc(s.at)}` : `<span class="down">failing</span> at ${esc(s.at)} — ${esc(s.error)}`) : "not yet"}</div>
    <div>Settings on display: ${st.display.in_sync ? '<span class="up">up to date</span>' : '<span class="down">older than saved config</span>'}${st.display.applied_at ? ` (applied ${ago(st.now - st.display.applied_at)})` : ""}</div>
    <div class="muted">Config revision ${st.config_revision}</div>`;
}

// ---- ticker -----------------------------------------------------------------
let quotes = {};
function tickerDirty() {
  return JSON.stringify(ticker) !== JSON.stringify(CFG.modules.finance);
}
function markTicker() {
  const d = tickerDirty();
  $("#ticker-dirty").hidden = !d;
  $("#ticker-save").disabled = !d;
  $("#ticker-revert").disabled = !d;
  $("#ticker-preview").disabled = !d;
}

function fmtPrice(p) {
  if (p == null) return "";
  return p >= 1000 ? p.toLocaleString(undefined, { maximumFractionDigits: 0 }) : p.toFixed(2);
}

function renderTicker() {
  const tb = $("#ticker-table tbody");
  tb.innerHTML = ticker.symbols.map((t, i) => {
    const q = quotes[t.symbol] || {};
    const ch = q.change;
    const stale = q._updated && Date.now() / 1000 - q._updated > 900;
    return `<tr draggable="true" data-i="${i}" class="${t.enabled === false ? "off" : ""}">
      <td class="handle" title="Drag to reorder">⠿</td>
      <td><input type="checkbox" data-f="enabled" ${t.enabled !== false ? "checked" : ""}></td>
      <td><button class="logo-btn" data-logo title="Change logo"><img src="/api/finance/icons/${encodeURIComponent(t.icon || t.symbol + ".png")}?v=${iconBust}" alt="" onerror="this.replaceWith(document.createTextNode('—'))"></button></td>
      <td class="sym">${esc(t.symbol)}</td>
      <td><input class="label" data-f="title" value="${esc(t.title ?? t.symbol)}"></td>
      <td><input class="width" type="number" min="20" max="448" data-f="width" value="${t.width ?? ""}" placeholder="auto"></td>
      <td class="quote ${ch > 0 ? "up" : ch < 0 ? "down" : ""}">${q.price != null ? `${fmtPrice(q.price)} ${ch != null ? (ch >= 0 ? "+" : "") + ch.toFixed(2) + "%" : ""}${stale ? ' <span class="muted">(stale)</span>' : ""}` : '<span class="muted">—</span>'}</td>
      <td><button class="small danger" data-del title="Remove">✕</button></td>
    </tr>`;
  }).join("");

  $$("tr", tb).forEach((tr) => {
    const i = +tr.dataset.i;
    $$("input", tr).forEach((inp) => inp.addEventListener("input", () => {
      const f = inp.dataset.f;
      if (f === "enabled") ticker.symbols[i].enabled = inp.checked;
      else if (f === "width") ticker.symbols[i].width = inp.value === "" ? null : parseInt(inp.value, 10);
      else ticker.symbols[i][f] = inp.value;
      tr.classList.toggle("off", ticker.symbols[i].enabled === false);
      markTicker();
    }));
    $("[data-del]", tr).addEventListener("click", () => { ticker.symbols.splice(i, 1); renderTicker(); markTicker(); });
    $("[data-logo]", tr).addEventListener("click", () => openIconDialog(i));
    // drag to reorder
    tr.addEventListener("dragstart", (e) => { tr.classList.add("dragging"); e.dataTransfer.setData("text/plain", i); });
    tr.addEventListener("dragend", () => tr.classList.remove("dragging"));
    tr.addEventListener("dragover", (e) => { e.preventDefault(); tr.classList.add("drop-above"); });
    tr.addEventListener("dragleave", () => tr.classList.remove("drop-above"));
    tr.addEventListener("drop", (e) => {
      e.preventDefault();
      tr.classList.remove("drop-above");
      const from = +e.dataTransfer.getData("text/plain");
      if (from === i) return;
      const [moved] = ticker.symbols.splice(from, 1);
      ticker.symbols.splice(from < i ? i - 1 : i, 0, moved);
      renderTicker(); markTicker();
    });
  });
}

async function refreshQuotes() {
  try { quotes = await api("/api/finance/data"); } catch { quotes = {}; }
  if (ticker) renderTicker();
}

$("#ticker-save").addEventListener("click", async () => {
  try {
    ticker.refresh_interval_sec = +$("#fin-refresh").value || 60;
    ticker.animation = { ...(ticker.animation || {}), hold_sec: +$("#fin-hold").value || 8 };
    await saveSection("finance", ticker, "ticker edited");
    ticker = clone(CFG.modules.finance);
    markTicker();
    setTimeout(refreshQuotes, 8000);
  } catch (e) { toast(e.message, true); }
});
$("#ticker-revert").addEventListener("click", () => { loadTickerDraft(); });
["#fin-refresh", "#fin-hold"].forEach((s) => $(s).addEventListener("input", () => {
  ticker.refresh_interval_sec = +$("#fin-refresh").value;
  ticker.animation = { ...(ticker.animation || {}), hold_sec: +$("#fin-hold").value };
  markTicker();
}));

function loadTickerDraft() {
  ticker = clone(CFG.modules.finance);
  $("#fin-refresh").value = ticker.refresh_interval_sec ?? 60;
  $("#fin-hold").value = ticker.animation?.hold_sec ?? 8;
  renderTicker();
  markTicker();
}

// ticker search / add
let searchTimer;
$("#ticker-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (!q) { $("#ticker-results").hidden = true; return; }
  searchTimer = setTimeout(async () => {
    let res = [];
    try { res = await api(`/api/finance/search?q=${encodeURIComponent(q)}`); } catch (err) { toast(err.message, true); }
    const upper = q.toUpperCase();
    if (!res.some((r) => r.symbol === upper)) res.push({ symbol: upper, name: "add exactly as typed", exchange: "", type: "" });
    const box = $("#ticker-results");
    box.innerHTML = res.map((r) => `<div data-sym="${esc(r.symbol)}"><span class="sym">${esc(r.symbol)}</span><span>${esc(r.name)}</span><span class="muted">${esc(r.exchange)} ${esc(r.type)}</span></div>`).join("");
    box.hidden = false;
    $$("[data-sym]", box).forEach((d) => d.addEventListener("click", () => addTicker(d.dataset.sym)));
  }, 250);
});
document.addEventListener("click", (e) => { if (!e.target.closest(".add-row")) $("#ticker-results").hidden = true; });

function addTicker(sym) {
  $("#ticker-results").hidden = true;
  $("#ticker-search").value = "";
  if (ticker.symbols.some((t) => t.symbol.toUpperCase() === sym.toUpperCase())) { toast(`${sym} is already in the list`); return; }
  ticker.symbols.push({ symbol: sym, title: sym, width: null, icon: `${sym}.png`, enabled: true });
  renderTicker(); markTicker();
}

// logo dialog
let iconBust = Date.now();
let iconIndex = null;
async function openIconDialog(i) {
  iconIndex = i;
  const t = ticker.symbols[i];
  $("#icon-sym").textContent = t.symbol;
  $("#icon-msg").textContent = "";
  $("#icon-file").value = "";
  setIconPreview();
  const icons = await api("/api/finance/icons");
  $("#icon-existing").innerHTML = `<option value="">— choose —</option>` + icons.map((n) => `<option ${n === t.icon ? "selected" : ""}>${esc(n)}</option>`).join("");
  $("#icon-dialog").showModal();
}
function setIconPreview() {
  const t = ticker.symbols[iconIndex];
  $("#icon-preview").src = `/api/finance/icons/${encodeURIComponent(t.icon || t.symbol + ".png")}?v=${iconBust}`;
}
function iconSaved(name) {
  ticker.symbols[iconIndex].icon = name;
  iconBust = Date.now();
  setIconPreview(); renderTicker(); markTicker();
  $("#icon-msg").textContent = "Logo updated. Save, then Apply to display.";
}
$("#icon-fetch").addEventListener("click", async () => {
  const t = ticker.symbols[iconIndex];
  try {
    const r = await api(`/api/finance/icons/${encodeURIComponent(t.symbol + ".png")}/from-domain`, { json: { domain: $("#icon-domain").value } });
    iconSaved(r.icon);
  } catch (e) { $("#icon-msg").textContent = e.message; }
});
$("#icon-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const t = ticker.symbols[iconIndex];
  try {
    const r = await api(`/api/finance/icons/${encodeURIComponent(t.symbol + ".png")}`, { method: "POST", body: f });
    iconSaved(r.icon);
  } catch (err) { $("#icon-msg").textContent = err.message; }
});
$("#icon-existing").addEventListener("change", (e) => { if (e.target.value) iconSaved(e.target.value); });

// ---- headlines ----------------------------------------------------------------
let feedStatus = {};
let headlineData = {};
const collapsed = new Set();

function newsDirty() { return JSON.stringify(news) !== JSON.stringify(CFG.modules.headlines); }
function markNews() {
  const d = newsDirty();
  $("#news-dirty").hidden = !d;
  $("#news-save").disabled = !d;
  $("#news-revert").disabled = !d;
}

function loadNewsDraft() {
  news = clone(CFG.modules.headlines);
  $("#news-items").value = news.items_per_category ?? 12;
  $("#news-per-source").value = news.max_per_source ?? 3;
  $("#news-age").value = news.max_age_hours ?? 36;
  $("#news-refresh").value = Math.round((news.refresh_interval_sec ?? 900) / 60);
  renderCategories();
  markNews();
}

[["#news-items", "items_per_category", 1], ["#news-per-source", "max_per_source", 1], ["#news-age", "max_age_hours", 1], ["#news-refresh", "refresh_interval_sec", 60]]
  .forEach(([sel, key, mult]) => $(sel).addEventListener("input", (e) => { news[key] = Math.round(+e.target.value * mult); markNews(); }));

function feedStatusHtml(catName, url) {
  const st = (feedStatus.categories?.[catName] || []).find((s) => s.url === url);
  if (!st) return '<span class="muted">not checked yet</span>';
  if (!st.ok) return `<span class="down" title="${esc(st.error || "")}">✕ ${esc((st.error || "failed").slice(0, 40))}</span>`;
  return `<span class="up">✓ ${st.items}</span> <span class="muted">newest ${st.newest_age_h ?? "?"}h</span>`;
}

function renderCategories() {
  const wrap = $("#categories");
  wrap.innerHTML = news.categories.map((c, ci) => `
    <div class="category ${collapsed.has(ci) ? "collapsed" : ""}" data-ci="${ci}">
      <div class="head">
        <button class="small" data-toggle>${collapsed.has(ci) ? "▸" : "▾"}</button>
        <input type="checkbox" data-cat-enabled ${c.enabled !== false ? "checked" : ""} title="Show this category">
        <input class="name" data-cat-name value="${esc(c.name)}">
        <span class="muted">${(headlineData[c.name] || []).length} headlines now</span>
        <span style="flex:1"></span>
        <button class="small" data-up ${ci === 0 ? "disabled" : ""}>↑</button>
        <button class="small" data-down ${ci === news.categories.length - 1 ? "disabled" : ""}>↓</button>
        <button class="small danger" data-cat-del>Delete</button>
      </div>
      <div class="body">
        ${c.feeds.map((f, fi) => `
          <div class="feed" data-fi="${fi}">
            <input type="checkbox" data-feed-enabled ${f.enabled !== false ? "checked" : ""}>
            <input data-feed-name value="${esc(f.name)}" placeholder="Outlet name">
            <input class="url" data-feed-url value="${esc(f.url)}" placeholder="https://…/rss">
            <span class="st">${feedStatusHtml(c.name, f.url)}</span>
            <span><button class="small" data-feed-test>Test</button> <button class="small danger" data-feed-del>✕</button></span>
          </div>`).join("")}
        <button class="small" data-feed-add>+ Add feed</button>
        <div class="preview">${(headlineData[c.name] || []).map((h) => `
          <div class="item"><img src="/api/favicon/${encodeURIComponent(h.domain)}" alt="" onerror="this.style.visibility='hidden'">
            <span>${esc(h.title)}</span><span class="by">${esc(h.domain)} · ${ago(Date.now() / 1000 - h.created_utc)}</span></div>`).join("") || '<div class="muted">No headlines yet. Save, then wait for the next fetch.</div>'}
        </div>
      </div>
    </div>`).join("");

  $$(".category", wrap).forEach((el) => {
    const ci = +el.dataset.ci;
    const c = news.categories[ci];
    const rerender = () => { renderCategories(); markNews(); };
    $("[data-toggle]", el).addEventListener("click", () => { collapsed.has(ci) ? collapsed.delete(ci) : collapsed.add(ci); renderCategories(); });
    $("[data-cat-enabled]", el).addEventListener("change", (e) => { c.enabled = e.target.checked; markNews(); });
    $("[data-cat-name]", el).addEventListener("input", (e) => { c.name = e.target.value; markNews(); });
    $("[data-up]", el).addEventListener("click", () => { news.categories.splice(ci - 1, 0, news.categories.splice(ci, 1)[0]); rerender(); });
    $("[data-down]", el).addEventListener("click", () => { news.categories.splice(ci + 1, 0, news.categories.splice(ci, 1)[0]); rerender(); });
    $("[data-cat-del]", el).addEventListener("click", () => {
      const b = $("[data-cat-del]", el);
      if (b.dataset.armed) { news.categories.splice(ci, 1); rerender(); }
      else { b.dataset.armed = 1; b.textContent = "Really delete?"; setTimeout(() => { b.textContent = "Delete"; delete b.dataset.armed; }, 3000); }
    });
    $("[data-feed-add]", el).addEventListener("click", () => { c.feeds.push({ name: "", url: "", enabled: true }); rerender(); });
    $$(".feed", el).forEach((fe) => {
      const f = c.feeds[+fe.dataset.fi];
      $("[data-feed-enabled]", fe).addEventListener("change", (e) => { f.enabled = e.target.checked; markNews(); });
      $("[data-feed-name]", fe).addEventListener("input", (e) => { f.name = e.target.value; markNews(); });
      $("[data-feed-url]", fe).addEventListener("input", (e) => { f.url = e.target.value.trim(); markNews(); });
      $("[data-feed-del]", fe).addEventListener("click", () => { c.feeds.splice(+fe.dataset.fi, 1); rerender(); });
      $("[data-feed-test]", fe).addEventListener("click", async (e) => {
        const st = $(".st", fe);
        st.textContent = "testing…";
        try {
          const r = await api("/api/headlines/test-feed", { json: { url: f.url, name: f.name } });
          st.innerHTML = r.ok ? `<span class="up">✓ ${r.items}</span> <span class="muted">newest ${r.newest_age_h ?? "?"}h</span>` : `<span class="down">✕ ${esc(r.error || "failed")}</span>`;
          if (r.ok && r.sample?.length) toast(`${r.sample[0].source || r.sample[0].domain}: “${r.sample[0].title}”`);
        } catch (err) { st.innerHTML = `<span class="down">✕ ${esc(err.message)}</span>`; }
      });
    });
  });
}

$("#add-category").addEventListener("click", () => {
  news.categories.push({ name: `New category ${news.categories.length + 1}`, enabled: true, feeds: [] });
  renderCategories(); markNews();
});
$("#news-save").addEventListener("click", async () => {
  try {
    await saveSection("headlines", news, "headlines edited");
    news = clone(CFG.modules.headlines);
    markNews();
    toast("Saved. Fetching headlines now…");
    setTimeout(refreshHeadlines, 12000);
  } catch (e) { toast(e.message, true); }
});
$("#news-revert").addEventListener("click", loadNewsDraft);

async function refreshHeadlines() {
  try {
    [feedStatus, headlineData] = await Promise.all([api("/api/headlines/feed-status"), api("/api/headlines/data")]);
  } catch {}
  if (news) renderCategories();
}

// ---- preview on the display (shared by Ticker + Display tabs) ---------------------
let previewKeep = null;          // async fn that saves the previewed draft
let previewUntil = 0;
async function startPreview(overlay, keepFn) {
  try {
    await api("/api/display/preview", { json: { overlay, ttl_sec: 120 } });
  } catch (e) { toast(`Preview failed: ${e.message}`, true); return; }
  previewKeep = keepFn;
  previewUntil = Date.now() + 120000;
  $("#preview-bar").hidden = false;
  tickPreview();
  toast("Showing on the display for 2 minutes");
}
function tickPreview() {
  const left = Math.max(0, Math.round((previewUntil - Date.now()) / 1000));
  $("#preview-left").textContent = `(reverts in ${left}s)`;
  if (left <= 0) { endPreviewUI(); return; }
  clearTimeout(tickPreview._t);
  tickPreview._t = setTimeout(tickPreview, 1000);
}
function endPreviewUI() { $("#preview-bar").hidden = true; previewKeep = null; clearTimeout(tickPreview._t); }
$("#preview-revert").addEventListener("click", async () => {
  try { await api("/api/display/preview/clear", { method: "POST" }); } catch (e) { toast(e.message, true); }
  endPreviewUI(); toast("Display reverted");
});
$("#preview-keep").addEventListener("click", async () => {
  const keep = previewKeep;
  endPreviewUI();
  try { if (keep) await keep(); await api("/api/display/preview/clear", { method: "POST" }); }
  catch (e) { toast(e.message, true); }
});

$("#ticker-preview").addEventListener("click", () => {
  ticker.refresh_interval_sec = +$("#fin-refresh").value || 60;
  startPreview({ modules: { finance: clone(ticker) } }, () => $("#ticker-save").click());
});

// ---- display tab ------------------------------------------------------------------
const PANEL_LABELS = {
  calendar: "Calendar (left only)", client_calls_upcoming: "Client calls: upcoming", client_calls_past: "Client calls: past",
  headlines: "Headlines", weather: "Weather info", weather_full: "Weather info (full width)",
  map: "Weather map", map_full: "Weather map (full width)", blank: "Blank",
};
const WHEN_LABELS = { always: "always", calendar_has_events: "when calendar has events", no_events: "when calendar is empty",
  weather_has_alert: "when a weather alert is active" };
let disp = null;
let displayPanels = Object.keys(PANEL_LABELS);

function loadDispDraft() {
  disp = { cycle: clone(CFG.modules.layout_cycle || { left: [], right: [] }),
           display: clone(CFG.system.display || {}), night: clone(CFG.system.night_mode || {}) };
  $("#d-bright").value = disp.display.brightness ?? 100;
  $("#d-night").checked = !!disp.night.enabled;
  $("#d-night-start").value = disp.night.start_time || "22:00";
  $("#d-night-end").value = disp.night.end_time || "06:00";
  $("#d-night-bright").value = disp.night.brightness ?? 40;
  $("#d-fps").value = String(disp.display.fps ?? 60);
  for (const k of ["gpio_slowdown", "pwm_bits", "pwm_lsb_nanoseconds", "pwm_dither_bits", "limit_refresh_rate_hz"]) $(`#t-${k}`).value = disp.display[k] ?? "";
  $("#t-show_refresh_rate").checked = !!disp.display.show_refresh_rate;
  syncRangeLabels();
  renderCycles();
  markDisp();
}
function syncRangeLabels() { $("#d-bright-v").textContent = $("#d-bright").value; $("#d-night-bright-v").textContent = $("#d-night-bright").value; }
function dispDirty() {
  return JSON.stringify(disp.cycle) !== JSON.stringify(CFG.modules.layout_cycle || { left: [], right: [] })
    || JSON.stringify(disp.display) !== JSON.stringify(CFG.system.display || {})
    || JSON.stringify(disp.night) !== JSON.stringify(CFG.system.night_mode || {});
}
function markDisp() {
  const d = dispDirty();
  $("#disp-dirty").hidden = !d;
  ["#disp-save", "#disp-revert", "#disp-preview"].forEach((s) => ($(s).disabled = !d));
}
[["#d-bright", () => (disp.display.brightness = +$("#d-bright").value)],
 ["#d-night", () => (disp.night.enabled = $("#d-night").checked)],
 ["#d-night-start", () => (disp.night.start_time = $("#d-night-start").value)],
 ["#d-night-end", () => (disp.night.end_time = $("#d-night-end").value)],
 ["#d-night-bright", () => (disp.night.brightness = +$("#d-night-bright").value)],
 ["#d-fps", () => (disp.display.fps = +$("#d-fps").value)],
 ["#t-show_refresh_rate", () => (disp.display.show_refresh_rate = $("#t-show_refresh_rate").checked)],
 ...["gpio_slowdown", "pwm_bits", "pwm_lsb_nanoseconds", "pwm_dither_bits", "limit_refresh_rate_hz"]
   .map((k) => [`#t-${k}`, () => (disp.display[k] = +$(`#t-${k}`).value)]),
].forEach(([sel, fn]) => $(sel).addEventListener("input", () => { fn(); syncRangeLabels(); markDisp(); }));

function renderCycles() {
  for (const side of ["left", "right"]) {
    const list = disp.cycle[side] || (disp.cycle[side] = []);
    const names = [...new Set([...displayPanels, ...list.map((e) => e.panel)])];
    $(`#cycle-${side}`).innerHTML = list.map((e, i) => {
      const extra = Object.entries(e).filter(([k]) => !["panel", "when", "dwell_sec", "track", "weather_screen"].includes(k)).map(([k, v]) => `${k}=${v}`).join(" ");
      const known = displayPanels.includes(e.panel);
      const tracks = (CFG.modules.weather_maps || {}).tracks || {};
      let sub = "";
      if (e.panel === "map" || e.panel === "map_full") {
        sub = `<select data-k="track">${Object.entries(tracks).map(([id, t]) => `<option value="${esc(id)}" ${id === e.track ? "selected" : ""}>${esc(t.label || id)}${(t.size === "full") !== (e.panel === "map_full") ? " (size mismatch)" : ""}</option>`).join("")}</select>`;
      } else if (e.panel === "weather" || e.panel === "weather_full") {
        sub = `<select data-k="weather_screen">${["current", "hourly", "forecast", "warning"].map((w) => `<option ${w === (e.weather_screen || "current") ? "selected" : ""}>${w}</option>`).join("")}</select>`;
      }
      return `<div class="cycle-row" data-i="${i}">
        <select data-k="panel">${names.map((n) => `<option value="${esc(n)}" ${n === e.panel ? "selected" : ""}>${esc(PANEL_LABELS[n] || n)}</option>`).join("")}</select>
        ${sub}
        <select data-k="when">${Object.entries(WHEN_LABELS).map(([k, l]) => `<option value="${k}" ${k === (e.when || "always") ? "selected" : ""}>${l}</option>`).join("")}</select>
        <input class="dwell" type="number" min="3" data-k="dwell_sec" value="${e.dwell_sec ?? ""}" placeholder="sec" title="Seconds on screen">s
        <button class="small" data-up ${i === 0 ? "disabled" : ""}>↑</button><button class="small" data-down ${i === list.length - 1 ? "disabled" : ""}>↓</button>
        <button class="small danger" data-del>✕</button>
        ${extra ? `<span class="extra">${esc(extra)}</span>` : ""}${known ? "" : '<span class="warn">not available on this display yet: skipped</span>'}
      </div>`;
    }).join("") || '<div class="muted">Nothing in rotation</div>';
    $$(`#cycle-${side} .cycle-row`).forEach((row) => {
      const i = +row.dataset.i;
      $$("[data-k]", row).forEach((el) => el.addEventListener("input", () => {
        const k = el.dataset.k;
        list[i][k] = k === "dwell_sec" ? (el.value === "" ? undefined : +el.value) : el.value;
        if (list[i][k] === undefined) delete list[i][k];
        if (k === "panel") {
          const p = list[i].panel;
          if ((p === "map" || p === "map_full") && !list[i].track) list[i].track = Object.keys((CFG.modules.weather_maps || {}).tracks || {})[0];
          if ((p === "weather" || p === "weather_full") && !list[i].weather_screen) list[i].weather_screen = "current";
          if (!p.startsWith("map")) delete list[i].track;
          if (!p.startsWith("weather")) delete list[i].weather_screen;
          renderCycles();
        }
        markDisp();
      }));
      $("[data-up]", row).addEventListener("click", () => { list.splice(i - 1, 0, list.splice(i, 1)[0]); renderCycles(); markDisp(); });
      $("[data-down]", row).addEventListener("click", () => { list.splice(i + 1, 0, list.splice(i, 1)[0]); renderCycles(); markDisp(); });
      $("[data-del]", row).addEventListener("click", () => { list.splice(i, 1); renderCycles(); markDisp(); });
    });
  }
}
$$("[data-add-cycle]").forEach((b) => b.addEventListener("click", () => {
  disp.cycle[b.dataset.addCycle].push({ panel: "headlines", when: "always", dwell_sec: 60 });
  renderCycles(); markDisp();
}));

async function saveDisp() {
  const next = clone(CFG);
  next.modules.layout_cycle = clone(disp.cycle);
  next.system.display = clone(disp.display);
  next.system.night_mode = clone(disp.night);
  const res = await api("/api/config", { method: "PUT", json: { config: next, reason: "display settings edited" } });
  CFG = res.config;
  updateBanner(res.display_in_sync);
  loadDispDraft();
  toast("Saved");
}
$("#disp-save").addEventListener("click", () => saveDisp().catch((e) => toast(e.message, true)));
$("#disp-revert").addEventListener("click", loadDispDraft);
$("#disp-preview").addEventListener("click", () => startPreview(
  { modules: { layout_cycle: clone(disp.cycle) }, system: { display: clone(disp.display), night_mode: clone(disp.night) } },
  saveDisp));

$("#ov-show").addEventListener("click", async () => {
  const dur = $("#ov-dur").value;
  try {
    await api("/api/display/override", { json: { side: $("#ov-side").value, panel: $("#ov-panel").value, duration_sec: dur ? +dur : null } });
    toast("Showing now");
  } catch (e) { toast(e.message, true); }
});
$("#ov-clear").addEventListener("click", async () => {
  try { await api("/api/display/override/clear", { method: "POST", json: {} }); toast("Back to the normal rotation"); }
  catch (e) { toast(e.message, true); }
});

let mirrorTimer = null;
let lastMirrorUrl = null;
async function mirrorLoop() {
  clearTimeout(mirrorTimer);
  if ($("#tab-display").hidden) return;
  try {
    const r = await fetch(`/api/display/frame.png?scale=2&t=${Date.now()}`);
    if (r.ok) {
      const url = URL.createObjectURL(await r.blob());
      $("#mirror").src = url;
      if (lastMirrorUrl) URL.revokeObjectURL(lastMirrorUrl);
      lastMirrorUrl = url;
      $("#mirror").hidden = false;
      $("#mirror-msg").textContent = "";
    } else {
      $("#mirror").hidden = true;
      $("#mirror-msg").textContent = r.status === 404
        ? "The live view needs the v2 display. The panels are currently driven by v1."
        : "Display unreachable.";
    }
  } catch { $("#mirror-msg").textContent = "Display unreachable."; }
  mirrorTimer = setTimeout(mirrorLoop, 500);
}
async function refreshDisplayStats() {
  if ($("#tab-display").hidden) return;
  try {
    const d = await api("/api/display/status");
    if (!d.reachable) { $("#display-stats").textContent = "Display unreachable"; return; }
    if (d.version !== "v2") { $("#display-stats").textContent = "Running the v1 renderer (no live stats)"; return; }
    const s = d.status;
    $("#display-stats").innerHTML = `${s.fps} fps · draw ${s.draw_ms_avg} ms (p95 ${s.draw_ms_p95}) · brightness ${s.brightness} · `
      + `left: <b>${esc(s.panels.left.panel || "-")}</b> · right: <b>${esc(s.panels.right.panel || "-")}</b>`
      + (s.preview ? ` · <span class="warn">preview active</span>` : "") + (s.recent_errors ? ` · <span class="down">${s.recent_errors} recent errors</span>` : "");
  } catch {}
}
function enterDisplayTab() {
  mirrorLoop();
  refreshDisplayStats();
}

// ---- boot -------------------------------------------------------------------------
window.addEventListener("beforeunload", (e) => {
  if ((ticker && tickerDirty()) || (news && newsDirty()) || (typeof wm !== "undefined" && wm && wmDirty())) { e.preventDefault(); e.returnValue = ""; }
});

(async function boot() {
  CFG = await api("/api/config");
  loadTickerDraft();
  loadNewsDraft();
  loadDispDraft();
  $("#ov-panel").innerHTML = displayPanels.map((n) => `<option value="${n}">${esc(PANEL_LABELS[n] || n)}</option>`).join("");
  setInterval(refreshDisplayStats, 2000);
  let tab = location.hash.slice(1);
  if (!$(`#tab-${tab}`)) {
    tab = "status";
    try { tab = localStorage.getItem("tab") || "status"; } catch {}
  }
  showTab(tab);
  refreshStatus();
  setInterval(refreshStatus, 5000);
  setInterval(() => { if (!$("#tab-ticker").hidden && !tickerDirty()) refreshQuotes(); }, 30000);
})();
