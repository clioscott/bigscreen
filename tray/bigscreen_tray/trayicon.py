"""The tray icon: its image, and a menu rebuilt from the latest wall status every time it opens."""

from __future__ import annotations

import pystray
from PIL import Image, ImageDraw
from pystray._util import win32
from pystray._win32 import Icon as _Win32Icon

from .hub import SIDES


def icon_image(online: bool, size: int = 64) -> Image.Image:
    """A little LED wall: a dark frame, two lit zones (grey when the wall can't be reached)."""
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1 * s, 12 * s, 63 * s, 52 * s], radius=6 * s, fill=(24, 28, 38, 255),
                        outline=(120, 130, 150, 255) if online else (90, 90, 90, 255), width=max(1, round(3 * s)))
    left, right = ((255, 176, 32), (64, 200, 255)) if online else ((120, 120, 120), (95, 95, 95))
    d.rectangle([8 * s, 19 * s, 29 * s, 45 * s], fill=left + (255,))
    d.rectangle([34 * s, 19 * s, 56 * s, 45 * s], fill=right + (255,))
    return img


class TrayIcon(_Win32Icon):
    """pystray's Windows icon, but left-click opens the menu too, and the menu is rebuilt as it opens.
    (Overrides a private method of pystray 0.19.5, which requirements pin.)"""

    def _on_notify(self, wparam, lparam):
        if lparam in (win32.WM_LBUTTONUP, win32.WM_RBUTTONUP):
            self._update_menu()
            return super()._on_notify(wparam, win32.WM_RBUTTONUP)
        return super()._on_notify(wparam, lparam)


def build_menu(app) -> pystray.Menu:
    Item, Sep = pystray.MenuItem, pystray.Menu.SEPARATOR

    def act(a):
        return lambda _icon, _item: app.do(a)

    def items():
        wall, online = app.wall, app.online
        if not online:
            yield Item(f"Can't reach the wall — {app.last_error or 'connecting…'}", None, enabled=False)
        for side in SIDES:
            if wall.known and online:
                yield Item(f"{side.capitalize()}: {wall.describe(side)}", None, enabled=False)
        yield Sep
        for side in SIDES:
            sub = [Item("Next screen", act({"do": "next", "side": side})),
                   Item("Previous screen", act({"do": "prev", "side": side})),
                   Item("Hold this screen", act({"do": "hold", "side": side}),
                        checked=lambda _i, side=side: app.wall.is_held(side)),
                   Sep, Item("Play once:", None, enabled=False)]
            playing = wall.playing(side) or {}
            for e in wall.entries(side):
                same = all(playing.get(k) == e.get(k) for k in ("panel", "track", "weather_screen"))
                sub.append(Item(wall.title(e), act({"do": "play", "side": side, "entry": e}),
                                checked=lambda _i, same=same: same, radio=True))
            yield Item(f"{side.capitalize()} side", pystray.Menu(*sub), enabled=online)
        cats = wall.categories
        if cats:
            cur = wall.headlines.get("current")
            news = [Item("Next category", act({"do": "news", "delta": 1})),
                    Item("Previous category", act({"do": "news", "delta": -1})), Sep]
            news += [Item(c, act({"do": "news", "category": c}), checked=lambda _i, c=c: c == cur, radio=True)
                     for c in cats]
            yield Item("News", pystray.Menu(*news), enabled=online)
        yield Item("Back to normal rotation", act({"do": "normal"}),
                   enabled=online and any(wall.is_preview(s) for s in SIDES))
        yield Sep
        yield Item("Hotkeys && settings…", lambda _i, _it: app.open_settings())
        yield Item("Open control panel", lambda _i, _it: app.do({"do": "open_panel"}))
        yield Sep
        yield Item("Quit", lambda _i, _it: app.quit())

    return pystray.Menu(items)
