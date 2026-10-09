"""
High-performance JSON data loader for the BigScreen client.

Reads pre-built JSON files from a LOCAL tmpfs directory (/dev/shm/bigscreen).
The server pushes updated files via rsync over Tailscale SSH — the Pi never
touches the network from Python. All I/O is local RAM backed by tmpfs.

Architecture:
  * One dedicated POLLER THREAD (started lazily on first get_data()) sweeps
    every registered module's JSON file at ``check_interval`` seconds.
  * Stat-first: only re-reads + JSON-parses when the file's mtime changes.
    Steady state is a single stat() per file per sweep (~1μs on tmpfs,
    releases the GIL).
  * ``get_data(name)`` is a pure dict read — no I/O, no locks. Consumers can
    call it every frame at 60+ FPS without any cost.
  * Uses orjson when available (5-10× faster than stdlib json, and releases
    the GIL during parsing). Falls back to stdlib json transparently.

No timeout/retry/worker-thread machinery is needed: /dev/shm is local RAM,
stat() and read() complete in microseconds, and there's no risk of kernel-
level hangs that sshfs/CIFS mounts had.
"""

import os
import time
import threading
import datetime


# --- JSON backend: prefer orjson (C, GIL-released), fallback to stdlib ------
try:
    import orjson as _json_mod

    def _parse_file(file_path):
        """Read + parse JSON from a local file using orjson (bytes mode)."""
        with open(file_path, 'rb') as f:
            return _json_mod.loads(f.read())

    _JSON_BACKEND = "orjson"

except ImportError:
    import json as _json_mod

    def _parse_file(file_path):
        """Read + parse JSON from a local file using stdlib json."""
        with open(file_path, 'r') as f:
            return _json_mod.load(f)

    _JSON_BACKEND = "json"


# How long data is considered "fresh" by default. The server writes JSON every
# few minutes, so 15 minutes is a generous staleness threshold.
DEFAULT_STALE_AFTER_SEC = 15 * 60


class DataLoader:
    """Lock-free, never-blocking data loader for local tmpfs files.

    Designed for /dev/shm — all filesystem calls are fast local RAM ops.
    The poller thread is the only thread that touches the filesystem.
    """

    def __init__(self, data_dir, check_interval=1.0,
                 stale_after_sec=DEFAULT_STALE_AFTER_SEC):
        self.data_dir = data_dir
        self.check_interval = float(check_interval)
        self.stale_after_sec = stale_after_sec

        self.cache = {}            # module_name -> parsed JSON (read by render thread)
        self.mtimes = {}           # module_name -> file mtime (epoch seconds)
        self._known_modules = set()    # registered module names

        # Throttled-log bookkeeping (so missing files don't spam the journal).
        self._last_missing_log = {}

        # Background poller state.
        self._poll_thread = None
        self._poll_wake = threading.Event()
        self._poll_lock = threading.Lock()

        # Compatibility with DataReceiver interface (used by main.py loading screen).
        self._connected = False

        print(f"[DATA-LOADER] JSON backend: {_JSON_BACKEND}")
        print(f"[DATA-LOADER] data_dir: {data_dir}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_data(self, module_name):
        """Return the parsed JSON for `module_name` or None if unavailable.

        Pure dict lookup — never touches the filesystem, never blocks.
        First call for a new module also kicks off the background poller.
        """
        if module_name not in self._known_modules:
            self._register(module_name)
        return self.cache.get(module_name)

    def get_mtime(self, module_name):
        """File mtime of the cached data, or None if not loaded yet."""
        return self.mtimes.get(module_name)

    def is_stale(self, module_name, max_age_sec=None):
        """True if the cached data is older than `max_age_sec` (default: stale_after_sec).

        Returns True if we have no data at all.
        """
        if max_age_sec is None:
            max_age_sec = self.stale_after_sec
        mtime = self.mtimes.get(module_name)
        if mtime is None:
            return True
        return (time.time() - mtime) > max_age_sec

    def invalidate(self, module_name):
        """Drop cached data so the next sweep re-reads the file."""
        self.mtimes.pop(module_name, None)
        self.cache.pop(module_name, None)
        # Wake the poller to re-fetch immediately.
        self._poll_wake.set()

    # --- DataReceiver-compatible interface (used by main.py loading screen) ---

    @property
    def connected(self):
        """True once /dev/shm/bigscreen directory exists and is readable."""
        return self._connected

    def has_data(self, *topics):
        """True once all requested topics have been loaded from disk."""
        if not topics:
            return bool(self.cache)
        return all(t in self.cache for t in topics)

    # ------------------------------------------------------------------
    # Background poller (the only thread that touches the filesystem)
    # ------------------------------------------------------------------

    def _register(self, module_name):
        """First-time registration for a module. Spawns the poller thread
        on the very first call across all modules.
        """
        with self._poll_lock:
            self._known_modules.add(module_name)
            if self._poll_thread is None or not self._poll_thread.is_alive():
                self._poll_thread = threading.Thread(
                    target=self._poll_loop, name="data-poller", daemon=True,
                )
                self._poll_thread.start()
        # Nudge the poller so the new module's first read happens promptly.
        self._poll_wake.set()

    def _poll_loop(self):
        """Sweep every registered module on a fixed interval. Never raises;
        any exception is logged and the loop continues.
        """
        while True:
            try:
                # Check if the data directory exists (controls "connected" state).
                self._connected = os.path.isdir(self.data_dir)

                # Snapshot the module list — it can grow during the sweep
                # (new modules registered) but won't shrink.
                modules = list(self._known_modules)
                for module_name in modules:
                    self._poll_one(module_name)
            except Exception as exc:
                print(f"[DATA-POLLER] sweep error: {exc}")
            # Sleep until either check_interval elapses or someone wakes us
            # (e.g. invalidate()). Clear BEFORE wait so a set() between the
            # end of a sweep and the wait() isn't lost.
            self._poll_wake.clear()
            self._poll_wake.wait(timeout=self.check_interval)

    def _poll_one(self, module_name):
        """Stat-first poll: only re-read + JSON-parse when the file's mtime
        has actually changed since the last successful read.

        On tmpfs (/dev/shm) stat() completes in ~1μs and releases the GIL.
        With orjson, the JSON parse also releases the GIL. Steady state is
        essentially free — the GIL is never held for more than a few μs.
        """
        file_path = os.path.join(self.data_dir, f"{module_name}.json")

        # ---- Fast path: stat() only (cheap, GIL-released) ----
        try:
            st = os.stat(file_path)
        except FileNotFoundError:
            self._throttled_log(self._last_missing_log, module_name,
                                f"[{module_name.upper()}] waiting for {file_path}")
            return
        except OSError as e:
            print(f"[{module_name.upper()}] stat error: {e}")
            return

        new_mtime = st.st_mtime
        old_mtime = self.mtimes.get(module_name)
        if old_mtime is not None and new_mtime == old_mtime:
            # File hasn't changed — nothing to do. No JSON parse, no
            # GIL contention, no cache write. This is the steady state
            # ~99% of the time.
            return

        # ---- Slow path: file changed; do the read + parse ----
        try:
            data = _parse_file(file_path)
        except Exception as e:
            print(f"[{module_name.upper()}] parse error: {e}. Serving cached data.")
            return

        if not data:
            return

        # Dict assignment is atomic under the GIL, so the render thread
        # always sees a complete cache entry — never a half-updated one.
        self.cache[module_name] = data
        self.mtimes[module_name] = new_mtime

        mtime_str = datetime.datetime.fromtimestamp(new_mtime).strftime('%Y-%m-%d %H:%M:%S')
        count = len(data) if isinstance(data, (list, dict)) else 0
        verb = "updated" if old_mtime else "loaded"
        print(f"[{module_name.upper()}] {verb}: {count} items, mtime {mtime_str}")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _throttled_log(state_dict, key, message, interval_sec=60):
        """Print `message` at most once per `interval_sec` per `key`."""
        now = time.time()
        if now - state_dict.get(key, 0) > interval_sec:
            print(message)
            state_dict[key] = now
