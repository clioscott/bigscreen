"""WeatherPanelsRenderer.

Cycles through six screens (current / forecast / hourly / minutely /
radar / warning) within the active design. PREV/NEXT button presses on
the control panel call ``step_design`` to switch design family without
disturbing the screen-cycle clock.

The renderer is shape-compatible with ``WeatherLayoutPreviewRenderer``
so it slots into the override server's ``weather_layout_renderers`` map
under different mode keys.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from display.gfx import graphics

from display.renderer import ModuleRenderer

from .common import (
    _C, SAMPLE, SCREEN_DWELL_SEC, NUM_SCREENS, SCREEN_NAMES, SCREEN_DISPATCH,
    screen_idx_at,
    text_width, draw_text,
)
from .designs_half import HALF_DESIGNS
from .designs_full import FULL_DESIGNS


logger = logging.getLogger(__name__)


class WeatherPanelsRenderer(ModuleRenderer):
    """Six-screen design preview module.

    mode="half" -> 224x138 zone (left or right).
    mode="full" -> 448x138 zone (whole main area).

    Within the active design, screens auto-cycle every SCREEN_DWELL_SEC
    seconds: current -> forecast -> hourly -> minutely -> radar ->
    warning -> ... PREV/NEXT button switches design without resetting
    the screen-cycle clock so the user can compare designs at the same
    screen position.
    """

    def __init__(self, config, mode: str = "half", loader=None):
        super().__init__(config)
        if mode not in ("half", "full"):
            raise ValueError(f"WeatherPanelsRenderer: mode must be 'half' or 'full', got {mode!r}")
        self.mode = mode
        self.loader = loader
        self._live_data: dict | None = None
        # Silence noisy PIL logs.
        logging.getLogger("PIL").setLevel(logging.WARNING)

        # Fonts -- map renderer-internal names to existing config fonts.
        def _load_font(cfg_name: str):
            f = graphics.Font()
            p = self.config.get_font_path(cfg_name)
            if p and os.path.exists(p):
                f.LoadFont(p)
            return f

        self._fonts = {
            "tiny":  _load_font("small"),            # tom-thumb 3x6
            "small": _load_font("top_bar"),           # 5x7
            "med":   _load_font("headlines_title"),   # nokia-small 5x10
            "big":   _load_font("clock"),             # 8x13B
        }

        # Register a default BDF font with the radar module so the
        # animated radar tracks can draw a per-frame timestamp overlay.
        # Done here (rather than in radar.py module init) to keep
        # radar.py free of rgbmatrix.graphics.Font instantiation.
        try:
            from . import radar as _radar
            _radar.set_timestamp_font(self._fonts["tiny"])
            _radar.set_marker_font(self._fonts["tiny"])
        except Exception:
            logger.warning("could not register radar timestamp/marker font", exc_info=True)

        # Pre-warm the radar animation cache for this mode in the background.
        # Pi has spare cores (htop typically shows 2 of 4 idle); PIL decoding
        # releases the GIL so this runs on a sleeping core without competing
        # with the render thread. By the time the cycle reaches a radar
        # screen, frames are already hot -- no static-fallback flicker on
        # the first viewing.
        try:
            from . import radar as _radar
            from .common import ANIMATION_TRACKS
            mode_suffix = "full" if self.mode == "full" else "half"
            for track_name, track_cfg in ANIMATION_TRACKS.items():
                preset = track_cfg.get(f"preset_{mode_suffix}")
                if preset:
                    # _load_anim_frames returns immediately + spawns the
                    # bg-decoder daemon thread.
                    _radar._load_anim_frames(track_name, preset)
        except Exception:
            logger.debug("anim pre-warm skipped", exc_info=True)

        # State.
        self._design_idx = 0
        self._cycle_anchor = time.time()
        self._lock = threading.Lock()

        # Configure-mode lock: when set, render() always paints the
        # specified (design_idx, screen_idx) instead of advancing the
        # screen cycle. Set/cleared by the override server in response
        # to control-panel actions.
        self._cfg_lock_design: int | None = None
        self._cfg_lock_screen: int | None = None
        self._cfg_lock_blank:  bool = False

        # Cycle screen pin: set by main.py when the active cycle entry
        # carries a ``weather_screen`` key. Takes effect only when
        # configure-mode is NOT active, so configure mode always wins.
        # Cleared whenever the cycle moves away from a weather-panel entry.
        self._cycle_screen_pin: int | None = None
        self._cycle_design_pin: int | None = None
        self._cycle_preset_pin: str | None = None

        # Screen animation timing — tracks when the current screen became active
        # so radar animations restart from frame 0 on screen entry.
        self._screen_entry_time: float = time.time()
        self._last_screen_idx: int | None = None

        # Layer overrides for live preview compositing.  When set (not None),
        # radar screens render with custom layer visibility/opacity instead
        # of the pre-baked animation frames.
        self._layer_overrides: dict | None = None

        # Animation completion flag — set to True by render() when the current
        # animation screen has played ANIM_LOOPS_PER_SCREEN full loops based on
        # the *actual* pack size on disk.  main.py polls this each frame and
        # calls force_advance() on the cycle engine so the cycle advances
        # immediately when the animation finishes, regardless of the cycle
        # entry's computed dwell_sec (which is based on theoretical full
        # retention and may be much longer than the actual animation).
        self._anim_complete: bool = False

        # Choose registry.
        self._designs = FULL_DESIGNS if mode == "full" else HALF_DESIGNS

    # ------------------------------------------------------------------
    # ModuleRenderer interface
    # ------------------------------------------------------------------

    def update(self):
        """Pull live weather data from the server's WebSocket push feed.
        Falls back to hardcoded SAMPLE if no live data is available yet."""
        if self.loader is not None:
            fresh = self.loader.get_data("weather")
            if fresh and isinstance(fresh, dict) and "current" in fresh:
                self._live_data = fresh

    def inject_weather(self, data: dict) -> None:
        """Inject weather data directly (used by the override server when
        the control panel pushes fetched EC data for configure-mode preview)."""
        if data and isinstance(data, dict) and "current" in data:
            self._live_data = data
            logger.info("Weather data injected (%.1f°C, %s)",
                        data["current"].get("temp_c", "?"),
                        data["current"].get("weather_text", "?"))

    def set_layer_overrides(self, overrides: dict | None) -> None:
        """Set per-layer visibility and opacity overrides for live preview.

        ``overrides`` shape::

            {
                "basemap":   {"enabled": True, "opacity": 100},
                "satellite": {"enabled": True, "opacity": 55},
                "radar":     {"enabled": True, "opacity": 100},
            }

        Pass ``None`` to clear overrides and return to normal rendering.
        """
        self._layer_overrides = overrides
        logger.info("Layer overrides %s",
                    "cleared" if overrides is None else f"set: {overrides}")

    def get_next_update_time(self):
        """Wake at ~50ms only when actively visible (animated GIF icons).
        Otherwise wake slowly — the main loop fps target handles rendering."""
        # If we're config-locked (actively displayed on panel), wake fast
        # for smooth icon animation. Otherwise, wake at 1s since we're
        # likely not being rendered this frame.
        if self.is_configure_locked() or self._live_data is not None:
            return time.time() + 0.05
        return time.time() + 1.0

    # ------------------------------------------------------------------
    # PREV / NEXT
    # ------------------------------------------------------------------

    def step_design(self, delta: int) -> int:
        """Advance the active design by ``delta``. Wraps modulo number of
        designs. Does NOT reset the screen-cycle clock so users can
        compare designs at the same screen position."""
        with self._lock:
            self._design_idx = (self._design_idx + delta) % len(self._designs)
            return self._design_idx

    # ------------------------------------------------------------------
    # Cycle screen pin (main.py sets this from cycle-entry weather_screen)
    # ------------------------------------------------------------------

    def set_cycle_screen_pin(self,
                             screen_idx: int | None,
                             design_idx: int | None = None,
                             preset_pin: str | None = None) -> None:
        """Pin the renderer to a specific screen (and optionally a design)
        while it is part of the normal display cycle.

        ``screen_idx`` is the integer index into SCREEN_NAMES.
        Pass ``None`` for either arg to clear that dimension of the pin.
        """
        with self._lock:
            # Reset animation timing when the pinned screen changes
            new_screen = int(screen_idx) % max(1, NUM_SCREENS) if screen_idx is not None else None
            if new_screen != self._cycle_screen_pin:
                self._screen_entry_time = time.time()
                self._last_screen_idx = None
            self._cycle_screen_pin = (int(screen_idx) % max(1, NUM_SCREENS)) if screen_idx is not None else None
            self._cycle_design_pin = (int(design_idx) % max(1, len(self._designs))) if design_idx is not None else None
            self._cycle_preset_pin = str(preset_pin) if preset_pin else None

    def clear_cycle_screen_pin(self) -> None:
        """Remove any cycle screen pin. The renderer will show the default
        screen (current conditions) until a new pin is applied."""
        with self._lock:
            self._cycle_screen_pin = None
            self._cycle_design_pin = None
            self._cycle_preset_pin = None
            self._screen_entry_time = time.time()
            self._last_screen_idx = None

    # ------------------------------------------------------------------
    # Configure-mode lock (control panel "Configure Layout" tab)
    # ------------------------------------------------------------------

    def configure_lock(self, design_idx: int | None = None,
                       screen_idx: int | None = None,
                       blank: bool = False) -> dict:
        """Pin the renderer to a specific (design, screen) so the screen
        cycle stops advancing while the control panel's Configure Layout
        tab is active. ``blank=True`` skips rendering entirely (used when
        the tab is open but the user hasn't picked a design yet).
        """
        with self._lock:
            if design_idx is not None:
                self._design_idx = int(design_idx) % max(1, len(self._designs))
                self._cfg_lock_design = self._design_idx
            if screen_idx is not None:
                self._cfg_lock_screen = int(screen_idx) % NUM_SCREENS
            self._cfg_lock_blank = bool(blank)
            self._screen_entry_time = time.time()
            self._last_screen_idx = None
            return {
                "locked":     True,
                "design_idx": self._cfg_lock_design,
                "screen_idx": self._cfg_lock_screen,
                "blank":      self._cfg_lock_blank,
            }

    def configure_unlock(self) -> dict:
        """Resume normal cycle rendering (called when the control panel
        leaves the Configure Layout tab or the user clicks Exit)."""
        with self._lock:
            self._cfg_lock_design = None
            self._cfg_lock_screen = None
            self._cfg_lock_blank = False
            self._screen_entry_time = time.time()
            self._last_screen_idx = None
            return {"locked": False}

    def is_configure_locked(self) -> bool:
        with self._lock:
            return (self._cfg_lock_design is not None
                    or self._cfg_lock_screen is not None
                    or self._cfg_lock_blank)

    def is_anim_complete(self) -> bool:
        """Return True if the current animation screen has played its
        configured loops (based on actual pack size on disk).

        main.py polls this after each render() call and calls
        force_advance() on the cycle engine when True, so the cycle
        moves on immediately once the animation finishes — regardless
        of the cycle entry's dwell_sec, which is based on the theoretical
        full-retention frame count and may far exceed the actual animation.

        Returns False for static (non-animation) screens — the cycle
        engine's dwell_sec is used normally for those.
        """
        return self._anim_complete

    def list_designs(self) -> list[dict]:
        """Return the list of designs for this renderer's mode -- used by
        the control panel to populate the Design dropdown without having
        to import the registry over the network."""
        return [{"idx": i, "name": d.name} for i, d in enumerate(self._designs)]

    def get_state(self) -> dict:
        with self._lock:
            idx = self._design_idx
            screen_idx = self._cycle_screen_pin
            if screen_idx is None:
                from .common import AUTO_CYCLE_IDXS
                screen_idx = AUTO_CYCLE_IDXS[0] if AUTO_CYCLE_IDXS else 0
        design = self._designs[idx]
        return {
            "mode":         self.mode,
            "design_idx":   idx,
            "design_name":  design.name,
            "screen_idx":   screen_idx,
            "screen_name":  SCREEN_NAMES[screen_idx],
            "num_designs":  len(self._designs),
            "num_screens":  NUM_SCREENS,
        }

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self, canvas, x, y, width, height):
        now = time.time()
        with self._lock:
            idx = self._design_idx
            anchor = self._cycle_anchor
            locked_design = self._cfg_lock_design
            locked_screen = self._cfg_lock_screen
            blank_lock    = self._cfg_lock_blank
            cycle_screen_pin   = self._cycle_screen_pin
            cycle_design_pin   = self._cycle_design_pin
            cycle_preset_pin   = self._cycle_preset_pin

        # Configure-mode: blank middle panels until the user picks
        # something on the Configure Layout tab.
        if blank_lock and locked_design is None:
            return

        # Configure-mode: pin to a specific (design, screen) regardless
        # of where the screen cycle would otherwise be. Configure mode
        # takes priority over the cycle screen pin.
        if locked_design is not None:
            idx = locked_design
        if locked_screen is not None:
            screen_idx = locked_screen
        elif cycle_screen_pin is not None:
            # Cycle entry has requested a specific screen (e.g. "current",
            # "hourly", "map_goes_day_night"). Render exactly that screen.
            screen_idx = cycle_screen_pin
            if cycle_design_pin is not None:
                idx = cycle_design_pin
        else:
            # No cycle pin active — the cycle tab controls what appears on
            # screen, so the renderer does not free-cycle on its own.
            # Default to the first static screen (current conditions) as a
            # safe placeholder, e.g. while the cycle engine is initialising.
            from .common import AUTO_CYCLE_IDXS
            screen_idx = AUTO_CYCLE_IDXS[0] if AUTO_CYCLE_IDXS else 0

        # Track how long this screen has been showing so radar animations
        # start from frame 0 on entry rather than mid-cycle.
        with self._lock:
            if self._last_screen_idx != screen_idx:
                self._screen_entry_time = now
                self._last_screen_idx = screen_idx
            anim_elapsed = now - self._screen_entry_time

        if not self._designs:
            graphics.DrawText(canvas, self._fonts["small"], x + 4, y + 12,
                              _C(255, 90, 80), "(no designs registered)")
            return

        design = self._designs[idx]

        # Palette — kept for backwards compat with radar.py internals
        from .common import PALETTES
        palette = PALETTES.get(design.palette, PALETTES["cool"])

        # Live weather data — prefer real data, but in configure mode
        # fall back to SAMPLE after a brief wait so the preview is usable.
        # Radar/map screens can render without weather data (they use
        # pre-built animation frames), so let those through.
        screen_name, method_name, track = SCREEN_DISPATCH[screen_idx]

        using_sample = False
        if self._live_data is None and track is None:
            # In configure mode (preview), fall back to sample data after
            # 5 seconds so the user can preview layouts without waiting for
            # the EC feed.  In normal cycle mode, keep showing the loading
            # screen until real data arrives.
            if self.is_configure_locked() and (now - self._cycle_anchor) > 5.0:
                using_sample = True
            else:
                loading_color = _C(150, 150, 150)
                graphics.DrawText(canvas, self._fonts["small"], x + 4, y + 20,
                                  loading_color, "Loading weather data...")
                graphics.DrawText(canvas, self._fonts["tiny"], x + 4, y + 32,
                                  _C(100, 100, 100), "Waiting for EC feed")
                return

        weather_data = self._live_data if self._live_data is not None else SAMPLE

        screen_fn: Callable = getattr(design, method_name, None)

        if screen_fn is None:
            graphics.DrawText(canvas, self._fonts["small"], x + 4, y + 12,
                              _C(255, 90, 80),
                              f"(missing {method_name})")
            return

        try:
            if track is not None:
                # Pass layer overrides if set (for live preview compositing)
                kwargs = {"track": track}
                if self._layer_overrides is not None:
                    kwargs["layer_overrides"] = self._layer_overrides
                kwargs["anim_elapsed"] = anim_elapsed
                if cycle_preset_pin:
                    kwargs["preset_base"] = cycle_preset_pin
                screen_fn(canvas, x, y, width, height, self._fonts, palette,
                          weather_data, now, **kwargs)
            else:
                screen_fn(canvas, x, y, width, height, self._fonts, palette,
                          weather_data, now)
        except Exception as exc:
            err_color = _C(255, 0, 0)
            graphics.DrawText(canvas, self._fonts["small"], x + 4, y + 12, err_color,
                              f"ERR {design.name} {SCREEN_NAMES[screen_idx]}")
            graphics.DrawText(canvas, self._fonts["tiny"], x + 4, y + 24, err_color,
                              str(exc)[:50])
            logger.exception("weather_panels design '%s' screen '%s' failed: %s",
                             design.name, SCREEN_NAMES[screen_idx], exc)

        # Check whether the animation has completed its loops so main.py
        # can call force_advance() immediately rather than waiting for the
        # dwell_sec (which is based on theoretical full-retention frame count
        # and may be much longer than the actual on-disk pack).
        # This is a no-op for static screens (track is None).
        self._anim_complete = False
        if track is not None and not self.is_configure_locked():
            try:
                from . import radar as _radar
                from .common import ANIMATION_TRACKS, ANIM_LOOPS_PER_SCREEN, get_radar_fps
                _tc = ANIMATION_TRACKS.get(track, {})
                if _tc.get("is_stitch_screen"):
                    # Stitch: sum actual frame counts from past + future packs
                    from .common import get_stitch_group
                    _grp = get_stitch_group(_tc.get("stitch_group", ""))
                    _mode_sfx = "full" if self.mode == "full" else "half"
                    _total_n = 0
                    _all_loaded = True
                    for _sn in (_grp.get("past"), _grp.get("future")):
                        if _sn:
                            _sc = ANIMATION_TRACKS.get(_sn, {})
                            _sp = _sc.get(f"preset_{_mode_sfx}", "")
                            _spk = _radar._load_anim_frames(_sn, _sp)
                            if _spk and len(_spk) > 0:
                                _total_n += len(_spk)
                            else:
                                _all_loaded = False
                    if _all_loaded and _total_n > 0:
                        _fps = max(1, get_radar_fps())
                        self._anim_complete = anim_elapsed >= ANIM_LOOPS_PER_SCREEN * _total_n / _fps
                else:
                    # Regular track: resolve preset the same way render_radar_animated does
                    _pkeys = _tc.get("preset_keys") or []
                    if _pkeys:
                        if width >= 400:
                            _pack_preset = next((k for k in _pkeys if "_full" in k), None) or _pkeys[-1]
                        else:
                            _pack_preset = next((k for k in _pkeys if "_half" in k), None) or _pkeys[0]
                    else:
                        _mode_sfx = "full" if self.mode == "full" else "half"
                        _pack_preset = _tc.get(f"preset_{_mode_sfx}", "")
                    if _pack_preset:
                        _pk = _radar._load_anim_frames(track, _pack_preset)
                        if _pk and len(_pk) > 0:
                            _fps = max(1, get_radar_fps())
                            self._anim_complete = anim_elapsed >= ANIM_LOOPS_PER_SCREEN * len(_pk) / _fps
            except Exception:
                pass  # never let completion-check errors disrupt rendering

        # In preview mode with sample data, show a small indicator so the
        # user knows they're looking at placeholder values.
        if using_sample:
            graphics.DrawText(canvas, self._fonts["tiny"],
                              x + width - 40, y + height - 2,
                              _C(255, 180, 0), "(SAMPLE)")
