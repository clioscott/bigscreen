"""Which webcams are down → data/webcam_health.json (the display skips them; the control panel says for how long).

Every webcam in ``modules.webcams.cams`` gets one current picture (hub/webcams.py grab_frame): a source that gives
none, or a black one (is_black), is down. A webcam stays "down" from the first failed check until one succeeds, so the
panel can say "down for 1:25".
Output, keyed by webcam id: {"up": bool, "why": "" | "black" | <error>, "since": epoch (when it went down, or came
up), "checked": epoch}.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from hub import paths
from hub.jobs import _harness
from hub.util import read_json

WORKERS = 4                  # checks at once (each ~5-10 s, mostly waiting on yt-dlp and the network)


def check(cam) -> tuple[bool, str]:
    """(up, why) for one webcam."""
    from hub import webcams
    try:
        kind, url = webcams.check_source((cam or {}).get("source"))
        frame = webcams.grab_frame(kind, url)
    except Exception as e:
        return False, (str(e) or type(e).__name__)[:200]
    if webcams.is_black(frame):
        return False, "black"
    return True, ""


def update(before: dict, results: dict, now: float) -> dict:
    """Each webcam's new state from its check, keeping when it went down (or came up)."""
    out = {}
    for cid, (up, why) in results.items():
        old = before.get(cid) if isinstance(before.get(cid), dict) else {}
        since = old.get("since") if old.get("up") == up and old.get("since") else now
        out[cid] = {"up": up, "why": why, "since": since, "checked": now}
    return out


def run(cfg):
    from hub.util import atomic_write_json
    cams = (((cfg.get("modules") or {}).get("webcams") or {}).get("cams") or {})
    cams = {cid: cam for cid, cam in cams.items() if isinstance(cam, dict)}
    with ThreadPoolExecutor(WORKERS) as pool:
        results = dict(zip(cams, pool.map(check, cams.values())))
    path = paths.DATA_DIR / "webcam_health.json"
    out = update(read_json(path, {}) or {}, results, time.time())
    atomic_write_json(path, out)
    down = sorted(cid for cid, s in out.items() if not s["up"])
    return {"ok": True, "webcams": len(out), "down": len(down), "note": ", ".join(down)[:200]}


if __name__ == "__main__":
    _harness.main(run)
