"""
Cycle engine for the BigScreen client.

Each side of the screen (left/right) is driven by a CycleEngine instance. Given
a list of cycle entries from app-config.json::

    [
      {"panel": "calendar",              "dwell_sec": 60, "when": "calendar_has_events"},
      {"panel": "client_calls_upcoming", "dwell_sec": 30, "when": "no_events"},
      {"panel": "client_calls_past",     "dwell_sec": 30, "when": "no_events"},
      {"panel": "headlines",             "dwell_sec": 60, "when": "always"}
    ]

`current_panel(now, ctx)` returns the panel name that should be shown right
now. The engine walks the list, skips entries whose `when` condition is
false, and respects an optional manual override that takes priority over
the cycle.

`when` values:
  * "always"               - always applicable
  * "calendar_has_events"  - applicable only if ctx['calendar_has_events'] is True
  * "no_events"            - applicable only if ctx['calendar_has_events'] is False

A manual override is set via ``set_override(panel, duration_sec)``:
  * duration_sec is None             -> permanent until cleared
  * duration_sec is a positive float -> auto-expires after that many seconds

Thread-safety: ``set_override`` / ``clear_override`` may be called from a
different thread (the override HTTP server). All mutation is guarded by an
internal lock.
"""

from __future__ import annotations

import threading
import time
from typing import Iterable


# Panels the cycle engine knows how to dispatch. Adding a panel name here is
# only half the work - the main loop's render dispatch needs to know how to
# draw it too. See _render_panel() in main.py.
KNOWN_PANELS = (
    "calendar",
    "client_calls_upcoming",
    "client_calls_past",
    "headlines",
    "blank",
    "weather_preview",
    "weather_panels_half",
    "weather_panels_full",
)

# Conditions the engine evaluates against the per-frame context.
KNOWN_CONDITIONS = ("always", "calendar_has_events", "no_events", "weather_has_alert")


def _resolve_dwell_sec(raw: dict, default: float) -> float:
    """Compute the effective dwell in seconds for a cycle entry.

    If the entry has ``dwell_loops`` and ``weather_screen`` maps to a known
    animation track, the dwell is ``loops × frame_count / fps`` so the panel
    advances exactly at the end of the last full animation loop.

    Falls back to ``dwell_sec`` (or ``default``) for text-only screens and
    entries that don't carry ``dwell_loops``.
    """
    loops = raw.get("dwell_loops")
    ws = raw.get("weather_screen")
    if loops and ws:
        try:
            # Lazy import — avoids a hard dependency on the weather_panels
            # module during early Pi startup before rgbmatrix is loaded.
            from src.modules.weather_panels.common import (  # type: ignore
                SCREEN_DISPATCH, get_animation_track, get_radar_fps, full_frame_count,
            )
            # ws is a screen name (e.g. "map_goes_day_night"); resolve the
            # underlying track name via SCREEN_DISPATCH so the look-up works
            # regardless of whether the caller passed a screen name or track name.
            track_name = next(
                (t for n, _, t in SCREEN_DISPATCH if n == ws and t),
                None,
            ) or ws  # fall back to ws itself in case it IS already a track name
            track_cfg = get_animation_track(track_name)
            if track_cfg:
                fps = max(1, get_radar_fps())
                n_frames = full_frame_count(track_cfg)
                computed = max(1, int(loops)) * n_frames / float(fps)
                if computed > 0:
                    return computed
        except Exception:
            pass  # if import fails, fall through to dwell_sec
    try:
        dwell = float(raw.get("dwell_sec") or default)
        return dwell if dwell > 0 else default
    except (TypeError, ValueError):
        return default


class CycleEngine:
    """Drives the panel cycle on one side of the screen.

    Args:
        side:    "left" or "right" (used only for logging/debugging).
        cycle:   list of {"panel": str, "dwell_sec": int|float, "when": str} dicts.
                 Bad entries (missing panel, unknown panel, non-positive dwell)
                 are silently dropped at construction time.
        default_dwell_sec: fallback dwell when an entry omits or has invalid
                           dwell_sec. Defaults to 30s.
    """

    def __init__(self, side: str, cycle: Iterable[dict], default_dwell_sec: float = 30.0) -> None:
        self.side = side
        self._cycle: list[dict] = []
        for raw in cycle or []:
            if not isinstance(raw, dict):
                continue
            panel = (raw.get("panel") or "").strip()
            if not panel:   # any name; the renderer skips panels it does not have
                continue
            dwell = _resolve_dwell_sec(raw, default_dwell_sec)
            when = (raw.get("when") or "always").strip()
            if when not in KNOWN_CONDITIONS:
                when = "always"
            entry: dict = {**{k: v for k, v in raw.items() if k not in ("panel", "dwell_sec", "when")},
                           "panel": panel, "dwell_sec": dwell, "when": when}
            # Optional: pin a specific weather screen for weather_panels_* entries.
            # Value is a screen name (e.g. "current", "hourly", "map_radar_local")
            # or None (free-cycle through all screens as normal).
            ws = raw.get("weather_screen")
            if ws and isinstance(ws, str):
                entry["weather_screen"] = ws.strip()
            # Optional: pin a specific design index.
            wdi = raw.get("weather_design_idx")
            if wdi is not None:
                try:
                    entry["weather_design_idx"] = int(wdi)
                except (TypeError, ValueError):
                    pass
            # Pass through allowed_days / allowed_hours so the serialise/
            # round-trip from app-config.json doesn't strip them.
            if raw.get("allowed_days"):
                entry["allowed_days"] = raw["allowed_days"]
            if raw.get("allowed_hours"):
                entry["allowed_hours"] = raw["allowed_hours"]
            # Pass through animation-specific fields so they round-trip
            # cleanly through describe() / reload and the UI can read them.
            if raw.get("dwell_loops") is not None:
                try:
                    entry["dwell_loops"] = int(raw["dwell_loops"])
                except (TypeError, ValueError):
                    pass
            if raw.get("weather_preset") and isinstance(raw["weather_preset"], str):
                entry["weather_preset"] = raw["weather_preset"].strip()
            self._cycle.append(entry)

        self._current_idx: int = -1
        self._current_until: float = 0.0
        self._override: dict | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API: cycle decisions
    # ------------------------------------------------------------------

    def current_entry(self, now: float, ctx: dict) -> dict | None:
        """Like ``current_panel`` but returns the full cycle-entry dict.

        Returns ``None`` if no entry is applicable. The returned dict always
        contains at least ``{"panel": str, "dwell_sec": float, "when": str}``
        plus any optional fields stored at construction time
        (``weather_screen``, ``weather_design_idx``, ``allowed_days``,
        ``allowed_hours``).
        """
        # Manual override — synthesise a minimal entry so callers don't
        # have to special-case overrides.
        ov = self._get_active_override()
        if ov:
            return {**ov.get("extra", {}), "panel": ov["panel"], "dwell_sec": 0.0, "when": "always"}

        if not self._cycle:
            return None

        # Advance the internal state (identical logic to current_panel).
        applicable_idxs = [i for i, e in enumerate(self._cycle) if self._eval_when(e["when"], ctx)]
        if not applicable_idxs:
            return None

        if 0 <= self._current_idx < len(self._cycle):
            cur = self._cycle[self._current_idx]
            if self._eval_when(cur["when"], ctx) and now < self._current_until:
                return dict(cur)

        next_idx = None
        for i in applicable_idxs:
            if i > self._current_idx:
                next_idx = i
                break
        if next_idx is None:
            next_idx = applicable_idxs[0]

        self._current_idx = next_idx
        entry = self._cycle[next_idx]
        self._current_until = now + entry["dwell_sec"]
        return dict(entry)

    def current_panel(self, now: float, ctx: dict) -> str | None:
        """Return the panel name to show on this side at time ``now``.

        ``ctx`` is a dict of context flags evaluated against entries' ``when``
        conditions. Currently the only flag is ``calendar_has_events: bool``.
        Returns ``None`` if no entry is applicable right now (caller can paint
        the side black in that case).

        Delegates to ``current_entry`` so the two methods share one code path.
        """
        entry = self.current_entry(now, ctx)
        return entry["panel"] if entry else None

    # ------------------------------------------------------------------
    # Public API: manual override
    # ------------------------------------------------------------------

    def set_override(self, panel: str, duration_sec: float | None, extra: dict | None = None) -> None:
        """Pin a specific panel on this side, overriding the cycle.

        ``duration_sec`` of ``None`` means permanent until ``clear_override``
        is called. A positive float means auto-expire after that many seconds.
        """
        if not panel:
            raise ValueError("panel is required")
        with self._lock:
            self._override = {
                "panel": panel,
                "extra": dict(extra or {}),      # entry fields such as "track" / "weather_screen"
                "expires_at": (None if duration_sec is None else time.time() + float(duration_sec)),
            }

    def force_advance(self) -> None:
        """Immediately end the current entry's dwell so the cycle advances
        on the next ``current_entry`` call.

        Used by main.py to let the headlines renderer drive its own
        exit point: when all subreddits have scrolled by, main.py calls
        this so the cycle doesn't wait for the arbitrary ``dwell_sec``.
        """
        with self._lock:
            self._current_until = 0.0

    def clear_override(self) -> None:
        with self._lock:
            self._override = None
            # Force the cycle to re-evaluate from a fresh starting point.
            self._current_idx = -1
            self._current_until = 0.0

    def reload_cycle(self, new_cycle: Iterable[dict], default_dwell_sec: float = 30.0) -> None:
        """Replace the current cycle entries in-place, thread-safely.

        Uses the same validation logic as ``__init__``. Resets the position
        counter so the next ``current_entry`` / ``current_panel`` call starts
        fresh at the beginning of the new cycle. Any active override is left
        untouched.
        """
        new_entries: list[dict] = []
        for raw in new_cycle or []:
            if not isinstance(raw, dict):
                continue
            panel = (raw.get("panel") or "").strip()
            if not panel:   # any name; the renderer skips panels it does not have
                continue
            dwell = _resolve_dwell_sec(raw, default_dwell_sec)
            when = (raw.get("when") or "always").strip()
            if when not in KNOWN_CONDITIONS:
                when = "always"
            entry: dict = {**{k: v for k, v in raw.items() if k not in ("panel", "dwell_sec", "when")},
                           "panel": panel, "dwell_sec": dwell, "when": when}
            ws = raw.get("weather_screen")
            if ws and isinstance(ws, str):
                entry["weather_screen"] = ws.strip()
            wdi = raw.get("weather_design_idx")
            if wdi is not None:
                try:
                    entry["weather_design_idx"] = int(wdi)
                except (TypeError, ValueError):
                    pass
            if raw.get("allowed_days"):
                entry["allowed_days"] = raw["allowed_days"]
            if raw.get("allowed_hours"):
                entry["allowed_hours"] = raw["allowed_hours"]
            if raw.get("dwell_loops") is not None:
                try:
                    entry["dwell_loops"] = int(raw["dwell_loops"])
                except (TypeError, ValueError):
                    pass
            if raw.get("weather_preset") and isinstance(raw["weather_preset"], str):
                entry["weather_preset"] = raw["weather_preset"].strip()
            new_entries.append(entry)

        with self._lock:
            self._cycle = new_entries
            self._current_idx = -1
            self._current_until = 0.0

    def get_override_info(self) -> dict | None:
        """Return a dict describing the current override, or None if not active.

        Used by the /status endpoint and by the control panel to display the
        current state.
        """
        with self._lock:
            if not self._override:
                return None
            ea = self._override.get("expires_at")
            if ea is not None and time.time() >= ea:
                self._override = None
                return None
            return {
                "panel": self._override["panel"],
                "expires_at": ea,
                "remaining_sec": (None if ea is None else max(0.0, ea - time.time())),
            }

    # ------------------------------------------------------------------
    # Public API: introspection (for control panel /status, debugging)
    # ------------------------------------------------------------------

    def describe(self) -> dict:
        return {
            "side": self.side,
            "cycle": [dict(e) for e in self._cycle],
            "current_idx": self._current_idx,
            "current_until": self._current_until,
            "override": self.get_override_info(),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_active_override(self) -> dict | None:
        with self._lock:
            if not self._override:
                return None
            ea = self._override.get("expires_at")
            if ea is not None and time.time() >= ea:
                self._override = None
                # Reset the cycle so we don't snap back into the middle of
                # whatever entry was current before the override fired.
                self._current_idx = -1
                self._current_until = 0.0
                return None
            return dict(self._override)

    @staticmethod
    def _eval_when(cond: str, ctx: dict) -> bool:
        if cond == "always":
            return True
        if cond == "calendar_has_events":
            return bool(ctx.get("calendar_has_events", False))
        if cond == "no_events":
            return not bool(ctx.get("calendar_has_events", False))
        if cond == "weather_has_alert":
            return bool(ctx.get("weather_has_alert", False))
        return True
