"""
Upcoming Client Calls panel: list of Consult/Export/Review calls from calendar,
with date/time in left column and title in right column. Shown in place of
headlines for a configured duration after each full headlines cycle.
Column 4 (call type) uses k12x8; its BDF is Unicode (ISO10646), so labels are
passed as Unicode (参照/移行/確認) with no JIS conversion.
"""
import os
import time
import datetime
from display.renderer import ModuleRenderer

from display.gfx import graphics


def _resolve_font_path(relative_path):
    """Resolve font path relative to project root so LoadFont gets a valid path (e.g. under systemd)."""
    if not relative_path:
        return None
    if os.path.isabs(relative_path) and os.path.exists(relative_path):
        return relative_path
    _here = os.path.abspath(os.path.dirname(__file__))
    project_root = os.path.dirname(os.path.dirname(_here))
    resolved = os.path.normpath(os.path.join(project_root, relative_path))
    return resolved if os.path.exists(resolved) else relative_path


class ClientCallsRenderer(ModuleRenderer):
    def __init__(self, config, loader):
        super().__init__(config)
        self.loader = loader

        # Headlines-style fonts: title (contact) = Nokia small, suffix = grey small
        self.font_title = graphics.Font()
        path_t = self.config.get_font_path("headlines_title")
        if not path_t:
            path_t = self.config.get_font_path("header")
        if path_t:
            self.font_title.LoadFont(path_t)

        self.font_suffix = graphics.Font()
        path_s = self.config.get_font_path("headlines_url")
        if not path_s:
            path_s = self.config.get_font_path("body")
        if path_s:
            self.font_suffix.LoadFont(path_s)

        # Left column: date/time (small font from calendar panel)
        self.font_time = graphics.Font()
        path_d = self.config.get_font_path("event_title_tiny")
        if path_d:
            self.font_time.LoadFont(path_d)

        # Column 4 (call type): k12x8 12×8 JIS 第一水準 BDF — labels 参照/移行/確認 via JIS codepoints
        self.font_label = graphics.Font()
        self._font_label_loaded = False
        path_label = self.config.get_font_path("k12x8")
        if path_label:
            path_label = _resolve_font_path(path_label)
            if path_label and os.path.exists(path_label):
                try:
                    self.font_label.LoadFont(path_label)
                    self._font_label_loaded = hasattr(self.font_label, "height") and self.font_label.height > 0
                except Exception:
                    pass
        # Call type labels: English keys -> Japanese (for toggle). Background colors keyed by English.
        self._label_ja = {"Consult": "参照", "Export": "移行", "Review": "確認"}
        # Toggle: config "call_type_label_language" = "ja" | "en" (default "en" for Consult/Export/Review)
        cfg = self.config.modules.get("client_calls_panel", {})
        self._use_japanese_labels = (cfg.get("call_type_label_language", "en").lower() == "ja")
        # Past calls with this Google Calendar colorId (e.g. "8" = Graphite) show client name in strikethrough
        self._strikethrough_past_color_id = str(cfg.get("strikethrough_past_color_id", "8"))
        # Background colors for call type labels (Consult/Export/Review) — text gets this bg, not a separate box
        self._call_type_bg = {
            "Consult": (92, 26, 26),   # #5C1A1A
            "Export": (15, 39, 68),    # #0F2744
            "Review": (14, 51, 32),    # #0E3320
        }
        # Pre-built Color objects for the 3 fixed call-type backgrounds
        self._call_type_bg_colors = {
            k: graphics.Color(*v) for k, v in self._call_type_bg.items()
        }
        # Small cache for dynamic overlay colors (keyed by RGB tuple)
        self._color_cache: dict[tuple, "graphics.Color"] = {}

        self.color = graphics.Color(*self.config.get_color("default_text"))
        self.color_dim = graphics.Color(150, 150, 150)
        self.color_white = graphics.Color(255, 255, 255)
        # Past panel: slightly dimmed white for event names (still reads as white on LED, less bright)
        self.color_past_text = graphics.Color(200, 200, 200)
        self.color_past_bg = graphics.Color(14, 14, 14)
        self.color_header_bg = graphics.Color(40, 40, 40)
        self.color_accent = graphics.Color(0, 200, 200)

        self.header_height = 15
        # Spacing: same for both upcoming and past panels
        self.row_gap = 3  # pixels between different events
        self.wrap_line_spacing = self.font_title.height  # pixels between wrapped lines within one event
        self._mode = "upcoming"  # "upcoming" | "past"

        self._calls = []
        self._rows = []
        self._total_content_height = 0
        self._rows_built_for_width = 0
        self._last_calendar_mtime = None
        self._compute_column_widths()

    def _text_width(self, font, text):
        """Exact pixel width of text in the given font."""
        return sum(font.CharacterWidth(ord(c)) for c in text)

    def _get_suffixes(self):
        ep = self.config.modules.get("calendar", {}).get("event_processing", {})
        return ep.get("client_call_title_suffixes", [" - Consult", " - Export", " - Review"])

    def _get_type_label(self, suffix_stripped):
        """Return display label: Japanese (参照/移行/確認) or English (Consult/Export/Review) per config toggle."""
        if self._use_japanese_labels:
            return self._label_ja.get(suffix_stripped, suffix_stripped)
        return suffix_stripped  # English: show Consult, Export, Review as-is

    def _legacy_done_overlay(self, ev, all_events):
        """Backward-compat fallback: if the server hasn't been updated to bake
        ``_status_overlay`` into events yet, detect a plain "done" overlay client-side
        and synthesize a strikethrough overlay so the past panel still works.
        """
        ev_start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")
        ev_end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")
        ev_id = ev.get("id")
        if not ev_start or not ev_end:
            return None
        for other in all_events:
            if other.get("id") == ev_id:
                continue
            other_start = (other.get("start") or {}).get("dateTime") or (other.get("start") or {}).get("date")
            other_end = (other.get("end") or {}).get("dateTime") or (other.get("end") or {}).get("date")
            if other_start != ev_start or other_end != ev_end:
                continue
            title = (other.get("summary_clean") or other.get("summary") or "").strip().lower()
            if title == "done":
                return {
                    "name": "Done",
                    "display_mode": "strikethrough",
                    "label_text": "",
                    "color": [200, 200, 200],
                }
        return None

    def _compute_column_widths(self):
        """
        Col1 (date) and Col2 (time) and Col4 (type): minimum width for content + 2px margin.
        Col3 (main/name) gets all remaining width.
        """
        margin_px = 2
        # Column 1: date ("Wed 12/25") — minimum width
        date_sample = "Wed 12/25"
        self._col1_width = self._text_width(self.font_time, date_sample) + margin_px
        # Column 2: time ("10:00a") — its own column, minimum width
        time_sample = "10:00a"
        self._col2_width = self._text_width(self.font_suffix, time_sample) + margin_px
        # Column 4 (type): width must fit both JA and EN so toggle works without layout shift
        col4_ja = 0
        if self._label_ja:
            col4_ja = max(self._text_width(self.font_label, w) for w in self._label_ja.values()) if self._font_label_loaded else 0
        label_words = list(self._label_ja.keys()) if self._label_ja else []
        if not label_words:
            label_words = [(s or "").replace(" - ", "").strip() for s in self._get_suffixes()]
            label_words = [w for w in label_words if w]
        col4_en = max(self._text_width(self.font_suffix, w) for w in label_words) if label_words else 0
        col4_content = max(col4_ja, col4_en)
        self._col3_width = col4_content + margin_px

    def _format_relative_time(self, timestamp):
        """Format a Unix timestamp as relative time for header (e.g. '5 min ago')."""
        if not timestamp or timestamp == 0:
            return ""
        now = time.time()
        diff = now - timestamp
        if diff < 60:
            return f"{int(diff)} sec ago"
        if diff < 3600:
            return f"{int(diff / 60)} min ago"
        if diff < 86400:
            return f"{int(diff / 3600)} hour{'s' if diff >= 7200 else ''} ago"
        if diff < 604800:
            return f"{int(diff / 86400)} day{'s' if diff >= 172800 else ''} ago"
        return f"{int(diff / 604800)} week{'s' if diff >= 1209600 else ''} ago"

    def _parse_start_dt(self, event):
        start_raw = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        if not start_raw or "T" not in start_raw:
            return None
        try:
            return datetime.datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _parse_end_dt(self, event):
        end_raw = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
        if not end_raw or "T" not in end_raw:
            return None
        try:
            return datetime.datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _is_client_call(self, event):
        summary = event.get("summary_clean") or event.get("summary") or ""
        return any(summary.endswith(s) for s in self._get_suffixes())

    def _split_title_and_suffix(self, title):
        """Return (contact_part, suffix) e.g. ('HSSK Law', ' - Export')."""
        for s in self._get_suffixes():
            if title.endswith(s):
                return title[: -len(s)].strip(), s
        return title, ""

    def set_mode(self, mode):
        """Switch between 'upcoming' (future events) and 'past' (past events, most recent first)."""
        if mode != self._mode:
            # Clear stale data immediately so a cache miss in update()
            # shows "no calls" instead of events from the wrong mode.
            self._calls = []
            self._rows = []
        self._mode = mode
        self._rows_built_for_width = 0

    def update(self):
        raw = self.loader.get_data("calendar")
        if raw is None:
            return
        # v1 rebuilt this list (with an O(n^2) overlay scan) every frame. Recompute only when
        # calendar.json changes, the mode flips, or 30 s pass (events move upcoming → past).
        key = (id(raw), self._mode)
        if key == getattr(self, "_upd_key", None) and time.time() - getattr(self, "_upd_t", 0) < 30:
            return
        self._upd_key, self._upd_t = key, time.time()
        self._last_calendar_mtime = self.loader.get_mtime("calendar")
        events_list = raw if isinstance(raw, list) else (raw.get("events", []) if isinstance(raw, dict) else [])
        cfg = self.config.modules.get("client_calls_panel", {})
        lookahead_days = cfg.get("lookahead_days", 45)
        lookback_days = cfg.get("past_lookback_days", 45)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        self._calls = []
        for e in events_list:
            if not self._is_client_call(e):
                continue
            start_dt = self._parse_start_dt(e)
            if start_dt is None:
                continue
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)
            end_dt = self._parse_end_dt(e)
            if end_dt is not None and end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)
            if self._mode == "upcoming":
                # Upcoming: event not yet ended (in-progress or future). Until end time, it stays here.
                if end_dt is not None:
                    if end_dt <= now_utc:
                        continue
                else:
                    if start_dt < now_utc:
                        continue
                if start_dt > now_utc + datetime.timedelta(days=lookahead_days):
                    continue
            else:
                # Past: event has ended (end_dt <= now). After end time, it appears only here.
                if end_dt is not None:
                    if end_dt > now_utc:
                        continue
                else:
                    if start_dt >= now_utc:
                        continue
                if start_dt < now_utc - datetime.timedelta(days=lookback_days):
                    continue
            self._calls.append(e)
        if self._mode == "upcoming":
            self._calls.sort(key=lambda ev: self._parse_start_dt(ev) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc))
        else:
            self._calls.sort(key=lambda ev: self._parse_start_dt(ev) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
        # Resolve status overlays. Server already attaches ``_status_overlay`` to
        # events that have an overlapping marker (e.g. titled "done" / "postponed"
        # / "noshow") via configurable rules in client_calls_panel.status_overlay_rules.
        # Fall back to the legacy "done" detection if running against a server that
        # hasn't been redeployed yet (so this client keeps working during a rollout).
        for ev in self._calls:
            if not ev.get("_status_overlay"):
                legacy = self._legacy_done_overlay(ev, events_list)
                if legacy:
                    ev["_status_overlay"] = legacy
        self._rows_built_for_width = 0

    def _build_rows(self, panel_width):
        """
        One row per event. Col1 = date, Col2 = time, Col3 = client name, Col4 = type.
        Date only for first event of day; time and name line up per row.

        Col4 (the call-type / overlay label cell) is sized to fit the widest label
        actually in this batch, so a single overlap labelled "RESCHEDULED" widens
        col4 dynamically without us needing to know the rule list ahead of time.
        """
        self._rows = []
        if not self._calls:
            return

        # Recompute col4 width based on what's actually in this batch. Start from
        # the static suffix labels (Consult/Export/Review or their JA equivalents)
        # so an empty past panel keeps the same layout it always had.
        margin_px = 2
        col4_ja = 0
        if self._label_ja and self._font_label_loaded:
            col4_ja = max(self._text_width(self.font_label, w) for w in self._label_ja.values())
        en_words = list(self._label_ja.keys()) if self._label_ja else []
        if not en_words:
            en_words = [(s or "").replace(" - ", "").strip() for s in self._get_suffixes()]
            en_words = [w for w in en_words if w]
        # Fold in any overlay labels that will be drawn in this batch.
        for ev in self._calls:
            ov = ev.get("_status_overlay")
            if ov and (ov.get("display_mode") or "label_override") == "label_override":
                lbl = (ov.get("label_text") or "").strip()
                if lbl:
                    en_words.append(lbl)
        col4_en = max((self._text_width(self.font_suffix, w) for w in en_words), default=0)
        self._col3_width = max(col4_ja, col4_en) + margin_px

        gap_time_to_name = 5
        main_col_width = max(20, panel_width - self._col1_width - self._col2_width - self._col3_width - gap_time_to_name)
        contact_width = main_col_width
        title_line_h = self.font_title.height
        cursor_y = 0
        prev_date_str = None

        for ev in self._calls:
            start_dt = self._parse_start_dt(ev)
            if not start_dt:
                continue
            date_str = start_dt.strftime("%a ") + str(start_dt.month) + "/" + str(start_dt.day)
            hour = start_dt.hour % 12 or 12
            minute = start_dt.minute
            ampm = "a" if start_dt.hour < 12 else "p"
            time_str = f"{hour}:{minute:02d}{ampm}"
            title = ev.get("summary_clean") or ev.get("summary") or "Event"
            contact, suffix = self._split_title_and_suffix(title)
            title_lines = self._wrap_title(contact, contact_width)
            show_date = date_str != prev_date_str
            prev_date_str = date_str

            first_line_h = title_line_h
            extra_lines = (len(title_lines) - 1) * self.wrap_line_spacing if len(title_lines) > 1 else 0
            row_h = max(first_line_h + extra_lines, title_line_h)
            color_id = ev.get("colorId")
            self._rows.append({
                "type": "event",
                "date_str": date_str if show_date else "",
                "time_str": time_str,
                "contact": contact,
                "suffix": suffix,
                "title_lines": title_lines,
                "y": cursor_y,
                "h": row_h,
                "color_id": str(color_id) if color_id is not None else None,
                "status_overlay": ev.get("_status_overlay"),
            })
            cursor_y += row_h + self.row_gap

        self._total_content_height = cursor_y
        self._rows_built_for_width = panel_width

    def _wrap_title(self, text, max_width):
        """Return list of lines for wrapped text (e.g. 'contact + time' as single cell)."""
        if not (text or "").strip():
            return []
        lines = []
        current = []
        current_px = 0
        space_w = self.font_title.CharacterWidth(ord(" "))
        for word in text.split():
            word_w = sum(self.font_title.CharacterWidth(ord(c)) for c in word)
            need = word_w + (space_w if current else 0)
            if current_px + need <= max_width:
                current.append(word)
                current_px += need
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]
                current_px = word_w
        if current:
            lines.append(" ".join(current))
        return lines[:4]

    def render(self, canvas, x, y, width, height):
        if not self._calls:
            self.update()
        # Past mode: fill entire panel with dark grey background (LED-friendly distinction from upcoming)
        if self._mode == "past":
            for i in range(height):
                ly = y + i
                graphics.DrawLine(canvas, x, ly, x + width - 1, ly, self.color_past_bg)
        # Content starts BELOW the header with 6px buffer so no text hides under the header
        header_total = self.header_height + self.font_suffix.height + 6
        content_start_y = y + header_total
        content_height = height - header_total

        if not self._calls:
            header_text = "Past Client Calls" if self._mode == "past" else "Upcoming Client Calls"
            for i in range(self.header_height):
                ly = y + i
                if y <= ly < y + height:
                    graphics.DrawLine(canvas, x, ly, x + width - 1, ly, self.color_header_bg)
            graphics.DrawText(canvas, self.font_suffix, x + 2, y + 10, self.color_accent, header_text)
            msg = "No past client calls" if self._mode == "past" else "No upcoming client calls"
            col = self.color_past_text if self._mode == "past" else self.color
            graphics.DrawText(canvas, self.font_title, x, content_start_y, col, msg)
            return

        # Build or rebuild rows with actual panel width so wrapping uses full right column
        if not self._rows or self._rows_built_for_width != width:
            self._build_rows(width)

        # Only full rows that fit; leave 1px so no row touches the status bar
        available_h = max(0, content_height - 1)
        row_index = 0
        acc_y = 0
        while row_index < len(self._rows):
            row = self._rows[row_index]
            if acc_y + row["h"] > available_h:
                break
            acc_y += row["h"] + self.row_gap
            row_index += 1
        visible_count = row_index

        col1_w = self._col1_width
        col2_w = self._col2_width
        main_x = x + col1_w + col2_w + 5
        label_content_left = x + width - self._col3_width + 2

        # Draw list content first (like headlines) so header can be drawn on top
        for i in range(visible_count):
            row = self._rows[i]
            screen_y = content_start_y + row["y"]
            row_h = row["h"]
            row_top = screen_y

            # Column 1: date only for first event of day (right-justified)
            if row["date_str"]:
                date_w = self._text_width(self.font_time, row["date_str"])
                date_x = x + col1_w - 2 - date_w
                graphics.DrawText(canvas, self.font_time, date_x, row_top, self.color_dim, row["date_str"])

            # Column 2: time (right-aligned like column 1)
            time_w = self._text_width(self.font_suffix, row["time_str"])
            time_x = x + col1_w + 2 + col2_w - 2 - time_w
            graphics.DrawText(canvas, self.font_suffix, time_x, row_top, self.color_dim, row["time_str"])

            # Column 3: client name. The line is drawn in the row's standard color.
            # Strikethrough is applied for past calls when EITHER:
            #  - the event has a status overlay with display_mode == "strikethrough"
            #  - the event's colorId matches the configured "graphite" sentinel
            # The overlay's color (RGB triple) is used for the strike line so each
            # status can have its own visual treatment.
            row_color = self.color_past_text if self._mode == "past" else self.color
            line_y = row_top
            overlay = row.get("status_overlay") if isinstance(row.get("status_overlay"), dict) else None
            overlay_mode = (overlay or {}).get("display_mode") or ""
            strike_line_color = None
            if self._mode == "past":
                if overlay and overlay_mode == "strikethrough":
                    rgb = list(overlay.get("color") or [200, 200, 200])
                    rgb = (rgb + [0, 0, 0])[:3]
                    try:
                        key = tuple(max(0, min(255, int(c))) for c in rgb)
                        if key not in self._color_cache:
                            self._color_cache[key] = graphics.Color(*key)
                        strike_line_color = self._color_cache[key]
                    except (TypeError, ValueError):
                        strike_line_color = row_color
                elif row.get("color_id") == self._strikethrough_past_color_id:
                    strike_line_color = row_color
            for line in row["title_lines"]:
                graphics.DrawText(canvas, self.font_title, main_x, line_y, row_color, line)
                if strike_line_color is not None:
                    line_w = self._text_width(self.font_title, line)
                    strike_y = line_y - self.font_title.height // 2
                    graphics.DrawLine(canvas, main_x, strike_y, main_x + line_w - 1, strike_y, strike_line_color)
                line_y += self.wrap_line_spacing

            # Column 4: call-type label. By default this is the Consult/Export/Review
            # text + matching dark-tinted background. A "label_override" status
            # overlay replaces both — e.g. "POSTPONED" on an orange cell — so the
            # original suffix is hidden behind the new status text.
            suffix_stripped = (row["suffix"] or "").replace(" - ", "").strip()
            override_active = bool(overlay and overlay_mode == "label_override")
            label_text_to_draw = ""
            bg_rgb = None
            use_label_font = False

            if override_active:
                # Overlay wins: label text + background come straight from the rule.
                label_text_to_draw = (overlay.get("label_text") or suffix_stripped or "").strip()
                ov_rgb = list(overlay.get("color") or [120, 60, 60])
                ov_rgb = (ov_rgb + [0, 0, 0])[:3]
                try:
                    bg_rgb = tuple(max(0, min(255, int(c))) for c in ov_rgb)
                except (TypeError, ValueError):
                    bg_rgb = (120, 60, 60)
                # Overlay labels always use the English suffix font (English text,
                # variable lengths). The Japanese k12x8 font is reserved for the
                # localized Consult/Export/Review labels.
                use_label_font = False
            elif suffix_stripped:
                label_text_to_draw = self._get_type_label(suffix_stripped)
                use_label_font = self._use_japanese_labels and self._font_label_loaded and suffix_stripped in self._label_ja
                bg_rgb = self._call_type_bg.get(suffix_stripped)

            if label_text_to_draw:
                font_used = self.font_label if use_label_font else self.font_suffix
                label_h = font_used.height
                text_x = label_content_left
                text_y = row_top
                if bg_rgb is not None:
                    cell_left = max(x, x + width - self._col3_width)
                    cell_right = x + width - 3
                    bg_key = tuple(bg_rgb)
                    if bg_key not in self._color_cache:
                        self._color_cache[bg_key] = graphics.Color(*bg_rgb)
                    bg_color = self._color_cache[bg_key]
                    cell_top = row_top - label_h
                    box_height = label_h + (2 if not use_label_font else 1)
                    for i in range(box_height):
                        ly = cell_top + i
                        if y <= ly < y + height:
                            graphics.DrawLine(canvas, cell_left, ly, cell_right - 1, ly, bg_color)
                label_text_color = self.color_past_text
                graphics.DrawText(canvas, font_used, text_x, text_y, label_text_color, label_text_to_draw)

        # Draw header last (on top), same as headlines - fills top 15px so content can use full height
        header_text = "Past Client Calls" if self._mode == "past" else "Upcoming Client Calls"
        for i in range(self.header_height):
            ly = y + i
            if y <= ly < y + height:
                graphics.DrawLine(canvas, x, ly, x + width - 1, ly, self.color_header_bg)
        graphics.DrawText(canvas, self.font_suffix, x + 2, y + 10, self.color_accent, header_text)

    def reset_scroll(self):
        """No-op; panel no longer scrolls."""
        pass

    def get_display_duration_sec(self, panel_height, fps=60, panel_width=None):
        """Fixed duration (no scrolling). Upcoming vs past use their own config keys."""
        if panel_width is not None and (self._rows_built_for_width != panel_width or not self._rows):
            self._build_rows(panel_width)
        cfg = self.config.modules.get("client_calls_panel", {})
        if self._mode == "past":
            return cfg.get("past_duration_sec", 60)
        return cfg.get("duration_sec", 60)

    def get_next_update_time(self):
        return time.time() + 0.5
