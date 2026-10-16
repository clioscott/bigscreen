"""Push hub data to the Pi's tmpfs (/dev/shm/bigscreen) with rsync over SSH.

rsync only sends files that changed, so syncing the whole data dir after every
job is cheap. Pushes are serialized; if one is already running, a single
follow-up push is queued instead of piling up.
"""

import subprocess
import threading
from datetime import datetime

from hub import config, paths

SSH = "ssh -o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5"

_lock = threading.Lock()
_pending = threading.Event()
last_result = {"at": None, "ok": None, "error": ""}


def pi_target(cfg=None):
    cfg = cfg or config.load()
    pi = cfg.get("hub", {}).get("pi", {})
    return f"{pi.get('user', 'scott')}@{pi.get('host', '192.168.1.100')}", pi.get("data_dir", "/dev/shm/bigscreen")


def push_data():
    """Non-blocking: run (or queue) an rsync of data/ → Pi."""
    _pending.set()
    if _lock.acquire(blocking=False):
        threading.Thread(target=_worker, name="sync", daemon=True).start()


def _worker():
    try:
        while _pending.is_set():
            _pending.clear()
            _push_once()
    finally:
        _lock.release()
    if _pending.is_set():          # raced with a new request after the loop ended
        push_data()


def _push_once():
    try:
        target, remote_dir = pi_target()
        cmd = ["rsync", "-a", "--timeout=20",
               "--include=*.json", "--include=icons/", "--include=icons/*.png",
               "--include=stock-icons/", "--include=stock-icons/*.png",
               "--include=alertmaps/", "--include=alertmaps/*.bin", "--include=alertmaps/*.mask", "--include=alertmaps/*.zones",
               "--include=quake/", "--include=quake/*.bin", "--include=alertpages/", "--include=alertpages/*.bin",
               "--include=bcmaps/", "--include=bcmaps/*.bin", "--include=drivebc/", "--include=drivebc/*.bin",
               "--exclude=*",
               "--rsync-path", f"mkdir -p {remote_dir} && rsync",
               "-e", SSH, f"{paths.DATA_DIR}/", f"{target}:{remote_dir}/"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        err = p.stderr.strip()[-500:]
        ok = p.returncode == 0
        anim = paths.DATA_DIR / "anim"
        if anim.is_dir():
            # map animation packs: own pass with --delete so removed tracks disappear from the Pi too
            q = subprocess.run(["rsync", "-a", "--delete", "--timeout=60", "--include=*.bin", "--include=*.json",
                                "--exclude=*", "--rsync-path", f"mkdir -p {remote_dir}/anim && rsync",
                                "-e", SSH, f"{anim}/", f"{target}:{remote_dir}/anim/"],
                               capture_output=True, text=True, timeout=300)
            ok = ok and q.returncode == 0
            err = (err + " " + q.stderr.strip()[-300:]).strip()
        last_result.update(at=datetime.now().isoformat(timespec="seconds"), ok=ok, error=err)
    except Exception as e:
        last_result.update(at=datetime.now().isoformat(timespec="seconds"), ok=False, error=str(e))
