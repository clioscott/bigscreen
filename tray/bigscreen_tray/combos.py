"""Hotkey combos: modifiers plus one trigger (a key or a mouse button).

Stored as strings like ``ctrl+alt+right``, ``ctrl+mouse4`` or ``shift+wheel_up`` (modifiers always in the
order ctrl, alt, shift, win). Keys are named after their Windows virtual-key code.
"""

from __future__ import annotations

from dataclasses import dataclass

MODIFIERS = ("ctrl", "alt", "shift", "win")
MOD_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
MOD_FLAGS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}           # RegisterHotKey's MOD_*

# virtual-key code -> (name, label)
_VK: dict[int, tuple[str, str]] = {
    0x08: ("backspace", "Backspace"), 0x09: ("tab", "Tab"), 0x0D: ("enter", "Enter"), 0x13: ("pause", "Pause"),
    0x14: ("capslock", "Caps Lock"), 0x1B: ("esc", "Esc"), 0x20: ("space", "Space"),
    0x21: ("pageup", "Page Up"), 0x22: ("pagedown", "Page Down"), 0x23: ("end", "End"), 0x24: ("home", "Home"),
    0x25: ("left", "←"), 0x26: ("up", "↑"), 0x27: ("right", "→"), 0x28: ("down", "↓"),
    0x2C: ("printscreen", "Print Screen"), 0x2D: ("insert", "Insert"), 0x2E: ("delete", "Delete"),
    0x5D: ("apps", "Menu"), 0x90: ("numlock", "Num Lock"), 0x91: ("scrolllock", "Scroll Lock"),
    0x6A: ("num_multiply", "Num *"), 0x6B: ("num_add", "Num +"), 0x6D: ("num_subtract", "Num -"),
    0x6E: ("num_decimal", "Num ."), 0x6F: ("num_divide", "Num /"),
    0xBA: ("semicolon", ";"), 0xBB: ("equals", "="), 0xBC: ("comma", ","), 0xBD: ("minus", "-"),
    0xBE: ("period", "."), 0xBF: ("slash", "/"), 0xC0: ("backtick", "`"), 0xDB: ("lbracket", "["),
    0xDC: ("backslash", "\\"), 0xDD: ("rbracket", "]"), 0xDE: ("quote", "'"), 0xE2: ("oem_102", "\\ (102)"),
    0xA6: ("browser_back", "Browser Back"), 0xA7: ("browser_forward", "Browser Forward"),
    0xA8: ("browser_refresh", "Browser Refresh"), 0xA9: ("browser_stop", "Browser Stop"),
    0xAA: ("browser_search", "Browser Search"), 0xAB: ("browser_favorites", "Browser Favorites"),
    0xAC: ("browser_home", "Browser Home"), 0xAD: ("volume_mute", "Mute"), 0xAE: ("volume_down", "Volume Down"),
    0xAF: ("volume_up", "Volume Up"), 0xB0: ("media_next", "Next Track"), 0xB1: ("media_prev", "Previous Track"),
    0xB2: ("media_stop", "Stop Media"), 0xB3: ("media_play_pause", "Play/Pause"), 0xB4: ("launch_mail", "Mail"),
    0xB5: ("launch_media", "Media"), 0xB6: ("launch_app1", "App 1"), 0xB7: ("launch_app2", "App 2"),
}
for _c in range(26):
    _VK[0x41 + _c] = (chr(97 + _c), chr(65 + _c))
for _c in range(10):
    _VK[0x30 + _c] = (str(_c), str(_c))
    _VK[0x60 + _c] = (f"num{_c}", f"Num {_c}")
for _c in range(24):
    _VK[0x70 + _c] = (f"f{_c + 1}", f"F{_c + 1}")
_VK_BY_NAME = {name: vk for vk, (name, _) in _VK.items()}

# modifier keys themselves (never a trigger)
MODIFIER_VKS = {0x10: "shift", 0xA0: "shift", 0xA1: "shift", 0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
                0x12: "alt", 0xA4: "alt", 0xA5: "alt", 0x5B: "win", 0x5C: "win"}

MOUSE = {"mouse_left": "Left Click", "mouse_right": "Right Click", "mouse_middle": "Middle Click",
         "mouse4": "Mouse 4 (Back)", "mouse5": "Mouse 5 (Forward)",
         "wheel_up": "Wheel Up", "wheel_down": "Wheel Down", "wheel_left": "Wheel Left", "wheel_right": "Wheel Right"}
# triggers that would hijack ordinary use unless a modifier is held
NEEDS_MODIFIER = {"mouse_left", "mouse_right", "wheel_up", "wheel_down", "wheel_left", "wheel_right"}


def vk_name(vk: int) -> str:
    return _VK.get(vk, (f"vk_{vk:02x}", ""))[0]


def name_vk(name: str) -> int | None:
    if name in _VK_BY_NAME:
        return _VK_BY_NAME[name]
    if name.startswith("vk_"):
        try:
            return int(name[3:], 16)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class Combo:
    mods: tuple[str, ...]
    trigger: str

    @classmethod
    def make(cls, mods, trigger: str) -> "Combo":
        return cls(tuple(m for m in MODIFIERS if m in set(mods)), trigger)

    @classmethod
    def parse(cls, s: str) -> "Combo | None":
        parts = [p for p in (s or "").strip().lower().split("+") if p]
        if not parts:
            return None
        *mods, trigger = parts
        if any(m not in MODIFIERS for m in mods) or trigger in MODIFIERS:
            return None
        if trigger not in MOUSE and name_vk(trigger) is None:
            return None
        return cls.make(mods, trigger)

    def __str__(self) -> str:
        return "+".join((*self.mods, self.trigger))

    @property
    def is_mouse(self) -> bool:
        return self.trigger in MOUSE

    @property
    def vk(self) -> int | None:
        return None if self.is_mouse else name_vk(self.trigger)

    @property
    def mod_flags(self) -> int:
        return sum(MOD_FLAGS[m] for m in self.mods)

    @property
    def label(self) -> str:
        if self.is_mouse:
            t = MOUSE[self.trigger]
        else:
            t = _VK.get(self.vk or 0, (None, ""))[1] or self.trigger.upper()
        return " + ".join([*(MOD_LABELS[m] for m in self.mods), t])

    def problem(self) -> str | None:
        """Why this combo can't be bound, if it can't."""
        if self.trigger in NEEDS_MODIFIER and not self.mods:
            return f"{MOUSE[self.trigger]} needs Ctrl, Alt, Shift or Win with it"
        return None


def label(s: str) -> str:
    c = Combo.parse(s)
    return c.label if c else s
