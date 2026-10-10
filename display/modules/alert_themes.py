"""Alert screen themes: one dense screen per alert, dark backgrounds, bright animated warning borders.

The wall is 4.5 ft wide and viewed from 3-4 ft (one LED ≈ 3 mm), so small pixel
fonts read fine: designs favour density — the whole ECCC message on one screen —
and use size, bold and colour to make the important parts jump out.

A theme combines
  * a structure   — how the screen is organised (header band, left rail, two
                    columns, EAS-style crawl band, boxed sections, terminal,
                    centred header, fact bar),
  * a border      — an animated frame in the severity colour (marching dashes,
                    slow pulse, hazard tape, double rule with blinking corners,
                    chasing lights, comet, corner brackets, glow, chevron rails),
  * a reveal      — how the text first appears (typewriter, line by line, wipe,
                    scan bar, decode/scramble, sections sliding in, blinds, fade),
  * a palette     — dark background, bright accents,
  * a type family — retro/pixel BDF fonts with a size ladder; the largest size at
                    which the whole message fits is used (capped at ~13 px).

Colours that say "sev" take the alert's severity colour at draw time.
"""

from __future__ import annotations

SEVERITY = {"warning": (255, 30, 30), "watch": (255, 140, 0), "advisory": (255, 210, 0), "statement": (120, 150, 255)}

PALETTES = {   # all dark; text roles: strong (summary), text (details), impact, advice, amount, dim, label
    "black":    {"name": "Black", "bg": (0, 0, 0), "panel": (16, 16, 20), "strong": (255, 255, 255), "text": (200, 200, 205),
                 "impact": (255, 170, 60), "advice": (90, 230, 255), "amount": (255, 230, 0), "dim": (120, 120, 130),
                 "label": "sev", "border": "sev", "icon": (255, 255, 255)},
    "midnight": {"name": "Midnight", "bg": (0, 6, 22), "panel": (0, 16, 44), "strong": (240, 246, 255), "text": (170, 190, 225),
                 "impact": (255, 190, 90), "advice": (120, 255, 200), "amount": (255, 225, 60), "dim": (90, 110, 160),
                 "label": "sev", "border": "sev", "icon": (240, 246, 255)},
    "oxblood":  {"name": "Oxblood", "bg": (14, 0, 0), "panel": (40, 4, 4), "strong": (255, 240, 230), "text": (225, 175, 165),
                 "impact": (255, 160, 60), "advice": (255, 225, 120), "amount": (255, 255, 255), "dim": (150, 90, 85),
                 "label": (255, 70, 50), "border": "sev", "icon": (255, 230, 220)},
    "charcoal": {"name": "Charcoal", "bg": (10, 10, 13), "panel": (26, 26, 32), "strong": (255, 255, 255), "text": (185, 188, 198),
                 "impact": (255, 150, 90), "advice": (150, 220, 255), "amount": (255, 215, 70), "dim": (110, 112, 125),
                 "label": "sev", "border": "sev", "icon": (230, 232, 240)},
    "amber_crt": {"name": "Amber CRT", "bg": (8, 4, 0), "panel": (30, 16, 0), "strong": (255, 200, 80), "text": (210, 140, 40),
                 "impact": (255, 120, 30), "advice": (255, 230, 150), "amount": (255, 255, 230), "dim": (130, 80, 20),
                 "label": (255, 170, 30), "border": "sev", "icon": (255, 200, 80)},
    "green_crt": {"name": "Green CRT", "bg": (0, 6, 0), "panel": (0, 22, 6), "strong": (120, 255, 140), "text": (60, 200, 90),
                 "impact": (220, 255, 120), "advice": (180, 255, 220), "amount": (255, 255, 255), "dim": (30, 120, 50),
                 "label": (90, 255, 120), "border": "sev", "icon": (120, 255, 140)},
    "plum":     {"name": "Plum", "bg": (10, 0, 16), "panel": (30, 6, 44), "strong": (250, 240, 255), "text": (195, 170, 220),
                 "impact": (255, 160, 120), "advice": (140, 230, 255), "amount": (255, 220, 90), "dim": (120, 95, 150),
                 "label": "sev", "border": "sev", "icon": (250, 240, 255)},
    "steel":    {"name": "Steel", "bg": (4, 10, 14), "panel": (14, 28, 36), "strong": (235, 250, 255), "text": (160, 195, 210),
                 "impact": (255, 175, 95), "advice": (100, 255, 220), "amount": (255, 240, 110), "dim": (85, 120, 135),
                 "label": "sev", "border": "sev", "icon": (235, 250, 255)},
    "ink":      {"name": "Ink & yellow", "bg": (0, 0, 0), "panel": (24, 22, 0), "strong": (255, 236, 0), "text": (230, 230, 230),
                 "impact": (255, 150, 0), "advice": (255, 255, 255), "amount": (255, 236, 0), "dim": (140, 130, 60),
                 "label": (255, 236, 0), "border": "sev", "icon": (255, 236, 0)},
}

A = "alert/"
# title: candidates for the alert name, largest first. ladder: body (regular, real bold face or None, scale) —
# with None, highlights at that size are colour-only (no fake bolding),
# largest first — the biggest size where everything fits wins. Capped small on purpose (close viewing).
FAMILIES = {
    "fixed":    {"name": "X11 fixed", "title": [(A + "10x20.bdf", 1), (A + "9x15B.bdf", 1), (A + "8x13B.bdf", 1)],
                 "key": A + "8x13B.bdf", "small": A + "5x8.bdf",
                 "ladder": [(A + "7x13.bdf", A + "7x13B.bdf", 1), (A + "6x13.bdf", A + "6x13B.bdf", 1),
                            (A + "6x10.bdf", None, 1), (A + "5x8.bdf", None, 1)]},
    "terminus": {"name": "Terminus", "title": [(A + "t0-18b-uni.bdf", 1), (A + "t0-16b-uni.bdf", 1), (A + "t0-14b-uni.bdf", 1)],
                 "key": A + "t0-14b-uni.bdf", "small": A + "t0-11-uni.bdf",
                 "ladder": [(A + "t0-13-uni.bdf", A + "t0-13b-uni.bdf", 1), (A + "t0-12-uni.bdf", A + "t0-12b-uni.bdf", 1),
                            (A + "t0-11-uni.bdf", A + "t0-11b-uni.bdf", 1)]},
    "spleen":   {"name": "Spleen", "title": [(A + "spleen-12x24.bdf", 1), (A + "spleen-8x16.bdf", 1)],
                 "key": A + "spleen-8x16.bdf", "small": A + "spleen-5x8.bdf",
                 "ladder": [(A + "spleen-6x12.bdf", None, 1), (A + "spleen-5x8.bdf", None, 1)]},
    "profont":  {"name": "ProFont", "title": [(A + "profont22.bdf", 1), (A + "profont17.bdf", 1)],
                 "key": A + "profont15.bdf", "small": A + "profont11.bdf",
                 "ladder": [(A + "profont12.bdf", None, 1), (A + "profont11.bdf", None, 1)]},
    "crox":     {"name": "Crox pixel sans", "title": [(A + "VictoriaBold-8.bdf", 2), (A + "win_crox2hb.bdf", 1)],
                 "key": A + "win_crox1hb.bdf", "small": A + "5x7.bdf",
                 "ladder": [(A + "win_crox1h.bdf", A + "win_crox1hb.bdf", 1), (A + "nokia-small.bdf", A + "nokia-small-bold.bdf", 1)]},
    "eightbit": {"name": "8-bit micro", "title": [(A + "amstrad_cpc_extended.bdf", 2)],
                 "key": A + "amstrad_cpc_extended.bdf", "small": A + "4x6.bdf",
                 "ladder": [(A + "amstrad_cpc_extended.bdf", None, 1), (A + "5x7.bdf", None, 1)]},
    "victoria": {"name": "Victoria pixel", "title": [(A + "VictoriaBold-8.bdf", 2), (A + "TorusSansBold-8.bdf", 2)],
                 "key": A + "TorusSansBold-8.bdf", "small": A + "4x6.bdf",
                 "ladder": [(A + "ArtosSans-8.bdf", A + "TorusSansBold-8.bdf", 1), (A + "5x7.bdf", None, 1)]},
    "dense":    {"name": "Squeezed dense", "title": [(A + "9x15B.bdf", 1), (A + "7x14B.bdf", 1)],
                 "key": A + "7x13B.bdf", "small": A + "4x6.bdf",
                 "ladder": [(A + "6x13.bdf", A + "6x13B.bdf", 1),
                            (A + "u8g2_squeezed_regular_7.bdf", A + "u8g2_squeezed_bold_7.bdf", 1),
                            (A + "u8g2_squeezed_regular_6.bdf", A + "u8g2_squeezed_bold_6.bdf", 1)]},
    "nokia":    {"name": "Nokia handset", "title": [(A + "nokia-small-bold.bdf", 2)],
                 "key": A + "nokia-small-bold.bdf", "small": A + "4x6.bdf",
                 "ladder": [(A + "nokia-small.bdf", A + "nokia-small-bold.bdf", 1), (A + "4x6.bdf", None, 1)]},
}

STRUCTURES = {"band": "Header band", "rail": "Left rail", "columns": "Two columns", "crawl": "Crawl band",
              "boxes": "Boxed sections", "terminal": "Terminal", "centered": "Centred header", "factbar": "Fact bar"}
BORDERS = {"chase": "Marching dashes", "pulse": "Slow pulse", "hazard": "Hazard tape", "double": "Double rule",
           "lights": "Chasing lights", "comet": "Comet", "corners": "Corner brackets", "glow": "Glow", "rails": "Chevron rails"}
REVEALS = {"type": "Typewriter", "lines": "Line by line", "wipe": "Wipe", "scan": "Scan bar", "decode": "Decode",
           "sections": "Sections slide in", "blinds": "Blinds", "fade": "Fade in"}

NAMES = ["Beacon", "Sentinel", "Klaxon", "Siren", "Tripwire", "Lighthouse", "Bulletin", "Dispatch", "Watchfire", "Flashpoint",
         "Lantern", "Bastion", "Outpost", "Radar Room", "Night Desk", "Switchboard", "Teletype", "Telex", "Signal Box", "Control",
         "Storm Desk", "Front Line", "Squall", "Gale", "Downpour", "Undertow", "Breakwater", "Floodgate", "Ridge", "Summit",
         "Tundra", "Glacier", "Cinder", "Ember", "Ashfall", "Thunderhead", "Anvil", "Spark", "Voltage", "Circuit",
         "Mainframe", "Phosphor", "Vector", "Arcade", "Pager", "Handset", "Marquee", "Floodlight", "Afterglow", "Vigil"]

_STRUCT, _BORD, _REV, _PAL, _FAM = list(STRUCTURES), list(BORDERS), list(REVEALS), list(PALETTES), list(FAMILIES)
_PREFER = {"terminal": ["green_crt", "amber_crt", "black", "midnight", "charcoal", "steel"]}

THEMES = {}
_seen = set()
for i in range(50):
    st = _STRUCT[i % len(_STRUCT)]
    k = i // len(_STRUCT)
    pal_choices = _PREFER.get(st, _PAL)
    for j in range(len(pal_choices) * len(_FAM)):
        pal = pal_choices[(k + j + i) % len(pal_choices)]
        fam = _FAM[(i * 5 + k * 2 + j) % len(_FAM)]
        if (st, pal, fam) not in _seen:
            break
    _seen.add((st, pal, fam))
    border = _BORD[(i * 4 + k) % len(_BORD)]
    reveal = _REV[(i * 3 + k * 5) % len(_REV)]
    if st == "terminal":
        reveal = ("type", "decode", "lines")[k % 3]
    tid = f"t{i + 1:02d}"
    THEMES[tid] = {
        "id": tid, "name": NAMES[i], "structure": st, "border": border, "reveal": reveal, "palette": pal, "family": fam,
        "upper": st in ("crawl", "terminal") or fam in ("eightbit",) or i % 7 == 3,
        "header": ("WEATHER WARNING", "ALERT IN EFFECT", "WEATHER ALERT", "SEVERE WEATHER", "ENVIRONMENT CANADA")[i % 5],
        "description": f"{STRUCTURES[st]} · {BORDERS[border]} · {REVEALS[reveal]} · {PALETTES[pal]['name']} · {FAMILIES[fam]['name']}",
    }

DEFAULT_THEME = "t01"


def resolve(theme_id):
    return THEMES.get(theme_id) or THEMES[DEFAULT_THEME]
