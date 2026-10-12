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


_JIS1 = None


def _jis1():
    """The 2,965 JIS X 0208 level-1 kanji (rows 16–47) — the yardstick for "full kanji support"."""
    global _JIS1
    if _JIS1 is None:
        out = set()
        for row in range(0xB0, 0xD0):
            for cell in range(0xA1, 0xFF):
                try:
                    out.add(ord(bytes([row, cell]).decode("euc_jp")))
                except (UnicodeDecodeError, TypeError):
                    pass
        _JIS1 = {c for c in out if 0x4E00 <= c <= 0x9FFF}
    return _JIS1


def _unicode(codes, reg):
    """The font's code points as Unicode (JIS-encoded fonts are mapped through EUC)."""
    if "JISX0201" in reg:
        return {c if c < 0x80 else 0xFF61 + c - 0xA1 for c in codes if c < 0x80 or 0xA1 <= c <= 0xDF}
    if "JISX" in reg:
        cd = "euc_jis_2004" if "0213" in reg else "euc_jp"
        out = set()
        for c in codes:
            if c < 0x80:
                out.add(c)
                continue
            try:
                u = bytes([(c >> 8) | 0x80, (c & 0xFF) | 0x80]).decode(cd)
                if len(u) == 1:
                    out.add(ord(u))
            except UnicodeDecodeError:
                pass
        return out
    return set(codes)


def _sets(cps):
    """Which Japanese / Latin character sets a font covers, as counts and readable labels."""
    hira = sum(1 for c in cps if 0x3041 <= c <= 0x3096)
    kata = sum(1 for c in cps if 0x30A1 <= c <= 0x30F6)
    half = sum(1 for c in cps if 0xFF61 <= c <= 0xFF9F)
    kanji = sum(1 for c in cps if 0x4E00 <= c <= 0x9FFF)
    jis1 = len(cps & _jis1()) / len(_jis1())
    upper = all(c in cps for c in range(0x41, 0x5B))
    lower = all(c in cps for c in range(0x61, 0x7B))
    digits = all(c in cps for c in range(0x30, 0x3A))
    labels = []
    for n, full, name in ((hira, 83, "Hiragana"), (kata, 86, "Katakana"), (half, 63, "Half-width katakana")):
        if n >= full - 3:
            labels.append(name)
        elif n:
            labels.append(f"{name} (partial: {n})")
    if upper and lower and digits:
        labels.append("Alphanumeric")
    elif upper and digits:
        labels.append("Uppercase + digits")
    elif digits:
        labels.append("Digits")
    if kanji:
        labels.append(f"{kanji:,} kanji · {jis1:.0%} of JIS level 1")
    category = "kanji" if jis1 >= 0.95 else "kana" if max(hira, kata, half) >= 40 else "latin"
    return {"hiragana": hira, "katakana": kata, "halfwidth": half, "kanji": kanji, "jis1": round(jis1, 3),
            "alnum": "full" if (upper and lower and digits) else "upper" if (upper and digits) else "digits" if digits else "none",
            "sets": labels, "category": category}


_PROBE = "AHgjy0あア漢ｱ"


def _ink_height(t, reg):
    """Real text height in LED pixels: from the top of the tallest to the bottom of the lowest of a few
    representative glyphs (caps, descenders, digits, kana, kanji), read from their BBX lines. Header
    sizes (PIXEL_SIZE, ascent + descent) include line spacing and are often off by several pixels."""
    want = {}
    for ch in _PROBE:
        cp = ord(ch)
        if "JISX0201" in reg:
            code = cp if cp < 0x80 else (0xA1 + cp - 0xFF61 if 0xFF61 <= cp <= 0xFF9F else None)
        elif "JISX" in reg:
            try:
                b = ch.encode("euc_jis_2004" if "0213" in reg else "euc_jp")
                code = ((b[0] & 0x7F) << 8) | (b[1] & 0x7F) if len(b) == 2 and b[0] >= 0xA1 else (cp if cp < 0x80 else None)
            except UnicodeEncodeError:
                code = None
        else:
            code = cp
        if code is not None:
            want[code] = ch
    top, bottom = None, None
    for code, h, yo in re.findall(r"ENCODING\s+(\d+)\s.*?BBX\s+-?\d+\s+(\d+)\s+-?\d+\s+(-?\d+)", t, flags=re.S):
        if int(code) in want:
            h, yo = int(h), int(yo)
            top = h + yo if top is None else max(top, h + yo)
            bottom = yo if bottom is None else min(bottom, yo)
    return (top - bottom) if top is not None else 0


def _stats(rel):
    """Metrics straight from the BDF text (no full parse): size, character sets, weight."""
    t = open(os.path.join(ROOT, rel), encoding="latin-1").read()
    head = t.split("STARTCHAR", 1)[0]
    reg = (re.search(r'CHARSET_REGISTRY\s+"?([^"\n]+)', head) or [None, ""])[1].upper()
    bbx = re.search(r"FONTBOUNDINGBOX\s+(-?\d+)\s+(-?\d+)", head)
    px = re.search(r"PIXEL_SIZE\s+(\d+)", head)
    weight = (re.search(r'WEIGHT_NAME\s+"([^"]*)"', head) or [None, ""])[1]
    codes = [int(c) for c in re.findall(r"ENCODING\s+(\d+)", t)]
    cps = _unicode(codes, reg)
    ink = _ink_height(t, reg)
    name = os.path.basename(rel)[:-4]
    bold = bool(re.search(r"bold|demi|black|heavy", weight, re.I)) or bool(re.search(r"(?i)bold", name)) or bool(re.search(r"([-_]b$|\db$|\db-uni$|[a-z0-9]B\d*$|[a-z]B\d|^clB|B[IO]?\d+$)", name))
    nominal = int(px.group(1)) if px else (int(bbx.group(2)) if bbx else 0)
    return {"glyphs": len(codes), "height": ink or nominal, "nominal": nominal,
            "cell_w": int(bbx.group(1)) if bbx else 0, "ascii": sum(1 for c in cps if 33 <= c <= 126),
            "jis": "JISX" in reg, "bold": bold, "bytes": len(t), **_sets(cps)}


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
        script = st["category"]
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
