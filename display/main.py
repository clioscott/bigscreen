"""BigScreen v2 display.

    python -m display.main                 # on the Pi: drives the LED panels
    python -m display.main --emulate       # anywhere: no hardware, frames served on /frame.png

Every frame is drawn into a numpy buffer (display.gfx.Canvas) and handed to the
panels in one call (FrameCanvas.SetBuffer). Layout follows v1 exactly: finance
ticker on top, two content zones driven by cycle engines, clock / weekday kanji
/ status / date along the bottom.
"""

import argparse
import os
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime

import numpy as np

from display.config import ConfigSource
from display.cycle_engine import CycleEngine
from display.data_loader import DataLoader
from display.gfx import Canvas, Color, graphics

DATA_DIR = os.environ.get("BIGSCREEN_DATA_DIR", "/dev/shm/bigscreen")
BOTTOM_STRIP_PX = 17
TILE_W, TILE_H = 64, 16      # dirty-tile granularity for pushing frames to the panels
BLACK = Color(0, 0, 0)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
class HardwareOutput:
    def __init__(self, cfg):
        from rgbmatrix import RGBMatrix, RGBMatrixOptions
        if "--led-no-drop-privs" not in sys.argv:
            sys.argv.append("--led-no-drop-privs")
        d = cfg.system.get("display", {})
        o = RGBMatrixOptions()
        o.rows, o.cols = 64, 64
        o.chain_length, o.parallel = 7, 3
        o.hardware_mapping = "regular"
        o.gpio_slowdown = d.get("gpio_slowdown", 3)
        o.limit_refresh_rate_hz = d.get("limit_refresh_rate_hz", 120)
        o.pwm_bits = d.get("pwm_bits", 8)
        o.pwm_lsb_nanoseconds = d.get("pwm_lsb_nanoseconds", 100)
        o.pwm_dither_bits = d.get("pwm_dither_bits", 1)
        o.brightness = d.get("brightness", 100)
        o.disable_hardware_pulsing = False
        o.drop_privileges = False
        o.show_refresh_rate = bool(d.get("show_refresh_rate", False))
        self.matrix = RGBMatrix(options=o)
        self.canvas = self.matrix.CreateFrameCanvas()
        self.hw_keys = {k: d.get(k) for k in ("gpio_slowdown", "limit_refresh_rate_hz", "pwm_bits",
                                              "pwm_lsb_nanoseconds", "pwm_dither_bits", "show_refresh_rate")}

        self.last = [None, None]     # what each of the two swapped hardware canvases currently holds
        self.parity = 0

    def present(self, fb):
        """Push only the 64x16 tiles that differ from what this hardware canvas already holds.

        Converting a pixel into PWM bit-planes costs ~120 ns in the library, so a full 448x192
        frame is ~10 ms. The panels are double-buffered: the canvas we draw into was last
        filled two frames ago, so diff against that.
        """
        buf = fb.buf
        last = self.last[self.parity]
        h, w = buf.shape[:2]
        if last is None or h % TILE_H or w % TILE_W:
            self.canvas.SetBuffer(buf)
            self.last[self.parity] = buf.copy()
        else:
            tiles = (buf != last).any(axis=2).reshape(h // TILE_H, TILE_H, w // TILE_W, TILE_W).any(axis=(1, 3))
            for ty in np.flatnonzero(tiles.any(axis=1)):
                row = tiles[ty]
                y0 = int(ty) * TILE_H
                tx, n = 0, len(row)
                while tx < n:
                    if not row[tx]:
                        tx += 1
                        continue
                    start = tx
                    while tx < n and row[tx]:
                        tx += 1
                    x0, x1 = start * TILE_W, tx * TILE_W
                    self.canvas.SetBuffer(np.ascontiguousarray(buf[y0:y0 + TILE_H, x0:x1]), x0, y0)
            np.copyto(last, buf)
        self.canvas = self.matrix.SwapOnVSync(self.canvas)
        self.parity ^= 1

    def set_brightness(self, value):
        self.matrix.brightness = max(1, min(100, int(value)))
        self.last = [None, None]      # brightness is baked into the bit-planes: rewrite everything


class EmulatorOutput:
    hw_keys = None

    def present(self, fb):
        pass

    def set_brightness(self, value):
        pass


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------
class Screen:
    """Owns the modules for one effective config. Rebuilt when the config changes."""

    def __init__(self, cfg, loader, prev=None, maps=None):
        from display.modules.calendar import CalendarRenderer
        from display.modules.client_calls import ClientCallsRenderer
        from display.modules.clock import ClockRenderer
        from display.modules.date_display import DateRenderer
        from display.modules.finance import FinanceTicker
        from display.modules.headlines import HeadlinesRenderer
        from display.modules.status import StatusBarRenderer

        self.cfg = cfg
        L = cfg.layout
        self.w, self.h = L.get("width", 448), L.get("height", 192)
        self.top_h = L.get("top_bar_height", 44)
        self.status_top = self.h - BOTTOM_STRIP_PX
        self.main_y = self.top_h + 1
        self.main_h = self.status_top - self.main_y
        self.cal_w = int(self.w * L.get("calendar_split_ratio", 0.66))
        self.right_x = self.cal_w + 1
        self.right_w = self.w - self.cal_w - 1
        self.clock_margin_left = L.get("clock_margin_left", 0)
        self.show_seconds = cfg.modules.get("clock", {}).get("show_seconds", True)
        self.border = Color(*cfg.get_color("zone_border"))

        self.clock = ClockRenderer(cfg)
        self.date = DateRenderer(cfg)
        self.status = StatusBarRenderer(cfg)
        self.finance = FinanceTicker(cfg, loader)
        self.calendar = CalendarRenderer(cfg, loader)
        self.headlines = HeadlinesRenderer(cfg, loader)
        self.client_calls = ClientCallsRenderer(cfg, loader)
        self.loader = loader

        cyc = cfg.modules.get("layout_cycle", {}) or {}
        self.cycle_raw = {side: cyc.get(side, []) for side in ("left", "right")}
        if prev is None:
            self.engines = {side: CycleEngine(side, self.cycle_raw[side]) for side in ("left", "right")}
            self.prev = {"left": None, "right": None}
            self.prev_key = {}
            self.passes = {}
        else:
            # Keep rotation position + overrides across config changes (saves, previews).
            self.engines = prev.engines
            for side in ("left", "right"):
                if self.cycle_raw[side] != prev.cycle_raw.get(side):
                    self.engines[side].reload_cycle(self.cycle_raw[side])
            self.prev = dict(prev.prev)
            self.prev_key = dict(getattr(prev, "prev_key", {}))
            self.passes = dict(getattr(prev, "passes", {}))
            self.finance.scroll_x = prev.finance.scroll_x
            self.finance.last_scroll_time = prev.finance.last_scroll_time
        # panel name -> draw(canvas, zone, entry, transition) -> "done" | None
        self.panels = {
            "calendar": self._draw_calendar,
            "client_calls_upcoming": lambda c, z, e, t: self._draw_calls(c, z, t, "upcoming"),
            "client_calls_past": lambda c, z, e, t: self._draw_calls(c, z, t, "past"),
            "headlines": self._draw_headlines,
            "blank": lambda c, z, e, t: None,
        }
        self.full_panels = {}     # name -> draw(canvas, zone, entry, transition); takes both zones

        from display.modules.weather_info import WeatherInfoPanel
        self.weather_half = WeatherInfoPanel(cfg, loader, full=False)
        self.weather_full = WeatherInfoPanel(cfg, loader, full=True)
        self.panels["weather"] = self.weather_half.draw
        self.full_panels["weather_full"] = self.weather_full.draw
        if maps is not None:
            from display.modules.weather_maps import MapPanel
            self.map_panel = MapPanel(cfg, maps)
            self.panels["map"] = self.map_panel.draw
            self.full_panels["map_full"] = self.map_panel.draw

    # -- panels ---------------------------------------------------------------
    def _draw_calendar(self, c, zone, entry, transition):
        x, y, w, h = zone
        if x == 0:                    # v1 calendar can only render in the left zone
            self.calendar.render(c, x, y, w, h)

    def _draw_calls(self, c, zone, transition, mode):
        x, y, w, h = zone
        self.client_calls.set_mode(mode)
        self.client_calls.update()
        if transition:
            self.client_calls.reset_scroll()
        self.client_calls.render(c, x, y, w, h)

    def _draw_headlines(self, c, zone, entry, transition):
        x, y, w, h = zone
        if transition and not self.headlines.has_pending_jump():
            self.headlines.reset_to_first_sub(w)
            self.headlines._cycle_complete = False
        self.headlines.render(c, x, y, w, h)
        if getattr(self.headlines, "_cycle_complete", False):
            return "done"

    # -- frame ----------------------------------------------------------------
    def update_data(self):
        self.calendar.update()
        self.headlines.update()
        self.finance.update()
        self.status.update()

    def draw(self, c, now):
        c.Clear()
        self.update_data()
        ctx = {"calendar_has_events": bool(getattr(self.calendar, "has_visible_events", True)),
               "weather_has_alert": self.weather_half.has_alert()}
        zones = {"left": (0, self.main_y, self.cal_w, self.main_h),
                 "right": (self.right_x, self.main_y, self.right_w, self.main_h)}

        chosen = {}
        for side in ("left", "right"):
            eng = self.engines[side]
            entry = None
            for _ in range(12):       # skip entries whose panel isn't available (yet)
                entry = eng.current_entry(now, ctx) or {}
                name = entry.get("panel", "blank")
                if name in self.panels or name in self.full_panels:
                    break
                eng.force_advance()
            chosen[side] = entry or {"panel": "blank"}

        full = next((s for s in ("left", "right") if chosen[s].get("panel") in self.full_panels), None)
        if full:
            entry = chosen[full]
            name = entry["panel"]
            zone = (0, self.main_y, self.w, self.main_h)
            transition = self._entry_key(entry) != self.prev_key.get(full)
            if self._finished(full, entry, self.full_panels[name](c, zone, entry, transition), transition):
                for s in ("left", "right"):
                    if chosen[s].get("panel") == name:
                        self.engines[s].force_advance()
        else:
            for side in ("left", "right"):
                entry = chosen[side]
                name = entry.get("panel", "blank")
                zone = zones[side]
                transition = self._entry_key(entry) != self.prev_key.get(side)
                if self._finished(side, entry, self.panels[name](c, zone, entry, transition), transition):
                    self.engines[side].force_advance()
            graphics.DrawLine(c, self.cal_w, self.main_y, self.cal_w, self.status_top, self.border)
        for side in ("left", "right"):
            self.prev[side] = chosen[side].get("panel")
            self.prev_key[side] = self._entry_key(chosen[side])

        # top bar (finance) over a black mask
        graphics.DrawLine(c, 0, self.top_h, self.w, self.top_h, self.border)
        c.FillRect(0, 0, self.w, self.top_h, BLACK)
        self.finance.render(c, 0, 0, self.w, self.top_h)

        # bottom strip: clock | weekday kanji | status | date
        c.FillRect(0, self.h - 16, self.w, 16, BLACK)
        now_str = datetime.now().strftime("%H:%M:%S" if self.show_seconds else "%H:%M")
        clock_text_w = sum(self.clock.font.CharacterWidth(ord(ch)) for ch in now_str) if self.clock.font else 60
        kanji_w = self.date.get_weekday_kanji_width()
        clock_x = self.clock_margin_left - 3
        status_x = clock_x + clock_text_w + 3 + kanji_w + 7
        date_w = self.date.get_zone_width()
        self.clock.render(c, clock_x, self.h - 14, status_x - clock_x, 14)
        self.date.render_weekday_beside_clock(c, clock_x + clock_text_w + 3, self.h - 3)
        cal = self.calendar
        self.status.render(c, status_x, self.h - 14, self.w - status_x - date_w, 14,
                           getattr(cal, "alert_active", False), getattr(cal, "alert_toggle", False),
                           getattr(cal, "alert_message", ""), getattr(cal, "status_overrides", []))
        self.date.render(c, self.w - date_w, self.h - 14, date_w, 14)
        graphics.DrawLine(c, 0, self.status_top, self.w, self.status_top, self.border)

    @staticmethod
    def _entry_key(entry):
        """Identity of a rotation entry (two map entries with different tracks are different screens)."""
        return (entry.get("panel"), entry.get("track"), entry.get("weather_screen"))

    def _finished(self, side, entry, result, transition):
        """True when an ``advance: "cycles"`` entry has completed its passes.

        Panels return "done" each time they finish one pass. Time-based entries ignore that and
        run for dwell_sec; overrides (no advance key) stay until they expire.
        """
        if transition:
            self.passes[side] = 0
        if result != "done" or entry.get("advance") != "cycles":
            return False
        self.passes[side] = self.passes.get(side, 0) + 1
        need = entry.get("cycles")
        if need is None and entry.get("panel", "").startswith("map") and hasattr(self, "map_panel"):
            need = self.map_panel.default_loops(entry.get("track"))
        return self.passes[side] >= max(1, int(need or 1))

    def headlines_jump(self, index=None, delta=0, hold_sec=300):
        """Show a headline category now. If headlines aren't on screen, pin them on the side the
        rotation normally uses (right by default) for ``hold_sec``."""
        h = self.headlines
        if not h.subreddits:
            return {"error": "no headline categories loaded yet"}
        if index is not None and not (0 <= int(index) < len(h.subreddits)):
            return {"error": f"category index {index} out of range"}
        side = next((s for s in ("left", "right") if self.prev.get(s) == "headlines"), None)
        if side is None:
            side = next((s for s in ("right", "left")
                         if any(e.get("panel") == "headlines" for e in getattr(self.engines[s], "_cycle", []))), "right")
            self.engines[side].set_override("headlines", hold_sec, {})
        h.request_jump(index=index, delta=delta)
        target = (int(index) if index is not None else (h.current_sub_index + int(delta))) % len(h.subreddits)
        return {"ok": True, "side": side, "category": h.subreddits[target], "index": target,
                "pinned_sec": None if self.prev.get(side) == "headlines" else hold_sec}

    def describe(self):
        return {side: {"panel": self.prev[side], **(self.engines[side].describe() or {})}
                for side in ("left", "right")}


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
class App:
    def __init__(self, emulate=False):
        self.emulate = emulate
        self.source = ConfigSource(os.path.join(DATA_DIR, "config.json"),
                                   fallback=os.environ.get("BIGSCREEN_CONFIG"))
        self.source.poll(time.time())
        self.cfg = self.source.effective()
        self.loader = DataLoader(DATA_DIR, check_interval=1.0)
        from display.modules.weather_maps import MapStore
        self.maps = MapStore(DATA_DIR)
        self.output = EmulatorOutput() if emulate else HardwareOutput(self.cfg)
        L = self.cfg.layout
        self.fb = Canvas(L.get("width", 448), L.get("height", 192))
        self.screen = Screen(self.cfg, self.loader, maps=self.maps)
        self._gen = self.source.generation
        self.frame_lock = threading.Lock()
        self.last_frame = self.fb.buf.copy()
        self.frame_seq = 0
        self.mirror_until = 0.0
        self.frame_ms = deque(maxlen=300)
        self.frame_times = deque(maxlen=300)
        self.started = time.time()
        self.brightness = None
        self.errors = deque(maxlen=20)
        self.restart_requested = False

    # -- config ---------------------------------------------------------------
    def _maybe_reload(self, now):
        self.source.poll(now)
        if self.source.generation == self._gen:
            return
        self._gen = self.source.generation
        new = self.source.effective()
        if self.output.hw_keys is not None:
            d = new.system.get("display", {})
            if any(d.get(k) != v for k, v in self.output.hw_keys.items()):
                print("[display] panel timing settings changed — restarting to apply")
                self.restart_requested = True
                return
        try:
            screen = Screen(new, self.loader, prev=self.screen, maps=self.maps)
        except Exception:
            self.errors.append(traceback.format_exc()[-600:])
            print("[display] new config failed to build, keeping previous:\n" + traceback.format_exc())
            return
        self.cfg, self.screen = new, screen
        self.brightness = None
        print(f"[display] config r{new.revision} applied")

    def _apply_brightness(self):
        d = self.cfg.system.get("display", {})
        level = d.get("brightness", 100)
        nm = self.cfg.system.get("night_mode", {})
        if nm.get("enabled"):
            start, end = nm.get("start_time", "22:00"), nm.get("end_time", "06:00")
            hm = time.strftime("%H:%M")
            night = (start <= hm < end) if start < end else (hm >= start or hm < end)
            if night:
                level = nm.get("brightness", level)
        if level != self.brightness:
            self.output.set_brightness(level)
            self.brightness = level

    # -- loop -------------------------------------------------------------------
    def run(self):
        if not self.emulate:
            try:
                os.sched_setaffinity(0, {2})
            except Exception as e:
                print(f"[display] could not pin to core 2: {e}")
        while not self.restart_requested:
            t0 = time.time()
            try:
                self._maybe_reload(t0)
                self._apply_brightness()
                self.screen.draw(self.fb, t0)
            except Exception:
                tb = traceback.format_exc()
                self.errors.append(tb[-600:])
                print(tb)
                time.sleep(0.5)
            t1 = time.time()
            if self.emulate or t1 < self.mirror_until:      # copy only while someone watches the mirror
                with self.frame_lock:
                    self.last_frame = self.fb.buf.copy()
                    self.frame_seq += 1
            self.output.present(self.fb)
            self.frame_ms.append((t1 - t0) * 1000.0)
            self.frame_times.append(time.time())
            fps = self.cfg.system.get("display", {}).get("fps", 60)
            spare = (1.0 / fps) - (time.time() - t0)
            if spare > 0.002:
                time.sleep(spare)
        sys.exit(3)       # systemd restarts us with the new panel settings

    def snapshot_frame(self):
        """Latest complete frame for the mirror. Asking keeps the render loop copying frames for 5 s."""
        was_active = time.time() < self.mirror_until
        self.mirror_until = time.time() + 5.0
        if not was_active:                       # the stored frame is stale; wait for the loop to copy one
            seq, t0 = self.frame_seq, time.time()
            while self.frame_seq == seq and time.time() - t0 < 0.2:
                time.sleep(0.01)
        with self.frame_lock:
            return self.last_frame, self.frame_seq

    def stats(self):
        ms = sorted(self.frame_ms)
        ft = list(self.frame_times)
        fps = (len(ft) - 1) / (ft[-1] - ft[0]) if len(ft) > 2 and ft[-1] > ft[0] else 0.0
        return {
            "fps": round(fps, 1),
            "draw_ms_avg": round(sum(ms) / len(ms), 2) if ms else None,
            "draw_ms_p95": round(ms[int(len(ms) * 0.95) - 1], 2) if ms else None,
            "draw_ms_max": round(ms[-1], 2) if ms else None,
            "uptime_s": int(time.time() - self.started),
            "config_revision": self.cfg.revision,
            "preview": self.source.preview_info(time.time()),
            "brightness": self.brightness,
            "panels": self.screen.describe(),
            "headlines": self.screen.headlines.describe(),
            "data_age_s": {k: round(time.time() - v) for k, v in self.loader.mtimes.items()},
            "recent_errors": len(self.errors),
            "emulated": self.emulate,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emulate", action="store_true", help="no LED hardware; render for the mirror only")
    ap.add_argument("--port", type=int, default=int(os.environ.get("BIGSCREEN_CONTROL_PORT", 8765)))
    args = ap.parse_args()
    app = App(emulate=args.emulate)
    from display.control import start_control_server
    start_control_server(app, args.port)
    print(f"[display] running ({'emulated' if args.emulate else 'hardware'}), data {DATA_DIR}, control :{args.port}")
    app.run()


if __name__ == "__main__":
    main()
