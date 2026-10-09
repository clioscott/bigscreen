import time
import os

import numpy as np
from display.renderer import ModuleRenderer

from display.gfx import graphics

def get_icon_path(domain, base_dir=None):
    if base_dir:
        local_dir = os.path.join(base_dir, 'icons')
        try:
            return os.path.join(local_dir, f"{domain}_16.png")
        except OSError:
            pass 
    return None

class HeadlinesRenderer(ModuleRenderer):
    def __init__(self, config, loader):
        super().__init__(config)
        self.loader = loader
        
        self.font = graphics.Font()
        font_path = self.config.get_font_path("headlines_title")
        if not font_path: font_path = self.config.get_font_path("header")
        if font_path: self.font.LoadFont(font_path)
        
        self.font_small = graphics.Font()
        path_s = self.config.get_font_path("headlines_url")
        if not path_s: path_s = self.config.get_font_path("body")
        if path_s: self.font_small.LoadFont(path_s)
        
        self.color = graphics.Color(*self.config.get_color("default_text"))
        self.color_dim = graphics.Color(150, 150, 150)
        self.color_accent = graphics.Color(0, 200, 200)
        self.color_ok = graphics.Color(0, 255, 0)
        self.color_fail = graphics.Color(255, 0, 0)
        self.color_warn = graphics.Color(255, 165, 0)
        self.color_header_bg = graphics.Color(40, 40, 40)
        
        self.feed_data = {}
        self._last_raw = None
        self._last_scroll_t = 0.0
        self.last_updated_utc = None  # set from headlines payload for "last updated X min ago"
        self.boot_log = []
        
        hcfg = self.config.modules.get("headlines", {})
        self.subreddits = [c["name"] for c in hcfg.get("categories", []) if c.get("enabled", True)]             or hcfg.get("subreddits", [])
        self.current_sub_index = 0
        
        self.state = "HOLD_TOP"
        self.state_start_time = time.time()
        self.scroll_y = 0.0
        headlines_config = self.config.modules.get("headlines", {})
        animation_config = headlines_config.get("animation", {})
        self.scroll_speed = animation_config.get("speed_pixels_per_frame", 1.0)
        # px/s; v1's per-frame speed was ~speed * 0.05 px at ~30 fps
        self.scroll_px_per_sec = float(headlines_config.get("scroll_px_per_sec") or self.scroll_speed * 0.05 * 30.0)
        
        self.current_lines = [] 
        self.total_content_height = 0
        self.header_line = None
        self.header_height = 15
        self.icon_cache = {}
        self._cycle_complete = False  # True when finished last subreddit and would advance to 0
    
    def _format_relative_time(self, timestamp_utc):
        """Format a Unix timestamp as relative time (e.g., '21 min ago', '4 days ago')"""
        if not timestamp_utc or timestamp_utc == 0:
            return ""
        
        now = time.time()
        diff_seconds = now - timestamp_utc
        
        if diff_seconds < 60:
            return f"{int(diff_seconds)} sec ago"
        elif diff_seconds < 3600:
            minutes = int(diff_seconds / 60)
            return f"{minutes} min ago"
        elif diff_seconds < 86400:
            hours = int(diff_seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif diff_seconds < 604800:
            days = int(diff_seconds / 86400)
            return f"{days} day{'s' if days != 1 else ''} ago"
        elif diff_seconds < 2592000:
            weeks = int(diff_seconds / 604800)
            return f"{weeks} week{'s' if weeks != 1 else ''} ago"
        elif diff_seconds < 31536000:
            months = int(diff_seconds / 2592000)
            return f"{months} month{'s' if months != 1 else ''} ago"
        else:
            years = int(diff_seconds / 31536000)
            return f"{years} year{'s' if years != 1 else ''} ago"

    def update(self):
        new_data = self.loader.get_data("headlines")
        if new_data is self._last_raw:
            return
        self._last_raw = new_data
        if new_data and isinstance(new_data, dict):
            cats = (new_data.get("_meta") or {}).get("categories")
            if cats:
                self.subreddits = [c for c in cats if isinstance(new_data.get(c), list)]
            # Extract last_updated_utc if present (server sends it for "last updated X min ago")
            # Fallback to file mtime so old payloads or pre-deploy files still show "last updated"
            self.last_updated_utc = new_data.get("last_updated_utc")
            if self.last_updated_utc is None and hasattr(self.loader, "get_mtime"):
                self.last_updated_utc = self.loader.get_mtime("headlines")
            # Feed data is all keys that map to lists (subreddit -> items)
            feed_data = {k: v for k, v in new_data.items() if k != "last_updated_utc" and isinstance(v, list)}
            if not feed_data:
                feed_data = {k: v for k, v in new_data.items() if k != "last_updated_utc"}
                if not feed_data:
                    feed_data = new_data  # legacy: use as-is
            # Check if data actually changed
            data_changed = (feed_data != self.feed_data)
            if data_changed:
                self.feed_data = feed_data
                # Reset display state to show new data
                self.current_lines = []
                self.header_line = None
                self.current_sub_index = 0
                self.state = "HOLD_TOP"
                self.state_start_time = time.time()
                self.scroll_y = 0.0
                if not hasattr(self, '_debug_logged_update'):
                    print(f"[HEADLINES] Data updated - resetting display state")
                    self._debug_logged_update = True
    
    def _load_favicon(self, domain):
        if domain in self.icon_cache: return self.icon_cache[domain]
        
        # Look in data_dir from loader
        path = get_icon_path(domain, self.loader.data_dir)
        
        if path and os.path.exists(path):
            try:
                from PIL import Image
                img = Image.open(path).convert('RGB')
                
                pixels = np.ascontiguousarray(np.asarray(img))
                self.icon_cache[domain] = pixels
                return pixels
            except Exception:
                pass
        return None

    def _wrap_text_pixels(self, text, font, max_width):
        words = text.split(' ')
        lines = []
        current_line = []
        current_width = 0
        space_width = font.CharacterWidth(ord(' '))
        
        for word in words:
            word_width = sum(font.CharacterWidth(ord(c)) for c in word)
            
            if current_width + word_width + (space_width if current_line else 0) <= max_width:
                current_line.append(word)
                if current_line:
                     current_width += (space_width if len(current_line) > 1 else 0) + word_width
                else:
                     current_width += word_width
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
                current_width = word_width
        
        if current_line:
            lines.append(' '.join(current_line))
            
        return lines

    def _prepare_page(self, width):
        if not self.subreddits: return
            
        current_sub_name = self.subreddits[self.current_sub_index]
        items = self.feed_data.get(current_sub_name, [])
        
        self.current_lines = []
        self.state = "HOLD_TOP"
        self.state_start_time = time.time()
        self.scroll_y = 0.0
        
        content_width = width - 4
        header_height = 15
        
        # Store header separately (fixed at top); show "last updated X min ago" when available
        header_text = current_sub_name
        if self.last_updated_utc:
            ago = self._format_relative_time(self.last_updated_utc)
            if ago:
                header_text += f" - last updated {ago}"
        self.header_line = {
            "type": "header",
            "text": header_text,
            "h": header_height
        }
        
        # Content lines start after the header
        cursor_y = 0  # This is relative to content area (below header)
        
        for item in items:
            icon_pixels = self._load_favicon(item.get('domain', ''))
            indent = 20 
            
            wrapped = self._wrap_text_pixels(item.get('title', ''), self.font, content_width - indent)
            
            first_line = True
            for line in wrapped:
                line_data = {
                    "type": "title", 
                    "text": line,
                    "y": cursor_y,
                    "h": 12,
                    "indent": indent
                }
                if first_line and icon_pixels is not None:
                    line_data['icon_pixels'] = icon_pixels
                    first_line = False
                    
                self.current_lines.append(line_data)
                cursor_y += 12
            
            domain = item.get('domain', '')
            created_utc = item.get('created_utc', 0)
            relative_time = self._format_relative_time(created_utc)
            
            byline_text = domain
            if relative_time:
                byline_text = f"{domain} - {relative_time}"
            
            self.current_lines.append({
                "type": "byline", 
                "text": byline_text,
                "y": cursor_y,
                "h": 15, 
                "indent": indent
            })
            cursor_y += 15
            
        self.total_content_height = cursor_y
        self.header_height = header_height

    def render(self, canvas, x, y, width, height):
        if not self.feed_data:
            # Simple waiting message
            graphics.DrawText(canvas, self.font, x, y + 10, self.color, "Waiting for News Data...")
            return

        if not self.current_lines:
            self._prepare_page(width)
            
        now = time.time()
        
        if self.state != "SCROLLING":
            self._last_scroll_t = now
        if self.state == "HOLD_TOP":
            if now - self.state_start_time > 3.0:
                self.state = "SCROLLING"
                
        elif self.state == "SCROLLING":
            # Available height for content is total height minus header
            content_height = height - self.header_height
            if self.total_content_height <= content_height:
                self.state = "HOLD_BOTTOM"
                self.state_start_time = now
            else:
                dt = min(now - self._last_scroll_t, 0.25) if self._last_scroll_t else 0.0
                self.scroll_y += self.scroll_px_per_sec * dt
                max_scroll = self.total_content_height - content_height
                if self.scroll_y >= max_scroll:
                    self.scroll_y = max_scroll
                    self.state = "HOLD_BOTTOM"
                    self.state_start_time = now
            self._last_scroll_t = now
                    
        elif self.state == "HOLD_BOTTOM":
            if now - self.state_start_time > 5.0:
                if self.current_sub_index == len(self.subreddits) - 1:
                    if not self._cycle_complete:
                        # First completion signal — let main.py force-advance the cycle.
                        self._cycle_complete = True
                        return
                    # render() was called again while _cycle_complete is already True.
                    # This means the cycle engine re-selected headlines without a panel
                    # transition (e.g. headlines is the only applicable entry — no
                    # calendar events — so force_advance() wraps straight back to us).
                    # Auto-restart from the first subreddit so we don't freeze on a
                    # blank screen indefinitely.
                    self._cycle_complete = False
                    self.current_sub_index = 0
                    self._prepare_page(width)
                    # Fall through to render the freshly prepared first page.
                else:
                    self.current_sub_index = (self.current_sub_index + 1) % len(self.subreddits)
                    self._prepare_page(width)
                    return

        # Render content lines first (scrollable, below header)
        # Content is rendered first so header can be drawn on top
        content_start_y = y + self.header_height
        content_height = height - self.header_height
        
        for line in self.current_lines:
            # Content lines are scrolled relative to content area
            screen_y = content_start_y + line['y'] - int(self.scroll_y)
            
            # Clip content that's completely outside the visible area (below content area)
            # Allow content to render in header area - it will be covered by header drawn on top
            if screen_y + line['h'] < y: continue  # Completely above visible area
            if screen_y > y + height: continue  # Completely below visible area 
            
            text_y = screen_y + 10 
            
            if line['type'] == "title":
                indent = line.get('indent', 0)
                if line.get('icon_pixels') is not None:
                    canvas.Blit(line['icon_pixels'], x, screen_y)
                
                graphics.DrawText(canvas, self.font, x + indent, text_y, self.color, line['text'])
                
            elif line['type'] == "byline":
                indent = line.get('indent', 0)
                graphics.DrawText(canvas, self.font_small, x + 5 + indent, text_y, self.color_dim, line['text'])
        
        # Render header last (on top) so it always appears above content
        if self.header_line:
            header_y = y
            header_text_y = header_y + 10
            # Build header text at draw time so "last updated X min ago" stays current
            current_sub_name = self.subreddits[self.current_sub_index]
            header_text = current_sub_name
            if self.last_updated_utc:
                ago = self._format_relative_time(self.last_updated_utc)
                if ago:
                    header_text += f" - last updated {ago}"
            
            # Draw header background
            canvas.FillRect(x, header_y, width, min(self.header_height, height), self.color_header_bg)
            
            # Draw header text
            graphics.DrawText(canvas, self.font_small, x + 2, header_text_y, self.color_accent, header_text)

    def advance_to_next_sub(self, width):
        """Advance to next subreddit (wraps to 0 after last). Call when cycle_complete but client_calls panel disabled."""
        self._cycle_complete = False
        self.current_sub_index = (self.current_sub_index + 1) % len(self.subreddits) if self.subreddits else 0
        self._prepare_page(width)
        self.state = "HOLD_TOP"
        self.state_start_time = time.time()
        self.scroll_y = 0.0

    def reset_to_first_sub(self, width):
        """Reset to first subreddit and prepare page. Call after client_calls panel ends."""
        self._cycle_complete = False
        self.current_sub_index = 0
        self._prepare_page(width)
        self.state = "HOLD_TOP"
        self.state_start_time = time.time()
        self.scroll_y = 0.0

    def get_next_update_time(self):
        if self.state == "SCROLLING":
            return time.time() + 0.05
        else:
            return time.time() + 0.5
