import { computed } from "vue";
import { store } from "../core.js";
import { LiveView } from "./overview.js";

export const DisplaySettings = {
  components: { LiveView },
  setup() {
    const d = computed(() => store.draft.system.display);
    const night = computed(() => (store.draft.system.night_mode = store.draft.system.night_mode || { enabled: false }));
    const live = computed(() => store.display?.status);
    const TIMING = [
      ["gpio_slowdown", "GPIO slowdown", 0, 5, 1], ["pwm_bits", "PWM bits", 1, 11, 1], ["pwm_lsb_nanoseconds", "PWM LSB (ns)", 50, 3000, 10],
      ["pwm_dither_bits", "PWM dither bits", 0, 2, 1], ["limit_refresh_rate_hz", "Refresh limit (Hz, 0 = none)", 0, 500, 10],
    ];
    return { d, night, live, TIMING };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Display</h1><p>Brightness, night mode and frame rate for the LED panels. Changes apply live when saved, or try them first with "Preview on display".</p></div></div>
    <div class="two-col">
      <div class="stack sticky">
        <div class="card"><div class="card-head"><span class="live">LIVE</span><h3></h3><span class="pill" v-if="live">{{ live.fps }} fps</span></div>
          <div class="card-body"><LiveView :scale="2"/></div></div>
      </div>
      <div class="stack">
        <div class="card"><div class="card-head"><Icon name="Sun" :size="15"/><h3>Brightness</h3></div><div class="card-body stack">
          <Field label="Daytime brightness"><Slider v-model="d.brightness" :min="1" :max="100" :step="1" unit="%"/></Field>
        </div></div>
        <div class="card"><div class="card-head"><Icon name="Moon" :size="15"/><h3>Night mode</h3><Toggle v-model="night.enabled"/></div>
          <div class="card-body stack" v-if="night.enabled">
            <div class="fields">
              <Field label="From"><input class="input" type="time" v-model="night.start_time"></Field>
              <Field label="Until"><input class="input" type="time" v-model="night.end_time"></Field>
            </div>
            <Field label="Night brightness"><Slider v-model="night.brightness" :min="1" :max="100" :step="1" unit="%"/></Field>
            <div class="callout" v-if="night.brightness > d.brightness"><Icon name="Info" :size="15"/>Night brightness is higher than daytime brightness.</div>
          </div></div>
        <div class="card"><div class="card-head"><Icon name="Gauge" :size="15"/><h3>Frame rate</h3></div><div class="card-body stack">
          <Seg v-model="d.fps" :options="[30, 45, 60, 90, 120].map(v => ({value: v, label: v + ' fps'}))"/>
          <div class="faint" style="font-size:12.5px">How often the picture is redrawn (scrolling smoothness). The panel refresh itself is set by the timing values below.</div>
        </div></div>
        <div class="card"><div class="card-head"><Icon name="Cpu" :size="15"/><h3>Panel timing</h3><span class="pill warn">advanced</span></div><div class="card-body stack">
          <div class="callout"><Icon name="TriangleAlert" :size="15"/>These came from your flicker testing on this hardware. Changing any of them restarts the display.</div>
          <div class="fields"><Field v-for="[k, l, mn, mx, st] in TIMING" :key="k" :label="l"><Stepper v-model="d[k]" :min="mn" :max="mx" :step="st"/></Field></div>
          <ToggleRow :modelValue="!!d.show_refresh_rate" @update:modelValue="v => d.show_refresh_rate = v" label="Print refresh rate" hint="Debug output from the refresh thread — leave off normally"/>
        </div></div>
      </div>
    </div>
  </div>`,
};
