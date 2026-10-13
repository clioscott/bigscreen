// What each side of the wall is playing, with the rotation controls (previous / next / hold / stop preview).
import { computed } from "vue";
import { store, PANEL_TYPES, entryTitle, rotationAction, dur } from "./core.js";

const SIDES = ["left", "right"];

function sideState(side) {
  const p = store.display?.status?.panels?.[side];
  if (!p) return null;
  const pl = p.playing;
  const e = pl?.entry;
  const pv = p.preview;
  let source = "In rotation";
  if (pv) source = pv.hold ? "Held" + (pv.remaining_sec != null ? ` · ${dur(pv.remaining_sec)} left` : "") : "Preview · plays once";
  else if (pl?.source === "priority") source = "Jumped the queue";
  let progress = null, pct = 0;
  if (pl) {
    if (pl.need) {
      progress = `pass ${Math.min(pl.passes + 1, pl.need)} of ${pl.need} · ${dur(pl.elapsed)}`;
      pct = Math.min(100, (100 * pl.passes) / pl.need);
    } else {
      progress = `${dur(pl.elapsed)} of ${dur(pl.limit)}`;
      pct = Math.min(100, (100 * pl.elapsed) / (pl.limit || 1));
    }
  }
  // what comes after: the next entry whose rules hold right now
  let next = null;
  const cyc = p.cycle || [], el = p.eligible || [];
  if (cyc.length) {
    const from = pv?.resumes ? -1 : p.pos;
    if (pv?.resumes) next = pv.resumes;
    else if (p.pending?.length) next = cyc[p.pending[0]];
    else for (let s = 1; s <= cyc.length; s++) {
      const i = (from + s) % cyc.length;
      if (el[i] !== false && cyc[i].enabled !== false) { next = cyc[i]; break; }
    }
  }
  return { side, entry: e, icon: PANEL_TYPES[e?.panel]?.icon || "Square", title: e ? entryTitle(e) : "Nothing (no screen's rules hold)",
    source, preview: !!pv, hold: !!pv?.hold, progress, pct, next: next ? entryTitle(next) : null, covered: p.visible === false && !!e };
}

const Controls = {
  props: { s: Object, small: Boolean },
  setup() { return { rotationAction }; },
  template: `<div class="row nowrap" style="gap:4px">
    <button class="btn sm ghost icon-only" title="Previous screen" @click="rotationAction('prev', s.side)"><Icon name="SkipBack" :size="14"/></button>
    <button class="btn sm ghost icon-only" :title="s.hold ? 'Release: let it finish and carry on' : 'Hold this screen on the wall'"
      @click="rotationAction(s.hold ? 'release' : 'hold', s.side)"><Icon :name="s.hold ? 'Play' : 'Pause'" :size="14"/></button>
    <button class="btn sm ghost icon-only" title="Next screen" @click="rotationAction('next', s.side)"><Icon name="SkipForward" :size="14"/></button>
    <button v-if="s.preview" class="btn sm" title="End the preview; the rotation carries on where it was" @click="rotationAction('stop', s.side)">
      <Icon name="Square" :size="13"/>{{ small ? 'Stop' : 'Back to rotation' }}</button>
  </div>`,
};

/** A card: both sides, what's playing, progress, what's next, controls. */
export const NowPlaying = {
  components: { Controls },
  setup() {
    const sides = computed(() => SIDES.map(sideState).filter(Boolean));
    return { sides };
  },
  template: `<div class="card now-playing" v-if="sides.length">
    <div class="np-side" v-for="s in sides" :key="s.side">
      <div class="np-head"><Icon :name="s.side === 'left' ? 'PanelLeft' : 'PanelRight'" :size="14"/>{{ s.side === 'left' ? 'Left' : 'Right' }}
        <span class="pill" :class="{accent: s.preview, warn: s.hold}">{{ s.source }}</span></div>
      <div class="row nowrap" style="gap:10px">
        <span class="kind"><Icon :name="s.icon" :size="18"/></span>
        <div style="flex:1; min-width:0"><b class="ellipsis" style="display:block">{{ s.title }}</b>
          <span class="faint" style="font-size:12px">{{ s.progress }}<template v-if="s.covered"> · paused (other side has the wall)</template></span></div>
        <Controls :s="s"/>
      </div>
      <div class="bar" style="margin-top:8px"><span :style="{width: s.pct + '%'}"></span></div>
      <div class="faint" style="font-size:12px; margin-top:6px" v-if="s.next">{{ s.preview && !s.hold ? 'Then back to' : 'Up next' }}: {{ s.next }}</div>
    </div>
  </div>`,
};

/** Floating bar, on every page, while a preview or hold is on the wall. */
export const PlayingBar = {
  components: { Controls },
  setup() {
    const sides = computed(() => SIDES.map(sideState).filter((s) => s && s.preview));
    return { sides, store };
  },
  template: `<div class="playing-bar" v-if="sides.length" :class="{lower: store.preview}">
    <div class="row nowrap" v-for="s in sides" :key="s.side" style="gap:10px">
      <Icon :name="s.hold ? 'Pause' : 'Eye'" :size="15"/>
      <span class="ellipsis" style="max-width:340px"><b>{{ s.title }}</b> <span class="faint">· {{ s.side }} · {{ s.source }}</span></span>
      <Controls :s="s" small/>
    </div>
  </div>`,
};
