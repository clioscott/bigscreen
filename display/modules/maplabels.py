"""Names and flags beside the icons on the Space Map (``map.labels.<kind>``, ``map.names``).

Each kind (the ISS, Tiangong, relays, ground stations, satellite layers, launch pads) has its own settings
(LABEL_DEFAULTS): its name, its flag (who runs it: the country that mainly controls a ground station, a relay's
owner), for crewed stations who's aboard, where the label goes (right, left, above, below, or auto), its colour and
its edge: a 1 px black outline round every letter (the default: it reads on anything), a dark box, or nothing.
The ISS's flag is a globe (it's everyone's) or the US and Russian flags, each in the label or beside the icon on a
side of its own; its text can be your own, with {exp} for the expedition's number and {crew} for how many are aboard.
Names come from the data (CelesTrak's names for satellites, the stations' own names) unless ``map.names`` has one of
your own for that object (by its usual name).

Auto puts each label where it covers the least: the other labels, the icons, the panels and the caption, and the
edges of the map. Labels are placed in order of importance (the ISS first), so the busiest places keep the ones
that matter."""

from __future__ import annotations

import numpy as np

KINDS = ("iss", "tiangong", "relays", "ground", "sats", "launch")
PLACES = ("auto", "right", "left", "above", "below")
EDGES = ("outline", "box", "none")
ISS_FLAGS = ("none", "globe", "partners")           # the ISS's flag: none, a globe, or the US and Russian flags
FLAG_AT = ("label", "right", "left", "above", "below")   # where each partner flag goes: in the label or by the icon
_E = {"edge": "outline", "backdrop": True}          # (backdrop: the old switch, kept so saved settings still load)
LABEL_DEFAULTS = {
    "iss": {"name": True, "flag": "globe", "crew": False, "pos": "right", "colour": "#ffd27a", "text": "ISS",
            "us_at": "label", "ru_at": "label", **_E},
    "tiangong": {"name": True, "flag": True, "crew": False, "pos": "auto", "colour": "#e66e5a", **_E},
    "relays": {"name": False, "flag": False, "pos": "auto", "colour": "#8fb0ff", **_E},
    "ground": {"name": False, "flag": False, "pos": "auto", "colour": "#c8c8d2", **_E},
    "sats": {"name": False, "flag": False, "pos": "auto", "colour": "#b4b4c8", "max": 40, "logo": True, **_E},   # (max, logo: mapsats)
    "launch": {"name": True, "flag": True, "pos": "auto", "colour": "#ff9f6b", **_E},
}
LH = 7                       # a label's height (tiny font)


def labels_of(mapset, kind) -> dict:
    got = ((mapset or {}).get("labels") or {}).get(kind) if isinstance((mapset or {}).get("labels"), dict) else None
    out = {**LABEL_DEFAULTS[kind], **(got if isinstance(got, dict) else {})}
    if out.get("edge") not in EDGES:
        out["edge"] = "outline"
    if kind == "iss":                                    # (saved before the globe: true was the crew's flags)
        f = out.get("flag")
        out["flag"] = f if f in ISS_FLAGS else "globe" if f is True else "none"
    return out


def name_for(mapset, key, default):
    names = (mapset or {}).get("names")
    got = names.get(key) if isinstance(names, dict) else None
    return str(got).strip() if isinstance(got, str) and got.strip() else default


def _candidates(X, Y, hw, hh, bw, pos, pad=None):
    p = pad or {}
    c = {"right": (X + hw + 2 + p.get("right", 0), Y - LH // 2), "left": (X - hw - 2 - bw - p.get("left", 0), Y - LH // 2),
         "above": (X - bw // 2, Y - hh - 1 - LH - p.get("above", 0)), "below": (X - bw // 2, Y + hh + 2 + p.get("below", 0))}
    if pos in c:                                        # a fixed place, then the other side for when it's off the map
        return [c[pos], c[{"right": "left", "left": "right", "above": "below", "below": "above"}[pos]]]
    return list(c.values()) + [(X + hw + 1, Y - hh - LH), (X + hw + 1, Y + hh + 1),
                               (X - hw - 1 - bw, Y - hh - LH), (X - hw - 1 - bw, Y + hh + 1)]


def place(items, obstacles, w, h):
    """[(item, x, y, text, bw)] for the items that fit: each item {"text", "anchor": (X, Y), "half": (hw, hh), "pos",
    "prio", "optional"}; ``obstacles``: boxes (x0, y0, x1, y1) a label should keep off."""
    from display.modules.mappanels import text_w
    items = [it for it in sorted(items, key=lambda i: i.get("prio", 9)) if it["text"]]
    obs = np.asarray(obstacles, float).reshape(-1, 4)
    taken = np.zeros((len(items), 4))                           # (each candidate against every box at once: the
    placed, n = [], 0                                           # satellites' names can be a hundred or more)
    for it in items:
        text = it["text"]
        bw = text_w(text) + 2
        X, Y = it["anchor"]
        hw, hh = it.get("half", (2, 2))
        cands = _candidates(int(X), int(Y), hw, hh, bw, it.get("pos", "auto"), it.get("pad"))
        if it.get("pos", "auto") != "auto":                     # a fixed place, or the other side if it's off the
            fits = [(x, y) for x, y in cands if x - 1 >= 0 and x + bw + 1 <= w and y >= 0 and y + LH <= h]
            x, y = fits[0] if fits else cands[0]                # map; kept on the map either way
            cands = [(max(0, min(w - bw, x)), max(0, min(h - LH, y)))]
        c = np.asarray(cands, float)
        boxes = np.column_stack((c[:, 0] - 1, c[:, 1], c[:, 0] + bw + 1, c[:, 1] + LH))
        out = (np.maximum(0, -boxes[:, 0]) + np.maximum(0, boxes[:, 2] - w) + np.maximum(0, -boxes[:, 1])
               + np.maximum(0, boxes[:, 3] - h))
        cost = out * LH * 4 + _overlaps(boxes, taken[:n]) * 3 + _overlaps(boxes, obs) + np.arange(len(c)) * 0.5
        k = int(np.argmin(cost))                                # (the first of the cheapest, as before)
        if it.get("optional") and cost[k] > bw * 2:
            continue                                            # (a minor label with no clear room: left out)
        placed.append((it, int(c[k, 0]), int(c[k, 1]), text, bw))
        taken[n] = boxes[k]
        n += 1
    return placed


def _overlaps(boxes, others):
    """How much each of ``boxes`` (n, 4) overlaps ``others`` (m, 4), summed: (n,)."""
    if not len(others):
        return np.zeros(len(boxes))
    w = np.clip(np.minimum(others[None, :, 2], boxes[:, None, 2]) - np.maximum(others[None, :, 0], boxes[:, None, 0]), 0, None)
    h = np.clip(np.minimum(others[None, :, 3], boxes[:, None, 3]) - np.maximum(others[None, :, 1], boxes[:, None, 1]), 0, None)
    return (w * h).sum(axis=1)


def draw(img, placed):
    from display.modules.mappanels import draw_text, draw_text_outlined
    h, w = img.shape[:2]
    for it, x, y, text, bw in placed:
        edge = it.get("edge", "outline")
        if edge == "outline":
            draw_text_outlined(img, x, y + 1, text, it["rgb"])
            continue
        if edge == "box":
            # 1 px each side of the ink: the text's width ends with the font's 1 px gap, which is the right margin
            x0, y0, x1, y1 = max(0, x - 1), max(0, y), min(w, x + bw - 2), min(h, y + LH)
            if x1 > x0 and y1 > y0:
                img[y0:y1, x0:x1] = (img[y0:y1, x0:x1] * 0.3).astype(np.uint8)
        draw_text(img, x, y + 1, text, it["rgb"])
