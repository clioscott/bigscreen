import time
import os
from datetime import date
from display.renderer import ModuleRenderer

from display.gfx import graphics

# Pickup schedule: YYYY-MM-DD -> tuple of "GARBAGE" | "RECYCLE" | "GREEN" (order = left-to-right)
# Rule: every Wednesday unless week has holiday, then Thursday. Hardcoded for 2026 into Jan 2027.
PICKUP_SCHEDULE = {
    "2026-01-01": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-01-07": ("RECYCLE", "GREEN"),
    "2026-01-14": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-01-21": ("RECYCLE", "GREEN"),
    "2026-01-28": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-02-04": ("RECYCLE", "GREEN"),
    "2026-02-11": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-02-19": ("RECYCLE", "GREEN"),
    "2026-02-25": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-03-04": ("RECYCLE", "GREEN"),
    "2026-03-11": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-03-18": ("RECYCLE", "GREEN"),
    "2026-03-25": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-04-01": ("RECYCLE", "GREEN"),
    "2026-04-09": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-04-15": ("RECYCLE", "GREEN"),
    "2026-04-22": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-04-29": ("RECYCLE", "GREEN"),
    "2026-05-06": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-05-13": ("RECYCLE", "GREEN"),
    "2026-05-21": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-05-27": ("RECYCLE", "GREEN"),
    "2026-06-03": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-06-10": ("RECYCLE", "GREEN"),
    "2026-06-17": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-06-24": ("RECYCLE", "GREEN"),
    "2026-07-02": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-07-08": ("RECYCLE", "GREEN"),
    "2026-07-15": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-07-22": ("RECYCLE", "GREEN"),
    "2026-07-29": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-08-06": ("RECYCLE", "GREEN"),
    "2026-08-12": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-08-19": ("RECYCLE", "GREEN"),
    "2026-08-26": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-09-02": ("RECYCLE", "GREEN"),
    "2026-09-10": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-09-16": ("RECYCLE", "GREEN"),
    "2026-09-23": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-10-01": ("RECYCLE", "GREEN"),
    "2026-10-07": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-10-15": ("RECYCLE", "GREEN"),
    "2026-10-21": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-10-28": ("RECYCLE", "GREEN"),
    "2026-11-04": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-11-12": ("RECYCLE", "GREEN"),
    "2026-11-18": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-11-25": ("RECYCLE", "GREEN"),
    "2026-12-02": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-12-09": ("RECYCLE", "GREEN"),
    "2026-12-16": ("GARBAGE", "RECYCLE", "GREEN"),
    "2026-12-23": ("RECYCLE", "GREEN"),
    "2026-12-30": ("GARBAGE", "RECYCLE", "GREEN"),
    "2027-01-06": ("RECYCLE", "GREEN"),
    "2027-01-13": ("GARBAGE", "RECYCLE", "GREEN"),
    "2027-01-20": ("RECYCLE", "GREEN"),
    "2027-01-27": ("GARBAGE", "RECYCLE", "GREEN"),
}

# Pickup label colors: (R, G, B) for background; text is white
PICKUP_COLORS = {
    "GARBAGE": (70, 45, 25),   # dark brown
    "RECYCLE": (0, 40, 80),    # dark blue
    "GREEN": (0, 70, 35),      # dark green
}
PICKUP_LABEL_PADDING = 2
PICKUP_LABEL_GAP = 2

class StatusBarRenderer(ModuleRenderer):
    def __init__(self, config):
        super().__init__(config)
        self.font = graphics.Font()
        font_path = self.config.get_font_path("bottom_bar")
        if font_path and os.path.exists(font_path):
            self.font.LoadFont(font_path)
            
        # Alert Font (Same as Finance Ticker = "top_bar")
        self.font_alert = graphics.Font()
        path_alert = self.config.get_font_path("top_bar")
        if path_alert and os.path.exists(path_alert):
            self.font_alert.LoadFont(path_alert)
        else:
            # Fallback to same as bottom bar if not found
            self.font_alert = self.font
            
        self.color = graphics.Color(*self.config.get_color("default_text"))
        self.bg_color = graphics.Color(*self.config.get_color("zone_border")) # Use grey for bar bg? Or black?

        # Pre-cached colors — avoids Cython graphics.Color allocation every frame.
        self._white = graphics.Color(255, 255, 255)
        self._red = graphics.Color(255, 0, 0)
        self._flash_bg = graphics.Color(100, 0, 0)
        self._bg_blue = graphics.Color(0, 0, 100)
        self._bg_grey = graphics.Color(50, 50, 50)
        self._pickup_label_colors = {
            label: graphics.Color(*rgb) for label, rgb in PICKUP_COLORS.items()
        }

        # Helper state
        self.message = self.config.modules.get("status_bar", {}).get("default_message", "Ready")
        self.last_update = 0
        
        # Pagination State
        self.page_index = 0
        self.page_start_time = time.time()
        self.anim_start_time = 0
        self.is_animating = False

    def update(self):
        # TODO: Check internet connection here
        pass

    def _draw_pickup_labels(self, canvas, x, y, height):
        """Draw GARBAGE/RECYCLE/GREEN labels on the left. Returns total pixel width used."""
        today_key = date.today().isoformat()
        pickups = PICKUP_SCHEDULE.get(today_key, ())
        if not pickups:
            return 0
        white = self._white
        font = self.font_alert
        baseline = y + (height // 2) + (font.height // 2) - 1
        cursor = x
        for label in pickups:
            bg_color = self._pickup_label_colors.get(label, self._bg_grey)
            text_w = sum(font.CharacterWidth(ord(c)) for c in label)
            box_w = PICKUP_LABEL_PADDING + text_w + PICKUP_LABEL_PADDING
            for i in range(height):
                graphics.DrawLine(canvas, cursor, y + i, cursor + box_w, y + i, bg_color)
            graphics.DrawText(canvas, font, cursor + PICKUP_LABEL_PADDING, baseline, white, label)
            cursor += box_w + PICKUP_LABEL_GAP
        return cursor - x

    def render(self, canvas, x, y, width, height, alert_active=False, alert_toggle=False, alert_message="", status_overrides=None):
        # Pickup labels on far left (GARBAGE / RECYCLE / GREEN) on scheduled days
        label_width = self._draw_pickup_labels(canvas, x, y, height)
        content_x = x + label_width
        content_w = width - label_width

        # Draw background line (optional, maybe just text)
        # graphics.DrawLine(canvas, x, y, x+width, y, self.bg_color)
        
        text_height = self.font.height
        baseline = y + (height // 2) + (text_height // 2) - 1
        
        if alert_active:
            # Use Alert Font for baseline calculation
            alert_baseline = y + (height // 2) + (self.font_alert.height // 2) - 1
            
            if alert_toggle:
                # Flash Background Red
                for i in range(height):
                    graphics.DrawLine(canvas, content_x, y+i, content_x+content_w, y+i, self._flash_bg)

                graphics.DrawText(canvas, self.font_alert, content_x + 2, alert_baseline, self._white, alert_message)
            else:
                # Normal Text, Red
                graphics.DrawText(canvas, self.font_alert, content_x + 2, alert_baseline, self._red, alert_message)
        else:
            # Check for overrides (e.g. In-Progress Event info)
            # Support list or single string, and dict format for typed messages
            msgs = []
            if isinstance(status_overrides, list):
                msgs = status_overrides
            elif isinstance(status_overrides, str):
                msgs = [status_overrides]
            
            # Helper to extract text and type from message
            def get_msg_info(msg):
                if isinstance(msg, dict):
                    return msg.get("text", ""), msg.get("type", "normal")
                return msg, "normal"
            
            if msgs:
                # Pagination Logic
                now = time.time()
                
                # Only animate if multiple messages
                if len(msgs) > 1 and (now - self.page_start_time > 15.0):
                    # Start Animation
                    if not self.is_animating:
                        self.is_animating = True
                        self.anim_start_time = now
                
                if self.is_animating:
                    # Animation Phase (0.5s)
                    progress = (now - self.anim_start_time) / 0.5
                    if progress >= 1.0:
                        # End Animation - advance to next message and fall through to static display
                        self.is_animating = False
                        self.page_index = (self.page_index + 1) % len(msgs)
                        self.page_start_time = now
                        # Don't draw animation, fall through to static display below
                    else:
                        # Still animating - draw transition
                        curr_text, curr_type = get_msg_info(msgs[self.page_index % len(msgs)])
                        next_idx = (self.page_index + 1) % len(msgs)
                        next_text, next_type = get_msg_info(msgs[next_idx])
                        
                        # Use grey for focus, blue for normal
                        # During animation, use the incoming message's color
                        if next_type == "focus":
                            bg_color = self._bg_grey
                        else:
                            bg_color = self._bg_blue
                        
                        for i in range(height):
                            graphics.DrawLine(canvas, content_x, y+i, content_x+content_w, y+i, bg_color)
                            
                        override_baseline = y + (height // 2) + (self.font_alert.height // 2) - 1
                        
                        # Animation Offset
                        offset = int(content_w * progress)
                        
                        # 1. Draw CURRENT message moving Left
                        graphics.DrawText(canvas, self.font_alert, content_x + 2 - offset, override_baseline, self._white, curr_text)

                        # 2. Draw NEXT message moving Left (from Right edge)
                        graphics.DrawText(canvas, self.font_alert, content_x + 2 + content_w - offset, override_baseline, self._white, next_text)
                        return  # Animation drawn, exit render
                    
                # Static Display (also handles post-animation)
                raw_msg = msgs[self.page_index % len(msgs)]
                msg_text, msg_type = get_msg_info(raw_msg)
                
                # Choose background color based on type
                if msg_type == "focus":
                    bg_color = self._bg_grey
                else:
                    bg_color = self._bg_blue

                for i in range(height):
                    graphics.DrawLine(canvas, content_x, y+i, content_x+content_w, y+i, bg_color)

                override_baseline = y + (height // 2) + (self.font_alert.height // 2) - 1
                graphics.DrawText(canvas, self.font_alert, content_x + 2, override_baseline, self._white, msg_text)
            else:
                graphics.DrawText(canvas, self.font, content_x + 2, baseline, self.color, self.message)

    def get_next_update_time(self):
        if self.is_animating:
            return time.time() + 0.03 # Smooth animation
        return time.time() + 0.5

