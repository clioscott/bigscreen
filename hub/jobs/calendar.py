"""Google Calendar events → data/calendar.json (v1 format, via the v1 CalendarService)."""

import os

from hub import paths
from hub.jobs import _harness
from hub.util import atomic_write_json


def run(cfg):
    # CalendarService looks for token.pickle in the cwd (and refreshes it in place).
    os.chdir(paths.SECRETS_DIR)
    if not (paths.SECRETS_DIR / "token.pickle").exists():
        return {"ok": False, "message": f"missing {paths.SECRETS_DIR / 'token.pickle'}"}

    from hub.jobs.calendar_service import CalendarService
    service = CalendarService(cfg)
    if service.service is None:
        return {"ok": False, "message": "Google auth failed — token expired or revoked (re-run auth_google.py)"}
    events = service.fetch_events()
    if events is None:
        return {"ok": False, "message": "calendar API returned no data"}
    atomic_write_json(paths.DATA_DIR / "calendar.json", events)
    colored = sum(1 for e in events if e.get("colorId") is not None)
    return {"ok": True, "message": f"{len(events)} events ({colored} with custom colour)",
            "files": ["calendar.json"]}


if __name__ == "__main__":
    _harness.main(run)
