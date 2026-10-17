"""One-off: put the alert lifecycle into a config's rotation (2026-10-17).

    python -m tools.alert_lifecycle_rotation [--dry-run]       # the hub's config, saved through hub.config (backed up)
    python -m tools.alert_lifecycle_rotation --file path.json  # edit a config file in place (dev copies)

Full-wall alert entries (weather, Alert Ready, DriveBC, earthquakes, tsunami, wildfire) get ``phase: "full"`` on
their alert rule; a half-width entry for each is added to the right side just before the first weather map; and
``modules.alert_lifecycle`` gets its defaults. Running it twice changes nothing.
"""

from __future__ import annotations

import json
import sys

FULL = {"weather_alert": "alert", "tsunami_alert": "tsunami", "quake_alert": "quakes", "naad_alert": "naad",
        "fire_alert": "wildfire", "road_alert": "roads"}


def migrate(cfg):
    mods = cfg.setdefault("modules", {})
    mods.setdefault("alert_lifecycle", {"full_showings": 3, "full_minutes": 30, "half": True})
    right = mods.setdefault("layout_cycle", {}).setdefault("right", [])
    changed = []
    for e in right:
        rule = FULL.get(e.get("panel"))
        if not rule:
            continue
        if e.get("when") == "weather_alert_active" or (not e.get("rules") and e.get("panel") == "weather_alert"):
            e.pop("when", None)
            e["rules"] = [{"if": "alert"}]
        for r in e.setdefault("rules", []):
            if r.get("if") == rule and not r.get("phase"):
                r["phase"] = "full"
                changed.append(e["panel"])
        e.setdefault("priority", "next")
    have = {e.get("panel") for e in right}
    at = next((i for i, e in enumerate(right) if str(e.get("panel", "")).startswith("map")), len(right))
    for panel, rule in FULL.items():
        half = panel + "_half"
        if half in have:
            continue
        right.insert(at, {"panel": half, "rules": [{"if": rule, "phase": "half"}], "advance": "cycles", "cycles": 1, "max_sec": 300})
        at += 1
        changed.append(half)
    return changed


def main():
    args = sys.argv[1:]
    if "--file" in args:
        p = args[args.index("--file") + 1]
        cfg = json.load(open(p))
        ch = migrate(cfg)
        json.dump(cfg, open(p, "w"), indent=2)
        print("changed:", ch)
        return
    from hub import config
    cfg = config.load()
    ch = migrate(cfg)
    print("changed:", ch)
    if ch and "--dry-run" not in args:
        s = config.save(cfg, reason="alert lifecycle: full-wall then half-width entries")
        print("saved r", s["revision"])


if __name__ == "__main__":
    main()
