import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick } from "vue";
import { store, api } from "../core.js";

const KEEP = 2500;          // lines kept per pane in the browser

// One live console pane (server or display). Lines arrive via `push`; the pane follows the tail unless
// the user scrolls up or pauses, then offers a "new lines" jump.
const ConsolePane = {
  props: { title: String, subtitle: String, icon: String, pane: Object, tagColors: Object },
  setup(props) {
    const body = ref(null);
    const follow = ref(true);
    const filter = ref("");
    const wrap = ref(false);
    const shown = computed(() => {
      const q = filter.value.trim().toLowerCase();
      const ls = props.pane.lines;
      return q ? ls.filter((l) => l.m.toLowerCase().includes(q) || (l.tag || "").includes(q)) : ls;
    });
    const atBottom = () => { const el = body.value; return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 24; };
    const scrollEnd = () => nextTick(() => { const el = body.value; if (el) el.scrollTop = el.scrollHeight; });
    const onScroll = () => { const b = atBottom(); if (b !== follow.value) follow.value = b; if (b) props.pane.unseen = 0; };
    props.pane.onAppend = (n) => { if (follow.value && !props.pane.paused) scrollEnd(); else props.pane.unseen += n; };
    const jump = () => { follow.value = true; props.pane.unseen = 0; scrollEnd(); };
    const clear = () => { props.pane.lines.splice(0); props.pane.unseen = 0; };
    const time = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });
    onMounted(scrollEnd);
    return { body, follow, filter, wrap, shown, onScroll, jump, clear, time };
  },
  template: `
  <div class="card console">
    <div class="card-head">
      <Icon :name="icon" :size="16" style="color:var(--accent)"/>
      <div style="flex:1; min-width:0"><h3>{{ title }}</h3><div class="faint" style="font-size:12px">{{ subtitle }}</div></div>
      <span v-if="pane.error" class="pill bad"><Icon name="Unplug" :size="12"/>{{ pane.error }}</span>
      <span v-else-if="pane.paused" class="pill warn"><Icon name="Pause" :size="12"/>paused</span>
      <span v-else class="live">LIVE</span>
      <div class="console-filter"><Icon name="Search" :size="13"/><input v-model="filter" placeholder="Filter"></div>
      <button class="btn sm ghost icon-only" :title="wrap ? 'Don\\'t wrap long lines' : 'Wrap long lines'" :class="{on: wrap}" @click="wrap = !wrap"><Icon name="WrapText" :size="14"/></button>
      <button class="btn sm ghost icon-only" :title="pane.paused ? 'Resume' : 'Pause'" @click="pane.paused = !pane.paused"><Icon :name="pane.paused ? 'Play' : 'Pause'" :size="14"/></button>
      <button class="btn sm ghost icon-only" title="Clear" @click="clear"><Icon name="Eraser" :size="14"/></button>
    </div>
    <slot/>
    <div class="console-body" :class="{wrap}" ref="body" @scroll="onScroll">
      <div v-if="!shown.length" class="console-empty">{{ filter ? 'No lines match “' + filter + '”' : 'Waiting for output…' }}</div>
      <div v-for="l in shown" :key="l.n" class="cl" :class="['s-' + l.s, l.level]">
        <span class="ct">{{ time(l.t) }}</span><span v-if="l.tag" class="ctag" :style="{color: tagColors[l.tag] || 'var(--text-2)'}">{{ l.tag }}</span><span class="cm">{{ l.m }}</span>
      </div>
    </div>
    <button v-if="pane.unseen" class="console-jump" @click="jump"><Icon name="ArrowDown" :size="13"/>{{ pane.unseen }} new line{{ pane.unseen === 1 ? '' : 's' }}</button>
  </div>`,
};

// "  maps | rendering …" (job output inside the hub log) and "[display] …" become a coloured tag.
function parse(raw, defaultTag) {
  let m = raw.m, tag = defaultTag;
  let x = /^\s{2}(\w+) \| (.*)$/.exec(m);
  if (x) { tag = x[1]; m = x[2]; }
  else if ((x = /^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (.*)$/.exec(m))) m = x[2];      // hub's own timestamp
  if ((x = /^\[(\w+)\] (.*)$/.exec(m))) { tag = x[1]; m = x[2]; }
  if (!x && (x = /^(\w+): (ok|FAILED) in /.exec(m))) tag = x[1];
  const level = raw.s === "err" || /\b(FAILED|Traceback|Error|Exception|failed)\b/.test(m) ? "lv-bad"
    : /\b(WARN|warning|stale|timed out)\b/i.test(m) ? "lv-warn"
    : /\bok in\b|\bapplied\b|\bup\b —/.test(m) ? "lv-ok" : "";
  return { ...raw, m, tag, level };
}

export const Consoles = {
  components: { ConsolePane },
  setup() {
    const panes = reactive({
      hub: { lines: [], cursor: 0, boot: null, paused: false, unseen: 0, error: "", onAppend: null },
      display: { lines: [], cursor: 0, boot: null, paused: false, unseen: 0, error: "", onAppend: null },
    });
    const running = ref([]);
    const now = ref(Date.now() / 1000);
    const TAGS = { finance: "#34d399", news: "#fbbf24", calendar: "#60a5fa", weather: "#22d3ee", maps: "#a78bfa",
      display: "#f472b6", hub: "#7c8cff" };
    const take = (p, res, tag) => {
      if (!res || res.error) { p.error = res?.error || "no answer"; return; }
      p.error = "";
      if (p.boot !== null && res.boot !== p.boot) p.lines.push(parse({ n: `r${res.boot}`, t: Date.now() / 1000, s: "sys", m: "——— restarted ———" }, ""));
      p.boot = res.boot;
      p.cursor = res.seq;
      if (!res.lines.length || p.paused) return;
      p.lines.push(...res.lines.map((l) => parse(l, tag)));
      if (p.lines.length > KEEP) p.lines.splice(0, p.lines.length - KEEP);
      p.onAppend?.(res.lines.length);
    };
    let timer, alive = true;
    const poll = async () => {
      try {
        // while paused keep the cursor where it was, so nothing is lost when resuming
        const r = await api(`/api/logs?hub=${panes.hub.cursor}&display=${panes.display.cursor}`);
        take(panes.hub, r.hub, "");
        take(panes.display, r.display, "display");
        running.value = r.running || [];
        now.value = r.now;
      } catch (e) { panes.hub.error = "hub unreachable"; }
      if (alive) timer = setTimeout(poll, document.hidden ? 4000 : 1000);
    };
    onMounted(poll);
    onBeforeUnmount(() => { alive = false; clearTimeout(timer); });

    const pct = (j) => {
      if (j.total) return Math.min(100, Math.round((100 * (j.done || 0)) / j.total));
      if (j.duration_last && j.started) return Math.min(95, Math.round((100 * (now.value - j.started)) / Math.max(1, j.duration_last)));
      return null;
    };
    const disp = computed(() => store.display?.status);
    const dispSub = computed(() => {
      const d = disp.value;
      if (!d) return "Pi · bigscreen (192.168.1.100) · bigscreen-display";
      const p = d.panels || {};
      return `Pi · bigscreen-display · ${d.fps} fps · left: ${p.left?.panel || "—"} · right: ${p.right?.panel || "—"}`;
    });
    return { panes, running, now, pct, TAGS, dispSub, store };
  },
  template: `<div class="page consoles-page">
    <div class="page-head"><div class="grow"><h1>Live consoles</h1>
      <p>What the server and the display are doing right now. Job output streams in as it happens; long jobs show their progress.</p></div></div>

    <ConsolePane title="Server" icon="Server" :pane="panes.hub" :tagColors="TAGS"
      :subtitle="'MAGI-server · bigscreen-hub · config r' + (store.saved?.revision ?? '?')">
      <div class="tasks" v-if="running.length">
        <div class="task" v-for="j in running" :key="j.name">
          <div class="row nowrap" style="gap:8px">
            <Icon name="LoaderCircle" :size="14" class="spin" :style="{color: TAGS[j.name]}"/>
            <b>{{ j.label }}</b>
            <span class="faint ellipsis" style="flex:1">{{ j.step }}<template v-if="j.total"> · {{ j.done || 0 }} of {{ j.total }}</template></span>
            <span class="mono faint">{{ Math.max(0, Math.round(now - j.started)) }}s<template v-if="j.duration_last"> / ~{{ Math.round(j.duration_last) }}s</template></span>
          </div>
          <div class="bar" :class="{indeterminate: pct(j) === null}"><span :style="{width: (pct(j) ?? 30) + '%', background: TAGS[j.name]}"></span></div>
        </div>
      </div>
    </ConsolePane>

    <ConsolePane title="Display" icon="Monitor" :pane="panes.display" :tagColors="TAGS" :subtitle="dispSub"/>
  </div>`,
};
