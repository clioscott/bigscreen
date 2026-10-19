"""Runs fetch jobs as short-lived subprocesses on their configured intervals.

Each job's health is kept in status.json so the control panel can show when a
feed last succeeded — v1 had no such signal, which is how headlines and finance
stayed frozen for months unnoticed.
"""

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

from hub import config, paths, sync
from hub.jobs._harness import PROGRESS_MARKER, RESULT_MARKER
from hub.util import atomic_write_json, read_json

# name: (config path of interval, default interval s, timeout s, label)
JOBS = {
    "finance":  ("modules.finance.refresh_interval_sec", 60, 120, "Stock ticker"),
    "news":     ("modules.headlines.refresh_interval_sec", 900, 120, "Headlines"),
    "calendar": ("modules.calendar.refresh_interval_sec", 60, 120, "Google Calendar"),
    "weather":  ("modules.weather.swob_refresh_interval_sec", 120, 90, "Weather"),
    "maps":     ("modules.weather_maps.job_interval_sec", 180, 600, "Weather maps & radar"),
    "alerts":   ("modules.weather_alerts.refresh_interval_sec", 120, 60, "Weather alerts"),
    "naad":     ("modules.naad_alerts.refresh_interval_sec", 300, 120, "Alert Ready (NAAD)"),
    "drivebc":  ("modules.road_alerts.refresh_interval_sec", 120, 120, "DriveBC"),
    "bridges":  ("modules.bridges.refresh_interval_sec", 120, 90, "Bridges now"),
    "webcam_weather": ("modules.webcams.weather_interval_sec", 900, 60, "Webcam weather"),
    "shield_signs": ("modules.webcams.signs_interval_sec", 3600, 120, "Webcam highway signs"),
    "quakes":   ("modules.bc_quakes.refresh_interval_sec", 60, 120, "Earthquakes"),
    "tsunami":  ("modules.tsunami.refresh_interval_sec", 120, 120, "Tsunami"),
    "wildfire": ("modules.wildfire.refresh_interval_sec", 600, 300, "Wildfire"),
}
# A job is "stale" when its last success is older than this many intervals.
STALE_INTERVALS = 3


def log(msg):
    # one write per line, so lines from concurrent jobs never interleave
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n", end="", flush=True)


class Scheduler:
    def __init__(self):
        self._lock = threading.Lock()
        self._running = set()
        self._progress = {}          # name -> {started, done, total, label} while a job runs
        self._wake = threading.Event()
        self.status = read_json(paths.STATUS_PATH, {}) or {}
        for name in JOBS:
            self.status.setdefault(name, {})
            self.status[name]["next_run"] = 0      # run everything once at startup

    # -- public -------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._loop, name="scheduler", daemon=True).start()

    def run_now(self, name):
        if name not in JOBS:
            raise KeyError(name)
        with self._lock:
            self.status[name]["next_run"] = 0
        self._wake.set()

    def snapshot(self):
        cfg = _safe_config()
        now = time.time()
        out = {}
        with self._lock:
            for name, (key, default, _, label) in JOBS.items():
                st = dict(self.status.get(name, {}))
                interval = _interval(cfg, key, default)
                last_ok = st.get("last_success") or 0
                st.update({
                    "progress": dict(self._progress[name]) if name in self._progress else None,
                    "label": label,
                    "interval": interval,
                    "running": name in self._running,
                    "age": now - last_ok if last_ok else None,
                    "health": ("running" if name in self._running and not last_ok else
                               "never" if not last_ok else
                               "stale" if now - last_ok > STALE_INTERVALS * interval else
                               "failing" if st.get("last_ok") is False else "ok"),
                })
                out[name] = st
        return out

    # -- internals ----------------------------------------------------------
    def _loop(self):
        while True:
            cfg = _safe_config()
            now = time.time()
            for name, (key, default, timeout, _) in JOBS.items():
                with self._lock:
                    due = name not in self._running and self.status[name].get("next_run", 0) <= now
                    if due:
                        self._running.add(name)
                        self.status[name]["next_run"] = now + _interval(cfg, key, default)
                if due:
                    threading.Thread(target=self._run, args=(name, timeout), name=f"job-{name}", daemon=True).start()
            self._wake.wait(1.0)
            self._wake.clear()

    def _run(self, name, timeout):
        t0 = time.time()
        result, ok, err = {}, False, ""
        with self._lock:
            self._progress[name] = {"started": t0, "done": None, "total": None, "label": ""}
        tail = []
        try:
            env = dict(os.environ, PYTHONPATH=str(paths.REPO_DIR), BIGSCREEN_STATE=str(paths.STATE_DIR),
                       PYTHONUNBUFFERED="1")
            proc = subprocess.Popen([sys.executable, "-m", f"hub.jobs.{name}"], cwd=paths.REPO_DIR, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            timer = threading.Timer(timeout, proc.kill)
            timer.start()
            try:
                # Stream the job's output into the hub log (and the panel's live console) as it happens.
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    if line.startswith(RESULT_MARKER):
                        result = json.loads(line[len(RESULT_MARKER):])
                    elif line.startswith(PROGRESS_MARKER):
                        try:
                            pr = json.loads(line[len(PROGRESS_MARKER):])
                            with self._lock:
                                self._progress[name].update(pr)
                        except ValueError:
                            pass
                    else:
                        print(f"  {name} | {line}\n", end="", flush=True)
                        tail = (tail + [line])[-40:]
                proc.wait()
            finally:
                timer.cancel()
            ok = proc.returncode == 0
            if proc.returncode < 0 and time.time() - t0 >= timeout - 1:
                err = f"timed out after {timeout}s"
            elif not ok:
                err = "\n".join(tail)[-1500:]
        except Exception:
            err = traceback.format_exc()[-1500:]
        dur = time.time() - t0

        with self._lock:
            st = self.status[name]
            st["last_run"] = t0
            st["duration"] = round(dur, 2)
            st["last_ok"] = ok
            st["message"] = result.get("message", "") if result else ""
            st["detail"] = {k: v for k, v in result.items() if k not in ("ok", "message", "files")}
            st["error"] = err
            st["runs"] = st.get("runs", 0) + 1
            if ok:
                st["last_success"] = time.time()
                st["fail_streak"] = 0
            else:
                st["fail_streak"] = st.get("fail_streak", 0) + 1
            self._running.discard(name)
            self._progress.pop(name, None)
            atomic_write_json(paths.STATUS_PATH, self.status, indent=1)

        last_err = err.strip().splitlines()[-1] if err.strip() else ""
        log(f"{name}: {'ok' if ok else 'FAILED'} in {dur:.1f}s — {st['message'] or last_err}")
        if ok:
            sync.push_data()


def _safe_config():
    try:
        return config.load()
    except Exception:
        return {}


def _interval(cfg, key, default):
    try:
        return max(15, int(config.get_path(cfg, key, default)))
    except (TypeError, ValueError):
        return default
