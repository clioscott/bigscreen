// The webcam caption's rows (display/modules/webcam_caption.py rows_of): each row a list of pieces, top to bottom in
// its own column here; a piece is a section (each line of the webcam's name its own), the LIVE badge, the highway
// shield, or a separator. Drag to reorder, between rows, and into "Not shown"; separators come from the palette.
// Every piece has a size ("Auto": the name's size for the name, the details' for the rest). Dragging is SortableJS
// (mouse and touch): the DOM is put back where Vue drew it and the data moves.
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from "vue";
import Sortable from "sortablejs";
import { api } from "../core.js";

export const PIECE_SIZES = [{ value: "", label: "Auto" }, { value: "tiny", label: "Tiny" }, { value: "small", label: "Small" },
  { value: "medium", label: "Medium" }, { value: "large", label: "Large" }, { value: "xlarge", label: "X-large" }, { value: "huge", label: "Huge" }];
export const SEP_KINDS = [{ value: "space", label: "Space", glyph: "␣" }, { value: "dot", label: "Dot", glyph: "·" },
  { value: "dash", label: "Dash", glyph: "-" }, { value: "en", label: "En dash", glyph: "–" }, { value: "em", label: "Em dash", glyph: "—" },
  { value: "pipe", label: "Pipe", glyph: "|" }, { value: "slash", label: "Slash", glyph: "/" }];
const MAX_ROWS = 8;
const EARTH = ["place", "time", "date", "offset", "weather", "conditions", "feels", "hilo", "wind", "humidity", "sun", "age"];
const SPACE = ["place", "coords", "orbit", "below", "crew", "mission"];
const ALWAYS = ["live", "credit", "custom", "shield"];

export const CaptionRows = {
  props: { cam: Object, ov: Object, labels: Object, hasText: Function },
  setup(props) {
    const root = ref(null);
    const legacy = ref(null);                       // the rows the wall draws for settings from before rows
    let sortables = [];
    const loadLegacy = async () => {
      if (props.ov.rows) return;
      try { legacy.value = (await api("/api/webcams/caption-rows", { json: { cam: { ...props.cam, overlay: props.ov } } })).rows; } catch { legacy.value = [{ items: [{ k: "live" }, { k: "name" }] }]; }
    };
    const rows = computed(() => props.ov.rows || legacy.value || []);
    // the first change writes the rows into the caption (until then it has none: opening a webcam isn't a change)
    const own = () => {
      if (!props.ov.rows) props.ov.rows = JSON.parse(JSON.stringify(legacy.value || [{ items: [] }]));
      return props.ov.rows;
    };
    const nameLines = computed(() => Math.max(1, String(props.cam.label || "").split("\n").filter((s) => s.trim()).length));
    const available = computed(() => {
      const names = Array.from({ length: Math.min(4, nameLines.value) }, (_, i) => (i ? "name:" + (i + 1) : "name"));
      const kinds = props.cam.space ? SPACE : EARTH.filter((k) => k !== "age" || ["drivebc", "image"].includes(props.cam.source?.type));
      return [...names, ...ALWAYS.slice(0, 1), ...kinds, ...ALWAYS.slice(1)];
    });
    const placed = computed(() => new Set(rows.value.flatMap((r) => r.items.map((i) => i.k))));
    const off = computed(() => available.value.filter((k) => !placed.value.has(k)));
    const label = (k) => {
      if (k === "name") return nameLines.value > 1 ? "Name line 1" : "Name";
      if (k.startsWith("name:")) return "Name line " + k.split(":")[1];
      if (k === "live") return "LIVE";
      if (k === "shield") return "Highway shield";
      if (k === "place" && props.cam.space) return props.labels.over;
      return props.labels[k] || k;
    };
    const sepGlyph = (it) => (SEP_KINDS.find((s) => s.value === it.s) || SEP_KINDS[0]).glyph;
    const setSize = (r, i, v) => { const it = own()[r].items[i]; if (v) it.size = v; else delete it.size; };
    const toggle = (r, i, key) => { const it = own()[r].items[i]; if (it[key]) delete it[key]; else it[key] = true; };
    const setSep = (r, i, v) => { own()[r].items[i].s = v; };
    const addRow = () => { if (rows.value.length < MAX_ROWS) own().push({ items: [] }); };
    const removeRow = (r) => { own().splice(r, 1); };
    const toggleWrap = (r) => { const row = own()[r]; row.wrap = !row.wrap; };
    const remove = (r, i) => { own()[r].items.splice(i, 1); };
    // a drop: from a row (or Not shown, or the palette) to a row (or Not shown)
    const move = (from, oldIndex, to, newIndex, kind, sep) => {
      const rs = own();
      let item;
      if (from === "palette") item = { k: "sep", s: sep, pad: sep !== "space" };
      else if (from === "off") item = { k: kind };
      else item = rs[+from].items.splice(oldIndex, 1)[0];
      if (to === "off" || !item) return;                // out of the rows: not shown (a separator just goes)
      rs[+to].items.splice(newIndex, 0, item);
    };
    const bind = () => {
      sortables.forEach((s) => s.destroy());
      sortables = [];
      if (!root.value) return;
      root.value.querySelectorAll(".cr-list").forEach((el) => {
        const isPalette = el.dataset.row === "palette";
        sortables.push(Sortable.create(el, {
          group: { name: "caption-rows", pull: isPalette ? "clone" : true, put: !isPalette },
          sort: !isPalette, animation: 140, draggable: ".cr-chip", filter: "select,button,input", preventOnFilter: false,
          ghostClass: "sortable-ghost", chosenClass: "sortable-chosen",
          onEnd(evt) {
            const { item, from, to, oldIndex, newIndex, clone, pullMode } = evt;
            const oldAt = evt.oldDraggableIndex ?? oldIndex, newAt = evt.newDraggableIndex ?? newIndex;   // (chips only)
            if (from === to && oldIndex === newIndex) return;
            if (pullMode === "clone") clone.parentNode.replaceChild(item, clone);   // the palette keeps its own
            else {
              to.removeChild(item);                          // back where Vue drew it; the data moves it
              from.insertBefore(item, from.children[oldIndex] || null);
            }
            if (from === to && from.dataset.row === "off") return;
            move(from.dataset.row, oldAt, to.dataset.row, newAt, item.dataset.k, item.dataset.sep);
          },
        }));
      });
    };
    onMounted(async () => { await loadLegacy(); await nextTick(); bind(); });
    watch(() => rows.value.length, async () => { await nextTick(); bind(); });
    watch(() => props.cam.space, loadLegacy);
    onBeforeUnmount(() => sortables.forEach((s) => s.destroy()));
    return { root, rows, off, label, sepGlyph, setSize, toggle, setSep, addRow, removeRow, toggleWrap, remove, PIECE_SIZES, SEP_KINDS, MAX_ROWS };
  },
  template: `<div class="cr" ref="root">
    <div class="cr-palette"><span class="faint">Separators</span>
      <div class="cr-list cr-pal" data-row="palette"><span v-for="s in SEP_KINDS" :key="s.value" class="cr-chip sep" :data-sep="s.value" data-k="sep" :title="s.label + ': drag into a row'">
        <span class="cr-glyph">{{ s.glyph }}</span>{{ s.label }}</span></div></div>
    <div class="cr-rows">
      <div v-for="(row, r) in rows" :key="'r' + r + ':' + rows.length" class="cr-row">
        <div class="cr-head"><b>Row {{ r + 1 }}</b><span class="spacer"></span>
          <button type="button" class="btn sm ghost" :class="{on: row.wrap}" @click="toggleWrap(r)" title="When the row is too wide: continue on the next line (off: its last pieces are left out)">
            <Icon name="WrapText" :size="13"/>{{ row.wrap ? 'Wraps' : 'Drops' }}</button>
          <button type="button" class="btn sm ghost icon-only" @click="removeRow(r)" title="Remove the row (its sections go to Not shown)"><Icon name="X" :size="13"/></button></div>
        <div class="cr-list" :data-row="r">
          <div v-for="(it, i) in row.items" :key="r + '.' + i + '.' + it.k" class="cr-chip" :class="{sep: it.k === 'sep', live: it.k === 'live', blank: it.k !== 'sep' && it.k !== 'live' && !hasText(it.k)}"
            :data-k="it.k" :title="it.k === 'sep' || hasText(it.k) ? 'Drag to move' : 'Nothing to show right now: it drops out (with the separator before it)'">
            <Icon name="GripVertical" :size="13" class="cr-grip"/>
            <template v-if="it.k === 'sep'">
              <select class="cr-sel" :value="it.s" @change="setSep(r, i, $event.target.value)" title="Separator">
                <option v-for="s in SEP_KINDS" :key="s.value" :value="s.value">{{ s.glyph }} {{ s.label }}</option></select>
              <button v-if="it.s !== 'space'" type="button" class="cr-opt" :class="{on: it.pad}" @click="toggle(r, i, 'pad')" title="A space on each side">␣·␣</button>
            </template>
            <span v-else class="cr-name">{{ label(it.k) }}</span>
            <span class="spacer"></span>
            <button v-if="it.k !== 'sep' && it.k !== 'live'" type="button" class="cr-opt" :class="{on: it.spill}" @click="toggle(r, i, 'spill')"
              title="If this row is too full, move this to the start of the next row instead of leaving it out"><Icon name="CornerDownLeft" :size="12"/></button>
            <select class="cr-sel" :value="it.size || ''" @change="setSize(r, i, $event.target.value)" title="Text size">
              <option v-for="s in PIECE_SIZES" :key="s.value" :value="s.value">{{ s.label }}</option></select>
            <button type="button" class="cr-opt" @click="remove(r, i)" :title="it.k === 'sep' ? 'Remove' : 'Not shown'"><Icon name="X" :size="12"/></button>
          </div>
          <div v-if="!row.items.length" class="cr-empty faint">Drag pieces here</div>
        </div>
      </div>
      <button v-if="rows.length < MAX_ROWS" type="button" class="cr-add" @click="addRow"><Icon name="Plus" :size="14"/>Add a row</button>
    </div>
    <div class="cr-offbox"><span class="faint">Not shown</span>
      <div class="cr-list cr-off" data-row="off"><span v-for="k in off" :key="k" class="cr-chip" :class="{live: k === 'live'}" :data-k="k" title="Drag into a row to show it">
        <Icon name="GripVertical" :size="13" class="cr-grip"/>{{ label(k) }}</span>
        <span v-if="!off.length" class="faint" style="font-size:12px">Everything is shown</span></div></div>
  </div>`,
};
