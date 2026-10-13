"""Helpers for install.ps1 (run with the installed venv's python):

    python -m bigscreen_tray.install find-hub [URL ...]     prints "URL<TAB>what's on the wall"; exit 1 if none answer
    python -m bigscreen_tray.install configure --hub URL [--autostart | --no-autostart]
    python -m bigscreen_tray.install show-icon              pin the tray icon next to the clock (Windows 11)
    python -m bigscreen_tray.install remove-autostart
"""

from __future__ import annotations

import argparse
import sys
import time
import winreg

from . import settings
from .hub import SIDES, Hub, HubError, Wall


def normalise_url(u: str) -> str:
    u = u.strip().rstrip("/")
    if "://" not in u:
        u = "http://" + u
    if u.count(":") < 2:
        u += ":8800"
    return u


def find_hub(candidates) -> int:
    seen = []
    for c in [*candidates, settings.Settings().hub_url, settings.DEFAULT_HUB, "http://MAGI-server:8800"]:
        if not (c or "").strip():
            continue
        u = normalise_url(c)
        if u in seen:
            continue
        seen.append(u)
        try:
            h = Hub(u, timeout=4)
            w = Wall(h.display_status(), h.track_labels())
        except HubError as e:
            print(f"# {u}: {e}", file=sys.stderr)
            continue
        print(f"{u}\t" + ", ".join(f"{s} side showing {w.describe(s)}" for s in SIDES))
        return 0
    return 1


def configure(hub: str, autostart: bool | None):
    s = settings.Settings()
    s.hub_url = normalise_url(hub)
    s.save()
    if autostart is not None:
        settings.set_autostart(autostart)


def show_icon(wait_sec=15) -> int:
    """Windows 11 hides new tray icons in the overflow (^). Each icon gets a key under NotifyIconSettings once
    it has been shown; IsPromoted=1 puts it on the taskbar."""
    root = r"Control Panel\NotifyIconSettings"
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    with winreg.OpenKey(k, sub, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as sk:
                        try:
                            tip = winreg.QueryValueEx(sk, "InitialTooltip")[0]
                            exe = winreg.QueryValueEx(sk, "ExecutablePath")[0]
                        except OSError:
                            continue
                        if str(tip).startswith("BigScreen") and "pythonw" in str(exe).lower():
                            winreg.SetValueEx(sk, "IsPromoted", 0, winreg.REG_DWORD, 1)
                            return 0
        except OSError:
            return 1                  # not Windows 11 (no such key): nothing to do
        time.sleep(1)
    return 1


def main():
    p = argparse.ArgumentParser(prog="bigscreen_tray.install")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find-hub")
    f.add_argument("urls", nargs="*")
    c = sub.add_parser("configure")
    c.add_argument("--hub", required=True)
    c.add_argument("--autostart", dest="autostart", action="store_true", default=None)
    c.add_argument("--no-autostart", dest="autostart", action="store_false")
    sub.add_parser("show-icon")
    sub.add_parser("remove-autostart")
    a = p.parse_args()
    if a.cmd == "find-hub":
        return find_hub(a.urls)
    if a.cmd == "configure":
        configure(a.hub, a.autostart)
        return 0
    if a.cmd == "show-icon":
        return show_icon()
    if a.cmd == "remove-autostart":
        settings.set_autostart(False)
        return 0


if __name__ == "__main__":
    sys.exit(main())
