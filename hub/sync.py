"""Push hub data to the Pi's tmpfs (/dev/shm/bigscreen) with rsync over SSH.

rsync only sends files that changed, so syncing the whole data dir after every
job is cheap. Pushes are serialized; if one is already running, a single
follow-up push is queued instead of piling up.
"""

import os
import subprocess
import threading
import time
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


RESYNC_SEC = 60.0            # with nothing changed, push anyway this often (a rebooted Pi's /dev/shm is empty)
_pushed = {"print": None, "t": 0.0}


def fingerprint():
    """What the data dir holds: (files, bytes, newest change). Cheap (a stat per file) next to an rsync run."""
    n = size = newest = 0
    stack = [str(paths.DATA_DIR)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif not e.name.startswith("."):          # (not atomic_write_json's temp files)
                        st = e.stat(follow_symlinks=False)
                        n, size, newest = n + 1, size + st.st_size, max(newest, st.st_mtime_ns)
        except OSError:
            pass
    return n, size, newest


def _push_once():
    try:
        # Most jobs finish without changing a file (nothing new, or a job that only checks): no rsync then.
        # Before this every job's success ran up to four rsync-over-ssh sessions to the Pi, ~10 times a minute.
        fp = fingerprint()
        if fp == _pushed["print"] and last_result["ok"] and time.time() - _pushed["t"] < RESYNC_SEC:
            return
        target, remote_dir = pi_target()
        cmd = ["rsync", "-a", "--timeout=20",
               "--include=*.json", "--include=icons/", "--include=icons/*.png",
               "--include=stock-icons/", "--include=stock-icons/*.png",
               "--include=alertmaps/", "--include=alertmaps/*.bin", "--include=alertmaps/*.mask", "--include=alertmaps/*.zones",
               "--include=quake/", "--include=quake/*.bin", "--include=alertpages/", "--include=alertpages/*.bin",
               "--include=bcmaps/", "--include=bcmaps/*.bin", "--include=drivebc/", "--include=drivebc/*.bin",
               "--include=shields/", "--include=shields/*.png",          # real highway signs (hub/shield_signs.py)
               "--include=tzmap/", "--include=tzmap/*.bin",              # time zone map rasters (hub/jobs/timezones.py)
               "--include=issmap/", "--include=issmap/*.bin", "--include=issmap/*.trk",   # ISS map: aurora, lights, orbit tracks
               "--include=flags/", "--include=flags/*.png",              # caption flags (display/modules/caption_flags.py)
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
        issmap = paths.DATA_DIR / "issmap"
        if any(issmap.glob("ov_*")) if issmap.is_dir() else False:
            # Satellites Overhead basemaps (hub/spacemap.py): one per size and zoom, so old zooms are deleted
            q = subprocess.run(["rsync", "-a", "--delete", "--timeout=30", "--include=ov_*", "--exclude=*",
                                "--rsync-path", f"mkdir -p {remote_dir}/issmap && rsync",
                                "-e", SSH, f"{issmap}/", f"{target}:{remote_dir}/issmap/"],
                               capture_output=True, text=True, timeout=120)
            ok = ok and q.returncode == 0
            err = (err + " " + q.stderr.strip()[-300:]).strip()
        bridges = paths.DATA_DIR / "bridges"
        if bridges.is_dir():
            # Bridges now tiles (hub/jobs/bridges.py): named by content, so --delete keeps /dev/shm from filling up
            q = subprocess.run(["rsync", "-a", "--delete", "--timeout=30", "--include=*.bin", "--exclude=*",
                                "--rsync-path", f"mkdir -p {remote_dir}/bridges && rsync",
                                "-e", SSH, f"{bridges}/", f"{target}:{remote_dir}/bridges/"],
                               capture_output=True, text=True, timeout=120)
            ok = ok and q.returncode == 0
            err = (err + " " + q.stderr.strip()[-300:]).strip()
        clips = paths.DATA_DIR / "clips"
        if clips.is_dir():
            # Clarity lab clips (hub/clarity.py): tens of MB each, so --delete keeps /dev/shm from filling up
            q = subprocess.run(["rsync", "-a", "--delete", "--timeout=60", "--include=*.bin", "--include=*.json",
                                "--exclude=*", "--rsync-path", f"mkdir -p {remote_dir}/clips && rsync",
                                "-e", SSH, f"{clips}/", f"{target}:{remote_dir}/clips/"],
                               capture_output=True, text=True, timeout=300)
            ok = ok and q.returncode == 0
            err = (err + " " + q.stderr.strip()[-300:]).strip()
        last_result.update(at=datetime.now().isoformat(timespec="seconds"), ok=ok, error=err)
        _pushed.update(print=fp if ok else None, t=time.time())
    except Exception as e:
        last_result.update(at=datetime.now().isoformat(timespec="seconds"), ok=False, error=str(e))
