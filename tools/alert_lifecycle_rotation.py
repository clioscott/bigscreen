"""Put the alert lifecycle into a config's rotation: one entry per alert screen (2026-10-17, combined 2026-10-19).

    python -m tools.alert_lifecycle_rotation [--dry-run]       # the hub's config, saved through hub.config (backed up)
    python -m tools.alert_lifecycle_rotation --file path.json  # edit a config file in place (dev copies)

Each full-wall alert entry (weather, Alert Ready, DriveBC, earthquakes, tsunami, wildfire) comes up while its screen
has anything to show, and plays full screen while an alert is new (not yet shown enough); otherwise it plays half
width on the right, through a ``variants`` item (display/rotation.py). Older configs had a separate
``<panel>_half`` entry: it is folded into the full entry's variant (keeping its time cap) and removed.
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
    lc = mods.setdefault("layout_cycle", {})
    right = lc.setdefault("right", [])
    changed = []
    for panel, cond in FULL.items():
        half = panel + "_half"
        full = next((e for e in right if e.get("panel") == panel), None)
        halves = [e for e in right if e.get("panel") == half]
        if full is None:
            continue                                           # no full-wall entry: leave any half entry alone
        if full.get("when") == "weather_alert_active" or (not full.get("rules") and panel == "weather_alert"):
            full.pop("when", None)
            full["rules"] = [{"if": "alert"}]
        rules = full.setdefault("rules", [])
        rule = next((r for r in rules if r.get("if") == cond and not r.get("not")), None)
        if rule is None:
            rule = {"if": cond}
            rules.append(rule)
        if rule.get("phase"):                                  # comes up at either stage; the variant picks the size
            rule.pop("phase")
            changed.append(panel)
        full.setdefault("priority", "next")
        variants = full.setdefault("variants", [])
        if not any(v.get("panel") == half for v in variants):
            cap = next((h.get("max_sec") for h in halves if h.get("max_sec")), 300)
            params = {k: v for k, v in rule.items() if k not in ("phase", "not")}
            variants.append({"panel": half, "max_sec": cap, "rules": [{**params, "phase": "full", "not": True}]})
            changed.append(f"{panel} + half width")
        if halves:
            right[:] = [e for e in right if e.get("panel") != half]
            changed.append(f"{half} folded in")
    return changed


def main():
    args = sys.argv[1:]
    if "--file" in args:
        p = args[args.index("--file") + 1]
        cfg = json.load(open(p, encoding="utf-8"))
        ch = migrate(cfg)
        json.dump(cfg, open(p, "w", encoding="utf-8"), indent=2)
        print("changed:", ch)
        return
    from hub import config
    cfg = config.load()
    ch = migrate(cfg)
    print("changed:", ch)
    if ch and "--dry-run" not in args:
        s = config.save(cfg, reason="alert lifecycle: one entry per alert screen, half width as its variant")
        print("saved r", s["revision"])


if __name__ == "__main__":
    main()
