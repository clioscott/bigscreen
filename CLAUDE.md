# BigScreen v2

448×192 LED wall (21× 64×64 panels: 3 parallel chains of 7) driven by a Pi 4. All fetching
happens on a separate server; the Pi only renders. Research and history: `RESEARCH.md`.

## Machines

| | Host | SSH | Role |
|---|---|---|---|
| Server | `MAGI-server` | `scott@192.168.1.111` | hub: fetch jobs, config, control panel on **:8800** |
| Pi 4 | `bigscreen` | `scott@192.168.1.100` | display (passwordless sudo) |

## Where things live

- **Canonical repo: GitHub `clioscott/bigscreen` (public).** Never commit secrets, personal data (calendar, client
  names, holdings, the home address) or Claude attribution (no `Co-Authored-By`/"Generated with" lines anywhere).
- The server's `~/bigscreen` is a deploy checkout of GitHub `main` (it no longer accepts pushes); the Pi's
  `~/bigscreen` pulls from the server's. This Windows folder is a clone with remote `origin` = GitHub.
- **Runtime state: `~/bigscreen-state/` on the server** (not in git): `config.json` (the single config),
  `config-backups/`, `status.json`, `data/` (synced to the Pi), `secrets/` (Google token), `cache/`.
- Pi reads data from `/dev/shm/bigscreen` (tmpfs), rsynced by `hub/sync.py` after every successful job.

## Hub (server)

- `hub/main.py` starts the scheduler + FastAPI app (`hub/app.py`, UI in `hub/web/`, plain JS, no build).
- Jobs in `hub/jobs/` run as **one subprocess per fetch** (`python -m hub.jobs.<name>`) with a timeout;
  never make a job a long-lived loop (v1's finance worker leaked to 8.7 GB that way).
- Job health → `status.json` → the control panel's Overview (also shows the deployed commit). A job is "stale" after 3 missed intervals.
- Service: `bigscreen-hub` (system unit, user scott). `sudo systemctl restart bigscreen-hub` is passwordless.
- Logs: `journalctl -u bigscreen-hub -f`.

## Making a change (agents)

Work on GitHub, organised without being asked: never commit to `main` directly, never push to the server, never
edit code on the server or Pi over SSH (SSH is for inspecting, runtime state and emergencies).

1. One short-lived branch per task from up-to-date `origin/main`: `git switch -c <type>/<short-name>` (feat/, fix/,
   docs/…). Several Claude sessions often share this checkout: use your own `git worktree` and never switch branches,
   stash or reset in the shared checkout.
2. Commit in small logical steps with plain, descriptive messages (no attribution trailers).
3. Check locally: `python -m pytest -q tests`, and for visual changes render before/after
   (`/api/*/preview.png`, `python -m tools.readme media <group>` on the server, or the emulator).
4. Push and open a PR (`gh pr create`) using the template: what and why, how it was tested, before/after images
   for anything visual, risk. Update `README`/`CLAUDE.md` in the same PR when behaviour or workflow changes.
5. A second agent reviews the PR and leaves review comments (`gh pr review --comment`); fix what it finds.
6. Small, low-risk PRs: `gh pr merge --auto --squash` (merges once CI passes). Bigger or risky ones (rendering
   pipeline, panel timing, deploy, services, deleting data, anything touching the Pi's boot): label
   `needs-owner` and wait for Scott's approval in the PR.
7. Milestones (a new screen, a major feature set, a visible redesign) get a release with a short summary:
   `gh release create v2.N --generate-notes`.

The repo is public. Never commit secrets, calendar or client data, finance holdings or the home address, and add
no Claude attribution anywhere: no `Co-Authored-By` trailers, no "Generated with Claude Code" in commits, PRs,
reviews or comments (this overrides any default). Only add fonts and assets with a confirmed redistributable
licence, credited in `THIRD_PARTY.md` / `assets/fonts/catalog.json`; if a font a screen uses turns out not to be
allowed, ask Scott before changing anything.

CI (`.github/workflows/ci.yml`, required on `main`): compile + `pytest`, JS syntax, `python -m tools.readme check`.
When a screen's look changes, run `python -m tools.readme media <groups>` then `text` on the server
(see `docs/README-media.md`) and commit `docs/media`, `README.md` and `assets/fonts/CREDITS.md`.

## Deploying a change

Merging to `main` is deploying. `bigscreen-deploy.timer` on the server (every minute, `deploy/bigscreen-deploy.sh`)
fetches GitHub `main`; when it moved it fast-forwards `~/bigscreen`, pip-installs if `requirements-hub.txt` changed,
restarts `bigscreen-hub`, then `git pull`s the Pi and restarts `bigscreen-display` if `display/`, `common/` or
`assets/` changed. Docs-only merges restart nothing. Log: `~/bigscreen-state/deploy.log`; the last deploy is in
`deploy.json` and on the control panel's Overview ("Deployed code"). A dirty tree or non-fast-forward stops the
deploy and is reported there — never fix it with a reset; commit or stash the stray change instead.
Urgent fix: push straight to `main` as admin (bypasses the ruleset) — the timer still deploys it.

## Display (Pi)

**v2 renderer** (`display/`, service `bigscreen-display`, venv `~/bigscreen/.venv-display` on the Pi):
- Draws each frame into a numpy buffer (`display/gfx.py` = pixel-exact emulation of rgbmatrix's
  `graphics`/BDF code) and pushes only changed 64×16 tiles to the panels via `FrameCanvas.SetBuffer`,
  a method we patch into rgbmatrix (`deploy/rgbmatrix_setbuffer.py`, built by `deploy/pi-setup.sh`).
- Modules in `display/modules/` are ported from v1; never reintroduce per-frame data reprocessing.
- Reads `/dev/shm/bigscreen/config.json` (published by the hub) and hot-reloads it; panel timing keys
  (`gpio_slowdown`, `pwm_*`, `limit_refresh_rate_hz`) make it exit so systemd restarts it.
- Control server :8765 — `/status`, `/frame.png` (live mirror), `/play` + `/rotation` (play an entry once or
  held; next/prev/hold/stop), `/override` (older form of `/play`), `/preview` (temporary config overlay with TTL).
  The hub proxies these under `/api/display/*`.
- Rotation: `display/rotation.py` (one per side; screens always play to their end, rules checked only when picking
  the next one, previews resume the rotation) and `display/rules.py` (the conditions). Entries live in
  `modules.layout_cycle.{left,right}` with `rules`; legacy `when` still works. One entry per screen: another size at
  certain times is a `variants` item on the same entry (`{"panel": "webcam", "rules": […], …overrides}`, first match
  wins when the screen starts), shown as a label on the Rotation page — never a second entry.
- Emulate anywhere: `BIGSCREEN_DATA_DIR=… python -m display.main --emulate --port 8799`.
- The deploy timer updates the Pi; by hand: `ssh scott@192.168.1.100 'cd ~/bigscreen && git pull && sudo systemctl restart bigscreen-display'`.

**Since 2026-10-09 v2 drives the panels** (`bigscreen-display` enabled at boot; hub `hub.display_mode = "v2"`,
so saves hot-reload — no restart). Pi boot line has `isolcpus=2,3`: render thread pinned to core 2, rgbmatrix
refresh thread on core 3. Backups: `/boot/firmware/cmdline.txt.pre-v2`.

**Rollback to v1** (kept installed): `sudo systemctl disable --now bigscreen-display && sudo systemctl enable --now bigscreen-client`
on the Pi, set `hub.display_mode` to `"v1"`, and on the server restore `~/bigscreen-server/server/sync.py.pre-v2`
and `config/app-config.json.pre-v2` (v1's server was neutralised: fetchers off, sync pointed at an invalid host).
`hub/pi.py:v1_config` still produces v1's config, using `hub.v1_layout_cycle` (v1's own rotation).

Profiling on the Pi: `sudo /tmp/pyspy/bin/py-spy record --pid <pid> --duration 20 --format raw -o out`.

## Windows tray app

`tray/`: a tray icon and global hotkeys on the PC, a thin client of the hub's `/api/display/*`. Installed by
`tray/Install BigScreen Tray.cmd` into `%LOCALAPPDATA%\BigScreenTray`. See `tray/README.md`. Plays are always
once (`hold: false`), so the rotation resumes by itself.

## Weather maps

- `hub/weather/`: `geo.py` (areas = centre + km/px → mercator view; XYZ tile basemaps), `carto.py`
  (scale-aware coastlines from a land/water mask, per-scale data sources, pixel-grid simplification,
  land-clipped boundaries), `wms.py` (GeoMet + legend → number decoders), `radar.py` (6-min archive up to 48 h,
  TV colours, rolling totals with snow-on-top), `tracks.py` (renders packs). Job: `hub/jobs/maps.py` (every 3 min).
- Packs: `data/anim/<track>.bin|.json` → Pi `/dev/shm/bigscreen/anim`, played by `display/modules/weather_maps.py`.
- Geodata lives in v1's `~/bigscreen-server/data` (override with `BIGSCREEN_GEODATA`); caches in `~/bigscreen-state/cache`.
- Config: `modules.weather_maps.{areas,tracks,palettes}`; edited on the panel's Weather tab.
- MSC usage policy: ≤ ~86k req/day, no bulk tile scraping, meaningful UA, show "ECCC" attribution (the map panel does).

## Webcams (live video)

- `hub/webcams.py`: one ffmpeg per source (YouTube via `python -m yt_dlp`, or any URL ffmpeg opens), started when
  someone watches and stopped `GRACE_SEC` after the last viewer; frames decoded at 640 px wide as fast as they arrive
  (HLS lands a segment at a time) into a playout buffer, and `_pace` publishes them at an even 1/fps `BUFFER_SEC` behind.
  Don't go back to `-re`: it froze the picture while each next segment downloaded (the "stutter every few seconds").
  A cold YouTube feed takes ~4–5 s to its first frame, so the display warms the next one: when a screen is under
  `rotation.WARM_AHEAD` s from its end (or on its last pass) and the next entry is a webcam, it calls
  `/api/webcams/warm` every `WARM_EVERY` s, and the screen then opens on a picture.
  Each viewer gets its own crop/scale/colour. At most `MAX_FEEDS` at once.
- API (`hub/app.py`): `/api/webcams/frames` (raw RGB for the Pi, 12-byte header per frame), `source.mjpeg` (crop
  editor), `preview` + `preview/{token}.mjpeg` (exact wall render incl. caption), `probe`, `status`.
- Display: `display/modules/webcam.py`, panels `webcam` (a side), `webcam_full`, `webcam_wall`; entry field `cam`.
  The Pi's reader thread runs on cores 0–1 and hangs up `IDLE_SEC` after the screen stops drawing it; a stream with no
  picture after `GIVE_UP_SEC` returns `"skip"` (display/rotation.py ends the screen at once).
- Captions: `display/modules/webcam_caption.py` (its docstring lists every `overlay` key). Sections (`Content`) are
  formatted once, then a layout (`LAYOUT_FUNCS`: tag, bar, lower_third, split, postcard, chips, clock, signage, ticker,
  stack, outline, dot, custom) places them. Detail lines "fit" by wrapping onto up to `FIT_LINES` before sections drop
  in `DROP` order; `dropped_sections()` tells the panel what didn't fit. Custom: `slots` {section: [column, line]},
  `line_sizes`, `columns` (align), `container`, `divider`. Captions render into a cached transparent layer (`_layer`). Weather and sunrise/sunset come from
  `hub/jobs/webcam_weather.py` (Open-Meteo, every 15 min, webcams with lat/lon) → `data/webcam_weather.json`.
  Re-render every layout after changes: `tests/test_webcams.py` renders them all; look at real frames before shipping.
- Config `modules.webcams`: `hub_url` (default `http://192.168.1.111:8800`), `cams.<id>` = label, place, credit, tz,
  lat, lon, fps, source, `crops.{left,right,full,wall}` = {x, y, w} fractions (height follows the zone), colour, overlay.
  Panel page: Webcams. Some YouTube streams need a JS runtime for yt-dlp: deno, installed user-level on the server
  (`curl -fsSL https://deno.land/install.sh | sh -s -- -y --no-modify-path` → `~/.deno/bin`, found by `deno_path()`).
- Still sources: `{"type": "drivebc", "id": N}` (a DriveBC camera) or `{"type": "image", "url", "refresh_sec"}`. A still
  feed publishes the latest image every second and fetches again every `refresh_sec`; for DriveBC the image time comes
  from `/api/webcams/{id}/` (its images have no Last-Modified) and a picture is downloaded only when it's new. Frames
  carry the image's age; captions show the exact capture time (`age` section, `taken_format`). Stills don't count
  against `MAX_FEEDS`.
- Traffic cams (Webcams page): `hub/traffic.py` groups DriveBC's cameras by bridge/tunnel (`CROSSINGS`: every camera
  on the crossing and its approaches, best view first) and by city (rough outlines in `BOUNDS` for the home cities,
  listed first; nearest centre for the rest); `/api/webcams/traffic`, thumbnails `/api/webcams/drivebc/{id}.jpg`.
  DriveBC mislabels some directions: `traffic.FACING` holds ones checked by eye, and `traffic.fix_all` is applied
  wherever the hub reads DriveBC's camera list (this page, the camera map, the DriveBC and Bridges jobs).
  The cities' own intersection cameras (`traffic.CITY_SOURCES`: City of Vancouver ~850 views from open data, City of
  Richmond ~600 from its camera map) are crawled daily in the background, cached in `cache/city_cams.json`, and join
  their city's tab as `image` sources; thumbnails `/api/webcams/city/{id}.jpg` (listed cameras only). The Map tab draws
  them as a layer (one marker per intersection, its views listed with Add); By city → "<city> on the map". The DriveBC camera map
  (`cameramap.js`) lives there now, with "Add as a webcam".
- Ready-made webcams: `hub/web/ui/pages/webcam_gallery.js` (YouTube ids; a restarted stream gets a new id). Cards show
  real frames from `/api/webcams/snapshot.jpg`, which never waits: the cached still (fresh or stale), else 503 +
  Retry-After while four background workers grab one (failures not retried for 10 min; `cache/webcam_snaps`), falling back to
  `/api/webcams/thumb/{id}.jpg` (YouTube's thumbnail, fetched by the hub).

## Webcams

- `hub/webcams.py` decodes (one ffmpeg per source) and crops; `display/modules/webcam.py` only blits; captions in
  `display/modules/webcam_caption.py`. Config: `modules.webcams.{cams, playlist, per_turn, grid}`.
- The rotation has one Webcams entry: a `webcam*` panel without `cam` plays `playlist` (order, `on`, `sec`; edited on
  the Webcams page), `per_turn` webcams a turn. An entry with `cam` still plays that one webcam (older rotations).
- Sizes: `webcam` (side), `_full`, `_tall` (over the ticker, clock strip stays: `Screen.tall_panels`), `_wall`.
- Camera grid (`webcam_grid*`): stills only, tiles filling the frame (`rows`, `cols` or auto) revealed one per
  `interval_ms`, each labelled; the hub sends all tiles at
  once (`/api/webcams/grid.bin`).
- City intersection cameras: crawlers in `hub/traffic.py` `CITY_SOURCES` (Vancouver, Richmond, Surrey, Township of
  Langley, SFU), re-crawled daily by the hub (`hub/app.py` `_city_lists`). Burnaby, Coquitlam, the North Shore, New West
  and Delta publish no intersection images (checked 2026-10-28); DriveBC covers their highways.

## BC alert screens

Five full-wall takeovers beside the ECCC weather alert, each with its own job, display module, themes and panel page:
Alert Ready (`hub/jobs/naad.py`, live TCP listener `hub/bc/naad_listener.py` started by `hub.main`), DriveBC
(`drivebc.py`, Metro Vancouver only, webcam stills in `data/drivebc/`), earthquakes (`quakes.py`, USGS + NRCan),
tsunami (`tsunami.py`, NTWC + DFO gauges), wildfire (`wildfire.py`, BCWS/EMCR, SW BC + Okanagan).
- Display: `display/modules/bc_*.py` on the shared `bc_kit.py`; panels `naad_alert`, `road_alert`, `quake_alert`,
  `tsunami_alert`, `fire_alert`; rotation rules `naad|roads|quakes|tsunami|wildfire`. Samples render labelled SAMPLE.
- No clashes: NAAD drops ECCC messages and routes quake/tsunami/fire/road ones to those screens (cache
  `naad_routed.json`); the weather alert job leaves tsunami alerts to the tsunami screen.
- Maps: `hub/bc/maps.py` renders each screen's basemap into `data/bcmaps/` (bump `RENDER_VERSION` after changes).
- Reference place: every zoomed or cropped alert map keeps home in view, or, when fitting home would shrink the
  alert area too far (> 3× zoom-out, or below 30 px for tiny areas), the biggest nearby BC city; failing that it
  points to the nearest place from the map's edge. City list and rules: `common/places.py`; crops:
  `bc_kit.frame_crop` + `ref_marker`; the wildfire evacuation zoom: `places.zoom_area`.
- Live seismometers (quake screen): `hub/bc/seismo.py` thread reads 35 CN/UW channels from EarthScope SeedLink
  (`hub/bc/mseed.py`, no obspy), pushes station intensities + QEPB trace to `/bc_quake_obs` only while a quake is fresh,
  detects ≥ 4 triggered stations. Replay a recorded quake: `POST /api/bc/quakes/replay {"name": "orcas2025", "as": "quake|eew|detect"}`.
- Takeovers: `hub/bc/common.takeover` plays a panel once per new item id; set `BIGSCREEN_NO_PI=1` for dev runs.
- Alert lifecycle (`display/modules/alert_life.py`, all six alert screens incl. the ECCC one): *new* → the full-wall
  entry jumps the queue (rotation priority token) and plays every page (`extra_pages`); *full* → full-wall in the
  rotation; after `modules.alert_lifecycle.full_showings` / `full_minutes` → *half*: the `<panel>_half` entries on
  the right (`bc_kit.HalfPanel` + each screen's `half_spec`). Rules take `"phase": "full"|"half"`; state in
  `/dev/shm/bigscreen-state/alert_life.json`. `tools/alert_lifecycle_rotation.py` set up the rotation entries.
- Highway shields: `assets/icons/shields/shields.json`, pixel-hinted per size by `tools/build_shields.py` from the
  Wikimedia SVGs (needs resvg-py, scikit-image; run outside the hub venv); drawn by `display/modules/shields.py`.

## Time zone map

US/Canada shaded by UTC offset with the time in each (BC, Alberta, the NWT and Manitoba stop changing clocks on
2026-11-01). Job `hub/jobs/timezones.py` (every 6 h) with `hub/timezones.py`: rules from the newest tzdata (PyPI
`tzdata`, cached in `cache/tzmap/tzdata/`, or the system's if newer), boundaries from timezone-boundary-builder
(shapefile fetched once per release, cut to `cache/tzmap/zones-<tag>.json.gz`), NE lines/lakes from the geodata.
Writes `data/tzmap.json` (zones, every distinct set of offsets for a year = "states", clock "changes", rule updates
between tzdata versions) and per-zone-size rasters `data/tzmap/<W>x<H>.{idx,lines}.bin`.
- Display: `display/modules/tzmap.py`, panels `timezones`, `timezones_full`, `timezones_wall`; colours by offset on
  the Pi, so colours and the lines between offsets are redrawn at every clock change. Canada and the US are kept
  apart (they follow different rules): Canada a shade brighter, the border a heavy line. The map fills its zone
  (half width, full width or whole wall) and carries the times as labels: each zone's time once per offset per
  country (`map_labels`, `map_label_hide`, lines from `map_label_lines`: time, offset from `home_zone`, UTC or any zone, name, day; home's time highlighted), optional
  city labels. The old list beside the map (Canada and US columns, a row per UTC offset or old zone) is behind
  `show_list`, off by default. Hawaii is left out (`hub/timezones.py` MAP_DROP); Alaska is on the map, cut off by
  the frame. `warn_days` before a change it also shows the upcoming map
  (zones with new rules hatched). An entry's `as_of` (epoch s) tests another moment (labelled TEST).
- Rotation rule `timechange` (`days_after`): a change is within the warning, or was under N days ago.
- Panel page "Time zone map": labels, names, places, colours, cities, preview by date, Test once / Hold.

## Rules

- Display tuning values in `system.display` came from exhaustive hardware testing — don't change them casually.
- Never run anything heavy on the Pi; it has no CPU headroom.
- Reddit is not a usable source (RSS ends 2026-11-13; `.json` 403s). Headlines come from publisher RSS.
