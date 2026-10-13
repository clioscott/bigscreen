"""Global hotkeys on Windows, on one thread with its own message loop.

* Keyboard combos use ``RegisterHotKey``: Windows tells us only when that combo is pressed, swallows it, and
  refuses a combo another program already owns (reported back as an error).
* Mouse combos (side buttons, modified clicks, the wheel) need a low-level mouse hook. It's installed only
  while a mouse combo is bound or a combo is being recorded, matches buttons only (moves pass straight
  through), and swallows a bound button so the click doesn't also reach the window under the cursor.
* Recording adds a low-level keyboard hook for as long as it takes to press one combo, then removes it.

Windows silently drops a low-level hook whose callback ever runs too slowly, so the mouse hook is
re-installed every minute while it's needed.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
from ctypes import wintypes

from .combos import MODIFIER_VKS, Combo, vk_name

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


def _sig(fn, res, *args):
    fn.restype, fn.argtypes = res, list(args)


_sig(user32.SetWindowsHookExW, wintypes.HHOOK, ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
_sig(user32.UnhookWindowsHookEx, wintypes.BOOL, wintypes.HHOOK)
_sig(user32.CallNextHookEx, LRESULT, wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.RegisterHotKey, wintypes.BOOL, wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_sig(user32.UnregisterHotKey, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_sig(user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_sig(user32.PeekMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
     wintypes.UINT)
_sig(user32.PostThreadMessageW, wintypes.BOOL, wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.SetTimer, ctypes.c_size_t, wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
_sig(user32.GetAsyncKeyState, ctypes.c_short, ctypes.c_int)
_sig(user32.keybd_event, None, wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t)
_sig(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
_sig(kernel32.GetCurrentThreadId, wintypes.DWORD)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_TIMER, WM_HOTKEY, WM_COMMAND_QUEUE = 0x113, 0x312, 0x8001        # WM_APP + 1
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
VK_ESCAPE, VK_DUMMY = 0x1B, 0xE8            # 0xE8 is unassigned: tapping it stops a held Win key opening Start

_MOUSE_DOWN = {0x201: "mouse_left", 0x204: "mouse_right", 0x207: "mouse_middle"}
_MOUSE_UP = {0x202: "mouse_left", 0x205: "mouse_right", 0x208: "mouse_middle"}
WM_XBUTTONDOWN, WM_XBUTTONUP, WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x20B, 0x20C, 0x20A, 0x20E


def held_modifiers() -> set[str]:
    down = lambda vk: user32.GetAsyncKeyState(vk) & 0x8000
    mods = {name for vk, name in ((0x11, "ctrl"), (0x12, "alt"), (0x10, "shift")) if down(vk)}
    if down(0x5B) or down(0x5C):
        mods.add("win")
    return mods


def _mouse_event(msg, data) -> tuple[str | None, bool]:
    """(trigger, is_press) for a low-level mouse message; (None, _) for moves and the like."""
    if msg in _MOUSE_DOWN:
        return _MOUSE_DOWN[msg], True
    if msg in _MOUSE_UP:
        return _MOUSE_UP[msg], False
    if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
        b = (data >> 16) & 0xFFFF
        return ({1: "mouse4", 2: "mouse5"}.get(b), msg == WM_XBUTTONDOWN)
    if msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
        delta = ctypes.c_short((data >> 16) & 0xFFFF).value
        if msg == WM_MOUSEWHEEL:
            return ("wheel_up" if delta > 0 else "wheel_down"), True
        return ("wheel_right" if delta > 0 else "wheel_left"), True
    return None, False


def _tap_dummy_if_win(mods):
    if "win" in mods:
        user32.keybd_event(VK_DUMMY, 0, 0, 0)
        user32.keybd_event(VK_DUMMY, 0, 2, 0)


class HotkeyThread(threading.Thread):
    """Owns every hotkey registration and hook. Other threads talk to it through ``set_bindings``,
    ``record`` and ``stop``; ``on_fire(combo)`` is called on this thread and must return at once."""

    def __init__(self, on_fire):
        super().__init__(name="hotkeys", daemon=True)
        self.on_fire = on_fire
        self._tid = None
        self._ready = threading.Event()
        self._commands: queue.Queue = queue.Queue()
        self._combos: list[str] = []                 # what should be bound
        self._registered: dict[int, str] = {}        # RegisterHotKey id -> combo
        self._mouse_combos: set[str] = set()
        self._swallow_up: set[str] = set()           # buttons whose release we owe a swallow
        self._recording = None                       # callback(combo or None) while recording
        self._mouse_hook = self._kb_hook = None
        self._mouse_proc = HOOKPROC(self._on_mouse)  # kept referenced for as long as the hook may call it
        self._kb_proc = HOOKPROC(self._on_key)
        self._hmod = kernel32.GetModuleHandleW(None)

    # -- called from other threads ---------------------------------------------------------
    def start(self):
        super().start()
        self._ready.wait(5)

    def _send(self, *cmd):
        self._commands.put(cmd)
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_COMMAND_QUEUE, 0, 0)

    def set_bindings(self, combos) -> dict[str, str]:
        """Bind these combos (replacing the old set). Returns {combo: why it couldn't be bound}."""
        reply: queue.Queue = queue.Queue()
        self._send("set", list(combos), reply)
        try:
            return reply.get(timeout=3)
        except queue.Empty:
            return {}

    def record(self, callback):
        """Capture the next combo pressed; ``callback(combo_str | None)`` runs on this thread (None = cancelled)."""
        self._send("record", callback)

    def cancel_record(self):
        self._send("cancel")

    def stop(self):
        self._send("quit")

    # -- the thread --------------------------------------------------------------------------
    def run(self):
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)        # creates this thread's message queue
        self._tid = kernel32.GetCurrentThreadId()
        user32.SetTimer(None, 0, 60_000, None)
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            try:
                if msg.message == WM_HOTKEY:
                    combo = self._registered.get(msg.wParam)
                    if combo and not self._recording:
                        self.on_fire(combo)
                elif msg.message == WM_COMMAND_QUEUE:
                    if not self._drain():
                        break
                elif msg.message == WM_TIMER:
                    if self._mouse_hook:
                        self._unhook_mouse()
                        self._update_hooks()
            except Exception:
                log.exception("hotkey thread")
        self._unregister_all()
        self._unhook_mouse()
        self._unhook_kb()

    def _drain(self) -> bool:
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return True
            if cmd[0] == "quit":
                return False
            if cmd[0] == "set":
                self._combos = cmd[1]
                cmd[2].put(self._apply())
            elif cmd[0] == "record":
                self._start_recording(cmd[1])
            elif cmd[0] == "cancel":
                self._finish_recording(None)
            elif cmd[0] == "hooks":
                self._update_hooks()

    # -- bindings ----------------------------------------------------------------------------
    def _unregister_all(self):
        for hid in list(self._registered):
            user32.UnregisterHotKey(None, hid)
        self._registered.clear()

    def _apply(self) -> dict[str, str]:
        self._unregister_all()
        self._mouse_combos.clear()
        errors = {}
        if not self._recording:
            for i, s in enumerate(self._combos, start=1):
                c = Combo.parse(s)
                if c is None:
                    errors[s] = "not a combo this app understands"
                elif c.problem():
                    errors[s] = c.problem()
                elif c.is_mouse:
                    self._mouse_combos.add(s)
                elif user32.RegisterHotKey(None, i, c.mod_flags | MOD_NOREPEAT, c.vk):
                    self._registered[i] = s
                else:
                    err = ctypes.get_last_error()
                    errors[s] = ("already used by Windows or another app" if err == ERROR_HOTKEY_ALREADY_REGISTERED
                                 else f"Windows refused it (error {err})")
        self._update_hooks()
        if errors:
            log.warning("hotkeys not bound: %s", errors)
        return errors

    def _update_hooks(self):
        want_mouse = bool(self._mouse_combos or self._recording or self._swallow_up)
        if want_mouse and not self._mouse_hook:
            self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, self._hmod, 0) or None
            if not self._mouse_hook:
                log.error("mouse hook failed: %s", ctypes.get_last_error())
        elif not want_mouse:
            self._unhook_mouse()
        if self._recording and not self._kb_hook:
            self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, self._hmod, 0) or None
        elif not self._recording:
            self._unhook_kb()

    def _unhook_mouse(self):
        if self._mouse_hook:
            user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None

    def _unhook_kb(self):
        if self._kb_hook:
            user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None

    # -- recording ---------------------------------------------------------------------------
    def _start_recording(self, callback):
        if self._recording:
            self._recording(None)
        self._recording = callback
        self._apply()                    # unregisters everything, so any combo can be pressed and none fire

    def _finish_recording(self, combo):
        cb, self._recording = self._recording, None
        if cb is None:
            return
        self._apply()
        try:
            cb(combo)
        except Exception:
            log.exception("record callback")

    # -- hook callbacks (keep these quick: Windows drops slow hooks) ---------------------------------
    def _on_mouse(self, code, wparam, lparam):
        try:
            if code == 0:
                info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                trigger, press = _mouse_event(wparam, info.mouseData)
                if trigger and self._mouse_event(trigger, press):
                    return 1
        except Exception:
            log.exception("mouse hook")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _mouse_event(self, trigger, press) -> bool:
        """True = swallow this event."""
        if not press:
            if trigger in self._swallow_up:
                self._swallow_up.discard(trigger)
                if not self._swallow_up:
                    self._send("hooks")
                return True
            return False
        mods = held_modifiers()
        combo = Combo.make(mods, trigger)
        if self._recording:
            if combo.problem():
                return False                # a plain click: let it through (the Cancel button still works)
            self._swallow_press(trigger, mods)
            self._finish_recording(str(combo))
            return True
        if str(combo) in self._mouse_combos:
            self._swallow_press(trigger, mods)
            self.on_fire(str(combo))
            return True
        return False

    def _swallow_press(self, trigger, mods):
        if not trigger.startswith("wheel"):
            self._swallow_up.add(trigger)
        _tap_dummy_if_win(mods)

    def _on_key(self, code, wparam, lparam):
        try:
            if code == 0 and self._recording:
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = info.vkCode
                if vk not in MODIFIER_VKS and vk != VK_DUMMY:     # injected keys count: macro keys send those
                    if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        mods = held_modifiers()
                        _tap_dummy_if_win(mods)
                        self._finish_recording(None if (vk == VK_ESCAPE and not mods) else str(Combo.make(mods, vk_name(vk))))
                    return 1                 # the key never reaches the settings window (no stray Alt+F4)
        except Exception:
            log.exception("keyboard hook")
        return user32.CallNextHookEx(None, code, wparam, lparam)
