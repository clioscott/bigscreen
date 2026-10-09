from abc import ABC, abstractmethod

class ModuleRenderer(ABC):
    """Abstract base class for all UI modules (Clock, Calendar, Tickers)"""
    
    def __init__(self, config):
        self.config = config
        
    @abstractmethod
    def update(self):
        """Called periodically to fetch data (runs in background thread preferably)"""
        pass
        
    @abstractmethod
    def render(self, canvas, x, y, width, height):
        """Called every frame to draw to the canvas"""
        pass
        
    @abstractmethod
    def get_next_update_time(self):
        """Returns unix timestamp of when this module NEXT needs a redraw"""
        pass




