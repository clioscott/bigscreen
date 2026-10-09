# BigScreen v2 — State-of-the-world research (2026-10-09)

## Machines (note: IPs are the reverse of what was assumed)

| Role | Hostname | LAN IP | Tailscale | OS | Code dir |
|---|---|---|---|---|---|
| Display client (Pi 4, DietPi) | `bigscreen` | **192.168.1.100** (eth0; wlan0 also up as .23) | 100.93.199.23 | Debian 12 aarch64, Python 3.11 | `~/bigscreen-calendar` |
| Data server (mini PC) | `MAGI-server` | **192.168.1.111** | 100.109.137.66 (`server`) | Debian 13 amd64, Python 3.13 | `~/bigscreen-server` |
| Dev PC (Windows) | `magi-miyuki` | — | 100.79.204.91 | Win 11 | `bigscreen.calendar` (stale) |

Both reachable via key-based `ssh scott@<ip>`. Pi user has passwordless sudo.

## How v1 works today

- **Server**: `bigscreen-server.service` runs `server/main.py`. Fetchers (finance = subprocess, calendar = subprocess,
  headlines = thread, weather = thread) put results on a multiprocessing queue; main writes `data/*.json` and
  `server/sync.py` rsyncs each file over Tailscale SSH into the Pi's **`/dev/shm/bigscreen`** (tmpfs).
- **Radar/satellite**: server cron runs `scripts/build_radar_anim.py --push` every 6/10 min, building raw RGB
  "frame packs" and rsyncing them to the Pi at `~/bigscreen-calendar/data/radar_anim` (**SD card**, ~180 MB).
- **Pi**: `bigscreen-client.service` (root, nice -10) runs `src/main.py`. `DataLoader` polls tmpfs JSON by mtime.
  An `OverrideHTTPServer` on :8765 accepts panel overrides/configure commands.
- **Control panel**: `bigscreen-control.service` = Flask `server/webui.py` (4.3k lines) on server :8766, proxies to Pi :8765.
  Plus `scripts/control_panel.py` (3k lines) and three Vite/React "weather-panel-simulator" apps on Windows (~250 MB).
- **No version control anywhere.** Windows copy is *behind* both machines (missing `webui.py`, `fetchers/weather.py`,
  `sync.py`; ~25 Pi modules differ). Live machines are the source of truth → snapshot in `_snapshot/`.
- **Two divergent `app-config.json`** (server vs Pi). Display settings actually used are the Pi's:
  `gpio_slowdown 3, pwm_bits 8, pwm_lsb_ns 100, dither 1, limit_refresh 120, brightness 85, show_refresh_rate true`.
  The server copy (which the web UI edits) says `2 / 7 / 70 / 0` — edits there don't reach the panel settings.

## Broken right now

1. **Headlines frozen since 2026-05-28.** Fetcher uses `reddit.com/r/<sub>/hot.json` → now **HTTP 403** for all 15 subs.
   `reddit.com/r/<sub>/.rss` (Atom) still returns **200** with 25 entries incl. title + external `[link]` URL
   (domain/favicon logic carries over). Config already says `source_type: reddit_rss`; code never implemented it.
2. **Finance frozen since 2026-08-26.** The finance worker (one long-lived process calling yfinance every 60 s,
   up 67 days) is at **8.7 GB RSS**; logs `Crypto fetch error: can't start new thread`. Server swap is 15/15 GB full.
   Restarting the service is a stopgap; fix = run each fetch in a short-lived process (or fresh session) with a timeout.
3. **No staleness detection** — nothing surfaced that two data feeds were dead for weeks/months.

### Reddit policy timeline (researched 2026-10-09) — Reddit is not a viable long-term headline source

- **Nov 2025:** Responsible Builder Policy — new OAuth/Data API access needs manual approval; reports say personal
  scripts are routinely rejected.
- **~May 28–30, 2026:** unauthenticated `.json` endpoints return 403 (matches our headlines freezing on May 28).
- **Sep 30, 2026 — official r/modnews post** ("Continuing our infrastructure updates", read firsthand via RSS):
  *"we'll stop supporting RSS feeds on November 13, 2026 … If you use RSS for feeds outside of a community you
  moderate, there is no replacement."* Old Reddit limited to logged-in mods + users active there in last 6 months.
- **Jan 12, 2027:** unregistered API apps lose access. **March 2027:** public Data API ends (commercial deals only).
- RSS also already rate-limits (got HTTP 429 after 3 requests from the server).

⇒ Switching to RSS buys ~5 weeks. v2 headlines should come from non-Reddit sources (publisher RSS, Google News
topic RSS, etc.) behind a pluggable "headline source" interface.
Configured subs: canada, onguardforthee, britishcolumbia, vancouver, canadapolitics, politics, worldnews, economics,
business, technology, BoycottUnitedStates, 50501, StockMarket, nottheonion, notthebeaverton.

### Verified replacement news feeds (tested from server 2026-10-09)

All return valid RSS/Atom with fresh items unless noted. Mixed per-category list, favicon per item from link domain.

| Category | Feeds |
|---|---|
| Canada News | CBC `cbc.ca/webfeed/rss/rss-canada`, Globe `theglobeandmail.com/arc/outboundfeeds/rss/category/canada/`, Global `globalnews.ca/canada/feed/`, Star `thestar.com/search/?f=rss&t=article&c=news/canada*&l=50&s=start_time&sd=desc` |
| Local News | CBC BC `…/rss-canada-britishcolumbia`, Vancouver Sun `vancouversun.com/feed`, Daily Hive `dailyhive.com/feed/vancouver`, Global BC `globalnews.ca/bc/feed/`, Globe BC (slow: newest 24h) |
| World News | BBC `feeds.bbci.co.uk/news/world/rss.xml`, Guardian `theguardian.com/world/rss`, CBC World, Al Jazeera `aljazeera.com/xml/rss/all.xml`, Globe World, NYT `rss.nytimes.com/services/xml/rss/nyt/World.xml` |
| US News | Politico `rss.politico.com/politics-news.xml`, MS NOW `ms.now/rss`, The Atlantic `theatlantic.com/feed/channel/politics/`, NYT Politics, WaPo `feeds.washingtonpost.com/rss/politics`, NPR `feeds.npr.org/1014/rss.xml`, Guardian US politics, New Yorker `newyorker.com/feed/news`; **CNN native RSS is dead (newest item 2+ yrs old)** → use Google News `news.google.com/rss/search?q=site:cnn.com+when:1d&hl=en-CA&gl=CA&ceid=CA:en` (links are news.google.com redirects; take favicon domain from `<source url=…>`) |
| Business & Economics | Globe Business, CBC Business, BBC Business, Guardian Business, NYT Business |
| Technology | Ars `feeds.arstechnica.com/arstechnica/index`, The Verge `theverge.com/rss/index.xml`, TechCrunch `techcrunch.com/feed/`, 404 Media `404media.co/rss/`, HN `hnrss.org/frontpage?points=150` |
| Satire | The Beaverton `thebeaverton.com/feed/` (**always gzip-encoded** — client must decompress), The Onion `theonion.com/feed/`, Borowitz Report `borowitzreport.com/feed` (moved to Substack), The Shovel (slow, 2 s) |

Dropped per user: equivalents of r/BoycottUnitedStates and r/50501. CTV RSS 404s (site rebuilt).

## Radar / weather data research (2026-10-09)

Source: MSC GeoMet WMS `https://geo.weather.gc.ca/geomet` (what v1 uses).

| Layer | Time extent kept online | Step | Notes |
|---|---|---|---|
| `RADAR_1KM_RRAI` (rain rate) | **3 h** | 6 min | styles incl. `Radar-Rain_14colors` (continuous ramp 0.1–200 mm/h) |
| `RADAR_1KM_RSNO` (snow rate) | **3 h** | 6 min | `Radar-Snow_14colors` (0.1–20 cm/h); covers same echoes as RRAI |
| `Radar_1km_SfcPrecipType` | 3 h | 6 min | 12 discrete classes: light/mod/heavy rain, snow, mixed, freezing rain (2), hail |
| `Radar_1km_{Rain,Snow}PrecipRate-Extrapolation`, `Radar_1km_dBZ-Extrapolation` | **+72 min forecast** | 6 min | radar nowcast |
| `GOES-West_1km_DayVis-NightIR`, `_NaturalColor`, `_2km_NightMicrophysics` | **48 h** | 10 min | no archiving needed for ≤48 h loops |
| `RDPA.6F_PR` / `RDPA.24F_PR` | since 2012 | 6 h / 24 h | gauge-corrected analysis, ~10 km, delayed — possible calibration/backfill only |

- **The 3-hour accumulation limit = GeoMet only keeps 3 h of radar.** 48 h rolling totals require the server to
  archive every 6-min frame itself (480 frames/48 h; at display resolution as float16 ≈ 30 MB — trivial).
- **v1's "accumulation" isn't one**: `_max_blend_accumulate` keeps the per-pixel *max colour*, i.e. peak intensity.
- **No numeric radar access**: WCS is disabled for radar layers ("WCS request not enabled"); Datamart
  (`dd.weather.gc.ca/today/radar/`) only has per-station pre-rendered GIFs (incl. `24_HR_ACCUM`).
- **Colour inversion works** (tested): sample the legend colour bar (`GetLegendGraphic`, 384 samples), nearest-colour
  lookup per pixel → rate. Max colour error 5.8 (rain) / 6.4 (snow) on a 0–441 scale over ~70–78k px → unambiguous.
  Needs exact tick-position calibration (bar has an "over" cap triangle at top).
- Accumulation algorithm: each frame, per pixel → type from `SfcPrecipType`; rain/freezing → add RRAI mm/h × 0.1 h to
  rain total; snow → add RSNO cm/h × 0.1 h to snow total; mixed → split. Render rain ramp (light green → dark green →
  yellow → orange → red → magenta) and snow ramp (white → light blue → deep blue) with configurable thresholds.

## Environment Canada (MSC) data access — full survey (tested from server 2026-10-09)

Three interfaces, all anonymous and free. Docs: https://eccc-msc.github.io/open-data/

### 1. GeoMet OGC API — `https://api.weather.gc.ca/collections/<id>/items` (JSON, the cleanest source)
Params: `bbox`, `datetime` (RFC3339, `a/..`), `filter` (CQL2), `sortby` (`-field` = desc), `limit`/`offset`
(≤10 000), `properties`, `lang`, `f=json|csv`. 104 collections; the useful ones:

| Collection | What we get for Vancouver | Notes |
|---|---|---|
| `citypageweather-realtime` [experimental] | `bc-74` = Vancouver: currentConditions, forecastGroup (13 periods + normals, UV), **hourlyForecastGroup (24 h)**, warnings, riseSet | one call, 600 ms; replaces v1's XML scraping + URL hunting |
| `swob-realtime` | all stations in a bbox, 30 days history, ~200 fields | **1-minute** obs from Point Atkinson, West Van, Pitt Meadows, Delta Burns Bog, White Rock; hourly from CYVR (manual), Vancouver Harbour, Sea Island CCG, etc. Fields incl. temps, wind avg/gust/dir (2/10/60 min), pcpn 1/3/6/24 h, mslp, tendency |
| `weather-alerts` | alert name/type/colour, impact, confidence, text, area, expiry | bbox query also returns ended alerts → filter `status_en != ended` + point-in-area |
| `aqhi-observations-realtime` / `aqhi-forecasts-realtime` | Metro Vancouver NW/NE/SE/SW… AQHI value | hourly |
| `prognos-hrdps-realtime` / `prognos-rdps-realtime` | statistically corrected point forecasts (temp etc.) | |
| `marineweather-realtime` | Strait of Georgia forecasts + warnings | |
| `hydrometric-realtime` | river level/discharge (e.g. Capilano) | |
| `weather:rdpa:*` coverages | RDPA 6 h/24 h precip analysis | coverage endpoint returned 400 with plain bbox — needs param work |

### 2. GeoMet WMS/WCS — `https://geo.weather.gc.ca/geomet`
- **WMS (images)**: radar `RADAR_1KM_RRAI/RSNO`, `Radar_1km_SfcPrecipType` (3 h kept, 6 min);
  radar nowcast `Radar_1km_*-Extrapolation` (+72 min); `Lightning_2.5km_Density` (3 h, 10 min);
  GOES-West/East ×9 products each — DayVis-NightIR, NaturalColor, DayCloudType-NightMicrophysics,
  SnowFog, Smoke, FireTemperature… (**48 h**, 10 min); HRDPS model fields (TT temp, PR precip, NT cloud…,
  hourly, **+48 h**); RAQDPS-FireWork wildfire smoke PM2.5 (hourly, +72 h).
- **WCS (numbers)**: works for model layers (HRDPS returns GeoTIFF) but is **disabled for radar**.
- **GetFeatureInfo**: numeric value at one point, e.g. radar `{'value': 0, 'class': 'Undetected', 'time': …}` —
  good for a "rain rate at home" readout, not for grids.
- Use the layer-specific GetCapabilities (`&layer=NAME`) — the full document is 40 MB.

### 3. MSC Datamart — `https://dd.weather.gc.ca/today/…` (files; mirror `hpfx.collab.science.gc.ca/<YYYYMMDD>/WXO-DD/`)
- 30-day retention, date tree `/<YYYYMMDD>/WXO-DD/…`; `/today/` = current day.
- **Path change broke v1:** `observations/swob-ml/latest/` moved under `/today/`, and `CYVR-AUTO` no longer
  exists (only `CYVR-MAN`, hourly). Fixed in hub config 2026-10-09.
- Numeric products: `lightning/` (GeoTIFF every 10 min, national 2.5 km), `model_hrdpa/2.5km/` (radar+gauge
  6 h precip analysis, GRIB2), `model_hrdps/` (GRIB2 forecasts). Radar here is only per-station GIFs.
- **AMQP push (recommended by MSC for real-time):** `amqps://anonymous:anonymous@dd.weather.gc.ca:5671`,
  exchange `xpublic`, routing key `v02.post.*.WXO-DD.<path with dots>.#` (also `v03.*`). Tested: 883 announcements
  in 60 s for swob/citypage/radar/alerts; body = `timestamp baseurl relpath`. Queues: name `q_anonymous.*`,
  set expiry, keep < 10 000 messages.

### Usage policy (must follow)
- ≤ ~86 400 requests/day (1/s) before contacting MSC; bulk/batch WMS tile retrieval prohibited.
- Meaningful User-Agent; never send `Cache-Control: no-cache`.
- "Use Datamart, not GeoMet, for building local archives" — our rolling radar cache is one image per area per
  6 min (normal map use), but keep it small and documented.
- **Attribution required** (End-use licence): show "Data: Environment and Climate Change Canada" (panel + radar screens).

### Recommended pull strategy for the hub
| Data | Source | Cadence | Est. req/day |
|---|---|---|---|
| Current + hourly + daily forecast, warnings, sun | OGC `citypageweather-realtime` item `bc-74` | 10 min (citypage updates ≥ hourly) | 144 |
| Live obs (temp/wind/gust/precip) | OGC `swob-realtime` bbox, `datetime=now-15m/..` | 2 min | 720 |
| Alerts | OGC `weather-alerts` bbox, filter active | 5 min | 288 |
| AQHI | OGC `aqhi-observations/forecasts-realtime` | 30 min | 96 |
| Radar rate + type (per map area) | WMS GetMap ×3 layers | 6 min | ~720 per area |
| Radar nowcast (optional) | WMS Extrapolation layers ×13 steps | 6 min | ~3 100 per area — only if wanted |
| GOES loops (per area/product) | WMS GetMap, only new timesteps | 10 min | 144 per area/product |
| Lightning | Datamart GeoTIFF or WMS | 10 min | 144 |
| HRDPS forecast maps (temp/precip/cloud) | WMS image or WCS numbers, only when model run changes | hourly check | ~50 |
Total with 3 map areas ≈ 5–6 k/day — well under the policy line. Polling is adequate; AMQP is an optional
later optimisation (e.g. trigger radar pulls the moment a new DPQPE frame is announced instead of on a timer).

## v2 decisions so far (from user, 2026-10-09)

- Headlines: mixed-source list per category ("Canada News", "Local News", "World News", "US News",
  "Business & Economics", "Technology", "Satire"), favicons restored. US News = reputable left-leaning outlets.
- Finance: control panel to manage tickers — add/remove, reorder, per-ticker label, icon, pixel-width override.
- Weather/radar: **rebuild from the ground up**. Must be easy to configure: radar screen areas (map extents),
  which animations/layers go on each basemap, and per-animation timeframe, frame interval, refresh cadence.
  Keep both styles: GOES day/night "real" cloud view and TV-style bright precip overlays on a static basemap.
  True rolling accumulation (rain green→red, snow white→deep blue), configurable window up to 48 h.
  **Mixed rain/snow display rule:** a pixel shows rain colours until its snow total passes a (configurable)
  threshold, then snow colours draw on top — snow-on-top bias.

## Pi performance findings

- Matrix refresh thread (SCHED_FIFO 99) runs a solid **120 Hz** (capped). Hardware side is fine.
- **Python render thread is pegged at 100% of core 2** and never sleeps. Saved benchmark: ~29 fps normal,
  ~15 fps during radar, 0% frames on 60 fps target.
- **Radar/satellite frames are blitted per-pixel from Python.** `radar.py` probes for `img.im.getim` (absent in Pillow 9.5)
  and falls back to `_blit_per_pixel`. Upstream rgbmatrix (installed build 2026-01) calls `image.getim()`, which exists
  only in **Pillow ≥ 11**. Upgrading Pillow in the Pi venv (or using `SetImage(..., unsafe=False)`, a Cython loop)
  should remove the ~16 fps ceiling. Needs a hardware test.
- Every frame redraws everything with Python-level `DrawLine` loops (zone clears, top/bottom bar masking = ~60 C calls/frame
  just for black fills) — nothing is cached between frames.
- **CPU isolation mismatch:** boot has `isolcpus=1,2`; Python pinned to core 2; rgbmatrix pins its refresh thread to
  **core 3, which is NOT isolated**. Core 1 is isolated but completely idle (14 timer IRQs in 67 days).
  Code comments assume `isolcpus=2,3`. Likely contributor to flicker.
- `show_refresh_rate: true` → library prints Hz continuously from the RT thread into the journal (48 KB every ~26 s).
- Wi-Fi is up alongside Ethernet (same subnet, unneeded radio + IRQs).

## Cruft / housekeeping

- Pi: `~/bigscreen-calendar/debug.log` = **1.0 GB** (old logging config); `src/data_receiver.py` dead;
  `led-matrix-simple` / `led-terminal` services + DietPi autostart for an old "matrix console" (inactive).
- Pi: radar packs written to SD every 6 min (wear) despite the service file saying tmpfs-only.
- Server crontab: legacy hand-written radar block runs every 6/10/60 min and **every run is a no-op**
  ("SKIP unknown track: local_radar/storm_past/…" ×131). Only the auto-generated block does work.
- Server `data/` has stray `tmp*` files; `config/backups/` has 34 auto-backups.

## Data sources in use

- Calendar: Google Calendar API, OAuth `token.pickle` on server (refreshed 2026-08-03), 60 s refresh, ~300 events.
- Finance: yfinance, 26 symbols (stocks + `-USD` crypto), 60 s.
- Headlines: Reddit, 15 subreddits, 30 min.
- Weather: Environment Canada citypage XML + SWOB obs (Vancouver / CYVR), 15 min — working.
- Radar/satellite: built on server from geodata/tiles (`build_radar_anim.py`, `basemap_presets.json`).
