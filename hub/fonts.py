"""Font audit: every BDF under assets/fonts with its metrics, origin, where it's used, and the user's verdict.

Metadata (family / origin / licence / tags) lives in assets/fonts/catalog.json; fonts under
assets/fonts/candidates/ are new finds that nothing uses yet. The verdicts (approved / dropped) are
config: ``hub.fonts.status`` — used when the alert and EEW themes are redone.
"""

from __future__ import annotations

import json
import os
import re
import threading

from hub import config

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, "assets", "fonts")
_cache = {"key": None, "fonts": None}
_lock = threading.Lock()

LATIN_SAMPLE = "The quick brown fox jumps over the lazy dog. 0123456789 °C % $ — RAIN 12mm WIND 60km/h"
JP_SAMPLE = "緊急地震速報 石川県能登地方で地震 震度5強 東京 S波到達まで23秒 強い揺れに警戒"


def _files():
    out = []
    for d, _, names in os.walk(ROOT):
        for n in names:
            if n.endswith(".bdf"):
                out.append(os.path.relpath(os.path.join(d, n), ROOT).replace("\\", "/"))
    return sorted(out)


def _stats(rel):
    """Metrics straight from the BDF text (no full parse): size, glyph coverage, weight."""
    t = open(os.path.join(ROOT, rel), encoding="latin-1").read()
    head = t.split("STARTCHAR", 1)[0]
    reg = (re.search(r'CHARSET_REGISTRY\s+"?([^"\n]+)', head) or [None, ""])[1].upper()
    bbx = re.search(r"FONTBOUNDINGBOX\s+(-?\d+)\s+(-?\d+)", head)
    px = re.search(r"PIXEL_SIZE\s+(\d+)", head)
    weight = (re.search(r'WEIGHT_NAME\s+"([^"]*)"', head) or [None, ""])[1]
    codes = [int(c) for c in re.findall(r"ENCODING\s+(\d+)", t)]
    jis = "JISX" in reg
    if jis:                                        # JIS row/cell: hiragana row 4, kanji from row 16
        kana = sum(1 for c in codes if 0x2421 <= c <= 0x2576)
        kanji = sum(1 for c in codes if c >= 0x3021)
        ascii_n = 0
    else:
        kana = sum(1 for c in codes if 0x3041 <= c <= 0x30FF)
        kanji = sum(1 for c in codes if 0x4E00 <= c <= 0x9FFF)
        ascii_n = sum(1 for c in codes if 33 <= c <= 126)
    name = os.path.basename(rel)[:-4]
    bold = bool(re.search(r"bold|demi|black|heavy", weight, re.I)) or bool(re.search(r"(?i)bold", name)) or bool(re.search(r"([-_]b$|\db$|\db-uni$|[a-z0-9]B\d*$|[a-z]B\d|^clB|B[IO]?\d+$)", name))
    return {"glyphs": len(codes), "height": int(px.group(1)) if px else (int(bbx.group(2)) if bbx else 0),
            "cell_w": int(bbx.group(1)) if bbx else 0, "ascii": ascii_n, "kana": kana, "kanji": kanji,
            "jis": jis, "bold": bold, "bytes": len(t)}


def _usage():
    """path → ["Weather alerts: Terminus family", "Japan EEW: Pachinko", "Main display: clock", …]."""
    use = {}

    def add(path, label):
        path = path.replace("fonts/", "", 1) if path.startswith("fonts/") else path
        use.setdefault(path, [])
        if label not in use[path]:
            use[path].append(label)

    def walk(obj, label):
        if isinstance(obj, str) and obj.endswith(".bdf"):
            add(obj, label)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v, label)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v, label)

    try:
        from display.modules import alert_themes
        for fid, fam in alert_themes.FAMILIES.items():
            walk(fam, f"Weather alerts: {fam.get('name', fid)}")
    except Exception:
        pass
    try:
        from display.modules import quake
        for tid, th in quake.THEMES.items():
            roles = {**quake.BASE_FONTS, **(th.get("fonts") or {})}
            walk(roles, f"Japan EEW: {th['label']}")
    except Exception:
        pass
    try:
        from display.modules import weather_alert
        src = open(weather_alert.__file__, encoding="utf-8").read()
        for m in re.findall(r'"(alert/[\w.+-]+\.bdf)"', src):
            add(m, "Weather alerts: icons & labels")
    except Exception:
        pass
    for role, spec in (config.load().get("fonts") or {}).items():
        if isinstance(spec, dict) and spec.get("file"):
            add(spec["file"], f"Main display: {role}")
    return use


def catalog():
    """All fonts with stats, metadata, usage and verdict (stats cached until a file changes)."""
    files = _files()
    key = tuple((f, os.path.getmtime(os.path.join(ROOT, f))) for f in files)
    with _lock:
        if _cache["key"] != key:
            _cache["fonts"] = {f: _stats(f) for f in files}
            _cache["key"] = key
        stats = _cache["fonts"]
    try:
        meta = json.load(open(os.path.join(ROOT, "catalog.json"), encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    status = ((config.load().get("hub") or {}).get("fonts") or {}).get("status") or {}
    use = _usage()
    out = []
    for f in files:
        m = meta.get(f, {})
        st = stats[f]
        script = "japanese" if st["kana"] or st["kanji"] else "latin"
        out.append({"path": f, "name": os.path.basename(f)[:-4], "family": m.get("family") or os.path.basename(f)[:-4],
                    "origin": m.get("origin", ""), "licence": m.get("licence", ""), "tags": m.get("tags", []),
                    "candidate": f.startswith("candidates/"), "script": script, "used_by": use.get(f, []),
                    "status": status.get(f) or ("candidate" if f.startswith("candidates/") else "unreviewed"), **st})
    return out


def render_png(rel, text, jp, scale=2, width=448, fg=(255, 255, 255), height=None):
    """The sample in one font, wrapped to the wall's width, as a PNG (cropped to the text)."""
    import io
    from PIL import Image
    from display.gfx import Canvas
    from display.modules import font_specimen as fs
    h = height or 192
    c = Canvas(width, h)
    y = fs.render(c, [rel], text, jp, fg, width=width, height=h, label=False)
    y = max(8, min(h, (height or y) + 1))
    k = max(1, min(4, int(scale)))
    img = Image.fromarray(c.buf[:y]).resize((width * k, y * k), Image.NEAREST)
    bio = io.BytesIO()
    img.save(bio, "PNG")
    return bio.getvalue()
