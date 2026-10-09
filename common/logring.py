"""In-memory console log for the control panel's live log view.

``install()`` tees this process's stdout/stderr into a ring buffer (the output
still reaches journald as before). ``since(seq)`` returns the lines after a
cursor, so the panel can poll cheaply: nothing is read from disk and nothing
is spawned per request, which matters on the Pi.
"""

import subprocess
import sys
import threading
import time
from collections import deque

MAX_LINES = 3000


class LogRing:
    def __init__(self, maxlen=MAX_LINES):
        self._lines = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0
        self.boot = int(time.time())          # changes when the process restarts; clients reset their cursor

    def add(self, text, stream="out", ts=None):
        with self._lock:
            for part in str(text).splitlines() or [""]:
                self._seq += 1
                self._lines.append((self._seq, ts or time.time(), stream, part.rstrip()[:2000]))

    def since(self, after=0, limit=600):
        with self._lock:
            seq = self._seq
            if after > seq:                   # cursor from before a restart
                after = 0
            first = self._lines[0][0] if self._lines else seq + 1
            if after >= seq:
                return {"seq": seq, "boot": self.boot, "lines": []}
            rows = [r for r in self._lines if r[0] > after] if after >= first - 1 else list(self._lines)
        rows = rows[-limit:]
        return {"seq": seq, "boot": self.boot, "lines": [{"n": n, "t": round(t, 3), "s": s, "m": m} for n, t, s, m in rows]}


class _Tee:
    def __init__(self, orig, ring, stream):
        self._orig, self._ring, self._stream, self._buf = orig, ring, stream, ""
        self._lock = threading.Lock()

    def write(self, s):
        n = self._orig.write(s)
        with self._lock:
            self._buf += s
            if "\n" in self._buf:
                done, self._buf = self._buf.rsplit("\n", 1)
                self._ring.add(done, self._stream)
        return n

    def flush(self):
        self._orig.flush()

    def __getattr__(self, name):           # isatty, fileno, encoding, ...
        return getattr(self._orig, name)


ring = LogRing()
_installed = False


def install(unit=None, history=200):
    """Tee stdout/stderr into ``ring``. With ``unit``, first seed it with that systemd unit's last log lines
    (one journalctl call at startup), so the panel shows what happened before this process started."""
    global _installed
    if _installed:
        return ring
    if unit:
        try:
            out = subprocess.run(["journalctl", "-u", unit, "-n", str(history), "--no-pager", "-o", "short-unix"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                ts, _, rest = line.partition(" ")
                try:
                    t = float(ts)
                except ValueError:
                    continue
                msg = rest.split(": ", 1)[1] if ": " in rest else rest      # drop "host unit[pid]:"
                ring.add(msg, "hist", t)
        except Exception:
            pass
        ring.add(f"——— {unit} started ———", "sys")
    sys.stdout = _Tee(sys.stdout, ring, "out")
    sys.stderr = _Tee(sys.stderr, ring, "err")
    _installed = True
    return ring
