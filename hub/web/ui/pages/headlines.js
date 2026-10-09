import { ref, computed, onMounted } from "vue";
import { store, api, fail, toast, ago } from "../core.js";

export const Headlines = {
  setup() {
    const h = computed(() => store.draft.modules.headlines);
    const status = ref({});
    const data = ref({});
    const open = ref(new Set());
    const testing = ref({});
    const load = async () => {
      try { [status.value, data.value] = await Promise.all([api("/api/headlines/feed-status"), api("/api/headlines/data")]); } catch {}
    };
    onMounted(load);
    const feedStatus = (cat, url) => (status.value.categories?.[cat] || []).find((s) => s.url === url);
    const toggleOpen = (name) => { const s = new Set(open.value); s.has(name) ? s.delete(name) : s.add(name); open.value = s; };
    const test = async (cat, f) => {
      testing.value = { ...testing.value, [f.url]: true };
      try {
        const r = await api("/api/headlines/test-feed", { json: { url: f.url, name: f.name } });
        const cats = { ...(status.value.categories || {}) };
        cats[cat] = [...(cats[cat] || []).filter((s) => s.url !== f.url), r];
        status.value = { ...status.value, categories: cats };
        toast(r.ok ? `${r.items} items — “${r.sample?.[0]?.title || ""}”` : `Failed: ${r.error}`, r.ok ? "ok" : "bad");
      } catch (e) { fail(e); } finally { testing.value = { ...testing.value, [f.url]: false }; }
    };
    const addCategory = () => {
      const name = `New category ${h.value.categories.length + 1}`;
      h.value.categories.push({ name, enabled: true, feeds: [] });
      toggleOpen(name);
    };
    const speed = computed({
      get: () => h.value.scroll_px_per_sec ?? (h.value.animation?.speed_pixels_per_frame ?? 1) * 1.5,
      set: (v) => { h.value.scroll_px_per_sec = v; },
    });
    // rough on-screen estimate: ~45 px per headline, 104 px visible, 8 s of holds per category
    const passMinutes = computed(() => {
      const cats = h.value.categories.filter((c) => c.enabled !== false);
      const items = cats.map((c) => Math.min((data.value[c.name] || []).length || h.value.items_per_category || 12, h.value.items_per_category || 12));
      const secs = items.reduce((t, n) => t + Math.max(0, n * 45 - 104) / Math.max(0.2, speed.value) + 8, 0);
      return Math.round(secs / 60);
    });
    const favicon = (d) => `/api/favicon/${encodeURIComponent(d)}`;
    return { h, feedStatus, open, toggleOpen, test, testing, addCategory, data, favicon, ago, speed, passMinutes, load };
  },
  template: `<div class="page">
    <div class="page-head"><div class="grow"><h1>Headlines</h1><p>Each category is one page on the display, mixing headlines from all its outlets (newest first, a few per outlet).</p></div>
      <button class="btn" @click="addCategory"><Icon name="Plus" :size="15"/>New category</button></div>

    <div class="card pad"><div class="fields">
      <Field label="Headlines per category"><Stepper v-model="h.items_per_category" :min="3" :max="40"/></Field>
      <Field label="Max per outlet" hint="Keeps each page mixed"><Stepper v-model="h.max_per_source" :min="1" :max="20"/></Field>
      <Field label="Ignore items older than"><Stepper v-model="h.max_age_hours" :min="1" :max="168" unit="h"/></Field>
      <Field label="Fetch every"><Stepper :modelValue="Math.round((h.refresh_interval_sec || 900) / 60)" @update:modelValue="v => h.refresh_interval_sec = v * 60" :min="5" :max="240" :step="5" unit="min"/></Field>
      <Field label="Scroll speed" :hint="'A full pass through every category takes about ' + passMinutes + ' min'">
        <Slider v-model="speed" :min="0.5" :max="20" :step="0.5" unit=" px/s"/></Field>
    </div></div>

    <div class="stack" v-sortable="{ list: h.categories, handle: '.handle' }">
      <div class="card" v-for="(c, ci) in h.categories" :key="ci">
        <div class="card-head">
          <span class="handle"><Icon name="GripVertical" :size="16"/></span>
          <Toggle :modelValue="c.enabled !== false" @update:modelValue="v => c.enabled = v"/>
          <input class="input" style="max-width: 280px; font-weight: 600" v-model="c.name">
          <span class="pill">{{ (data[c.name] || []).length }} now</span>
          <span class="pill">{{ c.feeds.length }} outlets</span>
          <span class="spacer"></span>
          <button class="btn sm ghost" @click="toggleOpen(c.name)"><Icon :name="open.has(c.name) ? 'ChevronDown' : 'ChevronRight'" :size="15"/>{{ open.has(c.name) ? 'Collapse' : 'Edit' }}</button>
        </div>
        <div v-if="open.has(c.name)">
          <div class="list">
            <div class="list-row" v-for="(f, i) in c.feeds" :key="i">
              <Toggle :modelValue="f.enabled !== false" @update:modelValue="v => f.enabled = v"/>
              <input class="input" style="width: 200px" v-model="f.name" placeholder="Outlet">
              <input class="input mono" style="flex:1; min-width: 200px" v-model.trim="f.url" placeholder="https://…/rss">
              <span style="width: 170px; font-size: 12px">
                <template v-if="feedStatus(c.name, f.url)">
                  <span v-if="feedStatus(c.name, f.url).ok" class="pill ok"><Icon name="Check" :size="12"/>{{ feedStatus(c.name, f.url).items }} · {{ feedStatus(c.name, f.url).newest_age_h }}h</span>
                  <span v-else class="pill bad" :title="feedStatus(c.name, f.url).error"><Icon name="X" :size="12"/>{{ (feedStatus(c.name, f.url).error || 'failed').slice(0, 22) }}</span>
                </template><span v-else class="faint">not checked</span></span>
              <button class="btn sm" :disabled="testing[f.url]" @click="test(c.name, f)"><Icon :name="testing[f.url] ? 'LoaderCircle' : 'Zap'" :size="13" :class="{spin: testing[f.url]}"/>Test</button>
              <button class="btn sm ghost icon-only" @click="c.feeds.splice(i, 1)"><Icon name="Trash2" :size="14"/></button>
            </div>
          </div>
          <div class="card-body row">
            <button class="btn sm" @click="c.feeds.push({name: '', url: '', enabled: true})"><Icon name="Plus" :size="14"/>Add outlet</button>
            <span class="spacer"></span>
            <ConfirmButton small label="Delete category" @confirm="h.categories.splice(h.categories.indexOf(c), 1)"/>
          </div>
          <div class="card-body" style="border-top: 1px solid var(--line)">
            <div class="section-title" style="margin-bottom:8px"><Icon name="Eye" :size="13"/>On the display now</div>
            <div class="stack" style="gap:6px">
              <div v-for="it in (data[c.name] || [])" :key="it.title" class="row nowrap" style="align-items:flex-start">
                <img :src="favicon(it.domain)" width="16" height="16" style="image-rendering:pixelated; margin-top:2px" onerror="this.style.visibility='hidden'" alt="">
                <span style="flex:1">{{ it.title }}</span><span class="faint" style="font-size:12px; white-space:nowrap">{{ it.domain }} · {{ ago(Date.now()/1000 - it.created_utc) }}</span></div>
              <div v-if="!(data[c.name] || []).length" class="faint">Nothing yet — save, then wait for the next fetch.</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>`,
};
