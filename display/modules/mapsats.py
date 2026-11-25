"""Satellite layers on the Space Map (common/satlayers.py; positions from hub/jobs/sats.py in
data/issmap/sats_<layer>.bin): each satellite where it is at the moment the map shows, interpolated between the
hub's positions (30 s apart for low orbits), as an icon or a single pixel; their orbit tracks (each one's ground
track for a whole orbit round where it is, faint and still: the satellites ride along them); for networks linked
by laser, the links between neighbours (a trellis), with data moving along some of them.

Settings (``map.sats.<layer>``, SAT_DEFAULTS): on, mark ("auto": icons when there are few enough, else pixels;
"icon"; "dot"), colour, label (names beside the icons, when there are at most LABEL_MAX), links (the trellis, or
for GPS the ISS's own receivers' view of them, for GOES the downlink to Wallops), tracks (how many satellites' orbit
tracks to draw: 0 none; spread evenly through the layer, or the nearest when near_km is set), near_km (the
trellis and tracks only within this of the ISS; 0: everywhere), brightness (0.1-1: thousands of pixels can drown the map), speed (the data along
the links)."""

from __future__ import annotations

import json
import os
import struct
import time

import numpy as np

from common.satlayers import LAYERS

SAT_DEFAULTS = {"on": False, "mark": "auto", "colour": None, "label": False, "links": False, "tracks": 0,
                "near_km": 3000, "brightness": 0.7, "speed": "normal"}
TRACKS_MAX = 500              # orbit tracks per layer at most
TRACK_DIM = 0.3               # how bright the tracks are against the layer's colour
TRK = struct.Struct("<4sIHddf")
MARKS = ("auto", "icon", "dot")
ICON_MAX = 80                 # auto: icons up to this many satellites on the map, pixels past it
LABEL_MAX = 40                # names only for layers this small
HEAD = struct.Struct("<4sIHIdf")
WALLOPS = (37.94, -75.46)     # NOAA's command and data station for GOES (Wallops, Virginia)
GPS_MASK_KM = 6378.0 + 420.0  # (the ISS sees a GPS satellite when Earth isn't in between)

SAT_ART = {"leo": ["S.S", ".S.", "S.S"], "meo": [".S.", "SSS", ".S."], "geo": ["SS.SS", ".SSS.", "SS.SS"]}
_files: dict = {}


def settings(mapset, key) -> dict:
    got = ((mapset or {}).get("sats") or {}).get(key) if isinstance((mapset or {}).get("sats"), dict) else None
    return {**SAT_DEFAULTS, "colour": LAYERS[key]["colour"], **(got if isinstance(got, dict) else {})}


def hex_rgb(s, default=(200, 200, 200)):
    try:
        s = str(s).lstrip("#")
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except (ValueError, TypeError, IndexError):
        return default


def load(key, data_dir):
    """(header, lat [steps, n] float32, lon, pairs [m, 2], names) for a layer, re-read when its file changes
    (checked every 10 s); None when there isn't one."""
    hit = _files.get(key)
    now = time.time()
    if hit and now - hit[0] < 10:
        return hit[2]
    path = os.path.join(data_dir, "issmap", f"sats_{key}.bin")
    try:
        mt = os.stat(path).st_mtime
        if hit and hit[1] == mt:
            _files[key] = (now, mt, hit[2])
            return hit[2]
        raw = open(path, "rb").read()
        magic, n, steps, m, t0, step = HEAD.unpack_from(raw)
        if magic != b"SAT2":
            raise ValueError("not a sats file")
        o = HEAD.size
        la = np.frombuffer(raw, "<i2", steps * n, o).reshape(steps, n)
        lo = np.frombuffer(raw, "<i2", steps * n, o + 2 * steps * n).reshape(steps, n)
        pr = np.frombuffer(raw, "<u2", 2 * m, o + 4 * steps * n).reshape(m, 2).astype(np.int64)
        try:
            with open(os.path.join(data_dir, "issmap", f"sats_{key}.json"), encoding="utf-8") as f:
                names = json.load(f).get("names") or []
        except (OSError, ValueError):
            names = []
        got = ({"n": n, "steps": steps, "t0": t0, "step": step}, la, lo, pr, names)
    except (OSError, ValueError, struct.error):
        mt, got = None, None
    _files[key] = (now, mt, got)
    return got


def load_tracks(key, data_dir):
    """(header, lat [steps, n] int16 ×100, lon) of a layer's orbit tracks (hub/jobs/sats.py .trk), re-read when the
    file changes (checked every 10 s); None when there isn't one."""
    hit = _files.get(key + ".trk")
    now = time.time()
    if hit and now - hit[0] < 10:
        return hit[2]
    path = os.path.join(data_dir, "issmap", f"sats_{key}.trk")
    try:
        mt = os.stat(path).st_mtime
        if hit and hit[1] == mt:
            _files[key + ".trk"] = (now, mt, hit[2])
            return hit[2]
        raw = open(path, "rb").read()
        magic, n, steps, t0, step, period = TRK.unpack_from(raw)
        if magic != b"TRK1":
            raise ValueError("not a tracks file")
        o = TRK.size
        la = np.frombuffer(raw, "<i2", steps * n, o).reshape(steps, n)
        lo = np.frombuffer(raw, "<i2", steps * n, o + 2 * steps * n).reshape(steps, n)
        got = ({"n": n, "steps": steps, "t0": t0, "step": step, "period": period}, la, lo)
    except (OSError, ValueError, struct.error):
        mt, got = None, None
    _files[key + ".trk"] = (now, mt, got)
    return got


def track_pick(lat, lon, ok, count, centre=None, near_km=0.0):
    """Which satellites get an orbit track: the nearest ``count`` to centre (within near_km) when both are given,
    else ``count`` spread evenly through the layer (the same ones from one moment to the next)."""
    cand = np.flatnonzero(ok)
    if count <= 0 or cand.size == 0:
        return cand[:0]
    if centre is not None and near_km > 0:
        d = _gc_deg(centre[0], centre[1], lat[cand], lon[cand]) * 111.2
        cand = cand[d <= near_km]
        return cand[np.argsort(d[d <= near_km])[:count]]
    if cand.size <= count:
        return cand
    return cand[np.unique(np.linspace(0, cand.size - 1, count).round().astype(int))]


def _tracks_onto(img, v, left, key, tr, t, sel, rgb):
    """The orbit tracks of satellites ``sel``: each one's ground track from half an orbit before t to half an orbit
    after (through where it is now), drawn faint into a cached mask (redrawn when the track's window moves a step)."""
    from display.modules import issmap
    h, la, lo = tr
    half = h["period"] / 2 / h["step"]
    c = (t - h["t0"]) / h["step"]
    i0, i1 = max(0, int(np.floor(c - half))), min(h["steps"] - 1, int(np.ceil(c + half)))
    if i1 - i0 < 2 or sel.size == 0:
        return

    def segs():
        a, o = la[i0:i1 + 1][:, sel], lo[i0:i1 + 1][:, sel]
        good = (a != 32767) & (o != 32767)
        x = v.wx(o / 100.0)
        y = v.y(a / 100.0) - v.y0
        k = (good[:-1] & good[1:]).ravel()
        x0 = x[:-1].ravel()
        x1 = issmap._unwrap(x0, x[1:].ravel(), v.W)
        return x0[k], y[:-1].ravel()[k], x1[k], y[1:].ravel()[k]

    _mesh_onto(img, v, left, (key + "/tracks", v.key, i0, i1, int(sel.size), int(sel.sum()), rgb), segs,
               np.array([int(ch * TRACK_DIM) for ch in rgb], np.uint8))


def positions(f, t):
    """(lat, lon, ok) arrays at time t, or None when the file doesn't cover t."""
    if f is None:
        return None
    h, la, lo, _pr, _names = f
    x = (t - h["t0"]) / h["step"]
    i = int(np.floor(x))
    if i < 0 or i + 1 >= h["steps"] or h["n"] == 0:
        return None
    fr = x - i
    a0, a1 = la[i].astype(np.float32), la[i + 1].astype(np.float32)
    o0, o1 = lo[i].astype(np.float32), lo[i + 1].astype(np.float32)
    ok = (a0 != 32767) & (a1 != 32767)
    d = o1 - o0
    d = np.where(d > 18000, d - 36000, np.where(d < -18000, d + 36000, d))   # across the date line
    lat = (a0 + (a1 - a0) * fr) / 100.0
    lon = ((o0 + d * fr) / 100.0 + 540.0) % 360.0 - 180.0
    return lat, lon, ok


def count(key, data_dir):
    f = load(key, data_dir)
    return f[0]["n"] if f else None


def _xyz(lat, lon, r):
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([r * np.cos(la) * np.cos(lo), r * np.cos(la) * np.sin(lo), r * np.sin(la)], axis=-1)


MESH_EVERY = 5.0              # s: the trellis is drawn again this often (its satellites move under a pixel)
_meshes: dict = {}


def _mesh_onto(img, v, left, key, segs, rgb):
    """A trellis (its lines worked out by segs(): world x, raster y), drawn into a mask of the world every MESH_EVERY
    and laid over the map from it: thousands of links cost one copy a second."""
    from display.modules import issmap
    hit = _meshes.get(key[0])
    if hit is None or hit[0] != key:
        x0, y0, x1, y1 = segs()
        m = np.zeros((v.mh, v.W), bool)
        xs, ys, _ = issmap._samples(x0, y0, x1, y1)
        xi, yi = np.rint(xs).astype(np.int64) % v.W, np.rint(ys).astype(np.int64)
        ok = (yi >= 0) & (yi < v.mh)
        m[yi[ok], xi[ok]] = True
        hit = _meshes[key[0]] = (key, m)
    sr, rr = v.rows()
    band = img[sr]
    win = np.empty(band.shape[:2], bool)
    issmap._window(win, hit[1][rr], left, v.W)
    band[win] = rgb


MESH_ANIM = 300               # at most this many trellis links carry a moving dot at once


def _gc_deg(lat1, lon1, lat2, lon2):
    la1, la2 = np.radians(lat1), np.radians(lat2)
    c = np.sin(la1) * np.sin(la2) + np.cos(la1) * np.cos(la2) * np.cos(np.radians(lon2 - lon1))
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


def draw(img, v, left, t, mapset, data_dir, iss_pos, iss_xy, paths, label_items, lab, lab_rgb, icons, observer=None,
         up=None):
    """Every layer switched on, onto the map (and its moving data into ``paths``, its names into ``label_items``).
    Returns {layer: how many satellites it has}.

    observer (Satellites Overhead): (lat, lon, min_el, outside) of the wall's home. Then near_km is measured from it,
    the satellites it can't see (less than min_el above its horizon) are drawn ``outside`` as bright (0: not at all),
    GPS's lines go from it to the ones it sees, and ``up`` gets {layer: how many it sees}."""
    from display.modules import issmap
    from display.modules.flags import flag_char
    from display.modules.maplabels import name_for
    counts = {}
    for key, L in LAYERS.items():
        st = settings(mapset, key)
        if not st.get("on"):
            continue
        f = load(key, data_dir)
        if f is None:
            continue
        counts[key] = f[0]["n"]
        got = positions(f, t)
        if got is None:
            continue
        lat, lon, ok = got
        rgb = hex_rgb(st.get("colour"), hex_rgb(L["colour"]))
        try:
            bright = max(0.1, min(1.0, float(st.get("brightness", 0.7))))
        except (TypeError, ValueError):
            bright = 0.7
        wx, wy = v.wx(lon), v.y(lat)
        seen = None
        if observer:
            rho = issmap.footprint_deg(float(L.get("alt_km", 550)), observer[2])
            seen = ok & (_gc_deg(observer[0], observer[1], lat, lon) <= rho)
            if up is not None:
                up[key] = int(seen.sum())
        outside = max(0.0, min(1.0, float(observer[3]))) if observer else 1.0
        on_map = ok & (wy >= 0) & (wy < v.h)
        if observer and outside <= 0:
            on_map &= seen
        n_map = int(on_map.sum())
        mark = st.get("mark") if st.get("mark") in MARKS else "auto"
        icon = mark == "icon" or (mark == "auto" and n_map <= ICON_MAX)
        # orbit tracks and links first (under the satellites)
        try:
            n_tracks = max(0, min(TRACKS_MAX, int(st.get("tracks") or 0)))
        except (TypeError, ValueError):
            n_tracks = 0
        tsel = np.zeros(0, np.int64)
        if n_tracks and L["orbit"] != "geo":
            tr = load_tracks(key, data_dir)
            if tr and tr[0]["n"] == f[0]["n"]:
                pick_ok = ok if seen is None else ok & seen
                centre = observer[:2] if observer else iss_pos
                tsel = track_pick(lat, lon, pick_ok, n_tracks, centre, float(st.get("near_km") or 0))
                _tracks_onto(img, v, left, key, tr, t, tsel, rgb)
        if st.get("links"):
            near = float(st.get("near_km") or 0)
            if L["mesh"] and f[3].size:
                a, b = f[3][:, 0], f[3][:, 1]
                k = ok[a] & ok[b]
                if seen is not None:                           # (overhead: the links home could see, both ends up)
                    k &= seen[a] & seen[b]
                centre = observer[:2] if observer else iss_pos
                if near > 0 and centre:
                    mid_la, mid_lo = (lat[a] + lat[b]) / 2, lon[a]
                    k &= _gc_deg(centre[0], centre[1], mid_la, mid_lo) * 111.2 <= near
                a, b = a[k], b[k]
                x0 = wx[a]
                x1 = issmap._unwrap(x0, wx[b], v.W)
                dim = np.array([int(c * 0.3) for c in rgb], np.uint8)   # (solid and dim)
                _mesh_onto(img, v, left, (key, v.key, int(t // MESH_EVERY), near, st.get("colour")),
                           lambda: (x0, wy[a] - v.y0, x1, wy[b] - v.y0), dim)
                if a.size:
                    pick = np.arange(a.size)
                    if a.size > MESH_ANIM:                     # (a different few every 20 s)
                        rng = np.random.default_rng(int(t // 20))
                        pick = rng.choice(a.size, MESH_ANIM, replace=False)
                    paths.append({"kind": "segs", "x0": x0[pick], "y0": wy[a][pick], "x1": x1[pick], "y1": wy[b][pick],
                                  "phase": (pick * 0.6180339) % 1.0, "rgb": rgb, "left": left, "W": v.W,
                                  "speed": st.get("speed", "normal"), "direction": "down"})
            elif key == "gps" and observer and seen.any():      # Satellites Overhead: the ones home can see
                hx, hy = float(v.wx(observer[1])), float(v.y(observer[0]))
                x1 = wx[seen]
                x0 = issmap._unwrap(x1, np.full(x1.size, hx), v.W)
                issmap._seg_line(img, v, left, x0, np.full(x1.size, hy), x1, wy[seen], tuple(int(c * 0.5) for c in rgb),
                                 np.full(x1.size, 0.5, np.float32))
                paths.append({"kind": "segs", "x0": x1, "y0": wy[seen], "x1": x0, "y1": np.full(x1.size, hy),
                              "phase": np.arange(x1.size) * 0.37 % 1.0, "rgb": rgb, "left": left, "W": v.W,
                              "speed": "normal", "direction": "down"})
            elif key == "gps" and iss_pos and not observer:    # the ISS's GPS receivers: the satellites it can see
                iss = _xyz(np.array([iss_pos[0]]), np.array([iss_pos[1]]), issmap.R_KM + issmap.ALT_KM)[0]
                sats = _xyz(lat, lon, issmap.R_KM + 20200.0)
                d = sats - iss
                f_ = np.clip(-(d @ iss) / np.maximum((d * d).sum(axis=1), 1e-9), 0, 1)
                closest = iss[None, :] + f_[:, None] * d
                seen = ok & (np.linalg.norm(closest, axis=1) > issmap.R_KM + 80)
                if seen.any():
                    x0 = np.full(int(seen.sum()), iss_xy[0])
                    x1 = issmap._unwrap(x0, wx[seen], v.W)
                    y0 = np.full(x0.size, iss_xy[1])
                    issmap._seg_line(img, v, left, x0, y0, x1, wy[seen], tuple(int(c * 0.5) for c in rgb),
                                     np.full(x0.size, 0.5, np.float32))
                    paths.append({"kind": "segs", "x0": x1, "y0": wy[seen], "x1": x0, "y1": y0,
                                  "phase": np.arange(x0.size) * 0.37 % 1.0, "rgb": rgb, "left": left, "W": v.W,
                                  "speed": "normal", "direction": "down"})
            elif key == "goes":                                # down to NOAA's station at Wallops
                gx, gy = float(v.wx(WALLOPS[1])), float(v.y(WALLOPS[0]))
                x0 = wx[ok]
                x1 = issmap._unwrap(x0, np.full(x0.size, gx), v.W)
                y1 = np.full(x0.size, gy)
                issmap._seg_line(img, v, left, x0, wy[ok], x1, y1, tuple(int(c * 0.6) for c in rgb),
                                 np.full(x0.size, 0.6, np.float32))
                paths.append({"kind": "segs", "x0": x0, "y0": wy[ok], "x1": x1, "y1": y1,
                              "phase": np.arange(x0.size) * 0.29 % 1.0, "rgb": rgb, "left": left, "W": v.W,
                              "speed": "slow", "direction": "down"})
        # the satellites (past home's horizon dimmer)
        sel = np.flatnonzero(on_map)
        alpha = np.full(sel.size, bright, np.float32)
        if seen is not None:
            alpha[~seen[sel]] *= outside
        if icon:
            art = SAT_ART[L["orbit"]]
            for j, row in enumerate(art):
                for i, ch in enumerate(row):
                    if ch != ".":
                        dx, dy = i - len(row) // 2, j - len(art) // 2
                        issmap._plot(img, wx[sel] + dx, wy[sel] + dy, rgb, alpha, left, v.W)
            hw_, hh_ = len(art[0]) // 2 + 1, len(art) // 2 + 1
            X = np.rint((wx[sel] - left) % v.W).astype(int)
            Y = np.rint(wy[sel]).astype(int)
            icons.extend((int(x) - hw_, int(y) - hh_, int(x) + hw_, int(y) + hh_) for x, y in zip(X, Y))
            if (st.get("label") or lab.get("name")) and sel.size <= LABEL_MAX:
                names = f[4]
                for x, y, i in zip(X, Y, sel):
                    nm = names[i] if i < len(names) else ""
                    text = (flag_char(L["flag"]) if lab.get("flag") else "") + name_for(mapset, nm, _short_name(nm)).upper()
                    label_items.append({"text": text, "anchor": (int(x), int(y)), "half": (hw_, hh_),
                                        "pos": lab.get("pos", "auto"), "prio": 6, "rgb": rgb if st.get("label") else lab_rgb,
                                        "edge": lab.get("edge", "outline"), "optional": True})
        else:
            issmap._plot(img, wx[sel], wy[sel], rgb, alpha, left, v.W)
            ts = tsel[on_map[tsel]]                            # the ones with a track: their icon, riding along it
            if ts.size:
                art = SAT_ART[L["orbit"]]
                for j, row in enumerate(art):
                    for i, ch in enumerate(row):
                        if ch != ".":
                            dx, dy = i - len(row) // 2, j - len(art) // 2
                            issmap._plot(img, wx[ts] + dx, wy[ts] + dy, rgb, np.ones(ts.size, np.float32), left, v.W)
    return counts


def _short_name(n):
    """'GPS BIIR-5  (PRN 22)' -> 'PRN 22'; 'GOES 18' stays; 'STARLINK-1008' stays."""
    import re
    m = re.search(r"\(PRN (\d+)\)", n)
    if m:
        return "PRN " + m.group(1)
    m = re.search(r"\((GOES \d+)\)", n)
    return m.group(1) if m else n.strip()
