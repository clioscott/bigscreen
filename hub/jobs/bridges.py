"""Bridges now: the latest DriveBC still from a camera on each Metro Vancouver bridge → data/bridges.json + tiles.

DriveBC cameras are stills that refresh every 3-15 minutes (there is no public live video of these bridges). Each
bridge in ``modules.bridges.cams`` lists DriveBC camera ids in order of preference; the first one that is on is used.
Images are cropped (DriveBC's caption strip goes) and scaled to the two tile sizes the display's grid uses, so the Pi
only copies them: TILE_S for the full-width and half-width grids, TILE_L for the whole-wall grid. Tiles are named after
their camera and image time and listed in bridges.json; hub/sync.py sends data/bridges/ in its own pass with --delete.
"""

from __future__ import annotations

import io
import time
from datetime import datetime

import numpy as np

from hub import paths
from hub.bc import common
from hub.jobs import _harness

API = "https://www.drivebc.ca/api"
TILE_S = (111, 66)           # 4×2 in 448×134, 2×2 in a 223×134 side
TILE_L = (111, 95)           # 4×2 on the whole wall (448×192)
OUT = paths.DATA_DIR / "bridges"
DEFAULT_CAMS = [             # label, DriveBC camera ids (first that is on wins)
    {"label": "Lions Gate", "ids": [18, 20]},
    {"label": "Ironworkers", "ids": [72, 73, 763]},
    {"label": "Port Mann", "ids": [292, 275]},
    {"label": "Alex Fraser", "ids": [92, 91]},
    {"label": "Knight St", "ids": [797, 796]},
    {"label": "Oak St", "ids": [70, 67, 66]},
    {"label": "Golden Ears", "ids": [720, 827]},
    {"label": "Peace Arch", "ids": [13, 14]},
]


def cams_of(cfg) -> list:
    cams = ((cfg.get("modules") or {}).get("bridges") or {}).get("cams")
    return cams if isinstance(cams, list) and cams else DEFAULT_CAMS


def _ts(s):
    try:
        return datetime.fromisoformat(s).timestamp() if s else None
    except ValueError:
        return None


def _tile(img, size):
    """Centre-crop to the tile's shape (after dropping DriveBC's caption strip) and scale."""
    from PIL import Image
    w, h = img.size
    h2 = int(h * 0.94)
    tw, th = size
    if w / h2 > tw / th:
        cw = int(h2 * tw / th)
        box = ((w - cw) // 2, 0, (w - cw) // 2 + cw, h2)
    else:
        ch = int(w * th / tw)
        box = (0, (h2 - ch) // 2, w, (h2 - ch) // 2 + ch)
    return np.ascontiguousarray(np.asarray(img.crop(box).resize(size, Image.LANCZOS)), dtype=np.uint8)


def run(cfg):
    from PIL import Image
    from hub.util import atomic_write_json, read_json
    OUT.mkdir(parents=True, exist_ok=True)
    old = {c.get("cam"): c for c in (read_json(paths.DATA_DIR / "bridges.json", {}) or {}).get("cams", [])
           if c.get("cam") and c.get("tiles")}
    listing = common.session.get(f"{API}/webcams/", timeout=40).json()
    from hub import traffic
    by_id = {c.get("id"): c for c in traffic.fix_all(listing if isinstance(listing, list) else listing.get("results", []))}
    have = lambda tiles: bool(tiles) and all((OUT / name).exists() for name in tiles.values())
    out, fetched, errors = [], 0, []
    bridges = cams_of(cfg)
    for n, bridge in enumerate(bridges):
        label = str(bridge.get("label") or f"Bridge {n + 1}")[:24]
        ids = [i for i in bridge.get("ids") or [] if isinstance(i, int) and not isinstance(i, bool)]
        cam = next((by_id[i] for i in ids if i in by_id and by_id[i].get("is_on") and not by_id[i].get("marked_stale")),
                   None)
        entry = {"key": f"b{n}", "label": label, "on": cam is not None}
        if cam is None:
            entry["why"] = "camera off"
            out.append(entry)
            continue
        updated = _ts(cam.get("last_update_modified"))
        entry.update(cam=cam["id"], name=cam.get("name") or "", caption=cam.get("caption") or "", updated=updated,
                     orientation=cam.get("orientation") or "", delayed=bool(cam.get("marked_delayed")),
                     period=cam.get("update_period_mean") or 300)
        prev = old.get(cam["id"]) or {}
        if prev.get("updated") == updated and have(prev.get("tiles")):
            entry.update(at=prev.get("at"), tiles=prev["tiles"])     # same image as last time: same tiles
            out.append(entry)
            continue
        try:
            url = "https://www.drivebc.ca" + ((cam.get("links") or {}).get("imageDisplay") or f"/images/{cam['id']}.jpg")
            r = common.session.get(url, timeout=20)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            # named after what's in them (camera + image time): a tile can never show under another bridge's label,
            # and the display can tell when the files bridges.json names have all arrived
            stamp = int(updated or time.time())
            tiles = {}
            for s, size in (("s", TILE_S), ("l", TILE_L)):
                name = f"c{cam['id']}_{stamp}_{s}.bin"
                tmp = OUT / (name + ".tmp")
                _tile(img, size).tofile(tmp)
                tmp.replace(OUT / name)
                tiles[s] = name
            entry.update(at=time.time(), tiles=tiles)
            fetched += 1
        except Exception as e:
            errors.append(f"{label}: {e}")
            if have(prev.get("tiles")):                           # the same camera's last image, with its own age
                entry.update(updated=prev.get("updated"), at=prev.get("at"), tiles=prev["tiles"])
            else:
                entry.update(on=False, why="no image")
        out.append(entry)
        _harness.progress(n + 1, len(bridges), label)
    keep = {name for c in out for name in (c.get("tiles") or {}).values()}
    for p in OUT.iterdir():
        if p.name not in keep:
            p.unlink(missing_ok=True)
    atomic_write_json(paths.DATA_DIR / "bridges.json", {"generated_at": time.time(), "tile_s": TILE_S, "tile_l": TILE_L,
                                                        "cams": out})
    return {"ok": True, "bridges": len(out), "on": sum(c["on"] for c in out), "fetched": fetched,
            "errors": errors[:3]}


if __name__ == "__main__":
    _harness.main(run)
