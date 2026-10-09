"""Talking to the Pi.

Bridge mode (until the v2 renderer replaces it): the v1 display reads its own
copy of app-config.json only at startup and loads ticker logos from its assets
folder. "Apply to display" therefore writes that file from the hub config,
syncs the logos, and restarts the display service.
"""

import copy
import hashlib
import json
import subprocess
import time

import requests

from hub import config, paths
from hub.sync import SSH
from hub.util import atomic_write_json, read_json

APPLY_STATE = paths.STATE_DIR / "display_apply.json"


def _pi(cfg):
    pi = cfg.get("hub", {}).get("pi", {})
    return {
        "target": f"{pi.get('user', 'scott')}@{pi.get('host', '192.168.1.100')}",
        "host": pi.get("host", "192.168.1.100"),
        "v1_dir": pi.get("v1_dir", "/home/scott/bigscreen-calendar"),
        "service": pi.get("display_service", "bigscreen-client"),
        "override_port": int(cfg.get("modules", {}).get("override_server", {}).get("port", 8765)),
    }


def ssh(cfg, command, input_text=None, timeout=30):
    p = _pi(cfg)
    return subprocess.run(SSH.split() + [p["target"], command], input=input_text, capture_output=True,
                          text=True, timeout=timeout)


def v1_config(cfg):
    """The hub config reshaped for the v1 display (it pages headlines by `subreddits`)."""
    out = copy.deepcopy(cfg)
    # v1 only understands its own panel types: keep sending it the rotation it had before the
    # v2 weather maps replaced weather_panels_* entries (saved at conversion time).
    v1_cycle = cfg.get("hub", {}).get("v1_layout_cycle")
    if v1_cycle:
        out.setdefault("modules", {})["layout_cycle"] = copy.deepcopy(v1_cycle)
    out.get("modules", {}).pop("weather_maps", None)
    for k in ("hub", "revision", "saved_at", "saved_reason"):
        out.pop(k, None)
    h = out.get("modules", {}).get("headlines", {})
    h["subreddits"] = [c["name"] for c in h.get("categories", []) if c.get("enabled", True)]
    syms = out.get("modules", {}).get("finance", {}).get("symbols", [])
    out["modules"]["finance"]["symbols"] = [s for s in syms if s.get("enabled", True)]
    return out


def apply_to_display(restart=True):
    cfg = config.load()
    if cfg.get("hub", {}).get("display_mode") == "v2":
        from hub import sync
        config.publish(cfg)
        sync.push_data()
        return {"ok": True, "steps": [{"step": "sync config to display", "ok": True, "error": ""}],
                "revision": cfg.get("revision")}
    p = _pi(cfg)
    steps = []

    body = json.dumps(v1_config(cfg), indent=2)
    r = ssh(cfg, f"sudo tee {p['v1_dir']}/config/app-config.json >/dev/null", input_text=body)
    steps.append({"step": "write display config", "ok": r.returncode == 0, "error": r.stderr.strip()[-300:]})

    errs = []
    for src in (paths.STOCK_ICONS_DIR, paths.CUSTOM_STOCK_ICONS_DIR):     # built-ins, then uploads win
        r = subprocess.run(["rsync", "-a", "--timeout=20", "-e", SSH, f"{src}/",
                            f"{p['target']}:{p['v1_dir']}/assets/icons/stocks/"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode:
            errs.append(r.stderr.strip()[-300:])
    steps.append({"step": "sync ticker logos", "ok": not errs, "error": "; ".join(errs)})

    if restart and all(s["ok"] for s in steps):
        r = ssh(cfg, f"sudo systemctl restart {p['service']}", timeout=60)
        steps.append({"step": "restart display", "ok": r.returncode == 0, "error": r.stderr.strip()[-300:]})

    ok = all(s["ok"] for s in steps)
    if ok:
        atomic_write_json(APPLY_STATE, {"revision": cfg.get("revision"), "at": time.time(),
                                        "fingerprint": display_fingerprint(cfg)})
    return {"ok": ok, "steps": steps, "revision": cfg.get("revision")}


def display_fingerprint(cfg):
    """Hash of only the settings the v1 display reads, so editing a feed URL or a
    refresh interval doesn't ask for a display restart."""
    v1 = v1_config(cfg)
    h = v1.get("modules", {}).get("headlines", {})
    h.pop("categories", None)
    for mod in v1.get("modules", {}).values():
        if isinstance(mod, dict):
            mod.pop("refresh_interval_sec", None)
    return hashlib.sha1(json.dumps(v1, sort_keys=True).encode()).hexdigest()


def display_in_sync(cfg):
    state = read_json(APPLY_STATE, {}) or {}
    if cfg.get("hub", {}).get("display_mode") == "v2":
        return True, state
    return state.get("fingerprint") == display_fingerprint(cfg), state


def display_url(path):
    p = _pi(config.load())
    return f"http://{p['host']}:{p['override_port']}{path}"


def display_status():
    try:
        r = requests.get(display_url("/status"), timeout=3)
        st = r.json() if r.ok else None
        # v2 reports fps/draw times; v1's override server doesn't.
        version = "v2" if isinstance(st, dict) and "fps" in st else "v1"
        return {"reachable": True, "version": version, "status": st}
    except Exception as e:
        return {"reachable": False, "error": str(e)[:200]}


def display_post(path, payload=None, timeout=5):
    r = requests.post(display_url(path), json=payload or {}, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = {"error": r.text[:200]}
    return r.status_code, body
