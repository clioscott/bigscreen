"""Filesystem layout for the hub.

Code lives in the git repo; everything the hub *writes* lives in STATE_DIR so the
repo stays clean and a `git pull` never fights with runtime files.

    ~/bigscreen-state/
        config.json          single source of truth (edited by the control panel)
        config-backups/      timestamped copies, one per save
        status.json          per-job health (last run, last success, error)
        data/                everything synced to the Pi's /dev/shm/bigscreen
            *.json
            icons/           headline favicons
        secrets/             token.pickle, credentials.json (Google)
        cache/               job-private caches (e.g. weather citypage)
"""

import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("BIGSCREEN_STATE", Path.home() / "bigscreen-state"))

CONFIG_PATH = STATE_DIR / "config.json"
BACKUP_DIR = STATE_DIR / "config-backups"
STATUS_PATH = STATE_DIR / "status.json"
DATA_DIR = STATE_DIR / "data"
ICONS_DIR = DATA_DIR / "icons"
SECRETS_DIR = STATE_DIR / "secrets"
CACHE_DIR = STATE_DIR / "cache"

# Ticker logos are part of the display's assets, kept in the repo so both
# machines get them from git.
STOCK_ICONS_DIR = REPO_DIR / "assets" / "icons" / "stocks"


def ensure_dirs():
    for d in (STATE_DIR, BACKUP_DIR, DATA_DIR, ICONS_DIR, SECRETS_DIR, CACHE_DIR, STOCK_ICONS_DIR):
        d.mkdir(parents=True, exist_ok=True)
