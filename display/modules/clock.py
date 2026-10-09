import time
import os
from datetime import datetime
from display.renderer import ModuleRenderer

# Import graphics from mocked library if on Windows
from display.gfx import graphics

class ClockRenderer(ModuleRenderer):
    def __init__(self, config):
        super().__init__(config)
        self.font = graphics.Font()
        font_path = self.config.get_font_path("clock")
        
        # Load font (handle absolute/relative paths)
        if font_path:
            # Check if font file exists
            if not os.path.exists(font_path):
                print(f"Error: Font file not found at {font_path}")
            else:
                self.font.LoadFont(font_path)
                
        self.color = graphics.Color(*self.config.get_color("default_text"))
        self._bg_black = graphics.Color(0, 0, 0)
        self.y_offset = self.config.get_font_offset("clock")
        
    def update(self):
        # Clock doesn't need external data fetching
        pass

    def render(self, canvas, x, y, width, height, baseline_offset_y=0):
        now = datetime.now()
        time_str = now.strftime("%H:%M:%S")
        
        # Fill clock zone with black background first (masks status bar sliding text)
        # Fill from left edge (0) to right edge of clock zone to fully mask any overflow
        for i in range(height):
            graphics.DrawLine(canvas, 0, y + i, x + width, y + i, self._bg_black)
        
        # Draw Text: centered in zone, with optional baseline offset (positive = down)
        text_height = self.font.height
        baseline = y + (height // 2) + (text_height // 2) + self.y_offset + baseline_offset_y
        graphics.DrawText(canvas, self.font, x, baseline, self.color, time_str)
        
    def get_next_update_time(self):
        # Update at the start of the next second
        now = time.time()
        return int(now) + 1

