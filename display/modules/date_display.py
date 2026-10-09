"""
Date display: (1) weekday kanji beside clock (bottom-left, colored by day type), (2) numeric date M/D/YYYY in bottom-right (clock font).
"""
import os
from datetime import datetime
from display.renderer import ModuleRenderer

from display.gfx import graphics

# JIS X 0208 (16-bit) for knj10.bdf. Weekday kanji 月火水木金土日 (Mon..Sun)
_WEEKDAY_JIS = (0x376E, 0x3250, 0x3F65, 0x4C5A, 0x3662, 0x455A, 0x467C)

# Weekday kanji colors by day (Mon=0 .. Sun=6): 月火水木金土日
_WEEKDAY_COLORS = (
    (0x44, 0x88, 0xFF),  # 月 Monday   #4488FF
    (0xFF, 0x66, 0x00),  # 火 Tuesday  #FF6600
    (0x00, 0xFF, 0xFF),  # 水 Wednesday #00FFFF
    (0x00, 0xFF, 0x00),  # 木 Thursday #00FF00
    (0xFF, 0xD7, 0x00),  # 金 Friday   #FFD700
    (0x00, 0x00, 0xFF),  # 土 Saturday #0000FF
    (0xFF, 0x00, 0x00),  # 日 Sunday   #FF0000
)


def _resolve_font_path(relative_path):
    """Resolve font path relative to project root so it works when cwd is not project root (e.g. systemd)."""
    if not relative_path:
        return None
    if os.path.isabs(relative_path) and os.path.exists(relative_path):
        return relative_path
    # This file is src/modules/date_display.py -> project root is two levels up
    _here = os.path.abspath(os.path.dirname(__file__))
    project_root = os.path.dirname(os.path.dirname(_here))
    resolved = os.path.normpath(os.path.join(project_root, relative_path))
    return resolved if os.path.exists(resolved) else relative_path


class DateRenderer(ModuleRenderer):
    def __init__(self, config):
        super().__init__(config)
        # Kanji font: weekday character only (月火水木金土日)
        self.kanji_font = graphics.Font()
        self._kanji_font_loaded = False
        font_path = self.config.get_font_path("mplus12bold")
        if font_path:
            font_path = _resolve_font_path(font_path)
            if font_path and os.path.exists(font_path):
                try:
                    self.kanji_font.LoadFont(font_path)
                    self._kanji_font_loaded = hasattr(self.kanji_font, "height") and self.kanji_font.height > 0
                    if self._kanji_font_loaded:
                        print(f"[Date] Kanji font loaded: {font_path} (height={self.kanji_font.height})")
                except Exception as e:
                    print(f"[Date] Kanji font load failed: {font_path} — {e}")
        # Clock font: date string M/D/YYYY (same as clock for symmetry)
        self.clock_font = graphics.Font()
        self._clock_font_loaded = False
        clock_path = self.config.get_font_path("clock")
        if clock_path:
            clock_path = _resolve_font_path(clock_path)
            if clock_path and os.path.exists(clock_path):
                try:
                    self.clock_font.LoadFont(clock_path)
                    self._clock_font_loaded = hasattr(self.clock_font, "height") and self.clock_font.height > 0
                    if self._clock_font_loaded:
                        print(f"[Date] Clock font loaded for date: {clock_path}")
                except Exception as e:
                    print(f"[Date] Clock font load failed: {clock_path} — {e}")
        self._date_color = graphics.Color(255, 255, 255)
        self._bg_black = graphics.Color(0, 0, 0)
        # Pre-build all 7 weekday kanji Color objects (Mon=0..Sun=6)
        self._weekday_colors_cached = tuple(
            graphics.Color(*rgb) for rgb in _WEEKDAY_COLORS
        )
        self._last_date_str = ""
        self._cached_width = 0
        self._min_zone_width = 60

    def _date_text_width(self, s):
        """Pixel width of string in clock font (used for date zone)."""
        if not s or not self.clock_font:
            return 0
        w = 0
        for c in s:
            cw = self.clock_font.CharacterWidth(ord(c))
            if cw <= 0:
                cw = self.clock_font.height if hasattr(self.clock_font, "height") else 8
            w += cw
        return w

    def _weekday_color(self):
        """Color for weekday kanji (one color per day: 月火水木金土日)."""
        wd = datetime.now().weekday()
        return self._weekday_colors_cached[wd] if 0 <= wd <= 6 else self._weekday_colors_cached[0]

    def get_weekday_kanji_char(self):
        """Single weekday character (月火水木金土日) as JIS-encoded string for drawing beside clock."""
        wd = datetime.now().weekday()
        jis = _WEEKDAY_JIS[wd] if 0 <= wd <= 6 else _WEEKDAY_JIS[0]
        return chr(jis)

    def get_weekday_kanji_width(self):
        """Pixel width of the single weekday kanji character."""
        if not self._kanji_font_loaded or not self.kanji_font:
            return 10
        c = self.get_weekday_kanji_char()
        w = self.kanji_font.CharacterWidth(ord(c))
        return w if w > 0 else (self.kanji_font.height if hasattr(self.kanji_font, "height") else 10)

    def render_weekday_beside_clock(self, canvas, x, clock_baseline_y):
        """Draw the single weekday kanji at x; color by day type; baseline 1px up to align with clock."""
        if not self._kanji_font_loaded:
            return
        char = self.get_weekday_kanji_char()
        baseline_y = clock_baseline_y - 1
        color = self._weekday_color()
        graphics.DrawText(canvas, self.kanji_font, x, baseline_y, color, char)

    def get_date_string(self):
        """Numeric date only: M/D/YYYY (same format and font as clock for symmetry)."""
        now = datetime.now()
        return f"{now.month}/{now.day}/{now.year}"

    def get_zone_width(self):
        """Width needed for date zone: clock-font text width + 3px (right margin) + 3px (left margin)."""
        s = self.get_date_string()
        if s != self._last_date_str:
            self._last_date_str = s
            self._cached_width = max(self._date_text_width(s) + 6, self._min_zone_width)
        return self._cached_width

    def update(self):
        pass

    def render(self, canvas, x, y, width, height):
        # Black background for date zone
        for i in range(height):
            graphics.DrawLine(canvas, x, y + i, x + width - 1, y + i, self._bg_black)
        if not self._clock_font_loaded:
            return
        date_str = self.get_date_string()
        text_w = self._date_text_width(date_str)
        if text_w <= 0:
            return
        # Match clock alignment: 3px from right and bottom (baseline); right-aligned
        margin = 3
        text_x = (x + width) - margin - text_w
        baseline_y = (y + height) - margin
        graphics.DrawText(canvas, self.clock_font, text_x, baseline_y, self._date_color, date_str)

    def get_next_update_time(self):
        # Update at start of next day (or next minute is enough for date)
        now = datetime.now()
        from datetime import timedelta
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return tomorrow.timestamp()
