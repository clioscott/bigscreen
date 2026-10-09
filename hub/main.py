"""Hub entry point: `python -m hub.main` (run by bigscreen-hub.service)."""

import uvicorn

from hub import app as web, config, paths
from hub.scheduler import Scheduler, log


def main():
    paths.ensure_dirs()
    cfg = config.load()
    port = int(cfg.get("hub", {}).get("panel_port", 8800))
    sched = Scheduler()
    web.scheduler = sched
    sched.start()
    log(f"hub up — config r{cfg.get('revision')}, state {paths.STATE_DIR}, panel :{port}")
    uvicorn.run(web.app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
