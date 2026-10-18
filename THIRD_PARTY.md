# Third-party code, assets and data

BigScreen's own code is licensed under the GNU GPL, version 2 or (at your option) any later version — see
[LICENSE](LICENSE). It stands on a lot of other people's work. Everything below keeps its own licence.

## Code

| Project | Used for | Licence |
|---|---|---|
| [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) by Henner Zeller and contributors | Drives the HUB75 panels from the Pi. `display/gfx.py` is a pixel-exact Python re-implementation of its `graphics` module (BDF parsing, text and line drawing), and `deploy/rgbmatrix_setbuffer.py` patches its Python binding with `FrameCanvas.SetBuffer`. The panel tuning in `system.display` uses its options. | GPL-2.0 |
| [u8g2](https://github.com/olikraus/u8g2) by Oliver Kraus | Many of the bitmap fonts (BDF sources from its `tools/font/bdf`), its font-group documentation for licences, and the 8/16 px Open Iconic icon glyphs. | Library: BSD-2-Clause; fonts: per font, see below |
| [NumPy](https://numpy.org), [Pillow](https://python-pillow.org), [FastAPI](https://fastapi.tiangolo.com), [Uvicorn](https://www.uvicorn.org), [scikit-image](https://scikit-image.org) and the other packages in `requirements-hub.txt` (installed, not vendored) | Rendering, image I/O, the hub's web API, map cartography | BSD / HPND / MIT / Apache-2.0 |
| [Vue](https://vuejs.org) 3.5, [Sortable](https://github.com/SortableJS/Sortable), [Lucide](https://lucide.dev) 0.468 (vendored in `hub/web/vendor`) | The control panel | MIT / MIT / ISC |

## Fonts

All fonts are unmodified or losslessly converted bitmap (BDF) fonts, drawn at native size. The full per-file
list with origin and licence is [assets/fonts/CREDITS.md](assets/fonts/CREDITS.md) (generated from
`assets/fonts/catalog.json` by `tools/readme.py`). Families:

- **X.Org** misc-fixed, Schumacher Clean, Sony, JIS (public domain / X11 licence); Adobe, Bigelow & Holmes
  (Lucida) and Bitstream 75-dpi screen fonts, redistributed under their X Consortium notices.
- **Cronyx / EWT Consulting** Helvetica-style "crox" fonts (via u8g2): "may be used, modified, copied,
  distributed, and sold … provided that the copyright and these terms are retained".
- **u8g2** squeezed fonts (public domain, by Oliver Kraus); /efont/ (BSD); Freedoom (BSD); ttyp0 (Uwe Waldmann).
- **Terminus** (Dimitar Zhekov, OFL-1.1), **ttyp0** (Uwe Waldmann, MIT-like), **Spleen** (Frederic Cambus,
  BSD-2), **Tamzen** (Suraj N. Kurapati, from Tamsyn by Scott Fial — free licence), **Dina** (Jørgen Ibsen, MIT),
  **ProFont** (MIT-style), **Cherry** (MIT), **ctrld** (MIT), **Cozette** (MIT), **creep** (MIT),
  **Gohufont** (WTFPL), **tom-thumb** (Robey Pointer, CC0/MIT), **Neep / Modd** (Jim Knoble, GPL),
  **artwiz** and **phallus/fonts** (GPL), **kakwafont** (OFL-1.1), **envypn** (Ypnose, MirOS),
  **Bitocra** / **Leggie** / **Curie** (OFL), **unscii** (viznut, public domain), **Tiny5** (CC0).
- **TakWolf** Ark Pixel, Fusion Pixel and Retro Pixel (OFL-1.1); **Galmuri** (Lee Minseo, OFL-1.1);
  **Zpix** (SolidZORO, OFL-1.1).
- **Japanese**: M+ Bitmap Fonts (M+ FONTS PROJECT, free licence); Misaki and k8x12 (門真 なむ / littlelimit.net,
  free licence, see `assets/fonts/alert/LICENSES/`); Shinonome, Kappa, Ayu, JIS X 0213 Kanji fonts, marumoji
  and GNU intlfonts (public domain); MNKANAME / kaname (GPL); GNU Unifont (GPL-2.0+ with font embedding
  exception / OFL-1.1); WenQuanYi Bitmap Song (GPL).

Fonts whose redistribution terms could not be confirmed are still used by a few screens and are marked
*unconfirmed* in the credits file: `nokia-small` (origin unknown), `knj10` / `knj10B` (naga10 by NAGAO Sadakazu —
the author's terms allow free non-commercial redistribution), `boxxy-bold` (caffeinebasedlifeform), Lokaltog
fixed, Haxor (ucw.cz) and knxt (Konstantin Bytensky). If you are an author and want a font removed, open an issue.

## Icons and images

- **Weather icons** (`assets/icons/weather/animatednew`): [Meteocons](https://github.com/basmilius/weather-icons)
  by Bas Milius, MIT.
- **24 px pixel icons** (`assets/icons/pixel_icons.json`): [pixelarticons](https://github.com/halfmage/pixelarticons)
  by Gerrit Halfmann, MIT (rain, thunder, freezing-rain, fog and tornado are composites on the same grid).
- **8 / 16 px icons**: [Open Iconic](https://github.com/iconic/open-iconic) (MIT / OFL), via u8g2.
- **Highway shields** (`assets/icons/shields/shields.json`): pixel art redrawn per size by `tools/build_shields.py`
  from Wikimedia Commons SVGs — BC, Trans-Canada, Interstate, US and Washington blanks (public domain) and the
  Yellowhead blank by MuzikMachine ([CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); the derived
  `yellowhead` sprites are shared under the same licence). The Crowsnest (BC Highway 3) shield is a low-resolution
  redraw of the BC government's road sign, traced from English Wikipedia's "British Columbia Highway 3.svg" (a
  non-free, fair-use file); the sign design belongs to the Province of British Columbia.
- **Flags** (`assets/icons/flags`): Canadian and US flags, public domain designs.
- **Stock logos** are not in the repository: the ticker loads them from `<data dir>/stock-icons` (uploaded from the
  control panel) and shows the symbol alone without one.

## Data shown on the wall

Nothing below is stored in this repository except small derived lookup tables in `assets/grids`, `assets/alerts`
and `assets/data`. Attribution is shown on screen where the provider asks for it.

- Weather, radar, alerts: [Environment and Climate Change Canada](https://eccc-msc.github.io/open-data/) — MSC
  GeoMet and Datamart, under the ECCC Data Servers End-use Licence ("ECCC" on the map panel).
- Earthquakes: [USGS](https://earthquake.usgs.gov) (public domain) and [Earthquakes Canada, NRCan](https://www.earthquakescanada.nrcan.gc.ca)
  (Open Government Licence – Canada). Live seismometer channels from the CN (Canadian National Seismograph Network)
  and UW (Pacific Northwest Seismic Network) networks via [EarthScope](https://www.earthscope.org) SeedLink.
- Tsunami: [NTWC / tsunami.gov](https://www.tsunami.gov) (public domain) and [DFO tide gauges](https://api-iwls.dfo-mpo.gc.ca)
  (Open Government Licence – Canada).
- Roads: [DriveBC](https://www.drivebc.ca) and wildfire: [BC Wildfire Service](https://www2.gov.bc.ca/gov/content/safety/wildfire-status)
  / [EMCR](https://openmaps.gov.bc.ca) (Open Government Licence – British Columbia).
- Public alerts: [NAAD System](https://alerts.pelmorex.com) (Alert Ready, Pelmorex).
- Japan EEW screen: [NIED Kyoshin Monitor](http://www.kmoni.bosai.go.jp) (station list in `assets/data`).
- Boundaries: Statistics Canada, BC Data Catalogue, City of Vancouver Open Data and [Represent](https://represent.opennorth.ca)
  (Open North) — open government licences.
- Maps: [Natural Earth](https://www.naturalearthdata.com) (public domain) and © [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors (ODbL) via Overpass; basemap tiles © [Esri](https://www.esri.com) and © [CARTO](https://carto.com/attributions).
- Headlines: publishers' own RSS feeds (titles only).
