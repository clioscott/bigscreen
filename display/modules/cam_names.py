"""Intersection camera names split into their streets and direction, for the camera grid's labels.

The cities name their views differently; all of them are "<street> <joiner> <street> - <view>":
    Vancouver   "Cambie St and W 30th Av - North"                  (the leg of the intersection)
    Richmond    "No. 4 Road at Westminster Highway - Eastbound"    (the approach)
    Surrey      "26 Ave & King George Blvd - Camera 1"             (or "- Panorama", "- North")
    Langley     "200 St & 72 Ave - looking north"
Anything else (DriveBC, SFU) has no second street: the whole name is street 1.
"""

from __future__ import annotations

import re

PARTS = ("street1", "street2", "dir", "view", "name")
DEFAULT_LINES = [["street1"], ["street2"]]          # the direction goes in a corner tag by default
_JOINERS = re.compile(r"\s+(?:and|&|at|/)\s+", re.I)
_COMPASS = {"north": "N", "northeast": "NE", "east": "E", "southeast": "SE", "south": "S", "southwest": "SW",
            "west": "W", "northwest": "NW"}
_DIR = re.compile(r"^(looking\s+)?(north|south|east|west)(east|west)?(bound)?(\s+\w+)?$", re.I)
_VIEW = re.compile(r"^(?:camera|cam)\s*(\d+)$|^(panorama|pano)$", re.I)
# street types, long → short (whole words only)
_SHORT = [("Avenue", "Ave"), ("Av", "Ave"), ("Road", "Rd"), ("Street", "St"), ("Boulevard", "Blvd"), ("Highway", "Hwy"),
          ("Drive", "Dr"), ("Place", "Pl"), ("Crescent", "Cres"), ("Parkway", "Pkwy"), ("Court", "Ct"), ("Lane", "Ln"),
          ("Gate", "Gt"), ("Block", "blk"), ("Blk", "blk"), ("Connector", "Conn")]
_SHORT_RE = re.compile(r"\b(" + "|".join(re.escape(a) for a, _ in _SHORT) + r")\b\.?")
_SHORT_MAP = dict(_SHORT)


def short_street(s: str) -> str:
    """'Westminster Highway' → 'Westminster Hwy', 'W 30th Av' → 'W 30th Ave', 'No. 4 Road' → 'No. 4 Rd'."""
    return _SHORT_RE.sub(lambda m: _SHORT_MAP[m.group(1)], s)


def split(name: str) -> dict:
    """{"street1", "street2", "dir": compass ("N", "NE"…) or "", "bound": True for an approach ("Eastbound"),
    "looking": True for "looking north", "view": "Camera 2" / "Panorama" / another word or "", "name": the whole name}."""
    name = re.sub(r"\s+", " ", name or "").strip()
    sep = " - " if " - " in name else " · " if " · " in name else ""     # " · ": the page's DriveBC labels
    main, tail = name.rsplit(sep, 1) if sep else (name, "")
    out = {"street1": main, "street2": "", "dir": "", "bound": False, "looking": False, "view": "", "name": name}
    parts = _JOINERS.split(main, maxsplit=1)
    if len(parts) == 2 and all(p.strip() for p in parts):
        out["street1"], out["street2"] = parts[0].strip(), parts[1].strip()
    tail = tail.strip()
    words = tail.split()
    if len(words) == 2 and words[0].lower() == words[1].lower():         # "Southbound Southbound"
        tail = words[0]
    m = _DIR.match(tail)
    if m:
        out["dir"] = _COMPASS[(m.group(2) + (m.group(3) or "")).lower()]
        out["bound"], out["looking"] = bool(m.group(4)), bool(m.group(1))
        if m.group(5):                                                     # "West approach", "West exit"
            out["view"] = m.group(5).strip()
    elif tail:
        v = _VIEW.match(tail)
        out["view"] = (f"Camera {v.group(1)}" if v.group(1) else "Panorama") if v else tail
    return out


def dir_text(p: dict, style: str = "short") -> str:
    """The direction as words: short "EB" (an approach) or "N"; full "Eastbound", "Looking north" or "North"."""
    d = p.get("dir") or ""
    if not d:
        return ""
    if style == "full":
        word = next(k for k, v in _COMPASS.items() if v == d)
        return ("Looking " + word) if p.get("looking") else (word.capitalize() + ("bound" if p.get("bound") else ""))
    return d + "B" if p.get("bound") and len(d) == 1 else d


def view_text(p: dict, style: str = "short") -> str:
    v = p.get("view") or ""
    if style == "short":
        v = re.sub(r"^Camera (\d+)$", r"Cam \1", v).replace("Panorama", "Pano")
    return v


def label_lines(name: str, lines=None, short=True, dir_style="short") -> list:
    """The label's lines for one camera: each line the chosen parts that have text, joined by spaces (empty lines
    dropped). lines: [[part, …], …] from PARTS."""
    p = split(name)
    if short:
        p = {**p, "street1": short_street(p["street1"]), "street2": short_street(p["street2"])}
    text = {"street1": p["street1"], "street2": p["street2"], "dir": dir_text(p, dir_style),
            "view": view_text(p, "short" if short else "full"), "name": short_street(p["name"]) if short else p["name"]}
    out = []
    for line in lines if isinstance(lines, list) and lines else DEFAULT_LINES:
        bits = [text[k] for k in (line if isinstance(line, list) else []) if k in text and text[k]]
        if bits:
            out.append(" ".join(bits))
    return out


def tag_text(name: str, dir_style="short", short=True) -> str:
    """The corner tag: the direction ("EB", "N"), else the view ("Cam 2", "Pano"); "" when the name has neither."""
    p = split(name)
    if p["view"] == "Camera 1":                       # Surrey's usual single camera: nothing worth a tag
        p = {**p, "view": ""}
    return dir_text(p, dir_style) or view_text(p, "short" if short else "full")
