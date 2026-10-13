// Reusable UI pieces.
import { h, ref, computed, onMounted, onBeforeUnmount, watch } from "vue";
import Sortable from "sortablejs";
import { store } from "./core.js";

// Lucide icon rendered from its data (no DOM scanning; works with Vue re-renders).
export const Icon = {
  props: { name: String, size: { type: Number, default: 16 }, stroke: { type: Number, default: 2 } },
  setup(props) {
    return () => {
      const node = window.lucide?.icons?.[props.name];
      if (!node) return h("span", { class: "icon", style: `display:inline-block;width:${props.size}px` });
      const [, attrs, children] = node;
      return h("svg", { ...attrs, width: props.size, height: props.size, "stroke-width": props.stroke, class: "icon" },
        children.map(([t, a]) => h(t, a)));
    };
  },
};

export const Toggle = {
  props: { modelValue: Boolean, disabled: Boolean },
  emits: ["update:modelValue"],
  template: `<span class="toggle" :class="{on: modelValue}" role="switch" :aria-checked="modelValue" tabindex="0"
    :style="disabled ? 'opacity:.4;pointer-events:none' : ''"
    @click="$emit('update:modelValue', !modelValue)" @keydown.space.prevent="$emit('update:modelValue', !modelValue)"></span>`,
};

export const ToggleRow = {
  components: { Toggle },
  props: { modelValue: Boolean, label: String, hint: String },
  emits: ["update:modelValue"],
  template: `<label class="toggle-row" @click.prevent="$emit('update:modelValue', !modelValue)">
    <span class="t"><slot name="label">{{ label }}</slot><small v-if="hint">{{ hint }}</small></span>
    <Toggle :modelValue="modelValue" @click.stop.prevent="$emit('update:modelValue', !modelValue)" /></label>`,
};

export const Seg = {
  components: { Icon },
  props: { modelValue: [String, Number, Boolean], options: Array },   // [{value, label, icon}]
  emits: ["update:modelValue"],
  template: `<div class="seg"><button v-for="o in options" :key="String(o.value)" type="button" :class="{on: o.value === modelValue}"
    @click="$emit('update:modelValue', o.value)"><Icon v-if="o.icon" :name="o.icon" :size="14"/>{{ o.label }}</button></div>`,
};

export const Slider = {
  props: { modelValue: Number, min: { type: Number, default: 0 }, max: { type: Number, default: 1 }, step: { type: Number, default: 0.01 },
    format: Function, unit: { type: String, default: "" } },
  emits: ["update:modelValue"],
  setup(props, { emit }) {
    const pct = computed(() => `${((Number(props.modelValue ?? props.min) - props.min) / (props.max - props.min)) * 100}%`);
    const shown = computed(() => props.format ? props.format(props.modelValue) : `${props.modelValue ?? ""}${props.unit}`);
    const onInput = (e) => emit("update:modelValue", Number(e.target.value));
    return { pct, shown, onInput };
  },
  template: `<div class="slider"><input type="range" :min="min" :max="max" :step="step" :value="modelValue ?? min" :style="{'--p': pct}" @input="onInput">
    <span class="val">{{ shown }}</span></div>`,
};

export const Stepper = {
  props: { modelValue: Number, min: { type: Number, default: 0 }, max: { type: Number, default: 999 }, step: { type: Number, default: 1 }, unit: String },
  emits: ["update:modelValue"],
  setup(props, { emit }) {
    const set = (v) => emit("update:modelValue", Math.min(props.max, Math.max(props.min, Math.round(v / props.step) * props.step)));
    return { set };
  },
  template: `<span class="stepper"><button type="button" @click="set((modelValue ?? min) - step)">−</button>
    <input :value="modelValue" @change="set(Number($event.target.value))"><span class="unit" v-if="unit">{{ unit }}</span>
    <button type="button" @click="set((modelValue ?? min) + step)">+</button></span>`,
};

export const ColorPick = {
  props: { modelValue: String },
  emits: ["update:modelValue"],
  template: `<span class="color"><input type="color" :value="modelValue || '#ffffff'" @input="$emit('update:modelValue', $event.target.value)">
    <span class="mono faint" style="font-size:12px">{{ (modelValue || '').toUpperCase() }}</span></span>`,
};

export const Field = {
  props: { label: String, hint: String },
  template: `<div class="field"><label v-if="label">{{ label }}</label><slot/><div class="hint" v-if="hint">{{ hint }}</div></div>`,
};

// Two-click confirm for destructive actions.
export const ConfirmButton = {
  components: { Icon },
  props: { label: { type: String, default: "Delete" }, confirm: { type: String, default: "Click again to confirm" }, icon: { type: String, default: "Trash2" }, small: Boolean },
  emits: ["confirm"],
  setup(props, { emit }) {
    const armed = ref(false);
    let t;
    const click = () => {
      if (armed.value) { clearTimeout(t); armed.value = false; emit("confirm"); return; }
      armed.value = true;
      t = setTimeout(() => (armed.value = false), 3500);
    };
    return { armed, click };
  },
  template: `<button type="button" class="btn danger" :class="{sm: small, armed}" @click="click"><Icon :name="icon" :size="14"/>{{ armed ? confirm : label }}</button>`,
};

export const Modal = {
  components: { Icon },
  props: { title: String },
  emits: ["close"],
  template: `<div class="modal-back" @click.self="$emit('close')"><div class="modal card">
    <div class="card-head"><h3>{{ title }}</h3><button class="btn ghost icon-only sm" @click="$emit('close')"><Icon name="X"/></button></div>
    <div class="card-body stack"><slot/></div></div></div>`,
};

// v-sortable="{ list, handle, onChange }" — reorders the bound array (Vue owns the DOM).
export const vSortable = {
  mounted(el, binding) {
    el._sortOpts = binding.value;
    el._sortable = Sortable.create(el, {
      animation: 160, handle: binding.value.handle || ".handle", draggable: binding.value.draggable || "> *",
      ghostClass: "sortable-ghost", chosenClass: "sortable-chosen",
      onEnd(evt) {
        const { oldIndex, newIndex, item, from } = evt;
        if (oldIndex === newIndex || oldIndex == null) return;
        // put the node back where Vue expects it, then reorder the data
        from.removeChild(item);
        from.insertBefore(item, from.children[oldIndex] || null);
        const list = el._sortOpts.list;
        list.splice(newIndex, 0, list.splice(oldIndex, 1)[0]);
        el._sortOpts.onChange?.();
      },
    });
  },
  updated(el, binding) { el._sortOpts = binding.value; },
  unmounted(el) { el._sortable?.destroy(); },
};

// Animated thumbnail of a rendered map pack (frames served by the hub).
const players = new Set();
setInterval(() => players.forEach((p) => p()), 125);
export const AnimThumb = {
  props: { track: String, full: Boolean, scale: { type: Number, default: 2 } },
  setup(props) {
    const src = ref("");
    const i = ref(0);
    const pack = computed(() => store.maps?.packs?.[props.track]);
    let acc = 0;
    const tick = () => {
      const pk = pack.value;
      if (!pk || !pk.n) { src.value = ""; return; }
      const fps = Math.min(12, pk.fps || 8);
      acc += 0.125 * fps;
      const hold = Math.round((pk.hold_last_sec || 1.5) * fps);
      while (acc >= 1) { acc -= 1; i.value = (i.value + 1) % (pk.n + hold); }
      src.value = `/api/maps/track/${encodeURIComponent(props.track)}/frame/${Math.min(i.value, pk.n - 1)}.png?scale=${props.scale}&v=${Math.round(pk.generated_at || 0)}`;
    };
    onMounted(() => { players.add(tick); tick(); });
    onBeforeUnmount(() => players.delete(tick));
    watch(() => props.track, () => { i.value = 0; });
    return { src, pack };
  },
  template: `<div class="thumb" :class="{full}"><img v-if="src" :src="src" alt=""><div v-else class="faint" style="font-size:12px">No frames yet</div><slot/></div>`,
};

export const common = { Icon, Toggle, ToggleRow, Seg, Slider, Stepper, ColorPick, Field, ConfirmButton, Modal, AnimThumb };
