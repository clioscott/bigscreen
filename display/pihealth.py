"""The Pi's own health for the control panel's Overview: temperature, memory, and whether its 5 V supply sagged.

A thread reads a few small /proc and /sys files every SAMPLE_SEC (no subprocess: forking the display would stall
it). Under-voltage comes from the kernel's rpi_volt sensor, which is only raised while the supply is low, so it is
counted here: a sag of a few seconds would otherwise be gone before anyone looked. A sagging supply makes the
firmware slow the CPU down, which the wall shows as a flicker or a dim moment.
"""

import glob
import os
import threading
import time

SAMPLE_SEC = 1.0             # (the kernel's sensor shows its last 2 s reading: sample faster than that)
_state = {"temp_c": None, "mem_available_mb": None, "rss_mb": None, "undervoltage_now": None,
          "undervoltage_events": 0, "undervoltage_last": None, "since": None}
_started = False
_lock = threading.Lock()


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _volt_alarm_path():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        if (_read(os.path.join(d, "name")) or "").strip() == "rpi_volt":
            return os.path.join(d, "in0_lcrit_alarm")
    return None


def sample(alarm_path=None, now=None):
    """Read everything once into the state (the thread's step; also what tests call)."""
    now = time.time() if now is None else now
    s = _state
    t = _read("/sys/class/thermal/thermal_zone0/temp")
    s["temp_c"] = round(int(t) / 1000.0, 1) if t and t.strip().lstrip("-").isdigit() else None
    for line in (_read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemAvailable:"):
            s["mem_available_mb"] = int(line.split()[1]) // 1024
    for line in (_read("/proc/self/status") or "").splitlines():
        if line.startswith("RssAnon:"):            # the display's own memory (not the map packs mapped from /dev/shm)
            s["rss_mb"] = int(line.split()[1]) // 1024
    a = _read(alarm_path) if alarm_path else None
    low = None if a is None else a.strip() == "1"
    if low and not s["undervoltage_now"]:
        s["undervoltage_events"] += 1
    if low:
        s["undervoltage_last"] = now
    s["undervoltage_now"] = low
    return s


def _loop():
    path = _volt_alarm_path()
    while True:
        try:
            sample(path)
        except Exception as e:                    # never let a odd /proc line end the thread
            print(f"[pihealth] {e}")
        time.sleep(SAMPLE_SEC)


def snapshot():
    """The latest readings (None where this machine has no such file); starts the sampler on first use."""
    global _started
    with _lock:
        if not _started:
            _started = True
            _state["since"] = time.time()
            threading.Thread(target=_loop, name="pihealth", daemon=True).start()
    return dict(_state)
