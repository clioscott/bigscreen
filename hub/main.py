"""Hub entry point: `python -m hub.main` (run by bigscreen-hub.service)."""

import uvicorn

from common import logring
from hub import app as web, config, paths
from hub.scheduler import Scheduler, log


def main():
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
    uvicorn.run(web.app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
