import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { store, api, fail, toast } from "../core.js";

export const Ticker = {
  setup() {
    const fin = computed(() => store.draft.modules.finance);
    const quotes = ref({});
    const q = ref("");
    const results = ref([]);
    const searching = ref(false);
    const bust = ref(Date.now());
    const logoFor = ref(null);           // ticker being edited in the logo dialog
    const icons = ref([]);
    const domain = ref("");
    let st, poll;

    const loadQuotes = async () => { try { quotes.value = await api("/api/finance/data"); } catch {} };
    onMounted(() => { loadQuotes(); poll = setInterval(loadQuotes, 30000); });
    onBeforeUnmount(() => clearInterval(poll));

    const search = () => {
      clearTimeout(st);
      const term = q.value.trim();
      if (!term) { results.value = []; return; }
      st = setTimeout(async () => {
        searching.value = true;
        try {
          const r = await api(`/api/finance/search?q=${encodeURIComponent(term)}`);
          if (!r.some((x) => x.symbol === term.toUpperCase())) r.push({ symbol: term.toUpperCase(), name: "Add exactly as typed", exchange: "" });
          results.value = r;
        } catch (e) { fail(e); } finally { searching.value = false; }
      }, 250);
    };
    const add = (sym) => {
      results.value = []; q.value = "";
      if (fin.value.symbols.some((t) => t.symbol.toUpperCase() === sym.toUpperCase())) { toast(`${sym} is already on the ticker`); return; }
      fin.value.symbols.push({ symbol: sym, title: sym, width: null, icon: `${sym}.png`, enabled: true });
    };
    const quote = (s) => quotes.value[s] || {};
    const price = (p) => (p == null ? "" : p >= 1000 ? p.toLocaleString(undefined, { maximumFractionDigits: 0 }) : p.toFixed(2));
    const stale = (s) => { const u = quote(s)._updated; return u && Date.now() / 1000 - u > 900; };
    const iconUrl = (t) => `/api/finance/icons/${encodeURIComponent(t.icon || t.symbol + ".png")}?v=${bust.value}`;

    const openLogo = async (t) => {
      logoFor.value = t; domain.value = "";
      try { icons.value = await api("/api/finance/icons"); } catch {}
    };
    const setIcon = (name) => { logoFor.value.icon = name; bust.value = Date.now(); };
    const fromDomain = async () => {
      try {
        const r = await api(`/api/finance/icons/${encodeURIComponent(logoFor.value.symbol + ".png")}/from-domain`, { json: { domain: domain.value } });
        setIcon(r.icon); toast("Logo fetched");
      } catch (e) { fail(e); }
    };
    const upload = async (ev) => {
      const f = ev.target.files[0];
      if (!f) return;
      try {
        const r = await api(`/api/finance/icons/${encodeURIComponent(logoFor.value.symbol + ".png")}`, { method: "POST", body: f });
        setIcon(r.icon); toast("Logo uploaded");
      } catch (e) { fail(e); }
    };
    return { fin, quote, price, stale, iconUrl, q, results, searching, search, add, logoFor, openLogo, icons, domain, fromDomain, upload, setIcon, bust };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Stock ticker</h1><p>The scrolling strip along the top. Drag to reorder (left to right on the display), rename, resize, or change logos.</p></div></div>

    <div class="card">
      <div class="card-head"><Icon name="Search" :size="16"/>
        <div class="rel" style="flex:1; max-width: 520px">
          <input class="input" v-model="q" @input="search" placeholder="Add a ticker — search by symbol or company name" autocomplete="off">
          <div class="dropdown" v-if="results.length">
            <div class="opt" v-for="r in results" :key="r.symbol" @click="add(r.symbol)">
              <b class="mono" style="min-width:96px">{{ r.symbol }}</b><span style="flex:1">{{ r.name }}</span><span class="faint" style="font-size:12px">{{ r.exchange }}</span></div>
          </div>
        </div>
        <Icon v-if="searching" name="LoaderCircle" class="spin" :size="16"/>
        <span class="spacer"></span><span class="faint">{{ fin.symbols.length }} tickers</span>
      </div>
      <div class="list" v-sortable="{ list: fin.symbols }">
        <div class="list-row" v-for="t in fin.symbols" :key="t.symbol" :style="t.enabled === false ? 'opacity:.5' : ''">
          <span class="handle"><Icon name="GripVertical" :size="16"/></span>
          <Toggle :modelValue="t.enabled !== false" @update:modelValue="v => t.enabled = v"/>
          <button class="btn icon-only" style="width:46px; background:#000" title="Change logo" @click="openLogo(t)">
            <img :src="iconUrl(t)" style="max-width:36px; max-height:22px; image-rendering: pixelated" alt="" onerror="this.style.visibility='hidden'"></button>
          <b class="mono" style="width:90px">{{ t.symbol }}</b>
          <div class="field" style="flex:1; min-width:120px"><input class="input" v-model="t.title" placeholder="Label"></div>
          <div class="field" style="width:120px"><input class="input mono" type="number" min="20" max="448" :value="t.width ?? ''" placeholder="auto width"
            @input="t.width = $event.target.value === '' ? null : parseInt($event.target.value, 10)"></div>
          <div style="width:150px; text-align:right" class="mono">
            <template v-if="quote(t.symbol).price != null">{{ price(quote(t.symbol).price) }}
              <span class="pill" :class="quote(t.symbol).change >= 0 ? 'ok' : 'bad'">{{ quote(t.symbol).change >= 0 ? '+' : '' }}{{ (quote(t.symbol).change || 0).toFixed(2) }}%</span>
              <div class="faint" style="font-size:11px" v-if="stale(t.symbol)">stale</div></template>
            <span v-else class="faint">—</span></div>
          <ConfirmButton small label="" confirm="Remove?" @confirm="fin.symbols.splice(fin.symbols.indexOf(t), 1)"/>
        </div>
      </div>
    </div>

    <div class="card pad"><div class="fields">
      <Field label="Refresh quotes every"><Stepper v-model="fin.refresh_interval_sec" :min="30" :max="3600" :step="30" unit="s"/></Field>
      <Field label="Scroll speed (px/s)" hint="Default 30"><Stepper v-model="fin.scroll_px_per_sec" :min="5" :max="120" :step="5"/></Field>
    </div></div>

    <Modal v-if="logoFor" :title="'Logo for ' + logoFor.symbol" @close="logoFor = null">
      <div class="led" style="display:grid; place-items:center; min-height:90px"><img :src="iconUrl(logoFor)" style="height:56px; width:auto; image-rendering:pixelated" alt=""></div>
      <Field label="Fetch from a website"><div class="row nowrap"><input class="input" v-model="domain" placeholder="amd.com"><button class="btn" @click="fromDomain">Fetch</button></div></Field>
      <Field label="Upload an image"><input class="input" type="file" accept="image/*" @change="upload" style="padding-top:5px"></Field>
      <Field label="Or reuse an existing logo"><div class="row" style="max-height:180px; overflow:auto">
        <button v-for="n in icons" :key="n" class="btn icon-only" style="width:44px; background:#000" :title="n" @click="setIcon(n)">
          <img :src="'/api/finance/icons/' + encodeURIComponent(n) + '?v=' + bust" style="max-width:34px; max-height:22px; image-rendering:pixelated" alt=""></button></div></Field>
    </Modal>
  </div>`,
};
