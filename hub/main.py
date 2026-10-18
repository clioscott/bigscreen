"""Hub entry point: `python -m hub.main` (run by bigscreen-hub.service)."""

import ctypes
import sys

import uvicorn

from common import logring
from hub import app as web, config, paths
from hub.scheduler import Scheduler, log


def tune_malloc():
    """glibc keeps freed memory per thread arena and raises its mmap threshold as big blocks are freed, so a hub
    decoding webcams (a ~700 KB frame every 66 ms per feed, many threads) grew to its 1 GB cap. Two arenas and a
    fixed 128 KB mmap threshold make big frames come from mmap and go straight back to the system when freed."""
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL("libc.so.6")
        M_ARENA_MAX, M_MMAP_THRESHOLD = -8, -3
        libc.mallopt(M_ARENA_MAX, 2)
        libc.mallopt(M_MMAP_THRESHOLD, 128 * 1024)
    except OSError:
        pass


def main():
    tune_malloc()
    logring.install("bigscreen-hub")          # live console for the control panel
    paths.ensure_dirs()
    cfg = config.load()
    config.publish(cfg)
    port = int(cfg.get("hub", {}).get("panel_port", 8800))
    sched = Scheduler()
    web.scheduler = sched
    sched.start()
    from hub import quake
    quake.start()                              # Japan EEW listener (does nothing visible unless enabled)
    from hub.bc import naad_listener
    naad_listener.start(lambda jobs: [sched.run_now(j) for j in jobs if j in ("naad", "quakes", "tsunami")])
    from hub.bc import seismo
    seismo.start(lambda jobs: [sched.run_now(j) for j in jobs if j == "quakes"])   # live seismometers (BC quakes)
    log(f"hub up — config r{cfg.get('revision')}, state {paths.STATE_DIR}, panel :{port}")
    # open webcam streams (the Pi's frames, panel previews) never end on their own: don't let them hold a restart
    uvicorn.run(web.app, host="0.0.0.0", port=port, log_level="warning", timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
