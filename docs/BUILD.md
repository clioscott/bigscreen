# Building BigScreen

How a wall of 21 LED panels became a calm, readable home display, and what it took to make it fast.

<!-- TODO(build): photos — the finished wall, the back (wiring and power), a single panel, the Pi and its HAT. -->

## The hardware

| Part | What | Notes |
|---|---|---|
| Panels | 21 × 64 × 64 RGB HUB75 panels | 448 × 192 pixels in total, wired as 3 chains of 7 panels |
| Controller | Raspberry Pi 4 Model B | display only: it never fetches anything itself |
| Interface | 3-output HUB75 adapter on the Pi's GPIO (`regular` pin mapping, 3 parallel chains) | <!-- TODO(build): exact board --> |
| Power | 5 V supplies feeding the panels directly | <!-- TODO(build): supplies, wattage, how power is distributed --> |
| Frame | <!-- TODO(build): how the panels are mounted --> | |
| Server | Intel N100 mini PC (Debian 13) | runs the hub: fetching, rendering maps, the control panel |

<!-- TODO(build): panel model / pitch / scan rate, total cost, size on the wall, how long the build took. -->

### Wiring

Each of the Pi's three HUB75 outputs drives one horizontal band of seven panels (64 rows each). Inside a band the
panels are daisy-chained, so rpi-rgb-led-matrix sees `chain_length = 7`, `parallel = 3`, `rows = cols = 64`.

<!-- TODO(build): wiring diagram or photo, cable lengths, anything that had to be re-routed. -->

## Getting a clean picture

Driving 86,016 RGB LEDs from a Pi is mostly a timing problem. The settings in `system.display` came from
exhaustive testing on the real wall:

| Setting | Value | What it controls |
|---|---|---|
| `gpio_slowdown` | 3 | slows GPIO writes so the panels' shift registers keep up with the Pi 4 |
| `pwm_bits` | 8 | colour depth: 256 levels per channel (each extra bit roughly halves the refresh rate) |
| `pwm_lsb_nanoseconds` | 100 | on-time of the least significant PWM bit: shorter is faster, too short ghosts |
| `pwm_dither_bits` | 1 | time-dithers the lowest bit to buy refresh rate back |
| `limit_refresh_rate_hz` | 120 | caps the refresh at a steady rate instead of a fluctuating maximum |
| `brightness` | 85 | |

The boot line reserves two CPU cores (`isolcpus=2,3`): rpi-rgb-led-matrix's refresh thread runs on core 3 at real-time
priority, and the Python render thread is pinned to core 2. With nothing else scheduled there, the refresh holds a
steady 120 Hz.

<!-- TODO(build): what flicker/ghosting looked like before tuning, and how you tested (camera, slow-mo, etc.). -->

## Version 1

The first version already split the work: a server fetched the calendar, quotes, headlines and weather and rsynced
JSON to the Pi, and a cron job built radar animations. The Pi redrew every frame from scratch in Python through
rgbmatrix's `graphics` calls. It worked, but it had grown without version control across three machines, and:

- the Python render thread sat at 100 % of its core and managed about 29 fps, dropping to 15 fps whenever radar
  played, because map frames were blitted pixel by pixel;
- the finance fetcher, one long-lived process, leaked memory until it reached 8.7 GB and stopped updating;
- Reddit closed the JSON endpoints the headlines came from, and nothing noticed that two feeds had been dead for weeks;
- the refresh thread was pinned to a core that wasn't isolated, which showed up as occasional flicker.

## Version 2

Version 2 (October 2026) rebuilt both halves in one repository.

- **The server fetches, the Pi renders.** Every fetch is a short-lived subprocess with a timeout, so nothing can leak
  for months, and each job's health shows on the control panel (stale after three missed runs). Results are rsynced
  to a tmpfs on the Pi, which never writes to its SD card.
- **A numpy frame buffer** replaces per-call drawing. `display/gfx.py` reproduces rgbmatrix's `graphics` module
  pixel for pixel, so every v1 screen ported over unchanged. Fills are slice assignments, text and icons are cached,
  and a frame reaches the panels in one C++ call through `FrameCanvas.SetBuffer`, which `deploy/rgbmatrix_setbuffer.py`
  patches into the library. Only the 64 × 16 tiles that changed are sent.
- **Maps are pre-rendered on the server** as raw frame packs (`data/anim/*.bin`) and played back frame-exact.
- **The same code renders everywhere.** The control panel's previews, this README's screenshots and the wall itself
  all come from the same modules, so a preview is never an approximation.

<!-- TODO(build): before/after numbers (fps, CPU) once measured on the wall. -->

## Typography

Every glyph on the wall comes from a real bitmap (BDF) font, drawn at its native size and never scaled. Text tops
out at 24 px, and legibility from across the room wins over decoration. An audit of nearly 500 bitmap fonts picked
the families the alert themes use; see [assets/fonts/CREDITS.md](../assets/fonts/CREDITS.md).

## Lessons

<!-- TODO(build): what you'd do differently, what surprised you. -->
