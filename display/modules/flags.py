"""Tiny pixel flags (9 x 6) for captions: the countries people fly to the ISS from, the EU for launches from
Kourou, and every other country with a space programme, a launch site or an astronaut,
redrawn by hand at LED size (flag designs are public domain). Each is a colour glyph (display/gfx.py
COLOR_GLYPHS): ``flag_char("us")`` is a single character that DrawText draws as the flag, so a flag can sit
anywhere in a line of text and widths stay right."""

import numpy as np

from display import gfx

W, H = 9, 6
C = {  # LED-friendly colours (saturated; the wall's white is bright)
    "R": (215, 30, 40), "W": (225, 225, 225), "B": (35, 70, 200), "N": (20, 40, 120), "K": (25, 25, 25),
    "Y": (245, 200, 20), "G": (20, 150, 60), "O": (240, 130, 30), "L": (90, 170, 235), "D": (150, 20, 40),
    "M": (140, 90, 40),
}


def _rows(*bands):
    """Horizontal bands, top to bottom, each (colour, rows)."""
    out = []
    for col, n in bands:
        out += [col * W] * n
    return out


def _cols(*bands):
    """Vertical bands, left to right, each (colour, columns)."""
    line = "".join(col * n for col, n in bands)
    return [line] * H


ART = {
    "us": ["NNNNRRRRR", "NWNWWWWWW", "NNNNRRRRR", "WWWWWWWWW", "RRRRRRRRR", "WWWWWWWWW"],
    "ru": _rows(("W", 2), ("B", 2), ("R", 2)),
    "ca": ["RRWWWWWRR", "RRWWRWWRR", "RRWRRRWRR", "RRWWRWWRR", "RRWWRWWRR", "RRWWWWWRR"],
    "jp": ["WWWWWWWWW", "WWWWRWWWW", "WWWRRRWWW", "WWWRRRWWW", "WWWWRWWWW", "WWWWWWWWW"],
    "cn": ["RYRRRRRRR", "YYYRRRRRR", "RYRRRRRRR", "RRRRRRRRR", "RRRRRRRRR", "RRRRRRRRR"],
    "de": _rows(("K", 2), ("R", 2), ("Y", 2)),
    "fr": _cols(("B", 3), ("W", 3), ("R", 3)),
    "it": _cols(("G", 3), ("W", 3), ("R", 3)),
    "be": _cols(("K", 3), ("Y", 3), ("R", 3)),
    "nl": _rows(("R", 2), ("W", 2), ("B", 2)),
    "es": _rows(("R", 1), ("Y", 4), ("R", 1)),
    "pl": _rows(("W", 3), ("R", 3)),
    "hu": _rows(("R", 2), ("W", 2), ("G", 2)),
    "at": _rows(("R", 2), ("W", 2), ("R", 2)),
    "in": ["OOOOOOOOO", "OOOOOOOOO", "WWWWBWWWW", "WWWWWWWWW", "GGGGGGGGG", "GGGGGGGGG"],
    "ae": ["RRGGGGGGG", "RRGGGGGGG", "RRWWWWWWW", "RRWWWWWWW", "RRKKKKKKK", "RRKKKKKKK"],
    "sa": ["GGGGGGGGG", "GWWWWWWWG", "GGGGGGGGG", "GGWWWWWGG", "GGGGGGGGG", "GGGGGGGGG"],
    "tr": ["RRRRRRRRR", "RRWWRRRRR", "RWRRRWRRR", "RWRRRRRRR", "RRWWRRRRR", "RRRRRRRRR"],
    "dk": ["RRRWRRRRR", "RRRWRRRRR", "WWWWWWWWW", "RRRWRRRRR", "RRRWRRRRR", "RRRWRRRRR"],
    "se": ["BBBYBBBBB", "BBBYBBBBB", "YYYYYYYYY", "BBBYBBBBB", "BBBYBBBBB", "BBBYBBBBB"],
    "no": ["RRWBWRRRR", "WWWBWWWWW", "BBBBBBBBB", "WWWBWWWWW", "RRWBWRRRR", "RRWBWRRRR"],
    "fi": ["WWWBWWWWW", "WWWBWWWWW", "BBBBBBBBB", "WWWBWWWWW", "WWWBWWWWW", "WWWBWWWWW"],
    "ch": ["RRRRRRRRR", "RRRRWRRRR", "RRRWWWRRR", "RRRRWRRRR", "RRRRRRRRR", "RRRRRRRRR"],
    "gb": ["WBRBWBRBW", "BWRBWBRWB", "RRRRRRRRR", "RRRRRRRRR", "BWRBWBRWB", "WBRBWBRBW"],
    "kz": ["LLLLLLLLL", "LLLLYLLLL", "LLLYYYLLL", "LLLLYLLLL", "LLLLLLLLL", "LLLLLLLLL"],
    "il": ["WWWWWWWWW", "BBBBBBBBB", "WWWWBWWWW", "WWWBWBWWW", "BBBBBBBBB", "WWWWWWWWW"],
    "br": ["GGGGGGGGG", "GGGYYYGGG", "GGYYBYYGG", "GGYYBYYGG", "GGGYYYGGG", "GGGGGGGGG"],
    "eu": ["BBBBBBBBB", "BBBYBYBBB", "BBYBBBYBB", "BBYBBBYBB", "BBBYBYBBB", "BBBBBBBBB"],  # Kourou (Ariane, Vega)
    # not a country: a globe on a plain field, for the ISS (everyone's station)
    "world": ["LLLWWWLLL", "LLWLWLWLL", "LLWWWWWLL", "LLWLWLWLL", "LLLWWWLLL", "LLLLLLLLL"],
    # other countries with a space programme, a launch site or an astronaut
    "au": ["WRWNNNNWN", "RRRNNNNNN", "WRWNNWNNW", "NNNNNNNNN", "NWNNNNNWN", "NNNNNNNNN"],
    "nz": ["WRWNNNNRN", "RRRNNNNNN", "WRWNNRNNR", "NNNNNNNNN", "NNNNNNNRN", "NNNNNNNNN"],
    "kr": ["KWWWWWWWK", "WKWRRRWKW", "WWWRRBWWW", "WWWRBBWWW", "WKWBBBWKW", "KWWWWWWWK"],
    "kp": ["BBBBBBBBB", "RWWWRRRRR", "RWRWRRRRR", "RWWWRRRRR", "RRRRRRRRR", "BBBBBBBBB"],
    "tw": ["BBBBRRRRR", "BWWBRRRRR", "BBBBRRRRR", "RRRRRRRRR", "RRRRRRRRR", "RRRRRRRRR"],
    "ir": ["GGGGGGGGG", "GGGGGGGGG", "WWWWRWWWW", "WWWWRWWWW", "RRRRRRRRR", "RRRRRRRRR"],
    "pk": ["WWGGGGGGG", "WWGGWWGWG", "WWGWGGGGG", "WWGWGGGGG", "WWGGWWGGG", "WWGGGGGGG"],
    "ua": _rows(("B", 3), ("Y", 3)),
    "by": ["WRRRRRRRR", "RWRRRRRRR", "WRRRRRRRR", "RWRRRRRRR", "WGGGGGGGG", "RWGGGGGGG"],
    "cz": ["BWWWWWWWW", "BBWWWWWWW", "BBBWWWWWW", "BBBRRRRRR", "BBRRRRRRR", "BRRRRRRRR"],
    "sk": ["WWWWWWWWW", "WRWRWWWWW", "BRRRBBBBB", "BRWRBBBBB", "RBBBRRRRR", "RRRRRRRRR"],
    "ro": _cols(("B", 3), ("Y", 3), ("R", 3)),
    "bg": _rows(("W", 2), ("G", 2), ("R", 2)),
    "lu": _rows(("R", 2), ("W", 2), ("L", 2)),
    "ie": _cols(("G", 3), ("W", 3), ("O", 3)),
    "pt": ["GGGRRRRRR", "GGYYRRRRR", "GYWWYRRRR", "GYWWYRRRR", "GGYYRRRRR", "GGGRRRRRR"],
    "gr": ["BWBBBBBBB", "WWWWWWWWW", "BWBWWWWWW", "BBBBBBBBB", "WWWWWWWWW", "BBBBBBBBB"],
    "mx": ["GGGWWWRRR", "GGGWWWRRR", "GGGWMWRRR", "GGGMMMRRR", "GGGWWWRRR", "GGGWWWRRR"],
    "ar": ["LLLLLLLLL", "LLLLLLLLL", "WWWWYWWWW", "WWWWWWWWW", "LLLLLLLLL", "LLLLLLLLL"],
    "cl": ["BBBWWWWWW", "BWBWWWWWW", "BBBWWWWWW", "RRRRRRRRR", "RRRRRRRRR", "RRRRRRRRR"],
    "co": _rows(("Y", 3), ("B", 2), ("R", 1)),
    "pe": _cols(("R", 3), ("W", 3), ("R", 3)),
    "ve": ["YYYYYYYYY", "YYYYYYYYY", "BBWBWBWBB", "BBBBBBBBB", "RRRRRRRRR", "RRRRRRRRR"],
    "cu": ["RBBBBBBBB", "RRWWWWWWW", "RWRBBBBBB", "RRRBBBBBB", "RRWWWWWWW", "RBBBBBBBB"],
    "vn": ["RRRRRRRRR", "RRRRYRRRR", "RRYYYYYRR", "RRRYYYRRR", "RRRYRYRRR", "RRRRRRRRR"],
    "mn": ["RRRBBBRRR", "RYRBBBRRR", "RYRBBBRRR", "RYRBBBRRR", "RYRBBBRRR", "RRRBBBRRR"],
    "my": ["NNNNRRRRR", "NYNYWWWWW", "NYNNRRRRR", "WWWWWWWWW", "RRRRRRRRR", "WWWWWWWWW"],
    "id": _rows(("R", 3), ("W", 3)),
    "th": _rows(("R", 1), ("W", 1), ("B", 2), ("W", 1), ("R", 1)),
    "ph": ["WBBBBBBBB", "WWBBBBBBB", "WYWBBBBBB", "WYWRRRRRR", "WWRRRRRRR", "WRRRRRRRR"],
    "ng": _cols(("G", 3), ("W", 3), ("G", 3)),
    "eg": ["RRRRRRRRR", "RRRRRRRRR", "WWWWYWWWW", "WWWWYWWWW", "KKKKKKKKK", "KKKKKKKKK"],
    "za": ["GGWRRRRRR", "KYGGWWWWW", "KKYGGGGGG", "KKYGGGGGG", "KYGGWWWWW", "GGWBBBBBB"],
}
BASE = 0xE100                   # Unicode private use: one code point per flag


def _picture(rows):
    a = np.zeros((H, W, 3), np.uint8)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row[:W]):
            a[y, x] = C[ch]
    return a


CODES = sorted(ART)
for _i, _code in enumerate(CODES):
    gfx.COLOR_GLYPHS[BASE + _i] = _picture(ART[_code])


def picture(code):
    """A flag as an (H, W, 3) image (the world flag for an unknown code)."""
    return gfx.COLOR_GLYPHS[BASE + CODES.index(code if code in ART else "world")]


def flag_char(code) -> str:
    """The flag of an ISO 3166 two-letter code as one character, or "" when there's no flag for it."""
    code = str(code or "").lower()
    return chr(BASE + CODES.index(code)) if code in ART else ""


# Operators' marks at text size, for labels where the name is long (STARLINK-31234 -> the mark + 31234): drawn by
# hand at LED size, like the flags. Each stands on the baseline in its own colours.
LOGO_C = {"W": (230, 230, 235), "g": (120, 120, 130)}
LOGOS = {
    # Starlink: its swoosh, a stroke rising left to right, thick at the start and tapering to a point
    "starlink": ["......ggW", "...gWWWg.", ".gWWWg...", "gWWg.....", "Wg......."],
}
LOGO_BASE = 0xE200


def _logo(rows):
    a = np.zeros((len(rows), max(len(r) for r in rows), 3), np.uint8)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != ".":
                a[y, x] = LOGO_C[ch]
    return a


LOGO_KEYS = sorted(LOGOS)
for _i, _key in enumerate(LOGO_KEYS):
    gfx.COLOR_GLYPHS[LOGO_BASE + _i] = _logo(LOGOS[_key])


def logo_char(key) -> str:
    """An operator's mark (LOGOS) as one character, or "" when there's none."""
    return chr(LOGO_BASE + LOGO_KEYS.index(key)) if key in LOGOS else ""
