"""Alert screen themes: palettes x type pairings x layouts, combined into 50 named designs.

A theme = a layout (how the screen is organised: sidebar, dashboard tiles,
poster, hazard-tape frame, terminal, columns, …) + a palette + a font pairing +
a few options (progress indicator, page transition, icon size, background
pattern, header text, capitals). Every layout appears several times but never
with the same palette and fonts, so no two themes are a pure recolour.

Palette colours that say "sev" take the alert's severity colour at draw time
(warning red, watch orange, advisory yellow, statement slate).
"""

from __future__ import annotations

SEVERITY = {"warning": (220, 20, 20), "watch": (255, 125, 0), "advisory": (235, 190, 0), "statement": (110, 120, 150)}

PALETTES = {
    "severity":  {"name": "Severity", "bg": (0, 0, 0), "panel": (22, 22, 26), "panel2": (40, 40, 48), "fg": (255, 255, 255),
                  "dim": (150, 150, 160), "accent": "sev", "accent_fg": (255, 255, 255), "hi": (255, 220, 60),
                  "label": "sev", "line": (70, 70, 80), "icon": (255, 255, 255)},
    "red_alert": {"name": "Red alert", "bg": (0, 0, 0), "panel": (60, 0, 0), "panel2": (110, 0, 0), "fg": (255, 255, 255),
                  "dim": (230, 140, 140), "accent": (225, 15, 15), "accent_fg": (255, 255, 255), "hi": (255, 230, 0),
                  "label": (255, 90, 80), "line": (150, 20, 20), "icon": (255, 255, 255)},
    "amber":     {"name": "Amber", "bg": (0, 0, 0), "panel": (40, 24, 0), "panel2": (80, 48, 0), "fg": (255, 196, 70),
                  "dim": (170, 115, 30), "accent": (255, 160, 0), "accent_fg": (0, 0, 0), "hi": (255, 255, 255),
                  "label": (255, 160, 0), "line": (120, 75, 0), "icon": (255, 196, 70)},
    "ice":       {"name": "Ice", "bg": (0, 6, 18), "panel": (0, 30, 60), "panel2": (0, 55, 100), "fg": (225, 245, 255),
                  "dim": (110, 160, 200), "accent": (0, 190, 255), "accent_fg": (0, 10, 30), "hi": (255, 255, 120),
                  "label": (90, 210, 255), "line": (0, 90, 140), "icon": (225, 245, 255)},
    "night":     {"name": "Night (dim red)", "bg": (0, 0, 0), "panel": (25, 0, 0), "panel2": (45, 0, 0), "fg": (200, 40, 30),
                  "dim": (110, 20, 15), "accent": (170, 20, 10), "accent_fg": (0, 0, 0), "hi": (255, 90, 40),
                  "label": (160, 30, 20), "line": (70, 10, 5), "icon": (200, 40, 30)},
    "mono":      {"name": "Monochrome", "bg": (0, 0, 0), "panel": (28, 28, 28), "panel2": (60, 60, 60), "fg": (205, 205, 205),
                  "dim": (140, 140, 140), "accent": (255, 255, 255), "accent_fg": (0, 0, 0), "hi": (255, 255, 255),
                  "label": (190, 190, 190), "line": (90, 90, 90), "icon": (255, 255, 255)},
    "phosphor":  {"name": "Phosphor green", "bg": (0, 4, 0), "panel": (0, 30, 8), "panel2": (0, 60, 18), "fg": (70, 255, 110),
                  "dim": (30, 150, 60), "accent": (70, 255, 110), "accent_fg": (0, 20, 0), "hi": (210, 255, 210),
                  "label": (40, 200, 80), "line": (0, 90, 30), "icon": (70, 255, 110)},
    "bulletin":  {"name": "Bulletin blue", "bg": (0, 14, 90), "panel": (0, 28, 150), "panel2": (210, 215, 230), "fg": (255, 255, 255),
                  "dim": (150, 175, 240), "accent": (255, 215, 0), "accent_fg": (0, 14, 90), "hi": (255, 215, 0),
                  "label": (255, 215, 0), "line": (60, 90, 200), "icon": (255, 255, 255)},
    "violet":    {"name": "Violet", "bg": (6, 0, 14), "panel": (35, 0, 60), "panel2": (70, 10, 110), "fg": (245, 230, 255),
                  "dim": (170, 130, 210), "accent": (190, 80, 255), "accent_fg": (255, 255, 255), "hi": (255, 200, 80),
                  "label": (200, 120, 255), "line": (90, 30, 140), "icon": (245, 230, 255)},
    "sunset":    {"name": "Sunset", "bg": (10, 0, 6), "panel": (70, 10, 30), "panel2": (130, 30, 40), "fg": (255, 235, 210),
                  "dim": (230, 140, 120), "accent": (255, 90, 40), "accent_fg": (255, 255, 255), "hi": (255, 220, 0),
                  "label": (255, 120, 70), "line": (140, 40, 50), "icon": (255, 235, 210)},
    "hivis":     {"name": "High-vis", "bg": (0, 0, 0), "panel": (255, 215, 0), "panel2": (40, 34, 0), "fg": (255, 230, 0),
                  "dim": (180, 160, 0), "accent": (255, 215, 0), "accent_fg": (0, 0, 0), "hi": (255, 255, 255),
                  "label": (255, 215, 0), "line": (120, 100, 0), "icon": (255, 230, 0)},
    "slate":     {"name": "Slate", "bg": (8, 10, 16), "panel": (28, 34, 48), "panel2": (48, 58, 80), "fg": (230, 236, 245),
                  "dim": (130, 145, 170), "accent": "sev", "accent_fg": (255, 255, 255), "hi": (120, 220, 255),
                  "label": (150, 175, 210), "line": (60, 72, 95), "icon": (230, 236, 245)},
    "teal":      {"name": "Teal", "bg": (0, 10, 10), "panel": (0, 50, 50), "panel2": (0, 90, 85), "fg": (220, 255, 250),
                  "dim": (100, 180, 170), "accent": (0, 220, 190), "accent_fg": (0, 20, 20), "hi": (255, 140, 60),
                  "label": (0, 220, 190), "line": (0, 100, 95), "icon": (220, 255, 250)},
    "crimson_gold": {"name": "Crimson & gold", "bg": (14, 0, 0), "panel": (110, 0, 10), "panel2": (160, 10, 20),
                  "fg": (255, 240, 200), "dim": (220, 170, 120), "accent": (255, 190, 30), "accent_fg": (60, 0, 0),
                  "hi": (255, 200, 40), "label": (255, 190, 30), "line": (150, 60, 20), "icon": (255, 220, 120)},
}

A = "alert/"
FONTSETS = {   # title: (font, scale); others: font
    "fixed":     {"label": "Fixed bold", "title": (A + "10x20.bdf", 1), "big": (A + "9x18B.bdf", 2), "head": A + "7x13B.bdf",
                  "label_f": A + "6x13B.bdf", "body": A + "7x13B.bdf", "small": A + "5x8.bdf"},
    "fixed_xl":  {"label": "Fixed extra-large", "title": (A + "9x18B.bdf", 2), "big": (A + "9x18B.bdf", 2), "head": A + "9x15B.bdf",
                  "label_f": A + "7x13B.bdf", "body": A + "9x15B.bdf", "small": A + "6x10.bdf"},
    "helv":      {"label": "Helvetica", "title": (A + "helvB24.bdf", 1), "big": (A + "helvB24.bdf", 1), "head": A + "helvB12.bdf",
                  "label_f": A + "helvB10.bdf", "body": A + "helvB12.bdf", "small": A + "helvR10.bdf"},
    "helv_light": {"label": "Helvetica light", "title": (A + "helvB18.bdf", 1), "big": (A + "helvB24.bdf", 1), "head": A + "helvB10.bdf",
                  "label_f": A + "helvB10.bdf", "body": A + "helvR12.bdf", "small": A + "helvR10.bdf"},
    "lucida":    {"label": "Lucida", "title": (A + "lubB24.bdf", 1), "big": (A + "lubB24.bdf", 1), "head": A + "luBS12.bdf",
                  "label_f": A + "luBS10.bdf", "body": A + "luBS12.bdf", "small": A + "luRS10.bdf"},
    "news":      {"label": "Newsroom serif", "title": (A + "lubB18.bdf", 1), "big": (A + "timB18.bdf", 2), "head": A + "timB12.bdf",
                  "label_f": A + "luBS10.bdf", "body": A + "lubB12.bdf", "small": A + "luRS10.bdf"},
    "spleen":    {"label": "Spleen", "title": (A + "spleen-12x24.bdf", 1), "big": (A + "spleen-12x24.bdf", 2), "head": A + "spleen-8x16.bdf",
                  "label_f": A + "spleen-6x12.bdf", "body": A + "spleen-8x16.bdf", "small": A + "spleen-5x8.bdf"},
    "profont":   {"label": "ProFont", "title": (A + "profont22.bdf", 1), "big": (A + "profont22.bdf", 2), "head": A + "profont17.bdf",
                  "label_f": A + "profont12.bdf", "body": A + "profont15.bdf", "small": A + "profont11.bdf"},
    "terminus":  {"label": "Terminus", "title": (A + "t0-22b-uni.bdf", 1), "big": (A + "t0-22b-uni.bdf", 2), "head": A + "t0-16b-uni.bdf",
                  "label_f": A + "t0-12b-uni.bdf", "body": A + "t0-14b-uni.bdf", "small": A + "t0-12-uni.bdf"},
    "pixel":     {"label": "Chunky pixel", "title": (A + "VictoriaBold-8.bdf", 3), "big": (A + "VictoriaBold-8.bdf", 4),
                  "head": A + "TorusSansBold-8.bdf", "label_f": A + "nokia-small-bold.bdf", "body": A + "win_crox2hb.bdf",
                  "small": A + "4x6.bdf"},
    "retro":     {"label": "Retro micro", "title": (A + "amstrad_cpc_extended.bdf", 3), "big": (A + "amstrad_cpc_extended.bdf", 4),
                  "head": A + "amstrad_cpc_extended.bdf", "label_f": A + "amstrad_cpc_extended.bdf", "body": A + "6x13B.bdf",
                  "small": A + "5x8.bdf"},
    "courier":   {"label": "Courier", "title": (A + "courB18.bdf", 1), "big": (A + "courB18.bdf", 2), "head": A + "courB12.bdf",
                  "label_f": A + "courB10.bdf", "body": A + "courB12.bdf", "small": A + "6x10.bdf"},
}

LAYOUTS = {
    "banner": "Banner", "sidebar": "Sidebar", "dashboard": "Dashboard tiles", "poster": "Poster", "tape": "Hazard tape",
    "terminal": "Terminal", "broadcast": "Broadcast", "columns": "Three columns", "minimal": "Minimal", "card": "Card",
    "split": "Split screen", "marquee": "Marquee lights",
}

# (name, layout, palette, fonts, options)
_T = [
    ("Beacon", "banner", "severity", "fixed", {"progress": "bar", "transition": "slide", "header": "WEATHER ALERT"}),
    ("Signal", "banner", "bulletin", "helv", {"progress": "dots", "transition": "slide", "header": "WEATHER BULLETIN"}),
    ("Dispatch", "banner", "slate", "terminus", {"progress": "count", "transition": "wipe", "header": "ALERT IN EFFECT"}),
    ("Lantern", "banner", "amber", "courier", {"progress": "bar", "transition": "cut", "header": "WEATHER ADVISORY", "pattern": "scan"}),
    ("Flare", "banner", "crimson_gold", "news", {"progress": "dots", "transition": "fade", "header": "SEVERE WEATHER"}),
    ("Sentinel", "sidebar", "severity", "helv", {"progress": "bar", "transition": "slide", "icon": "large"}),
    ("Lookout", "sidebar", "ice", "spleen", {"progress": "dots", "transition": "wipe", "icon": "large"}),
    ("Watchtower", "sidebar", "sunset", "lucida", {"progress": "count", "transition": "fade", "icon": "large"}),
    ("Bastion", "sidebar", "mono", "fixed_xl", {"progress": "bar", "transition": "cut", "icon": "large", "upper": True}),
    ("Control Room", "dashboard", "slate", "helv_light", {"progress": "dots", "transition": "fade"}),
    ("Ops Desk", "dashboard", "teal", "terminus", {"progress": "bar", "transition": "slide"}),
    ("Instrument", "dashboard", "amber", "profont", {"progress": "count", "transition": "cut"}),
    ("Cockpit", "dashboard", "phosphor", "spleen", {"progress": "bar", "transition": "wipe", "pattern": "grid"}),
    ("Headline", "poster", "severity", "lucida", {"progress": "dots", "transition": "fade", "icon": "large"}),
    ("Broadsheet", "poster", "mono", "news", {"progress": "bar", "transition": "slide", "icon": "large"}),
    ("Billboard", "poster", "hivis", "fixed_xl", {"progress": "count", "transition": "cut", "icon": "large", "upper": True}),
    ("Placard", "poster", "violet", "helv", {"progress": "dots", "transition": "wipe", "icon": "large"}),
    ("Neon", "poster", "teal", "pixel", {"progress": "bar", "transition": "fade", "icon": "large", "upper": True}),
    ("Caution Tape", "tape", "hivis", "fixed", {"progress": "bar", "transition": "slide", "pattern": "stripes", "upper": True}),
    ("Roadwork", "tape", "amber", "helv", {"progress": "dots", "transition": "cut", "pattern": "chevrons"}),
    ("Hazmat", "tape", "red_alert", "terminus", {"progress": "count", "transition": "wipe", "pattern": "stripes"}),
    ("Barricade", "tape", "severity", "pixel", {"progress": "bar", "transition": "slide", "pattern": "checker", "upper": True}),
    ("Mainframe", "terminal", "phosphor", "terminus", {"progress": "count", "transition": "type", "upper": True}),
    ("Teletype", "terminal", "amber", "courier", {"progress": "bar", "transition": "type", "upper": True}),
    ("Console", "terminal", "ice", "profont", {"progress": "count", "transition": "type"}),
    ("Night Shift", "terminal", "night", "fixed", {"progress": "bar", "transition": "type", "upper": True}),
    ("Newsroom", "broadcast", "bulletin", "helv", {"progress": "bar", "transition": "slide"}),
    ("Storm Centre", "broadcast", "severity", "lucida", {"progress": "dots", "transition": "fade"}),
    ("Live Desk", "broadcast", "crimson_gold", "fixed", {"progress": "bar", "transition": "wipe"}),
    ("Channel 7", "broadcast", "slate", "spleen", {"progress": "count", "transition": "cut"}),
    ("Ledger", "columns", "mono", "fixed", {"progress": "bar", "transition": "fade"}),
    ("Triptych", "columns", "severity", "helv_light", {"progress": "dots", "transition": "slide"}),
    ("Almanac", "columns", "sunset", "news", {"progress": "count", "transition": "wipe"}),
    ("Field Guide", "columns", "teal", "profont", {"progress": "bar", "transition": "cut"}),
    ("Quiet", "minimal", "mono", "helv_light", {"progress": "bar", "transition": "fade", "icon": "small"}),
    ("Ember", "minimal", "night", "lucida", {"progress": "dots", "transition": "fade", "icon": "small"}),
    ("Frost Line", "minimal", "ice", "helv", {"progress": "bar", "transition": "slide", "icon": "small"}),
    ("Ink", "minimal", "slate", "courier", {"progress": "count", "transition": "cut", "icon": "none"}),
    ("Notice", "card", "slate", "helv", {"progress": "dots", "transition": "slide", "pattern": "dots"}),
    ("Postcard", "card", "sunset", "spleen", {"progress": "bar", "transition": "fade", "pattern": "grid"}),
    ("Ticket", "card", "violet", "terminus", {"progress": "count", "transition": "wipe", "pattern": "dots"}),
    ("Memo", "card", "amber", "fixed", {"progress": "dots", "transition": "cut", "pattern": "scan"}),
    ("Horizon", "split", "severity", "fixed_xl", {"progress": "bar", "transition": "slide", "icon": "large"}),
    ("Twin Peaks", "split", "ice", "lucida", {"progress": "dots", "transition": "fade", "icon": "large"}),
    ("Equator", "split", "crimson_gold", "profont", {"progress": "count", "transition": "wipe", "icon": "large"}),
    ("Overpass", "split", "teal", "retro", {"progress": "bar", "transition": "cut", "icon": "large", "upper": True}),
    ("Theatre", "marquee", "crimson_gold", "helv", {"progress": "dots", "transition": "fade"}),
    ("Arcade", "marquee", "violet", "retro", {"progress": "bar", "transition": "cut", "upper": True}),
    ("Boardwalk", "marquee", "amber", "pixel", {"progress": "count", "transition": "slide", "upper": True}),
    ("Premiere", "marquee", "severity", "news", {"progress": "bar", "transition": "wipe"}),
]

THEMES = {}
for i, (name, layout, pal, fonts, opts) in enumerate(_T, 1):
    tid = f"t{i:02d}"
    THEMES[tid] = {"id": tid, "name": name, "layout": layout, "palette": pal, "fonts": fonts,
                   "progress": opts.get("progress", "bar"), "transition": opts.get("transition", "slide"),
                   "icon": opts.get("icon", "medium"), "pattern": opts.get("pattern"),
                   "header": opts.get("header", "WEATHER ALERT"), "upper": bool(opts.get("upper", False))}
    THEMES[tid]["description"] = f"{LAYOUTS[layout]} · {PALETTES[pal]['name']} · {FONTSETS[fonts]['label']}"

DEFAULT_THEME = "t01"
# themes from the first version map onto close new ones
LEGACY = {"ebs": "t01", "eas": "t19", "alert_ready": "t21", "bulletin": "t02", "severity": "t06", "terminal": "t23"}


def resolve(theme_id):
    tid = LEGACY.get(theme_id, theme_id)
    return THEMES.get(tid) or THEMES[DEFAULT_THEME]
