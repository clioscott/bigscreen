"""Which webcams are down → data/webcam_health.json (the display skips them; the control panel says for how long).

Every webcam in ``modules.webcams.cams`` except YouTube ones gets one current picture (hub/webcams.py grab_frame): a
source that gives none, or a black one (is_black), is down. YouTube webcams are never looked up here: a lookup sweep
every few minutes got the hub's address bot-checked by YouTube. The hub notes their health from what their feeds do
while playing (hub/webcams.py note_health), and this job keeps that. A webcam stays "down" from the first failed check until one succeeds, so the
panel can say "down for 1:25".
Output, keyed by webcam id: {"up": bool, "why": "" | "black" | <error>, "since": epoch (when it went down, or came
up), "checked": epoch}.
"""

from __future__ import annotations

import threading
import time

from hub import paths
from hub.jobs import _harness
from hub.util import read_json

WORKERS = 4                  # checks at once (each ~5-10 s, mostly waiting on yt-dlp and the network)
STEP_SEC = 30                # each step of a check (yt-dlp, ffmpeg) gives up after this
BUDGET_SEC = 170             # the whole run: what's checked by then is written (the scheduler kills it at 240 s);
                             # a webcam not checked in time keeps its last state


def check(cam) -> tuple[bool, str] | None:
    """(up, why) for one webcam; None for one not checked here (YouTube)."""
    from hub import webcams
    try:
        kind, url = webcams.check_source((cam or {}).get("source"))
        if kind == "youtube":
            return None
        frame = webcams.grab_frame(kind, url, timeout=STEP_SEC)
    except Exception as e:
        return False, (str(e) or type(e).__name__)[:200]
    if webcams.is_black(frame):
        return False, "black"
    return True, ""


def update(before: dict, results: dict, now: float, keep=()) -> dict:
    """Each webcam's new state from its check, keeping when it went down (or came up). Webcams in ``keep`` that
    weren't checked keep their last state."""
    out = {cid: s for cid, s in before.items() if cid in keep and isinstance(s, dict)}   # not checked this time
    for cid, (up, why) in results.items():
        old = before.get(cid) if isinstance(before.get(cid), dict) else {}
        since = old.get("since") if old.get("up") == up and old.get("since") else now
        out[cid] = {"up": up, "why": why, "since": since, "checked": now}
    return out


def check_all(cams: dict, budget: float) -> dict:
    """{id: (up, why)} for the webcams checked within ``budget`` seconds, WORKERS at a time. Daemon threads: a check
    still running when the budget is up is left behind (its subprocesses have their own timeouts), never waited on."""
    results, lock, slots = {}, threading.Lock(), threading.Semaphore(WORKERS)

    def one(cid, cam):
        try:
            got = check(cam)
            with lock:
                results[cid] = got                         # None: not checked here (counted as done)
        finally:
            slots.release()
    end = time.time() + budget
    for cid, cam in cams.items():
        if not slots.acquire(timeout=max(0.0, end - time.time())):
            break
        threading.Thread(target=one, args=(cid, cam), name=f"health-{cid}", daemon=True).start()
    while time.time() < end:
        with lock:
            if len(results) == len(cams):
                break
        time.sleep(0.2)
    with lock:
        return {k: v for k, v in results.items() if v is not None}


def run(cfg):
    from hub.util import atomic_write_json
    cams = (((cfg.get("modules") or {}).get("webcams") or {}).get("cams") or {})
    cams = {cid: cam for cid, cam in cams.items() if isinstance(cam, dict)}
    results = check_all(cams, BUDGET_SEC)
    path = paths.DATA_DIR / "webcam_health.json"
    out = update(read_json(path, {}) or {}, results, time.time(), keep=set(cams))
    atomic_write_json(path, out)
    down = sorted(cid for cid, s in out.items() if not s["up"])
    return {"ok": True, "webcams": len(out), "down": len(down), "note": ", ".join(down)[:200]}


if __name__ == "__main__":
    _harness.main(run)
