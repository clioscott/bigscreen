"""Names and flags beside the icons on the Space Map (``map.labels.<kind>``, ``map.names``).

Each kind (the ISS, Tiangong, relays, ground stations, satellite layers, launch pads) has its own settings
(LABEL_DEFAULTS): its name, its flag (who runs it: the country that mainly controls a ground station, a relay's
owner), for crewed stations who's aboard, where the label goes (right, left, above, below, or auto) and its colour.
Names come from the data (CelesTrak's names for satellites, the stations' own names) unless ``map.names`` has one of
your own for that object (by its usual name).

Auto puts each label where it covers the least: the other labels, the icons, the panels and the caption, and the
edges of the map. Labels are placed in order of importance (the ISS first), so the busiest places keep the ones
that matter."""

from __future__ import annotations

import numpy as np

KINDS = ("iss", "tiangong", "relays", "ground", "sats", "launch")
PLACES = ("auto", "right", "left", "above", "below")
LABEL_DEFAULTS = {
    "iss": {"name": False, "flag": False, "crew": False, "pos": "auto", "colour": "#ffd27a", "backdrop": True},
    "tiangong": {"name": True, "flag": True, "crew": False, "pos": "auto", "colour": "#e66e5a", "backdrop": True},
    "relays": {"name": False, "flag": False, "pos": "auto", "colour": "#8fb0ff", "backdrop": True},
    "ground": {"name": False, "flag": False, "pos": "auto", "colour": "#c8c8d2", "backdrop": True},
    "sats": {"name": False, "flag": False, "pos": "auto", "colour": "#b4b4c8", "backdrop": True},
    "launch": {"name": True, "flag": True, "pos": "auto", "colour": "#ff9f6b", "backdrop": True},
}
LH = 7                       # a label's height (tiny font)


def labels_of(mapset, kind) -> dict:
    got = ((mapset or {}).get("labels") or {}).get(kind) if isinstance((mapset or {}).get("labels"), dict) else None
    return {**LABEL_DEFAULTS[kind], **(got if isinstance(got, dict) else {})}


def name_for(mapset, key, default):
    names = (mapset or {}).get("names")
    got = names.get(key) if isinstance(names, dict) else None
    return str(got).strip() if isinstance(got, str) and got.strip() else default


def _candidates(X, Y, hw, hh, bw, pos):
    c = {"right": (X + hw + 2, Y - LH // 2), "left": (X - hw - 2 - bw, Y - LH // 2),
         "above": (X - bw // 2, Y - hh - 1 - LH), "below": (X - bw // 2, Y + hh + 2)}
    if pos in c:
        return [c[pos]]
    return list(c.values()) + [(X + hw + 1, Y - hh - LH), (X + hw + 1, Y + hh + 1),
                               (X - hw - 1 - bw, Y - hh - LH), (X - hw - 1 - bw, Y + hh + 1)]


def _overlap(b, boxes):
    if not boxes:
        return 0.0
    a = np.asarray(boxes, float)
    w = np.clip(np.minimum(a[:, 2], b[2]) - np.maximum(a[:, 0], b[0]), 0, None)
    h = np.clip(np.minimum(a[:, 3], b[3]) - np.maximum(a[:, 1], b[1]), 0, None)
    return float((w * h).sum())


def place(items, obstacles, w, h):
    """[(item, x, y, text, bw)] for the items that fit: each item {"text", "anchor": (X, Y), "half": (hw, hh), "pos",
    "prio", "optional"}; ``obstacles``: boxes (x0, y0, x1, y1) a label should keep off."""
    from display.modules.mappanels import text_w
    placed, taken = [], []
    for it in sorted(items, key=lambda i: i.get("prio", 9)):
        text = it["text"]
        if not text:
            continue
        bw = text_w(text) + 2
        X, Y = it["anchor"]
        hw, hh = it.get("half", (2, 2))
        best = None
        for k, (x, y) in enumerate(_candidates(int(X), int(Y), hw, hh, bw, it.get("pos", "auto"))):
            if it.get("pos", "auto") != "auto":                 # (a fixed place: kept on the map)
                x, y = max(0, min(w - bw, x)), max(0, min(h - LH, y))
            box = (x - 1, y, x + bw + 1, y + LH)
            out = max(0, -box[0]) + max(0, box[2] - w) + max(0, -box[1]) + max(0, box[3] - h)
            cost = out * LH * 4 + _overlap(box, taken) * 3 + _overlap(box, obstacles) + k * 0.5
            if best is None or cost < best[0]:
                best = (cost, x, y, box)
        if best is None or (it.get("optional") and best[0] > bw * 2):
            continue                                            # (a minor label with no clear room: left out)
        placed.append((it, best[1], best[2], text, bw))
        taken.append(best[3])
    return placed


def draw(img, placed):
    from display.modules.mappanels import draw_text
    h, w = img.shape[:2]
    for it, x, y, text, bw in placed:
        if it.get("backdrop", True):
            # 1 px each side of the ink: the text's width ends with the font's 1 px gap, which is the right margin
            x0, y0, x1, y1 = max(0, x - 1), max(0, y), min(w, x + bw - 2), min(h, y + LH)
            if x1 > x0 and y1 > y0:
                img[y0:y1, x0:x1] = (img[y0:y1, x0:x1] * 0.3).astype(np.uint8)
        draw_text(img, x, y + 1, text, it["rgb"])
