"""Alert screen themes (rebuilt): every theme is a hand-picked combination of

  * a design     — the screen's layout and graphic concept (alert_designs.py),
  * a family     — a set of approved BDF fonts, all at native size (no scaling, real bold faces only),
  * a palette    — dark ground, bright accents (colours that say "sev" take the alert's severity colour),
  * a border     — an animated frame in the severity colour,
  * a reveal     — how the text first appears.

Fonts come only from the Fonts page's approved list (config hub.fonts.status); tests/check_fonts.py checks it.
Text density (Dense / Balanced / Minimal) is a global switch applied to whichever theme is shown.
"""

from __future__ import annotations

SEVERITY = {"warning": (255, 30, 30), "watch": (255, 140, 0), "advisory": (255, 210, 0), "statement": (120, 150, 255)}

PALETTES = {   # text roles: strong (summary), text (details), impact, advice, amount, dim, label; panel = raised surface
    "blackout":  {"name": "Blackout", "bg": (0, 0, 0), "panel": (18, 18, 22), "strong": (255, 255, 255), "text": (196, 198, 206),
                  "impact": (255, 168, 64), "advice": (96, 226, 255), "amount": (255, 228, 0), "dim": (118, 120, 132),
                  "label": "sev", "border": "sev", "icon": (255, 255, 255)},
    "midnight":  {"name": "Midnight", "bg": (0, 4, 20), "panel": (4, 16, 46), "strong": (236, 244, 255), "text": (164, 186, 226),
                  "impact": (255, 188, 92), "advice": (118, 255, 204), "amount": (255, 222, 64), "dim": (88, 108, 158),
                  "label": "sev", "border": "sev", "icon": (236, 244, 255)},
    "oxide":     {"name": "Oxide", "bg": (12, 2, 0), "panel": (40, 10, 4), "strong": (255, 236, 222), "text": (222, 170, 150),
                  "impact": (255, 150, 60), "advice": (255, 224, 128), "amount": (255, 255, 255), "dim": (150, 92, 76),
                  "label": (255, 92, 48), "border": "sev", "icon": (255, 226, 210)},
    "graphite":  {"name": "Graphite", "bg": (9, 10, 12), "panel": (28, 30, 34), "strong": (250, 250, 250), "text": (184, 188, 196),
                  "impact": (255, 146, 92), "advice": (148, 216, 255), "amount": (255, 210, 72), "dim": (108, 112, 122),
                  "label": "sev", "border": "sev", "icon": (232, 234, 240)},
    "sodium":    {"name": "Sodium lamp", "bg": (6, 3, 0), "panel": (34, 18, 0), "strong": (255, 196, 96), "text": (214, 150, 60),
                  "impact": (255, 120, 40), "advice": (255, 232, 170), "amount": (255, 255, 236), "dim": (132, 84, 28),
                  "label": (255, 168, 40), "border": "sev", "icon": (255, 196, 96)},
    "phosphor":  {"name": "Phosphor", "bg": (0, 5, 2), "panel": (0, 22, 8), "strong": (132, 255, 150), "text": (66, 204, 96),
                  "impact": (220, 255, 120), "advice": (186, 255, 226), "amount": (255, 255, 255), "dim": (32, 118, 52),
                  "label": (96, 255, 128), "border": "sev", "icon": (132, 255, 150)},
    "cyanotype": {"name": "Cyanotype", "bg": (0, 8, 22), "panel": (0, 30, 62), "strong": (220, 248, 255), "text": (128, 196, 232),
                  "impact": (255, 196, 110), "advice": (255, 255, 255), "amount": (120, 255, 255), "dim": (60, 116, 160),
                  "label": (90, 220, 255), "border": "sev", "icon": (180, 240, 255)},
    "violet":    {"name": "Violet hour", "bg": (8, 0, 14), "panel": (30, 8, 46), "strong": (250, 238, 255), "text": (196, 168, 222),
                  "impact": (255, 162, 126), "advice": (136, 232, 255), "amount": (255, 218, 96), "dim": (122, 94, 152),
                  "label": "sev", "border": "sev", "icon": (250, 238, 255)},
    "slate":     {"name": "Slate", "bg": (6, 10, 14), "panel": (20, 30, 38), "strong": (236, 246, 252), "text": (158, 190, 206),
                  "impact": (255, 176, 96), "advice": (110, 255, 214), "amount": (255, 238, 112), "dim": (84, 116, 132),
                  "label": "sev", "border": "sev", "icon": (236, 246, 252)},
    "signal":    {"name": "Signal yellow", "bg": (0, 0, 0), "panel": (26, 24, 0), "strong": (255, 236, 0), "text": (228, 228, 228),
                  "impact": (255, 150, 0), "advice": (255, 255, 255), "amount": (255, 236, 0), "dim": (140, 130, 60),
                  "label": (255, 236, 0), "border": "sev", "icon": (255, 236, 0)},
    "ember":     {"name": "Ember", "bg": (10, 0, 0), "panel": (36, 4, 6), "strong": (255, 244, 240), "text": (228, 180, 176),
                  "impact": (255, 200, 90), "advice": (255, 255, 200), "amount": (255, 120, 90), "dim": (150, 86, 86),
                  "label": "sev", "border": "sev", "icon": (255, 230, 226)},
}

A, C = "alert/", "candidates/"
# Approved fonts only, each at native size. title: alert-name candidates, largest first. key: labels and chips.
# small: captions. ladder: body text (regular, real bold or None, scale) — largest size where everything fits wins.
FAMILIES = {
    "crox":       {"name": "Crox sans", "title": [(C + "u8-win-crox4hb.bdf", 1), (C + "u8-win-crox3hb.bdf", 1), (A + "win_crox2hb.bdf", 1), (A + "win_crox1hb.bdf", 1)],
                   "key": A + "win_crox1hb.bdf", "small": A + "5x8.bdf",
                   "ladder": [(A + "win_crox1h.bdf", A + "win_crox1hb.bdf", 1), (A + "nokia-small.bdf", A + "nokia-small-bold.bdf", 1),
                              (A + "u8g2_squeezed_regular_7.bdf", A + "u8g2_squeezed_bold_7.bdf", 1)]},
    "helvetica":  {"name": "Helvetica & Lucida", "title": [(C + "x11-helvB18.bdf", 1), (C + "x11-helvB14.bdf", 1), (C + "x11-helvB10.bdf", 1)],
                   "key": C + "x11-luBS12.bdf", "small": A + "5x8.bdf",
                   "ladder": [(C + "x11-luRS12.bdf", C + "x11-luBS12.bdf", 1), (C + "x11-luRS10.bdf", C + "x11-luBS10.bdf", 1),
                              (C + "x11-helvR10.bdf", C + "x11-helvB10.bdf", 1)]},
    "typewriter": {"name": "Lucida Typewriter", "title": [(C + "x11-lutBS18.bdf", 1), (C + "x11-lutBS14.bdf", 1), (C + "x11-lutBS12.bdf", 1)],
                   "key": C + "x11-lutBS12.bdf", "small": C + "x11-lutRS10.bdf",
                   "ladder": [(C + "x11-lutRS14.bdf", C + "x11-lutBS14.bdf", 1), (C + "x11-lutRS12.bdf", C + "x11-lutBS12.bdf", 1),
                              (C + "x11-lutRS10.bdf", C + "x11-lutBS10.bdf", 1)]},
    "terminus":   {"name": "Terminus", "title": [(C + "u8-t0-15b-uni.bdf", 1), (C + "ter-u14b.bdf", 1), (A + "t0-13b-uni.bdf", 1)],
                   "key": C + "ter-u14b.bdf", "small": A + "t0-11-uni.bdf",
                   "ladder": [(C + "ter-u14n.bdf", C + "ter-u14b.bdf", 1), (A + "t0-13-uni.bdf", A + "t0-13b-uni.bdf", 1),
                              (C + "ter-u12n.bdf", C + "ter-u12b.bdf", 1), (A + "t0-11-uni.bdf", A + "t0-11b-uni.bdf", 1)]},
    "fixed":      {"name": "X11 fixed", "title": [(A + "9x15B.bdf", 1), (A + "7x14B.bdf", 1), (A + "7x13B.bdf", 1)],
                   "key": A + "7x13B.bdf", "small": A + "5x8.bdf",
                   "ladder": [(A + "7x14.bdf", A + "7x14B.bdf", 1), (A + "7x13.bdf", A + "7x13B.bdf", 1), (A + "6x12.bdf", None, 1),
                              (A + "6x10.bdf", None, 1), (A + "5x8.bdf", None, 1)]},
    "neep":       {"name": "Neep", "title": [(C + "jmk-neep-10x20-bold.bdf", 1), (C + "jmk-neep-06x13-bold.bdf", 1)],
                   "key": C + "jmk-neep-06x13-bold.bdf", "small": C + "jmk-neep-05x10.bdf",
                   "ladder": [(C + "jmk-neep-06x13.bdf", C + "jmk-neep-06x13-bold.bdf", 1), (C + "jmk-neep-06x11.bdf", None, 1),
                              (C + "jmk-neep-05x11.bdf", None, 1), (C + "jmk-neep-05x10.bdf", None, 1)]},
    "tamzen":     {"name": "Tamzen", "title": [(A + "Tamzen8x16b.bdf", 1), (A + "Tamzen7x14b.bdf", 1)],
                   "key": A + "Tamzen7x14b.bdf", "small": A + "spleen-5x8.bdf",
                   "ladder": [(A + "Tamzen7x14r.bdf", A + "Tamzen7x14b.bdf", 1), (A + "Tamzen6x12r.bdf", None, 1),
                              (A + "spleen-6x12.bdf", None, 1), (A + "spleen-5x8.bdf", None, 1)]},
    "dina":       {"name": "Dina & ctrld", "title": [(A + "Dina_r700-9.bdf", 1), (A + "ctrld-fixed-13b.bdf", 1)],
                   "key": A + "ctrld-fixed-13b.bdf", "small": A + "ctrld-fixed-10r.bdf",
                   "ladder": [(A + "Dina_r400-9.bdf", A + "Dina_r700-9.bdf", 1), (A + "Dina_r400-8.bdf", None, 1),
                              (A + "ctrld-fixed-10r.bdf", A + "ctrld-fixed-10b.bdf", 1)]},
    "cherry":     {"name": "Cherry & Boxxy", "title": [(C + "boxxy-bold.bdf", 1), (A + "cherry-13-b.bdf", 1)],
                   "key": A + "cherry-13-b.bdf", "small": A + "cherry-10-r.bdf",
                   "ladder": [(A + "cherry-13-r.bdf", A + "cherry-13-b.bdf", 1), (A + "cherry-11-r.bdf", A + "cherry-11-b.bdf", 1),
                              (A + "cherry-10-r.bdf", None, 1)]},
    "mplus":      {"name": "M+ Latin", "title": [(C + "mplus-f12b.bdf", 1), (C + "mplus-f10b.bdf", 1)],
                   "key": C + "mplus-f12b.bdf", "small": A + "5x8.bdf",
                   "ladder": [(C + "mplus-f12r.bdf", C + "mplus-f12b.bdf", 1), (C + "mplus-f10r.bdf", C + "mplus-f10b.bdf", 1),
                              (C + "mplus-h10r.bdf", C + "mplus-h10b.bdf", 1), (C + "mplus-s10r.bdf", C + "mplus-s10b.bdf", 1)]},
    "profont":    {"name": "ProFont", "title": [(A + "profont22.bdf", 1), (A + "profont17.bdf", 1)],
                   "key": A + "profont15.bdf", "small": C + "u8-profont10.bdf",
                   "ladder": [(A + "profont15.bdf", None, 1), (A + "profont12.bdf", None, 1), (A + "profont11.bdf", None, 1),
                              (C + "u8-profont10.bdf", None, 1)]},
    "clean":      {"name": "Schumacher Clean", "title": [(C + "x11-helvB14.bdf", 1), (C + "clean-clB6x12.bdf", 1)],
                   "key": C + "clean-clB6x12.bdf", "small": C + "clean-clR5x8.bdf",
                   "ladder": [(C + "clean-clR7x14.bdf", None, 1), (C + "clean-clR6x12.bdf", C + "clean-clB6x12.bdf", 1),
                              (C + "clean-clR5x8.bdf", None, 1)]},
    "retro":      {"name": "Retro Pixel", "title": [(C + "retro-pixel-thick.bdf", 1)],
                   "key": C + "retro-pixel-thick.bdf", "small": C + "retro-pixel-petty-5x5.bdf",
                   "ladder": [(C + "retro-pixel-cute-prop.bdf", None, 1), (C + "retro-pixel-cute-mono.bdf", None, 1),
                              (C + "retro-pixel-petty-5h.bdf", None, 1)]},
    "spectrum":   {"name": "Spectrum 8-bit", "title": [(C + "knxt.bdf", 1), (C + "berry.bdf", 1)],
                   "key": C + "berry.bdf", "small": C + "limey.bdf",
                   "ladder": [(C + "berry.bdf", None, 1), (C + "limey.bdf", None, 1), (C + "antidote.bdf", None, 1)]},
    "kakwa":      {"name": "Kakwa & Curie", "title": [(C + "boxxy-bold.bdf", 1), (C + "kakwafont-12-b.bdf", 1)],
                   "key": C + "kakwafont-12-b.bdf", "small": C + "scientifica-11.bdf",
                   "ladder": [(C + "kakwafont-12-n.bdf", C + "kakwafont-12-b.bdf", 1), (C + "curiemedium-12.bdf", C + "curiebold-12.bdf", 1),
                              (C + "scientifica-11.bdf", C + "scientificabold-11.bdf", 1)]},
    "envy":       {"name": "Envy Code & Haxor", "title": [(C + "envypn7x15.bdf", 1), (C + "haxormedium-13.bdf", 1)],
                   "key": C + "haxormedium-13.bdf", "small": C + "lokaltog-fixed-10r-iso10646.bdf",
                   "ladder": [(C + "envypn7x15.bdf", None, 1), (C + "envypn7x13.bdf", None, 1), (C + "haxormedium-12.bdf", None, 1),
                              (C + "lokaltog-fixed-10r-iso10646.bdf", None, 1)]},
    "gohu":       {"name": "Gohu & Tewi", "title": [(A + "9x15B.bdf", 1), (A + "gohufont-11b.bdf", 1)],
                   "key": A + "gohufont-11b.bdf", "small": A + "tom-thumb.bdf",
                   "ladder": [(A + "gohufont-11.bdf", A + "gohufont-11b.bdf", 1), (C + "tewi-normal-11.bdf", None, 1),
                              (A + "nokia-small.bdf", A + "nokia-small-bold.bdf", 1)]},
}

STRUCTURES = {"command": "Command map", "headline": "Where-first headline", "headline_cols": "Where-first, two columns",
              "dashboard": "Instrument dashboard", "storyboard": "Now · Expect · Do", "scene": "Animated scene",
              "broadcast": "TV broadcast", "ladder": "Severity ladder", "countdown": "Countdown", "gazette": "Gazette",
              "sign": "Overhead sign", "scope": "Radar scope", "split": "Split panel", "split_hazard": "Split, hazard colour",
              "stack": "Fact stack", "teletype": "Teletype + region table"}
BORDERS = {"chase": "Marching dashes", "pulse": "Slow pulse", "hazard": "Hazard tape", "double": "Double rule",
           "lights": "Chasing lights", "comet": "Comet", "corners": "Corner brackets", "glow": "Glow", "rails": "Chevron rails",
           "bevel": "Bevel", "notch": "Notched tech frame", "textloop": "Text loop", "scanbars": "Scan bars"}
REVEALS = {"type": "Typewriter", "lines": "Line by line", "wipe": "Wipe", "scan": "Scan bar", "decode": "Decode",
           "sections": "Sections slide in", "blinds": "Blinds", "fade": "Fade in", "cascade": "Cascade",
           "sweep": "Highlight sweep"}
DENSITIES = {"dense": "Dense", "balanced": "Balanced", "minimal": "Minimal"}

# (name, design, family, palette, border, reveal, header, upper, pulse, opts) — curated, not generated.
# opts: extent "metro" (Metro Vancouver) | "lm" (Lower Mainland incl. Howe Sound … Chilliwack); pages: text pages;
# lit: overhead-sign lamp colour.
M, LM = {"extent": "metro"}, {"extent": "lm"}
_T = [
    # map-led
    ("Command", "command", "helvetica", "blackout", "pulse", "lines", "WEATHER ALERT", False, True, M),
    ("Ops Centre", "command", "terminus", "graphite", "notch", "scan", "STORM DESK", True, True, M),
    ("Region Watch", "command", "kakwa", "midnight", "glow", "fade", "WEATHER ALERT", False, False, LM),
    ("Coastline", "command", "dina", "cyanotype", "corners", "sections", "COASTAL WEATHER", False, True, LM),
    ("Situation Room", "command", "gohu", "slate", "bevel", "decode", "SITUATION", True, False, M),
    # location-first
    ("Front Page", "headline", "crox", "blackout", "double", "wipe", "WEATHER ALERT", False, True, M),
    ("Neighbourhood", "headline", "helvetica", "midnight", "chase", "lines", "LOCAL ALERT", False, False, M),
    ("Lower Mainland", "headline", "mplus", "slate", "comet", "cascade", "LOWER MAINLAND", False, True, LM),
    ("Two Column", "headline_cols", "clean", "graphite", "corners", "fade", "WEATHER ALERT", False, False, M),
    ("Region Brief", "headline_cols", "kakwa", "violet", "glow", "sweep", "BRIEFING", False, True, LM),
    # instruments
    ("Dashboard", "dashboard", "terminus", "blackout", "notch", "cascade", "WEATHER", True, True, M),
    ("Cockpit", "dashboard", "envy", "midnight", "bevel", "scan", "INSTRUMENTS", True, False, M),
    ("Gauges", "dashboard", "profont", "slate", "corners", "sections", "STATUS", False, True, LM),
    ("Fact Stack", "stack", "helvetica", "graphite", "double", "sections", "WEATHER ALERT", False, False, M),
    ("Index Cards", "stack", "cherry", "violet", "corners", "cascade", "ALERT", False, True, M),
    ("Field Notes", "stack", "dina", "oxide", "comet", "lines", "FIELD NOTES", False, False, M),
    # what to do
    ("Storyboard", "storyboard", "helvetica", "blackout", "pulse", "sections", "WEATHER ALERT", False, True, M),
    ("Action Plan", "storyboard", "clean", "slate", "double", "cascade", "ACTION PLAN", False, False, M),
    ("Checklist", "storyboard", "kakwa", "cyanotype", "corners", "lines", "CHECKLIST", False, True, M),
    # animated weather
    ("Weather Window", "scene", "crox", "midnight", "glow", "fade", "WEATHER ALERT", False, True, M),
    ("Storm Watch", "scene", "neep", "blackout", "lights", "wipe", "STORM WATCH", True, False, M),
    ("Skylight", "scene", "mplus", "cyanotype", "pulse", "sections", "SKY", False, True, M),
    ("Broadcast", "broadcast", "helvetica", "blackout", "rails", "wipe", "WEATHER", False, True, M),
    ("News at Six", "broadcast", "crox", "midnight", "chase", "sections", "BREAKING WEATHER", False, False, LM),
    ("Radar Room", "scope", "terminus", "phosphor", "corners", "scan", "RADAR", True, True, M),
    ("Sonar", "scope", "gohu", "cyanotype", "comet", "fade", "TRACKING", False, False, LM),
    # severity and time
    ("Severity Scale", "ladder", "helvetica", "graphite", "double", "lines", "WEATHER ALERT", False, True, M),
    ("Threat Level", "ladder", "fixed", "ember", "hazard", "blinds", "THREAT LEVEL", True, False, M),
    ("Countdown", "countdown", "mplus", "slate", "notch", "lines", "IN EFFECT", False, True, M),
    ("Hourglass", "countdown", "kakwa", "oxide", "glow", "fade", "VALID UNTIL", False, False, M),
    # bold graphic
    ("Colour Block", "split", "helvetica", "blackout", "pulse", "wipe", "ALERT", False, True, M),
    ("Poster", "split", "crox", "ember", "glow", "fade", "WARNING", True, False, M),
    ("Weather Colour", "split_hazard", "kakwa", "graphite", "double", "sections", "WEATHER", False, True, M),
    ("Paint Chip", "split_hazard", "cherry", "blackout", "corners", "blinds", "WEATHER", False, False, M),
    # vernacular
    ("Gazette", "gazette", "typewriter", "graphite", "double", "type", "THE WEATHER GAZETTE", False, False, LM),
    ("Morning Edition", "gazette", "crox", "oxide", "corners", "wipe", "MORNING EDITION", False, True, M),
    ("Overhead Sign", "sign", "fixed", "blackout", "lights", "blinds", "ROAD WEATHER", True, False, dict(lit=(255, 176, 0))),
    ("Gantry", "sign", "neep", "signal", "lights", "decode", "ROAD WEATHER", True, True, dict(lit=(255, 236, 60))),
    ("Teletype", "teletype", "fixed", "phosphor", "scanbars", "type", "WWCN", True, False, M),
    ("Telex", "teletype", "typewriter", "sodium", "double", "type", "TELEX", True, True, M),
]
THEMES = {}
for i, (name, design, fam, pal, border, rev, header, upper, pulse, opts) in enumerate(_T):
    tid = f"t{i + 1:02d}"
    THEMES[tid] = {"id": tid, "name": name, "structure": design, "family": fam, "palette": pal, "border": border,
                   "reveal": rev, "header": header, "upper": upper, "pulse": pulse, "opts": dict(opts),
                   "description": f"{STRUCTURES[design]} · {FAMILIES[fam]['name']} · {PALETTES[pal]['name']} · "
                                  f"{BORDERS[border]} · {REVEALS[rev]}"}

DEFAULT_THEME = "t01"


def resolve(theme_id):
    return THEMES.get(theme_id) or THEMES[DEFAULT_THEME]
