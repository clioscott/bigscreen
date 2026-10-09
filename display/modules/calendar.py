import time
import datetime
from display.renderer import ModuleRenderer

from display.gfx import graphics

class CalendarRenderer(ModuleRenderer):
    def __init__(self, config, loader):
        super().__init__(config)
        self.loader = loader
        
        self.events = []
        self._processed_at = 0.0
        self.bg_events_processed = []
        self.normal_events_processed = []
        
        # Visual Config
        self.font_hour = graphics.Font()
        path_h = self.config.get_font_path("event_hour_labels")
        if path_h: self.font_hour.LoadFont(path_h)
        
        self.font_event_title = graphics.Font()
        path_t = self.config.get_font_path("event_title")
        if path_t: self.font_event_title.LoadFont(path_t)
        
        self.font_event_title_tiny = graphics.Font()
        path_tt = self.config.get_font_path("event_title_tiny")
        if path_tt: self.font_event_title_tiny.LoadFont(path_tt)
        
        self.font_event_desc = graphics.Font()
        path_d = self.config.get_font_path("event_desc")
        if path_d: self.font_event_desc.LoadFont(path_d)
        
        self.color_now_line = graphics.Color(255, 0, 0)
        self.color_border = graphics.Color(*self.config.get_color("zone_border"))
        self.color_text = graphics.Color(*self.config.get_color("default_text"))
        self.color_text_outline = graphics.Color(*self.config.get_color("text_outline"))

        # Pre-allocate colors used every render frame (avoid per-frame allocation)
        self.color_alert_box = graphics.Color(100, 0, 0)
        self.color_alert_border = graphics.Color(255, 0, 0)
        self.color_alert_text = graphics.Color(255, 255, 255)
        self.color_bg_event = graphics.Color(60, 60, 60)
        self.color_bg_text = graphics.Color(255, 255, 255)
        self.color_hour_line = graphics.Color(130, 130, 130)
        self.color_hour_text = graphics.Color(150, 150, 150)
        
        self.pixels_per_minute = self.config.modules.get("calendar", {}).get("pixels_per_minute", 0.5)
        self.now_line_pct = self.config.modules.get("calendar", {}).get("current_time_line_y_pct", 0.15)
        self.min_height = self.config.modules.get("calendar", {}).get("min_event_box_height", 24)
        
        # Alert State
        self.alert_active = False
        self.alert_toggle = False
        self.alert_message = ""
        self.last_flash_time = 0
        self.status_overrides = []
        # Conservative default — show calendar until first data load proves otherwise.
        self.has_visible_events = True

    def update(self):
        new_events = self.loader.get_data("calendar")
        # v1 re-parsed every event on every frame. Re-process only when calendar.json changes,
        # plus every 15 s so time-dependent state (has_visible_events, alerts) stays current.
        now = time.time()
        if new_events is not None and (new_events is not self.events or now - self._processed_at > 15):
            self.events = new_events
            self._process_events()
            self._processed_at = now
        elif self.events and not self.normal_events_processed:
            self._process_events()
            self._processed_at = now

    def _process_events(self):
        bg_keywords = self.config.modules.get("calendar", {}).get("event_processing", {}).get("background_events_containing", [])
        
        self.bg_events_processed = []
        normal_temp = []
        
        for event in self.events:
            # Parse Dates Once — guard against malformed events missing start/end
            start_obj = event.get('start')
            end_obj = event.get('end')
            if not isinstance(start_obj, dict) or not isinstance(end_obj, dict):
                continue
            start_raw = start_obj.get('dateTime', start_obj.get('date'))
            end_raw = end_obj.get('dateTime', end_obj.get('date'))
            
            try:
                if 'T' in start_raw:
                    start_dt = datetime.datetime.fromisoformat(start_raw.replace('Z', '+00:00'))
                    end_dt = datetime.datetime.fromisoformat(end_raw.replace('Z', '+00:00'))
                    start_ts = start_dt.timestamp()
                    end_ts = end_dt.timestamp()
                else:
                    continue # Skip all-day for now or handle differently
            except ValueError:
                continue

            # Determine Type
            summary = event.get('summary', '')
            is_bg = any(k.lower() in summary.lower() for k in bg_keywords)
            
            if not is_bg:
                evt_type = event.get('eventType', 'default')
                if evt_type in ['focusTime', 'outOfOffice']:
                    is_bg = True
            
            processed_item = {
                'event': event,
                'start_dt': start_dt,
                'end_dt': end_dt,
                'start_ts': start_ts,
                'end_ts': end_ts,
                'col_index': 0,
                'total_cols': 1
            }

            if is_bg:
                self.bg_events_processed.append(processed_item)
            else:
                normal_temp.append(processed_item)
                
        # Pack Normal Events
        self._pack_events(normal_temp)
        self.normal_events_processed = normal_temp

        # Pre-compute has_visible_events using the SAME pixel-viewport geometry
        # that render() uses, so the cycle engine's "calendar_has_events" /
        # "no_events" conditions match exactly what's actually on screen.
        #
        # How the viewport maps to time:
        #   main_h = total content panel height (pixels)
        #   now_line_pct fraction of that is above the now-line (past)
        #   pixels_per_minute converts the remaining height to minutes
        #
        # When no events fall within this window the cycle should switch to
        # client_calls_upcoming / client_calls_past instead of staying on
        # calendar and drawing a "next event" countdown overlay.
        _now = time.time()
        layout = self.config.layout
        screen_h   = layout.get("height",          192)
        top_bar_h  = layout.get("top_bar_height",   44)
        bot_bar_h  = layout.get("bottom_bar_height", 14)
        main_h     = screen_h - top_bar_h - 1 - bot_bar_h   # mirrors main.py
        cal_cfg    = self.config.modules.get("calendar", {})
        ppm        = cal_cfg.get("pixels_per_minute",        self.pixels_per_minute)
        nlp        = cal_cfg.get("current_time_line_y_pct",  self.now_line_pct)
        # Convert pixel extents to seconds on each side of now.
        _past_sec   = (main_h * nlp          / ppm) * 60
        _future_sec = (main_h * (1.0 - nlp)  / ppm) * 60
        self.has_visible_events = any(
            item["end_ts"]   > _now - _past_sec
            and item["start_ts"] < _now + _future_sec
            for item in self.normal_events_processed
        )

    def _wrap_text_to_lines(self, text, font, max_width, max_lines=2):
        """
        Wrap text to multiple lines, up to max_lines.
        Returns a list of lines (up to max_lines).
        If text doesn't fit in max_lines, the last line is truncated with ellipsis.
        """
        if not text:
            return [""]
        
        # Split text into words (preserving spaces)
        words = text.split(' ')
        lines = []
        current_line = ""
        current_width = 0
        
        def truncate_to_fit(text_to_truncate, max_w):
            """Helper to truncate text to fit max_w with ellipsis"""
            truncated = ""
            ellipsis_width = sum(font.CharacterWidth(ord(c)) for c in "...")
            running_width = 0
            for char in text_to_truncate:
                char_width = font.CharacterWidth(ord(char))
                if running_width + char_width + ellipsis_width > max_w:
                    truncated += "..."
                    break
                truncated += char
                running_width += char_width
            return truncated if truncated else "..."
        
        for word in words:
            # Measure word width (including space before it if not first word)
            word_with_space = (' ' + word) if current_line else word
            word_width = sum(font.CharacterWidth(ord(c)) for c in word_with_space)
            
            # Check if adding this word would exceed max_width
            if current_width + word_width <= max_width:
                # Word fits on current line
                current_line += word_with_space
                current_width += word_width
            else:
                # Word doesn't fit - start new line
                if current_line:
                    lines.append(current_line)
                    if len(lines) >= max_lines:
                        # We've reached max_lines - truncate the last line if needed
                        # (in case there's more text that won't fit)
                        lines[-1] = truncate_to_fit(lines[-1], max_width)
                        return lines
                    current_line = ""
                    current_width = 0
                
                # Check if word itself is too long for a single line
                word_only_width = sum(font.CharacterWidth(ord(c)) for c in word)
                if word_only_width > max_width:
                    # Word is too long - truncate it
                    if len(lines) >= max_lines - 1:
                        # This would be the last line - truncate it
                        truncated = truncate_to_fit(word, max_width)
                        lines.append(truncated)
                        return lines
                    else:
                        # Truncate and add to current line
                        truncated = truncate_to_fit(word, max_width)
                        current_line = truncated
                        current_width = sum(font.CharacterWidth(ord(c)) for c in truncated)
                else:
                    # Word fits on new line
                    current_line = word
                    current_width = word_only_width
        
        # Add the last line if we haven't reached max_lines
        if current_line and len(lines) < max_lines:
            lines.append(current_line)
        elif current_line and len(lines) >= max_lines:
            # We've hit max_lines but still have text - truncate the last line
            lines[-1] = truncate_to_fit(lines[-1], max_width)
        
        return lines if lines else [""]

    def _pack_events(self, processed_events):
        if not processed_events: return
        
        # 1. Group overlapping events
        groups = []
        if not processed_events: return
        
        # Sort by start time just in case
        processed_events.sort(key=lambda x: x['start_ts'])
        
        current_group = [processed_events[0]]
        group_end = processed_events[0]['end_ts']
        
        for e in processed_events[1:]:
            if e['start_ts'] < group_end:
                current_group.append(e)
                group_end = max(group_end, e['end_ts'])
            else:
                groups.append(current_group)
                current_group = [e]
                group_end = e['end_ts']
        groups.append(current_group)
        
        # 2. Assign columns within groups
        for group in groups:
            columns = [] 
            for e in group:
                placed = False
                for i, col_end in enumerate(columns):
                    if e['start_ts'] >= col_end:
                        columns[i] = e['end_ts']
                        e['col_index'] = i
                        placed = True
                        break
                if not placed:
                    columns.append(e['end_ts'])
                    e['col_index'] = len(columns) - 1
            
            max_cols = len(columns)
            for e in group:
                e['total_cols'] = max_cols

    def render(self, canvas, x, y, width, height):
        # 1. Calculate Time & Geometry
        now_dt = datetime.datetime.now()
        now_ts = now_dt.timestamp()
        
        now_line_y_offset = int(height * self.now_line_pct)
        now_line_y = y + now_line_y_offset
        
        self.status_overrides = []
        
        # 1b. Build render queue and determine visibility BEFORE drawing (so main can skip calendar when no events)
        suppress = self.config.modules.get("calendar", {}).get("event_processing", {}).get("suppress_alerts_containing", [])
        any_burst_active = False
        for item in self.normal_events_processed:
            s_ts = item['start_ts']
            if 0 <= (now_ts - s_ts) < 30.0:
                raw_sum = item['event'].get('summary', '')
                if not any(s.lower() in raw_sum.lower() for s in suppress):
                    any_burst_active = True
                    break
        if any_burst_active:
            self.alert_toggle = (int(time.time() * 20) % 2) == 0
            self.alert_active = True
        else:
            self.alert_active = False
            self.alert_message = ""
            if time.time() - self.last_flash_time > 0.5:
                self.alert_toggle = not self.alert_toggle
                self.last_flash_time = time.time()
        render_queue = []
        next_upcoming_event = None
        for item in self.normal_events_processed:
            event = item['event']
            start_ts = item['start_ts']
            end_ts = item['end_ts']
            start_dt = item['start_dt']
            time_until_start = start_ts - now_ts
            is_alert = False
            raw_summary = event.get('summary', '')
            should_suppress = any(s.lower() in raw_summary.lower() for s in suppress)
            if not should_suppress and -120 <= time_until_start <= 10:
                self.alert_active = True
                is_alert = True
                if time_until_start > 0:
                    time_str = start_dt.strftime('%H:%M')
                    self.alert_message = f"{time_str} {event.get('summary_clean', 'Event')}"
                else:
                    self.alert_message = f"In Progress: {event.get('summary_clean', 'Event')}"
            is_in_progress = (start_ts <= now_ts < end_ts) and not should_suppress
            if start_ts > now_ts:
                if next_upcoming_event is None or start_ts < next_upcoming_event['ts']:
                    next_upcoming_event = {'event': event, 'ts': start_ts, 'start_dt': start_dt, 'end_dt': item['end_dt']}
            if is_in_progress and not self.alert_active:
                time_str = start_dt.strftime('%H:%M')
                msg = f"{time_str} {event.get('summary_clean', 'Event')}"
                if msg not in self.status_overrides:
                    self.status_overrides.append(msg)
            event_color_rgb = event.get('_event_color', (0, 0, 100))
            event_color = graphics.Color(*event_color_rgb)
            z_index = 0
            if is_alert and self.alert_toggle:
                box_color = self.color_alert_box
                border_color = self.color_alert_border
                text_color = self.color_alert_text
                z_index = 2
            elif is_in_progress:
                box_color = event_color
                border_color = self.color_now_line  # reuse red
                text_color = self.color_text
                z_index = 1
            else:
                box_color = event_color
                border_color = self.color_border
                text_color = self.color_text
                z_index = 0
            render_queue.append({
                'item': item,
                'box_color': box_color,
                'border_color': border_color,
                'text_color': text_color,
                'z_index': z_index
            })
        render_queue.sort(key=lambda x: x['z_index'])
        # Note: no early return here based on pixel-visible event count. The
        # calendar always renders its full UI (timeline, hour markers, now-line)
        # when the cycle engine selects it. Whether to show the calendar panel
        # at all is decided by _process_events() → has_visible_events (lookahead
        # time window check) before render() is called. A pre-check pixel count
        # here that returned early caused a blank-screen loop: _process_events()
        # kept resetting has_visible_events True (event in window), the cycle
        # kept picking calendar, and render() kept returning blank because the
        # event box wasn't in the visible pixel zone yet.

        # 2. Draw Background Events (Focus Time, Out of Office, etc.)
        
        for item in self.bg_events_processed:
            s_ts = item['start_ts']
            e_ts = item['end_ts']
            start_dt = item['start_dt']
            
            diff_min = (s_ts - now_ts) / 60.0
            duration_min = (e_ts - s_ts) / 60.0
            
            ev_y = now_line_y + int(diff_min * self.pixels_per_minute)
            ev_h = int(duration_min * self.pixels_per_minute)
            if ev_h < 1: ev_h = 1
            
            # Check if Focus Time is in progress - add to status bar
            is_focus_in_progress = (s_ts <= now_ts < e_ts)
            if is_focus_in_progress:
                event = item['event']
                title = event.get('summary_clean', event.get('summary', 'Focus Time'))
                time_str = start_dt.strftime('%H:%M')
                # Use dict format with type marker for grey highlighting
                focus_msg = {"text": f"{time_str} {title}", "type": "focus"}
                # Avoid duplicates
                if focus_msg not in self.status_overrides:
                    self.status_overrides.append(focus_msg)
            
            # Cull for rendering
            if (ev_y + ev_h < y) or (ev_y > y + height): continue
            
            grid_x = x + 30
            grid_w = width - 30
            
            draw_y = max(y, ev_y)
            draw_h = min(ev_y + ev_h, y + height) - draw_y
            
            if draw_h > 0:
                for i in range(draw_h):
                    graphics.DrawLine(canvas, grid_x, draw_y + i, grid_x + grid_w, draw_y + i, self.color_bg_event)
                    
                if ev_h > 10:
                    title = item['event'].get('summary_clean', 'Focus')
                    text_y = ev_y + 8
                    if text_y < y + height:
                        graphics.DrawText(canvas, self.font_event_title_tiny, grid_x + 10, text_y, self.color_bg_text, title)

        # 3. Draw Hour Markers
        minutes_to_top = now_line_y_offset / self.pixels_per_minute
        view_start_dt = now_dt - datetime.timedelta(minutes=minutes_to_top)
        
        next_hour_dt = view_start_dt.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
        curr_hour_dt = next_hour_dt
        
        while True:
            diff_min = (curr_hour_dt.timestamp() - now_ts) / 60.0
            hour_y = now_line_y + int(diff_min * self.pixels_per_minute)
            
            if hour_y > y + height:
                break
                
            if hour_y >= y:
                graphics.DrawLine(canvas, x + 30, hour_y, x + width, hour_y, self.color_hour_line)
                time_str = curr_hour_dt.strftime("%H:%M")
                graphics.DrawText(canvas, self.font_hour, x + 2, hour_y + 3, self.color_hour_text, time_str)
            
            curr_hour_dt += datetime.timedelta(hours=1)

        # 4. Draw "Now" Line
        graphics.DrawLine(canvas, x, now_line_y, x + width, now_line_y, self.color_now_line)

        # 5. Render Loop (render_queue already built in 1b)
        visible_events_count = 0
        for render_item in render_queue:
            item = render_item['item']
            box_color = render_item['box_color']
            border_color = render_item['border_color']
            text_color = render_item['text_color']
            
            event = item['event']
            start_ts = item['start_ts']
            end_ts = item['end_ts']
            col_index = item['col_index']
            total_cols = item['total_cols']
            start_dt = item['start_dt']
            end_dt = item['end_dt']
            
            diff_min = (start_ts - now_ts) / 60.0
            duration_min = (end_ts - start_ts) / 60.0
            
            event_y = now_line_y + int(diff_min * self.pixels_per_minute)
            event_h = int(duration_min * self.pixels_per_minute)
            
            if event_h < 1: event_h = 1
            
            if (event_y + event_h < y) or (event_y > y + height):
                continue
            
            visible_events_count += 1
            
            avail_w = width - 35
            col_w = int(avail_w / total_cols)
            col_x = x + 35 + (col_index * col_w)
            
            for i in range(event_h):
                line_y = event_y + i
                if y <= line_y < y + height:
                    graphics.DrawLine(canvas, col_x, line_y, col_x + col_w - 2, line_y, box_color) 
            
            graphics.DrawLine(canvas, col_x, event_y, col_x+col_w-2, event_y, border_color)
            graphics.DrawLine(canvas, col_x, event_y+event_h, col_x+col_w-2, event_y+event_h, border_color)
            graphics.DrawLine(canvas, col_x, event_y, col_x, event_y+event_h, border_color)
            graphics.DrawLine(canvas, col_x+col_w-2, event_y, col_x+col_w-2, event_y+event_h, border_color)
            
            show_title = False
            show_time = False
            use_tiny_title = False
            title_lines_drawn = 0
            text_y = event_y
            
            if event_h >= 15:
                show_title = True
                show_time = True
            elif event_h >= 8:
                show_title = True
            elif event_h >= 6:
                show_title = True
                use_tiny_title = True
            
            if show_title:
                title = event.get('summary_clean', 'Event')
                font = self.font_event_title_tiny if use_tiny_title else self.font_event_title
                font_y_offset = -1 if not use_tiny_title else 0 
                
                # Calculate natural text position (relative to event box top)
                natural_text_y = event_y + font.height + font_y_offset + 3
                
                # STICKY TEXT LOGIC:
                # Text should "stick" to top boundary (y + 2px buffer) until
                # the bottom of the event box catches up and pushes it off
                
                top_boundary = y + 2 + font.height  # Where text baseline would hit top
                event_bottom = event_y + event_h
                
                # Height of text block: up to 2 lines of title + time if shown
                max_title_lines = 2
                text_block_height = font.height * max_title_lines + 1  # 1px gap between lines
                if show_time:
                    text_block_height += 8  # time line height + gap
                
                # Calculate where text should be drawn
                if natural_text_y < top_boundary:
                    # Text would be above boundary - check if we can pin it
                    # Pin position = top_boundary
                    # But only if event_bottom is still below the pinned text block
                    pinned_text_bottom = top_boundary + text_block_height - font.height  # Adjust for multi-line
                    
                    if event_bottom > pinned_text_bottom + 2:
                        # Still room - pin text at top
                        text_y = top_boundary
                    else:
                        # Bottom is catching up - text moves with bottom
                        text_y = event_bottom - text_block_height - 2
                else:
                    # Normal position
                    text_y = natural_text_y
                
                if text_y < y + height and text_y > y:
                    max_text_w = col_w - 6
                    # Wrap text to up to 2 lines
                    title_lines = self._wrap_text_to_lines(title, font, max_text_w, max_lines=2)
                    
                    # Draw each line
                    line_spacing = font.height + 1  # 1px gap between lines
                    lines_drawn = 0
                    for i, line in enumerate(title_lines):
                        if i >= max_title_lines:
                            break
                        line_y = text_y + (i * line_spacing)
                        # Make sure line doesn't go below the event box or viewport
                        if line_y < y + height and line_y >= y and line_y < event_bottom:
                            graphics.DrawText(canvas, font, col_x + 3, line_y, text_color, line)
                            lines_drawn += 1
                    
                    # Store number of lines drawn for time positioning
                    title_lines_drawn = lines_drawn
            
            if show_time:
                time_str = f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}"
                # Position time after title lines (accounting for actual number of lines drawn)
                title_height = (title_lines_drawn * (font.height + 1)) if title_lines_drawn > 0 else font.height
                text_y_2 = text_y + title_height + 2  # 2px gap after title
                if text_y_2 < y + height and text_y_2 > y and text_y_2 < event_bottom:
                    graphics.DrawText(canvas, self.font_event_desc, col_x + 3, text_y_2, text_color, time_str)

        # Section 7 (empty-state countdown/fallback) removed.
        # has_visible_events is now computed in _process_events() using the same
        # pixel-viewport geometry as render(), so when visible_events_count == 0
        # the cycle engine will have already switched to client_calls_upcoming /
        # client_calls_past and this code is never reached.

    def get_next_update_time(self):
        if self.alert_active:
            return time.time() + 0.05
        return time.time() + 10
