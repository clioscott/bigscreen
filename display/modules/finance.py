import time
import os
import math
from display.renderer import ModuleRenderer

import numpy as np

from display.gfx import Canvas, graphics


def _normalize_finance_symbols(raw):
    """Mirror of server.utils.normalize_finance_symbols (kept inline so the client
    has no server-side import). Accepts plain strings OR objects with
    {symbol,title,width,icon} and returns a list of full dicts."""
    out = []
    for item in (raw or []):
        if isinstance(item, str):
            sym = item.strip()
            if not sym:
                continue
            out.append({"symbol": sym, "title": sym, "width": None, "icon": f"{sym}.png"})
        elif isinstance(item, dict):
            sym = (item.get("symbol") or "").strip()
            if not sym:
                continue
            title = item.get("title") or sym
            width = item.get("width")
            if width is not None:
                try:
                    width = int(width)
                    if width <= 0:
                        width = None
                except (TypeError, ValueError):
                    width = None
            icon = item.get("icon") or f"{sym}.png"
            out.append({"symbol": sym, "title": title, "width": width, "icon": icon})
    return out



def _icon_arrays(img, alpha_threshold):
    """RGBA PIL image → {"w", "h", "rgb": uint8[h,w,3], "mask": bool[h,w]} (pixels with alpha > threshold)."""
    a = np.asarray(img.convert("RGBA"))
    return {"w": a.shape[1], "h": a.shape[0], "rgb": np.ascontiguousarray(a[..., :3]),
            "mask": a[..., 3] > alpha_threshold}

class FinanceTicker(ModuleRenderer):
    def __init__(self, config, loader):
        super().__init__(config)
        
        # Silence noisy PIL logs
        import logging
        logging.getLogger("PIL").setLevel(logging.WARNING)

        self.loader = loader
        
        self.font = graphics.Font()
        font_path = self.config.get_font_path("top_bar")
        if font_path and os.path.exists(font_path):
            self.font.LoadFont(font_path)
            
        self.font_small = graphics.Font()
        font_path_small = self.config.get_font_path("small")
        if font_path_small and os.path.exists(font_path_small):
            self.font_small.LoadFont(font_path_small)
            
        self.color_pos = graphics.Color(*self.config.get_color("ticker_positive"))
        self.color_neg = graphics.Color(*self.config.get_color("ticker_negative"))
        self.color_text = graphics.Color(*self.config.get_color("default_text"))
        self.color_sep = graphics.Color(100, 100, 100)
        self.color_dim = graphics.Color(150, 150, 150)      # high/low + prev close
        self.color_dashed = graphics.Color(60, 60, 60)       # prev-close dashed line
        
        # Config may be plain ["NVDA", ...] OR new [{symbol, title, width, icon}, ...].
        # We normalize to the rich form. If the server has written `_display` into
        # finance.json, that takes precedence at render time over this local config —
        # so users editing config on the server don't need to redeploy the client.
        self._cfg_meta = {
            s["symbol"]: s
            for s in _normalize_finance_symbols(
                self.config.modules.get("finance", {}).get("symbols", [])
            )
        }
        self.symbols = list(self._cfg_meta.keys())
        self.data = {}
        self._last_raw = None
        self._ver = {}            # per-symbol data version → invalidates the item cache
        self._item_cache = {}
        self.scroll_x = 0.0 # Float for sub-pixel precision
        self.last_scroll_time = time.time()

        # Load Icons
        self.icons = {}
        # path: root/assets/icons/stocks/
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        icon_dir = os.path.join(base_dir, 'assets', 'icons', 'stocks')
        flag_dir = os.path.join(base_dir, 'assets', 'icons', 'flags')
        
        # Load Flags
        self.flags = {}
        flag_map = {"USD": "flag_usa.png", "CAD": "flag_can.png"}
        
        if os.path.exists(flag_dir):
            try:
                from PIL import Image
                for k, fname in flag_map.items():
                    p = os.path.join(flag_dir, fname)
                    if os.path.exists(p):
                        try:
                            img = Image.open(p).convert('RGBA')
                            target_h = 14
                            if img.height != target_h:
                                ratio = target_h / img.height
                                new_w = int(img.width * ratio)
                                img = img.resize((new_w, target_h), Image.LANCZOS)

                            self.flags[k] = _icon_arrays(img, 128)
                        except Exception:
                            pass
            except ImportError:
                pass

        if os.path.exists(icon_dir):
            try:
                from PIL import Image
                for s in self.symbols:
                    icon_filename = self._cfg_meta.get(s, {}).get("icon") or f"{s}.png"
                    p = os.path.join(icon_dir, icon_filename)
                    if os.path.exists(p):
                        try:
                            img = Image.open(p).convert('RGBA')
                            # Force resize to exactly 14px height to match text baseline
                            target_h = 14
                            if img.height != target_h:
                                ratio = target_h / img.height
                                new_w = int(img.width * ratio)
                                img = img.resize((new_w, target_h), Image.LANCZOS)

                            self.icons[s] = _icon_arrays(img, 200 if s == "AMD" else 128)
                        except Exception:
                            pass
            except ImportError:
                pass
        
        # Initialize Default Data
        for s in self.symbols:
            self.data[s] = {"price": 0.00, "change": 0.00, "history": []}
            
        # Cache widths to avoid recalculating every frame
        self.item_widths = {}
        self._recalc_all_widths()

    def _recalc_all_widths(self):
        # Pass 1: Find max text width across ALL symbols using a "worst case" scenario
        # We don't just measure the CURRENT text, we measure the MAXIMUM POSSIBLE text.
        # Max Symbol: We know the symbols list.
        # Max Price: 000000.00 (9 chars approx)
        # Max Change: (00.0%) (7 chars approx)
        
        # Calculate max symbol width
        max_sym_w = 0
        for s in self.symbols:
            w = sum(self.font.CharacterWidth(ord(c)) for c in s)
            if w > max_sym_w:
                max_sym_w = w
                
        # Calculate max possible price width (8 digits + dot = 9 chars?)
        # User said "8 digit number (000000.00)" -> That is 6+1+2 = 9 chars.
        # Plus we add " " padding.
        test_price = "000000.00"
        max_price_w = sum(self.font.CharacterWidth(ord(c)) for c in test_price)
        
        # Calculate max possible change width "(00.0%)"
        test_change = "(+00.0%)"
        max_change_w = sum(self.font.CharacterWidth(ord(c)) for c in test_change)
        
        # Icon width (assuming 16px roughly if present, plus padding)
        icon_w = 12 + 3 # standard icon width in this module is often resized to height 12?
        # Let's check _draw_item: "if sym in self.icons: width += self.icons[sym]['w'] + 3"
        # We should assume an icon is present for sizing consistency if we want perfect alignment?
        # Or just max(icon_w) if any have it. Most have it.
        
        # Crypto Flag adjustment
        # BTC-USD gets an extra flag (width ~20px + 1px spacing)
        # So total width needs to accommodate that.
        # Max width calculation handles this:
        flag_extra_w = 21 + 3 # approx flag width + spacing
        
        # Total Max Width
        # [Icon] [Symbol] ..pad.. [Price] ..pad.. [Change]
        # icon_w + max_sym_w + 10 + max_price_w + 5 + max_change_w
        
        total_max_w = icon_w + flag_extra_w + max_sym_w + 10 + max_price_w + 5 + max_change_w
        
        # Enforce exact width for 85 points * 2px = 170px
        # User requested chart size to be uniform 170px for ALL tickers.
        self.global_chart_width = 170

        # Pass 2: Set item widths
        for s in self.symbols:
            # All items are now same width: 170px + padding
            # Crypto flag offset is handled inside the 170px space (or we add to it?)
            # "make all the charts / tickers 170 pixels wide"
            # If we add flag width on top, they won't be uniform.
            # But the flag takes up space.
            # If we want the CHART to be 170px wide, then the total item width is 170 + padding.
            # But wait, crypto has an extra flag icon.
            # If we fix the chart width to 170, the crypto ticker text/icon area is wider.
            # So the total item width MUST be larger for crypto if the chart width is fixed.
            
            # Let's interpret "make all the charts / tickers 170 pixels wide" as "chart area = 170px".
            # The item width is determined by chart width.
            
            # Re-reading: "the cad chart is bleeding into the usd chart. the spacing is all wrongg."
            # This implies overlap.
            
            width = self.global_chart_width
            
            # Crypto has extra elements (flag) in the header line.
            # But the width of the ITEM is usually determined by the chart width (which is the widest thing).
            # Unless the header text is wider than the chart?
            # Max text width is likely < 170px.
            
            # However, in _draw_item, we calculate right_edge = start_x + item_width
            # And we draw the chart from start_x to start_x + chart_w.
            # So item_width should be >= chart_w.
            
            # The issue before was chart_w > item_width.
            
            # We will set item_width = chart_w + padding.
            # And we need to ensure chart_w is consistent 170px.
            
            # Special case: If crypto has a flag, does it shift the chart?
            # In _draw_item: "chart_x = start_x". No shift.
            # The flag is in the text line (Line 1).
            # So the chart is always aligned left.
            
            # So we just need to ensure item_width is enough to cover the chart.
            
            self.item_widths[s] = width + 10

    def update(self):
        # Fetch from file — the loader returns the same object until finance.json changes,
        # so skip the per-frame re-merge v1 did.
        new_data = self.loader.get_data("finance")
        if new_data is self._last_raw:
            return
        self._last_raw = new_data
        if new_data:
            # The hub writes tickers in the configured display order and drops disabled ones,
            # so order/add/remove edits from the control panel apply without a restart.
            order = [s for s in new_data.keys() if isinstance(new_data.get(s), dict)]
            if order and order != self.symbols:
                self.symbols = order
            for s, info in new_data.items():
                if s not in self.data: self.data[s] = {}
                # Update logic could be optimized to check for diffs, but updating all is safer
                self.data[s].update(info)
                self._ver[s] = self._ver.get(s, 0) + 1
                # If the server has decorated this ticker with display metadata
                # (title/width/icon), and the icon filename has changed since we
                # last loaded it, try to load the new icon now.
                disp = info.get("_display") if isinstance(info, dict) else None
                if disp and disp.get("icon"):
                    self._maybe_load_icon(s, disp["icon"])
                # Update width for this item immediately
                self.item_widths[s] = self._measure_item(s, self.data[s])
                
    def _measure_item(self, sym, info):
        # If the server (or local config) specified a per-ticker width, honor it.
        # _display.width wins over self._cfg_meta[sym].width wins over global default.
        disp = info.get("_display") if isinstance(info, dict) else None
        per_w = None
        if disp and disp.get("width"):
            per_w = disp.get("width")
        else:
            per_w = self._cfg_meta.get(sym, {}).get("width")
        if isinstance(per_w, int) and per_w > 0:
            return per_w + 10
        # Default: global chart width.
        if hasattr(self, 'global_chart_width'):
            return self.global_chart_width + 10
        return 78 + 10

    def _ticker_width(self, sym, info):
        """Per-ticker chart width (no padding). Mirrors _measure_item logic."""
        disp = info.get("_display") if isinstance(info, dict) else None
        per_w = None
        if disp and disp.get("width"):
            per_w = disp.get("width")
        else:
            per_w = self._cfg_meta.get(sym, {}).get("width")
        if isinstance(per_w, int) and per_w > 0:
            return per_w
        return getattr(self, "global_chart_width", 170)

    def _ticker_title(self, sym, info):
        """Display text for this ticker (falls back to symbol)."""
        disp = info.get("_display") if isinstance(info, dict) else None
        if disp and disp.get("title"):
            return disp["title"]
        return self._cfg_meta.get(sym, {}).get("title") or sym

    def _maybe_load_icon(self, sym, icon_filename):
        """Load (or reload) a ticker's icon from assets/icons/stocks/<icon_filename>.

        Called when finance.json arrives with a `_display.icon` we haven't seen yet
        for this symbol, e.g. after the user picked a different icon in the control
        panel and the server has re-written its data.
        """
        if not icon_filename:
            return
        # Skip if already loaded with this filename.
        already = self.icons.get(sym, {}).get("__icon_filename")
        if already == icon_filename:
            return
        try:
            from PIL import Image
        except ImportError:
            return
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        icon_dir = os.path.join(base_dir, 'assets', 'icons', 'stocks')
        p = os.path.join(icon_dir, icon_filename)
        if not os.path.exists(p):
            return
        try:
            img = Image.open(p).convert('RGBA')
            target_h = 14
            if img.height != target_h:
                ratio = target_h / img.height
                new_w = int(img.width * ratio)
                img = img.resize((new_w, target_h), Image.LANCZOS)
            self.icons[sym] = _icon_arrays(img, 200 if sym == "AMD" else 128)
            self.icons[sym]["__icon_filename"] = icon_filename
        except Exception:
            pass

    def _draw_icon(self, canvas, icon, x_pos, y_pos):
        canvas.Blit(icon["rgb"], x_pos, y_pos, icon["mask"])

    def _draw_item(self, canvas, sym, info, start_x, y, height):
        # Ensure start_x is integer to prevent jitter in relative calculations
        start_x = int(start_x)

        # Top Bar Height is now 40px (passed as height arg)

        # Per-ticker chart width (config / _display override → global default).
        chart_w = self._ticker_width(sym, info)
        item_width = chart_w

        # Display title (config / _display override → symbol itself).
        display_title = self._ticker_title(sym, info)

        # Calculate content widths first for alignment
        icon_w = 0
        if sym in self.icons:
            icon_w = self.icons[sym]['w'] + 3

        sym_w = sum(self.font.CharacterWidth(ord(c)) for c in display_title)

        price_str = f"{info.get('price', 0):.2f}"
        price_w = sum(self.font.CharacterWidth(ord(c)) for c in price_str)

        change = info.get('change', 0)
        chg_str = f"({change:+.1f}%)"
        change_w = sum(self.font.CharacterWidth(ord(c)) for c in chg_str)
        
        # --- Drawing Line 1: Ticker Info ---
        
        # Padding Top: 2px
        line1_y = y + 8 # Baseline for 5x7 font (height 7). Top at y+1.
        
        # Left Align: Icon + Symbol
        current_x = start_x
        if sym in self.icons:
            self._draw_icon(canvas, self.icons[sym], current_x, y + 2)
            current_x += self.icons[sym]['w'] + 1 # 1px spacing
            
        # Draw Secondary Flag for Crypto
        flag_key = None
        if "USD" in sym and ("BTC" in sym or "ETH" in sym): flag_key = "USD"
        elif "CAD" in sym and ("BTC" in sym or "ETH" in sym): flag_key = "CAD"
        
        if flag_key and flag_key in self.flags:
            self._draw_icon(canvas, self.flags[flag_key], current_x, y + 2)
            current_x += self.flags[flag_key]['w'] + 3 # 3px spacing before text
        elif sym in self.icons:
             # Just add the remaining padding we skipped above
             current_x += 2 # total 3px spacing from icon
            
        graphics.DrawText(canvas, self.font, current_x, line1_y, self.color_text, display_title)
        
        # Right Align: Price + Change
        right_edge = start_x + item_width
        col = self.color_pos if change >= 0 else self.color_neg
        graphics.DrawText(canvas, self.font, right_edge - change_w, line1_y, col, chg_str)
        graphics.DrawText(canvas, self.font, right_edge - change_w - 5 - price_w, line1_y, self.color_text, price_str)
        
        # --- Drawing Line 2: High/Low + Prev Close ---
        # 1px margin from Line 1. Line 1 bottom is ~y+8.
        # Line 2 Baseline: y + 8 + 1 (margin) + 6 (tom-thumb height) = y + 15
        
        line2_y = line1_y + 1 + 6 
        
        # Left Align: Day High/Low
        day_high = info.get("day_high")
        day_low = info.get("day_low")
        
        # Hide High/Low for crypto tickers (usually contain "-USD" or "-CAD") to avoid bunching
        is_crypto = "-" in sym and ("USD" in sym or "CAD" in sym)
        
        if day_high is not None and day_low is not None and not is_crypto:
            # Add spaces for separation as requested
            hl_str = f"H:{day_high:.2f}   L:{day_low:.2f}"
            graphics.DrawText(canvas, self.font_small, current_x, line2_y, self.color_dim, hl_str)

        # Right Align: Prev Close
        prev_close_val = info.get("prev_close", 0.0)
        prev_str = f"Prev: {prev_close_val:.2f}"
        prev_w = sum(self.font_small.CharacterWidth(ord(c)) for c in prev_str)

        graphics.DrawText(canvas, self.font_small, right_edge - prev_w, line2_y, self.color_dim, prev_str)
        
        # --- Drawing Chart ---
        # 1px margin from Line 2. Line 2 bottom is ~line2_y (baseline).
        # Chart Top: line2_y + 1
        # Height: Remaining space.
        # Total Height 40. 
        # line2_y is y+15.
        # chart_y = y+16.
        # chart_h = 40 - 16 - 2 (padding bottom) = 22px?
        
        chart_x = start_x
        chart_y = line2_y + 2
        chart_h = height - (chart_y - y) - 2 # Dynamic fit
        if chart_h < 10: chart_h = 10 # Safety minimum
        
        prev = info.get("prev_close")
        min_v = info.get("min_v")
        rng = info.get("rng")
        
        if prev is not None and min_v is not None and rng:
            norm_prev = (prev - min_v) / rng
            norm_prev = max(0.0, min(1.0, norm_prev))
            base_y = int(chart_y + chart_h - (norm_prev * chart_h))
            
            # Dashed line
            for i in range(0, int(chart_w), 4):
                graphics.DrawLine(canvas, chart_x + i, base_y, chart_x + i + 2, base_y, self.color_dashed)
        
        hist = info.get("history", [])
        chart_type = info.get("chart_type", "crypto")  # Default to crypto for backwards compat

        # Helper to clip Y values to chart area
        def clip_y(val):
            return int(max(chart_y, min(chart_y + chart_h - 1, val)))
            
        if hist:
            last_x = -1
            last_y = -1
            
            if chart_type == "stock":
                # STOCK CHARTS: Draw LEFT-TO-RIGHT (market open to current time)
                # Data starts at market open (left), line grows rightward as day progresses
                x_cursor = int(chart_x)
                
                for i in range(len(hist)):
                    if x_cursor > chart_x + chart_w: break
                    
                    point = hist[i]
                    val = point.get('c', 0.5) if isinstance(point, dict) else point
                    
                    y_pos = int(chart_y + chart_h - (val * chart_h))
                    y_pos = clip_y(y_pos)
                    
                    if last_x != -1:
                        graphics.DrawLine(canvas, last_x, last_y, x_cursor, y_pos, col)
                    else:
                        canvas.SetPixel(x_cursor, y_pos, col.red, col.green, col.blue)
                    
                    last_x = x_cursor
                    last_y = y_pos
                    x_cursor += 2
            else:
                # CRYPTO CHARTS: Draw RIGHT-TO-LEFT (rolling 24h, always full)
                # Newest data on right, oldest on left
                x_cursor = int(chart_x + chart_w - 1)
                
                for i in range(len(hist) - 1, -1, -1):
                    if x_cursor < chart_x: break
                    
                    point = hist[i]
                    val = point.get('c', 0.5) if isinstance(point, dict) else point
                    
                    y_pos = int(chart_y + chart_h - (val * chart_h))
                    y_pos = clip_y(y_pos)
                    
                    if last_x != -1:
                        graphics.DrawLine(canvas, x_cursor, y_pos, last_x, last_y, col)
                    else:
                        canvas.SetPixel(x_cursor, y_pos, col.red, col.green, col.blue)
                    
                    last_x = x_cursor
                    last_y = y_pos
                    x_cursor -= 2

        
        # Separator line
        # Use int() to ensure alignment to pixel grid and avoid "jitter" from sub-pixel float rounding differences
        sep_x = int(start_x + item_width + 5)
        # Shortened line to avoid 32px hardware artifact (ends at 30px)
        graphics.DrawLine(canvas, sep_x, y+2, sep_x, y+30, self.color_sep)
        
        return item_width + 10

    def _blit_item(self, canvas, sym, x, y, height):
        """Each ticker only changes when its data does; render it once, then blit it as it scrolls."""
        w = self.item_widths.get(sym, 50)
        key = (self._ver.get(sym, 0), w, height, self._ticker_title(sym, self.data[sym]))
        hit = self._item_cache.get(sym)
        if hit is None or hit[0] != key:
            tmp = Canvas(w, height)
            self._draw_item(tmp, sym, self.data[sym], 0, 0, height)
            hit = (key, tmp.buf)
            self._item_cache[sym] = hit
        canvas.Blit(hit[1], x, y)

    def render(self, canvas, x, y, width, height):
        if not any(d.get('price') for d in self.data.values()):
             graphics.DrawText(canvas, self.font, x, y + 10, self.color_text, "Loading Finance...")
             return

        # 1. Update Scroll Position (Time-based for smoothness)
        now = time.time()
        dt = now - self.last_scroll_time
        
        # Reset if huge delta (e.g. after sleep)
        if dt > 1.0: dt = 0.016 
        
        # Pixels per second (slower is smoother typically)
        speed = 30.0 
        
        self.scroll_x += speed * dt
        self.last_scroll_time = now
            
        cursor_x = width - self.scroll_x
        
        # 2. Calculate Total Width (fast, cached)
        total_list_width = 0
        for s in self.symbols:
            total_list_width += self.item_widths.get(s, 50) # Fallback 50

        # 3. Handle Wrap
        if cursor_x < -total_list_width:
            self.scroll_x -= total_list_width
            cursor_x = width - self.scroll_x
            
        # 4. Draw Loop (With Culling)
        # We need to draw the sequence starting at cursor_x.
        # If the sequence doesn't fill the screen, we draw it again (wrapping).
        
        # Round to integer for drawing, but keep float internally
        draw_x_start = cursor_x
        
        temp_x = draw_x_start
        
        # Pass 1: Draw until end of list
        for s in self.symbols:
            w = self.item_widths.get(s, 50)
            
            # Draw only if visible
            # Item is visible if [temp_x, temp_x + w] overlaps [0, width]
            if (temp_x + w > 0) and (temp_x < width):
                try:
                    self._blit_item(canvas, s, math.floor(temp_x), y, height)
                except Exception as e:
                    print(f"Error drawing ticker {s}: {e}")
                    # Draw error placeholder
                    graphics.DrawText(canvas, self.font, math.floor(temp_x), y + 10, self.color_neg, f"ERR: {s}")
                
            temp_x += w
            
        # Pass 2: Draw start of list again if we have space left on right
        if temp_x < width:
            for s in self.symbols:
                if temp_x > width: break # Fully off-screen right
                
                w = self.item_widths.get(s, 50)
                
                # Check overlap (left side check is implicit since temp_x starts > previous)
                if (temp_x + w > 0):
                    try:
                        self._blit_item(canvas, s, math.floor(temp_x), y, height)
                    except Exception as e:
                        print(f"Error drawing ticker {s} (wrap): {e}")
                    
                temp_x += w

    def get_next_update_time(self):
        # 60fps is plenty for smooth scrolling (was 200Hz, wasting CPU)
        return time.time() + 0.016
