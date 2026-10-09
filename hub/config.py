"""The single config file, owned by the hub.

Every save bumps `revision`, writes a timestamped backup, and is then pushed to
the Pi (see hub/pi.py). The top-level keys `system`, `fonts`, `colors`,
`layout`, `modules` keep the v1 shape so the v1 display can read the same file
while the v2 renderer is being built.
"""

import copy
import threading
import time
from datetime import datetime

from hub import paths
from hub.util import atomic_write_json, read_json

MAX_BACKUPS = 100

_lock = threading.Lock()


class ConfigError(ValueError):
    pass


def load():
    cfg = read_json(paths.CONFIG_PATH)
    if cfg is None:
        raise ConfigError(f"No config at {paths.CONFIG_PATH} — run tools/migrate_v1.py first")
    return cfg


def save(cfg, reason=""):
    """Validate, back up, and atomically write. Returns the saved config."""
    validate(cfg)
    with _lock:
        current = read_json(paths.CONFIG_PATH, {}) or {}
        cfg = copy.deepcopy(cfg)
        cfg["revision"] = int(current.get("revision", 0)) + 1
        cfg["saved_at"] = time.time()
        if reason:
            cfg["saved_reason"] = reason
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        atomic_write_json(paths.BACKUP_DIR / f"config-{stamp}-r{cfg['revision']}.json", cfg, indent=2)
        atomic_write_json(paths.CONFIG_PATH, cfg, indent=2)
        _prune_backups()
    return cfg


def _prune_backups():
    backups = sorted(paths.BACKUP_DIR.glob("config-*.json"))
    for old in backups[:-MAX_BACKUPS]:
        old.unlink(missing_ok=True)


def list_backups():
    out = []
    for p in sorted(paths.BACKUP_DIR.glob("config-*.json"), reverse=True):
        d = read_json(p, {}) or {}
        out.append({"file": p.name, "revision": d.get("revision"), "saved_at": d.get("saved_at"),
                    "reason": d.get("saved_reason", "")})
    return out


def validate(cfg):
    if not isinstance(cfg, dict):
        raise ConfigError("config must be an object")
    for key in ("system", "layout", "modules"):
        if not isinstance(cfg.get(key), dict):
            raise ConfigError(f"missing section: {key}")
    mods = cfg["modules"]

    fin = mods.get("finance", {})
    seen = set()
    for i, t in enumerate(fin.get("symbols", [])):
        if not isinstance(t, dict) or not str(t.get("symbol", "")).strip():
            raise ConfigError(f"finance.symbols[{i}] needs a symbol")
        sym = t["symbol"].strip().upper()
        if sym in seen:
            raise ConfigError(f"duplicate ticker {sym}")
        seen.add(sym)
        w = t.get("width")
        if w is not None and (not isinstance(w, int) or not 20 <= w <= 448):
            raise ConfigError(f"{sym}: width must be empty or 20–448 px")

    news = mods.get("headlines", {})
    names = set()
    for i, c in enumerate(news.get("categories", [])):
        name = str(c.get("name", "")).strip()
        if not name:
            raise ConfigError(f"headlines.categories[{i}] needs a name")
        if name in names:
            raise ConfigError(f"duplicate category {name}")
        names.add(name)
        for f in c.get("feeds", []):
            if not str(f.get("url", "")).startswith(("http://", "https://")):
                raise ConfigError(f"{name}: feed URL must start with http(s)://")


def get_path(cfg, dotted, default=None):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
