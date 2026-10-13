# BigScreen Tray

A system-tray icon for controlling the wall from a Windows PC, plus optional global hotkeys.

**Install / update:** double-click `Install BigScreen Tray.cmd`. It finds Python, installs into
`%LOCALAPPDATA%\BigScreenTray`, finds the hub, asks about starting with Windows, starts the icon, and pins it
next to the clock. Run it again to update; your settings and hotkeys are kept.
**Remove:** `Uninstall BigScreen Tray.cmd`.

Click the icon (left or right) for the menu:

- **Left side / Right side**: next, previous, hold this screen, or play any rotation entry once.
- **News**: next/previous category or a specific one.
- **Back to normal rotation**: ends every manual play and hold.

Anything played from here plays once, then the rotation picks up where it was. Only *Hold* keeps a screen up.

**Hotkeys & settings…**: click a box, then press a key combo or a mouse button (side buttons work; left and right
click and the wheel need a modifier). Combos already owned by another app are refused. The same window has
the hub address and the start-with-Windows switch.

## How it works

It's a thin client of the hub's `/api/display/*` endpoints (the same ones the web panel uses). It polls
`/api/display/status` every 4 s for the tooltip and menu. Code is in `bigscreen_tray/`:

| file | |
|---|---|
| `app.py` | wiring: poller, action worker, notifications |
| `actions.py` | every action as a dict (what hotkeys store) and how it's sent to the hub |
| `winput.py` | hotkeys: `RegisterHotKey` for keys, a low-level mouse hook only while mouse combos are bound, recording |
| `trayicon.py` | icon image and menu (pystray, with left-click opening the menu) |
| `settings_window.py` | tkinter settings / hotkey recorder |
| `install.py` | helpers for `install.ps1` |

Dev run: `.venv\Scripts\python -m bigscreen_tray --settings` from this folder (log: `%LOCALAPPDATA%\BigScreenTray\tray.log`).
Commands run from inside the Claude desktop app are sandboxed, so `%LOCALAPPDATA%` and HKCU writes land in
its package and not in your real profile. Run the installer yourself.
