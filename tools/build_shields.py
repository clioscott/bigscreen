"""Build the LED highway-shield sprites (assets/icons/shields/shields.json) from Wikimedia Commons SVGs.

    python tools/build_shields.py <dir with the SVGs> [--png-dir out]     (needs resvg-py, pillow, numpy, scipy, scikit-image)

Plain downsampling turns a 20-px shield into mush, so each size is "pixel-hinted" instead:

  1. the blank SVG is rendered at 900 px and every pixel snapped to the sign's few flat colours;
  2. its tiny lettering ("INTERSTATE", "B.C.", "TRANS CANADA", "YELLOWHEAD") is painted out with the band
     colour behind it — the display redraws it in a real micro font only where one fits;
  3. for each target height, colours wider than ~1.5 LED px are filled by coverage (each LED pixel takes the
     colour covering most of it; averaged with the mirror image for symmetric signs; lone pixels cleaned up);
  4. colours thinner than that (borders, outlines, fine stripes) are drawn as exact 1-px lines along their
     centre lines (skeleton, scaled to the size and thinned again), so they stay crisp and unbroken.

The route number is never downsampled: the display draws it with a bitmap font chosen per size to fill the
sign's number box (stored here as fractions of the sign). The output is plain pixel-art rows, so any size
can be hand-touched in the JSON afterwards.

Sources (Wikimedia Commons, public domain): BC-blank.svg, TCH-blank.svg, BC Yellowhead blank.svg,
I-blank.svg, US blank.svg, WA-blank.svg; the Crowsnest shield is English Wikipedia's
"British Columbia Highway 3.svg" (a fair-use file — used here only to derive the wall's own pixel art).
"""

from __future__ import annotations

import io
import json
import os
import re
import sys

import numpy as np
from PIL import Image

SIZES = [12, 14, 16, 18, 20, 22, 24, 28, 32, 40, 48, 56, 64]
KEYS = ".abcdefghij"            # '.' = transparent; letters index the palette
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icons", "shields", "shields.json")

# zones: (label, x0, y0, x1, y1 as fractions, text colour index painted out → fill index)
SPECS = {
    "bc": {"file": "BC-blank.svg", "name": "British Columbia highway", "number_bg": 0, "inner": [0, [0.10, 0.30, 0.90, 0.86]],
           "palette": ["#f8f8f8", "#1850a0", "#e81828", "#f8d038", "#1070c0", "#000000"],
           "number": 1, "box": [0.15, 0.40, 0.85, 0.80], "sym": True, "crest": [0.35, 0.0, 0.65, 0.28],
           "rim": [[1, 0.065]], "simple": {"below": 16, "fill": 0, "number": 1, "box": [0.14, 0.24, 0.86, 0.84]},
           "zones": [{"label": "B.C.", "box": [0.24, 0.72, 0.76, 0.89], "text": [1, 4], "fill": 0}]},
    "tch": {"file": "TCH-blank.svg", "name": "Trans-Canada Highway", "span_top": 0.16, "inner": [1, [0.10, 0.14, 0.90, 0.76]],
            "palette": ["#006048", "#f8f8f8", "#000000"],
            "number": 0, "box": [0.33, 0.30, 0.67, 0.71], "sym": True, "emblem": {"cls": 1, "box": [0.1, 0.14, 0.9, 0.76], "scale": 1.1, "scale_mid": 1.28},
            "rim": [[0, 0.0], [1, 0.0, 18]],
            "simple": {"below": 16, "fill": 0, "number": 1, "box": [0.12, 0.12, 0.88, 0.88]},
            "zones": [{"label": "TRANS CANADA", "box": [0.05, 0.0, 0.95, 0.15], "text": [1], "fill": 0}]},
    "yellowhead": {"file": "BC_Yellowhead_blank.svg", "name": "Yellowhead Highway", "inner": [1, [0.15, 0.36, 0.90, 0.85]],
                   "palette": ["#f8f8f8", "#f8c800", "#000000", "#008000"],
                   "number": 2, "box": [0.30, 0.40, 0.76, 0.76], "sym": False, "broad": [3],
                   "emblem": {"cls": 1, "box": [0.2, 0.36, 0.85, 0.82], "scale": 1.3, "scale_mid": 1.45}, "rim": [[2, 0.03]],
                   "simple": {"below": 16, "fill": 0, "number": 2, "box": [0.22, 0.34, 0.78, 0.84],
                              "disc": [0.5, 0.59, 0.38, 0.31, 1]},
                   "outline": {"of": 1, "colour": 2, "against": [0, 3], "min_h": 20},
                   "zones": [{"label": "YELLOWHEAD", "box": [0.12, 0.22, 0.88, 0.355], "text": [2], "fill": 0},
                             {"label": "B.C.", "box": [0.30, 0.82, 0.70, 0.95], "text": [2], "fill": 0}]},
    "crowsnest": {"file": "British_Columbia_Highway_3.svg", "name": "Crowsnest Highway", "inner": [1, [0.28, 0.30, 0.74, 0.74]],
                  "palette": ["#f8f8f8", "#000000"], "number": 0, "box": [0.38, 0.36, 0.64, 0.70], "sym": False,
                  "rim": [[1, 0.03]], "broad": [1], "emblem": {"cls": 1, "box": [0.16, 0.18, 0.88, 0.76], "scale": 1.15, "scale_mid": 1.3},
                  "zones": [{"label": "3", "box": [0.36, 0.34, 0.66, 0.72], "text": [0], "fill": 1},
                            {"label": "B.C.", "box": [0.28, 0.76, 0.72, 0.93], "text": [1], "fill": 0}],
                  "simple": {"below": 16, "fill": 0, "number": 0, "box": [0.2, 0.25, 0.8, 0.8], "disc": [0.5, 0.52, 0.30, 0.27, 1]}},
    "interstate": {"file": "I-blank.svg", "name": "US Interstate", "span_top": 0.27, "inner": [0, [0.04, 0.28, 0.96, 0.96]],
                   "palette": ["#003880", "#a81828", "#f8f8f8", "#000000"],
                   "number": 2, "box": [0.14, 0.34, 0.86, 0.84], "sym": True, "rim": [[2, 0.03]],
                   "zones": [{"label": "INTERSTATE", "box": [0.10, 0.03, 0.90, 0.22], "text": [2], "fill": 1}]},
    "us": {"file": "US_blank.svg", "name": "US Highway", "span_top": 0.0, "inner": [0, [0.06, 0.08, 0.94, 0.92]], "palette": ["#f8f8f8", "#000000"],
           "number": 1, "box": [0.12, 0.17, 0.88, 0.84], "sym": True, "zones": [], "shapes": [0]},
    "wa": {"file": "WA-blank.svg", "name": "Washington State Route", "inner": [0, [0.04, 0.14, 0.96, 0.66]], "palette": ["#f8f8f8", "#000000"],
           "number": 1, "box": [0.17, 0.24, 0.85, 0.60], "sym": False, "zones": [], "shapes": [0]},
}
ALIASES = {}


def _hex(c):
    return np.array([int(c[i:i + 2], 16) for i in (1, 3, 5)], np.float32)


def render(path, H=900):
    import resvg_py
    s = open(path, encoding="utf8").read()
    vb = re.search(r'viewBox="([\d.\s-]+)"', s)
    if vb:
        _, _, w, h = [float(v) for v in vb.group(1).split()]
        s = re.sub(r'(<svg[^>]*?)\swidth="[^"]*"', r"\1", s, count=1)
        s = re.sub(r'(<svg[^>]*?)\sheight="[^"]*"', r"\1", s, count=1)
        s = s.replace("<svg", f'<svg width="{w * H / h:.1f}" height="{H}"', 1)
    im = Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=s, shape_rendering="crisp_edges")))).convert("RGBA")
    if im.height != H:
        im = im.resize((round(im.width * H / im.height), H), Image.LANCZOS)
    return np.asarray(im)


def classify(rgba, palette):
    """Hi-res RGBA → class map (0 transparent, 1+i palette colour i)."""
    pal = np.stack([_hex(c) for c in palette])
    rgb = rgba[..., :3].astype(np.float32)
    d = ((rgb[:, :, None, :] - pal[None, None]) ** 2).sum(-1)
    cls = d.argmin(-1) + 1
    cls[rgba[..., 3] < 128] = 0
    return cls


def thinness(cls, n):
    """Typical stroke width (hi-res px) of each class: twice the median distance-to-edge along its skeleton."""
    from scipy import ndimage
    from skimage.morphology import skeletonize
    out = {}
    for c in range(1, n + 1):
        m = cls == c
        if not m.any():
            out[c] = 1e9
            continue
        dt = ndimage.distance_transform_edt(m)
        sk = skeletonize(m)
        out[c] = 2.0 * float(np.median(dt[sk])) if sk.any() else 1e9
        if m.mean() > 0.2:                               # a main fill is never a "line"
            out[c] = max(out[c], 1e6)
    return out


def hint(cls, n, h, sym, widths, keep=()):
    """One size: broad colours by (weighted) coverage, fine lines as 1-px strokes along their centre lines."""
    from scipy import ndimage
    from skimage.morphology import skeletonize
    H0, W0 = cls.shape
    w = max(4, round(W0 * h / H0))
    k = h / H0
    thin = [c for c in range(1, n + 1) if widths[c] * k < 1.5]
    # 1. broad layer: thin colours handed to the nearest broad colour, then coverage per LED pixel
    broad = cls.copy()
    tm = np.isin(cls, thin)
    if tm.any():
        _, (iy, ix) = ndimage.distance_transform_edt(tm, return_indices=True)
        broad = cls[iy, ix]
    cov = np.zeros((n + 1, h, w), np.float32)
    for c in range(n + 1):
        img = Image.fromarray(((broad == c) * 255).astype(np.uint8))
        cov[c] = np.asarray(img.resize((w, h), Image.BOX), np.float32) / 255.0
    if sym:
        cov = (cov + cov[:, :, ::-1]) / 2
    opaque = cov[1:].sum(0) >= 0.5
    out = np.where(opaque, cov[1:].argmax(0) + 1, 0)
    # 2. clean-up: a lone pixel surrounded by one other colour joins it
    for y in range(h):
        for x in range(w):
            v = out[y, x]
            nb = [out[yy, xx] for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)) if 0 <= yy < h and 0 <= xx < w]
            if len(nb) == 4 and all(q != v for q in nb):
                vals, counts = np.unique(nb, return_counts=True)
                if counts.max() >= 3:
                    out[y, x] = vals[counts.argmax()]
    # 3. fine lines: skeleton of each thin colour, scaled to this size, thinned again to exactly 1 px
    for c in thin:
        sk = skeletonize(cls == c)
        ys, xs = np.nonzero(sk)
        m = np.zeros((h, w), bool)
        m[np.clip((ys * k).astype(int), 0, h - 1), np.clip((xs * w / W0).astype(int), 0, w - 1)] = True
        if sym:
            m |= m[:, ::-1]
        m = skeletonize(m) | (m & ~ndimage.binary_dilation(skeletonize(m)))   # keep isolated dots too
        out[m & opaque] = c
        out[m & ~opaque] = c                          # an outline on the very edge still belongs to the sign
    despeckle(out, set(thin) | set(keep), min_px=max(2, h // 7))
    if sym:
        out = np.where(np.arange(w)[None, :] < w / 2, out, out[:, ::-1])
    broad_px = np.where(opaque, cov[1:].argmax(0) + 1, 0)
    return out, broad_px


def crest(px, box, _k=1.0):
    """The BC crest, simplified for LED sizes: the Union flag (blue with a red cross) over a gold sun rising from
    blue water — palette 1 blue, 2 red, 3 gold, 4 sky blue, 0 white (class = index + 1)."""
    h, w = px.shape
    x0, y0, x1, y1 = int(round(box[0] * w)), int(round(box[1] * h)), int(round(box[2] * w)), max(3, int(round(box[3] * h)))
    cw, ch = x1 - x0, y1 - y0
    if cw < 3 or ch < 3:
        return
    fh = max(1, ch // 2)                                 # flag on top
    px[y0:y0 + fh, x0:x1] = 2
    mid = x0 + cw // 2
    px[y0:y0 + fh, mid - (1 if cw % 2 == 0 and cw >= 6 else 0):mid + 1] = 3
    if fh >= 3:
        px[y0 + fh // 2, x0:x1] = 3
    sh = ch - fh                                          # sun: gold half-disc over a sky-blue band
    for yy in range(sh):
        r = sh - 0.5
        dy = sh - yy - 0.5
        half = (max(0.0, r * r - dy * dy)) ** 0.5 * (cw / 2) / max(r, 1)
        for xx in range(cw):
            if abs(xx + 0.5 - cw / 2) <= half:
                px[y0 + fh + yy, x0 + xx] = 4
            else:
                px[y0 + fh + yy, x0 + xx] = 1
    px[y1 - 1, x0:x1] = 5 if sh >= 2 else px[y1 - 1, x0:x1]


def enlarge(cls, em):
    """Scale one colour's shape (the leaf, the head) about its centre inside a box, so the number fits in it."""
    H0, W0 = cls.shape
    x0, y0, x1, y1 = int(em["box"][0] * W0), int(em["box"][1] * H0), int(em["box"][2] * W0), int(em["box"][3] * H0)
    sub = cls[y0:y1, x0:x1]
    m = sub == em["cls"] + 1
    if not m.any():
        return
    ys, xs = np.nonzero(m)
    cy, cx = ys.mean(), xs.mean()
    s = float(em["scale"])
    hh, ww = m.shape
    yy, xx = np.mgrid[0:hh, 0:ww]
    sy = np.clip(((yy - cy) / s + cy).round().astype(int), 0, hh - 1)
    sx = np.clip(((xx - cx) / s + cx).round().astype(int), 0, ww - 1)
    big = m[sy, sx]
    sub[big] = em["cls"] + 1


def _smooth(mask, sym):
    """Pixel-art clean-up of a shape's edge: drop 1-px spurs, fill 1-px notches (twice), keep it symmetric."""
    m = mask.copy()
    for _ in range(2):
        p = np.pad(m, 1)
        n4 = p[:-2, 1:-1].astype(int) + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]
        m = (m & (n4 >= 2)) | (~m & (n4 >= 3))
        if sym:
            w = m.shape[1]
            m = np.where(np.arange(w)[None, :] < w / 2, m, m[:, ::-1])
    return m


def _ring(mask):
    """The 1-px, 8-connected outer boundary of a shape (pixels with a 4-neighbour outside it)."""
    p = np.pad(mask, 1)
    inner = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return mask & ~inner


def rims(px, broad, spec, h, sym):
    """Crisp silhouette and outline(s): smooth the sign's outline, then draw each rim colour as exact 1-px rings
    from the outside in (width ~ fraction of the height, at least 1 px). Inner shapes get the same smoothing."""
    sil = _smooth(px > 0, sym)
    fill_src = np.where(px > 0, px, broad)
    px[:] = np.where(sil, np.where(px > 0, px, fill_src), 0)
    for c in spec.get("shapes", []):                     # e.g. the white US shield on its black plate
        m = _smooth(px == c + 1, sym) & sil
        rest = px[sil & (px != c + 1)]
        plate = np.bincount(rest).argmax() if rest.size else 0
        gone = ~m & (px == c + 1)
        px[m] = c + 1
        px[gone] = plate
    cur = sil.copy()
    band = np.zeros_like(sil)
    for rim in spec.get("rim", []):
        c, frac = rim[0], rim[1]
        if len(rim) > 2 and h < rim[2]:
            continue
        for _ in range(max(1, int(round(frac * h)))):
            r = _ring(cur)
            px[r] = c + 1
            band |= r
            cur &= ~r
    if spec.get("rim"):                                    # the next ring in: no stray rim-coloured specks there
        inner = _ring(cur)
        rc = {r[0] + 1 for r in spec["rim"]}
        for y, x in zip(*np.nonzero(inner)):
            if px[y, x] in rc:
                nb = [px[yy, xx] for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
                      if 0 <= yy < px.shape[0] and 0 <= xx < px.shape[1] and cur[yy, xx] and px[yy, xx] not in rc]
                if nb:
                    v, cnt = np.unique(nb, return_counts=True)
                    px[y, x] = v[cnt.argmax()]


def despeckle(px, keep, min_px=3):
    """Merge connected bits of a colour smaller than min_px into the colour around them (lines in ``keep`` stay)."""
    from scipy import ndimage
    for _ in range(2):
        for c in np.unique(px):
            if c == 0 or c in keep:
                continue
            lab, n = ndimage.label(px == c)
            for i in range(1, n + 1):
                comp = lab == i
                if comp.sum() >= min_px:
                    continue
                ring = ndimage.binary_dilation(comp) & ~comp
                vals = px[ring]
                vals = vals[vals != c]
                if len(vals):
                    v, cnt = np.unique(vals, return_counts=True)
                    px[comp] = v[cnt.argmax()]


def inner_box(px, colour, sym=False, within=None, margin=0):
    """Largest rectangle made only of ``colour`` pixels (the inside of the head or leaf), as fractions of the
    sprite — where the number may go without touching the emblem's outline. Centred when the sign is symmetric."""
    h, w = px.shape
    m = px == colour
    if margin:                                           # keep a ring of the background round the number
        from scipy import ndimage
        m = ndimage.binary_erosion(m, iterations=margin, border_value=0)
    if within:
        lim = np.zeros_like(m)
        lim[int(within[1] * h):int(np.ceil(within[3] * h)), int(within[0] * w):int(np.ceil(within[2] * w))] = True
        m &= lim
    best, bb = 0, (0, 0, w, h)
    heights = np.zeros(w, int)
    for y in range(h):
        heights = np.where(m[y], heights + 1, 0)
        for x0 in range(w):
            hmin = 10 ** 9
            for x1 in range(x0, w):
                hmin = min(hmin, heights[x1])
                if hmin == 0:
                    break
                bw = x1 - x0 + 1
                if sym and abs((x0 + x1 + 1) - w) > 1:   # keep it centred on symmetric signs
                    continue
                score = bw * hmin * (1.0 if bw >= hmin * 0.6 else 0.7)
                if score > best:
                    best, bb = score, (x0, y - hmin + 1, x1 + 1, y + 1)
    x0, y0, x1, y1 = bb
    return [x0 / w, y0 / h, x1 / w, y1 / h]


def span_box(px, colour, top_rows=0, margin=1):
    """BC: the white below the crest, from the crest's bottom to the bottom of the white at the centre column
    (each less a margin), as wide as the white is at the middle of that span. The number centres in it."""
    h, w = px.shape
    col = px[:, w // 2] == colour
    y0 = top_rows
    while y0 < h and not col[y0]:
        y0 += 1
    y1 = y0
    while y1 < h and col[y1]:
        y1 += 1
    y0, y1 = y0 + margin, y1 - margin
    mid = (y0 + y1) // 2
    row = px[mid] == colour
    x0 = w // 2
    while x0 > 0 and row[x0 - 1]:
        x0 -= 1
    x1 = w - x0
    x0, x1 = x0 + margin, x1 - margin
    return [x0 / w, y0 / h, x1 / w, y1 / h]


def allow_area(px, colour, within, margin, span_top=None):
    """Pixels the number may cover (its background colour inside the region, less a margin) and the point it
    centres on: the middle of the centre column's run (signs with a crest/band above) or the area's centroid."""
    from scipy import ndimage
    h, w = px.shape
    m = px == colour
    lim = np.zeros_like(m)
    lim[int(within[1] * h):int(np.ceil(within[3] * h)), int(within[0] * w):int(np.ceil(within[2] * w))] = True
    m &= lim
    full = m.copy()
    if margin:
        m = ndimage.binary_erosion(m, iterations=margin, border_value=0)
    if span_top is not None:
        col = full[:, w // 2]
        runs, y = [], span_top                            # the longest run below span_top (skips 1-px rules)
        while y < h:
            if col[y]:
                y0 = y
                while y < h and col[y]:
                    y += 1
                runs.append((y - y0, y0, y))
            y += 1
        _, y0, y1 = max(runs) if runs else (0, span_top, h)
        cy = (y0 + y1) / 2.0
        cx = w / 2.0
    else:
        ys, xs = np.nonzero(m if m.any() else full)
        cy, cx = (ys.mean() + 0.5, xs.mean() + 0.5) if len(ys) else (h / 2.0, w / 2.0)
    return m, [round(cx, 2), round(cy, 2)]


def outline(px, of, colour, against):
    """A 1-px outline on the rim of one colour where it meets the given others (the Yellowhead's head)."""
    h, w = px.shape
    src = px.copy()
    for y in range(h):
        for x in range(w):
            if src[y, x] != of:
                continue
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < h and 0 <= xx < w and src[yy, xx] in against:
                    px[y, x] = colour
                    break


DIGIT_HEIGHTS = list(range(5, 43))
GLYPHS = "0123456789ABCDEW"                  # route numbers and their suffixes (99A, 1A, 97W…)


def digits(font_path, png_dir=None):
    """Route-number digits in Overpass Bold (an open font derived from FHWA Highway Gothic, the signs' own face),
    hinted at each digit height: rendered at 400 px, coverage-sampled, stems kept at least 1 px. A narrow cut
    (squeezed to 76 %, like Series C/D) serves three-digit routes."""
    from PIL import ImageDraw, ImageFont
    f = ImageFont.truetype(font_path, 560)
    f.set_variation_by_name("Bold")
    out = {"bold": {}, "narrow": {}}
    glyphs = {}
    for d in GLYPHS:
        img = Image.new("L", (520, 640), 0)
        ImageDraw.Draw(img).text((40, 20), d, font=f, fill=255)
        a = np.asarray(img) > 127
        ys, xs = np.nonzero(a)
        glyphs[d] = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    cap = max(glyphs[d].shape[0] for d in "0123456789")
    for name, squeeze in (("bold", 1.0), ("narrow", 0.76)):
        for hd in DIGIT_HEIGHTS:
            k = hd / cap
            out[name][str(hd)] = {}
            for d, g in glyphs.items():
                w = max(1, round(g.shape[1] * k * squeeze))
                gh = max(1, round(g.shape[0] * k))
                cov = np.asarray(Image.fromarray((g * 255).astype(np.uint8)).resize((w, gh), Image.BOX), np.float32) / 255
                if gh < hd:                                   # letters a touch shorter than digits sit on the baseline
                    cov = np.vstack([np.zeros((hd - gh, w), np.float32), cov])
                cov = cov[-hd:]
                m = cov >= 0.42
                # keep thin stems: any column/row of the glyph whose coverage peaks above 0.25 keeps its best pixel
                for x in range(w):
                    if not m[:, x].any() and cov[:, x].max() > 0.25:
                        m[cov[:, x].argmax(), x] = True
                for y in range(hd):
                    if not m[y].any() and cov[y].max() > 0.25:
                        m[y, cov[y].argmax()] = True
                out[name][str(hd)][d] = ["".join("#" if v else "." for v in row) for row in m]
    if png_dir:
        rows = []
        for name in ("bold", "narrow"):
            for hd in DIGIT_HEIGHTS:
                ims = [np.array([[c == "#" for c in r] for r in out[name][str(hd)][d]]) for d in GLYPHS]
                wd = sum(i.shape[1] + 2 for i in ims)
                strip = np.zeros((hd, wd), bool)
                x = 0
                for i in ims:
                    strip[:, x:x + i.shape[1]] = i
                    x += i.shape[1] + 2
                rows.append(strip)
        W = max(r.shape[1] for r in rows)
        sheet = np.zeros((sum(r.shape[0] + 3 for r in rows), W), np.uint8)
        y = 0
        for r in rows:
            sheet[y:y + r.shape[0], :r.shape[1]] = r * 255
            y += r.shape[0] + 3
        Image.fromarray(sheet).resize((W * 4, sheet.shape[0] * 4), Image.NEAREST).save(os.path.join(png_dir, "digits.png"))
    return out


def build(src_dir, png_dir=None):
    data = {"_about": __doc__.strip().split("\n\n")[0], "_source": "Wikimedia Commons (public domain)",
            "aliases": ALIASES, "kinds": {}}
    for kind, sp in SPECS.items():
        rgba = render(os.path.join(src_dir, sp["file"]))
        n = len(sp["palette"])
        cls = classify(rgba, sp["palette"])
        H0, W0 = cls.shape
        for z in sp["zones"]:
            x0, y0, x1, y1 = z["box"]
            sub = cls[int(y0 * H0):int(y1 * H0), int(x0 * W0):int(x1 * W0)]
            for t in z["text"]:
                sub[sub == t + 1] = z["fill"] + 1
        sizes, meta = {}, {}
        widths0 = None
        for h in SIZES:
            c2 = cls.copy()
            em = sp.get("emblem")
            if em:                                       # exaggerated more at medium sizes, so the number fits
                enlarge(c2, dict(em, scale=em["scale"] if h >= 24 else em.get("scale_mid", em["scale"])))
            widths = thinness(c2, n)
            for c in sp.get("broad", []):
                widths[c + 1] = 1e6                      # drawn as solid shapes, never as traced lines
            widths0 = widths0 or widths
            px, broad_px = hint(c2, n, h, sp["sym"], widths, keep=[em["cls"] + 1] if em else [])
            simple = sp.get("simple") if sp.get("simple") and h < sp["simple"]["below"] else None
            if simple:                                   # too small for the design: the shape and colours only
                sil = _smooth(px > 0, sp["sym"])
                px[:] = np.where(sil, simple["fill"] + 1, 0)
                if simple.get("disc"):
                    cx, cy, rx, ry, col = simple["disc"]
                    hh, ww = px.shape
                    yy, xx = np.mgrid[0:hh, 0:ww]
                    d = ((xx + 0.5 - cx * ww) / (rx * ww)) ** 2 + ((yy + 0.5 - cy * hh) / (ry * hh)) ** 2
                    px[(d <= 1.0) & sil] = col + 1
                rims(px, px.copy(), {**sp, "shapes": []}, h, sp["sym"])
                bg = simple["disc"][4] if simple.get("disc") else simple["fill"]
                allow, centre = allow_area(px, bg + 1, simple["box"], 0)
                meta[str(h)] = {"number": simple["number"], "allow": ["".join("#" if v else "." for v in r) for r in allow],
                                "centre": centre}
            else:
                rims(px, broad_px, sp, h, sp["sym"])
                if sp.get("crest"):
                    if h >= 16:
                        crest(px, sp["crest"], W0 * h / H0 / px.shape[1])
                    else:                                # too small for a crest: the plain shield top
                        x0, x1 = int(sp["crest"][0] * px.shape[1]), int(np.ceil(sp["crest"][2] * px.shape[1]))
                        top = int(round(sp["crest"][3] * h))
                        sub = px[:top, x0:x1]
                        sub[(sub > 1) & (sub != 2)] = 1
                ol = sp.get("outline")
                if ol and h >= ol["min_h"]:
                    outline(px, ol["of"] + 1, ol["colour"] + 1, [a + 1 for a in ol["against"]])
                ib = sp.get("inner")                     # the number goes inside its area, never over an outline
                span = int(round(sp["crest"][3] * h)) if sp.get("crest") and h >= 16 else \
                    int(round(sp["span_top"] * h)) if sp.get("span_top") is not None else None
                if ib:
                    allow, centre = allow_area(px, ib[0] + 1, ib[1], 1 if h >= 14 else 0, span)
                    meta[str(h)] = {"number": sp["number"], "allow": ["".join("#" if v else "." for v in r) for r in allow],
                                    "centre": centre}
            sizes[str(h)] = ["".join(KEYS[v] for v in row) for row in px]
            if png_dir:
                pal = np.vstack([[0, 0, 0]] + [_hex(c) for c in sp["palette"]]).astype(np.uint8)
                Image.fromarray(pal[px]).resize((px.shape[1] * 8, h * 8), Image.NEAREST).save(
                    os.path.join(png_dir, f"{kind}_{h}.png"))
        widths = widths0
        data["kinds"][kind] = {"name": sp["name"], "file": sp["file"], "palette": sp["palette"], "number": sp["number"],
                               "box": sp["box"], "sizes": sizes, "size_meta": meta}
        print(kind, {c: round(widths[c] * 20 / H0, 2) for c in widths}, "stroke px at 20")
    font = os.path.join(src_dir, "..", "overpass.ttf")
    if os.path.exists(font):
        data["digits"] = digits(font, png_dir)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf8") as f:
        json.dump(data, f, indent=1)
    print("wrote", OUT)


if __name__ == "__main__":
    a = sys.argv[1:]
    build(a[0], a[a.index("--png-dir") + 1] if "--png-dir" in a else None)
