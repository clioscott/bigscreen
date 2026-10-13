"""Everything the tray can do to the wall, as plain dicts so hotkeys can store them.

    {"do": "next" | "prev", "side": "left" | "right" | "both"}
    {"do": "hold", "side": "left" | "right"}                   hold the screen there / let it finish (toggle)
    {"do": "play", "side": ..., "entry": {"panel", "track"?, "weather_screen"?}}
                                                               play once, then the rotation resumes
    {"do": "normal"}                                           end every manual play and hold
    {"do": "news", "delta": 1 | -1}  /  {"do": "news", "category": "Technology"}
    {"do": "open_panel"}  /  {"do": "settings"}                local
"""

from __future__ import annotations

import json

from .hub import SIDES, Hub, HubError, Wall, entry_key

SIDE_NAMES = {"left": "Left", "right": "Right", "both": "Both sides"}


def action_id(a: dict) -> str:
    return json.dumps(a, sort_keys=True)


def label(a: dict, wall: Wall | None = None) -> str:
    """How an action reads in the hotkey list (and in notifications)."""
    do, side = a.get("do"), SIDE_NAMES.get(a.get("side"), "")
    if do == "next":
        return "Next screen on both sides" if a.get("side") == "both" else f"{side}: next screen"
    if do == "prev":
        return "Previous screen on both sides" if a.get("side") == "both" else f"{side}: previous screen"
    if do == "hold":
        return f"{side}: hold this screen / let it go"
    if do == "play":
        return f"{side}: play {(wall or Wall()).title(a.get('entry'))} once"
    if do == "normal":
        return "Back to normal rotation"
    if do == "news":
        if a.get("category"):
            return f"News: show {a['category']}"
        return "News: next category" if a.get("delta", 1) > 0 else "News: previous category"
    if do == "open_panel":
        return "Open the control panel"
    if do == "settings":
        return "Open tray settings"
    return do or "?"


def catalog(wall: Wall) -> list[tuple[str, list[dict]]]:
    """Every bindable action, in sections, for the current rotation and categories."""
    sections = [("General", [{"do": "normal"}, {"do": "next", "side": "both"}, {"do": "prev", "side": "both"},
                             {"do": "open_panel"}, {"do": "settings"}])]
    for side in SIDES:
        acts = [{"do": "next", "side": side}, {"do": "prev", "side": side}, {"do": "hold", "side": side}]
        acts += [{"do": "play", "side": side, "entry": entry_key(e)} for e in wall.entries(side)]
        sections.append((f"{SIDE_NAMES[side]} side", acts))
    sections.append(("News", [{"do": "news", "delta": 1}, {"do": "news", "delta": -1}]
                     + [{"do": "news", "category": c} for c in wall.categories]))
    return sections


def run(hub: Hub, a: dict) -> str | None:
    """Do a wall action. Returns a short note for the user when there's something to say; raises HubError."""
    do = a.get("do")
    side = a.get("side")
    if do in ("next", "prev"):
        hub.post("/api/display/rotation", {"action": do} if side == "both" else {"action": do, "side": side})
    elif do == "hold":
        held = Wall(hub.display_status()).is_held(side)
        hub.post("/api/display/rotation", {"action": "release" if held else "hold", "side": side})
    elif do == "play":
        want = json.dumps(entry_key(a.get("entry") or {}), sort_keys=True)
        try:
            entries = Wall(hub.display_status()).entries(side)
        except HubError:
            entries = []
        # the rotation's own settings for that entry (dwell, cycles…), or just the panel if it's gone from it
        entry = next((e for e in entries if json.dumps(entry_key(e), sort_keys=True) == want), a.get("entry") or {})
        entry = {k: v for k, v in entry.items() if k != "enabled"}
        hub.post("/api/display/play", {"side": side, "entry": entry, "hold": False})
    elif do == "normal":
        hub.post("/api/display/override/clear", {})
    elif do == "news":
        if a.get("category"):
            cats = Wall(hub.display_status()).categories
            if a["category"] not in cats:
                return f"There's no “{a['category']}” news category any more"
            hub.post("/api/display/headlines/jump", {"index": cats.index(a["category"])})
        else:
            hub.post("/api/display/headlines/jump", {"delta": int(a.get("delta") or 1)})
    else:
        raise ValueError(f"not a wall action: {do}")
    return None
