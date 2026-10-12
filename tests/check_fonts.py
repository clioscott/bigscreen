"""Every font the weather-alert and Japan EEW themes can draw with must be approved on the Fonts page.

    PYTHONPATH=. python tests/check_fonts.py [config.json]

Reads verdicts from the config (hub.fonts.status), collects fonts from the alert families, the EEW roles and
theme overrides, and any "alert/…" / "candidates/…" font path written in the theme code. Exit 1 on any miss.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
cfg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/bigscreen-state/config.json")
status = ((json.load(open(cfg_path)).get("hub") or {}).get("fonts") or {}).get("status") or {}
approved = {p for p, v in status.items() if v == "approved"}

from display.modules import alert_themes, quake  # noqa: E402

used = {}


def add(path, where):
    used.setdefault(path, set()).add(where)


for fid, fam in alert_themes.FAMILIES.items():
    for p, _ in fam["title"]:
        add(p, f"alert family {fid}")
    add(fam["key"], f"alert family {fid}")
    add(fam["small"], f"alert family {fid}")
    for r, b, _ in fam["ladder"]:
        add(r, f"alert family {fid}")
        if b:
            add(b, f"alert family {fid}")
for role, (p, scale) in quake.BASE_FONTS.items():
    add(p, f"EEW role {role}")
    assert scale == 1, f"EEW role {role} is scaled ×{scale}"
for tid, th in quake.THEMES.items():
    for role, (p, scale) in (th.get("fonts") or {}).items():
        add(p, f"EEW theme {tid}.{role}")
        assert scale == 1, f"EEW theme {tid}.{role} is scaled ×{scale}"
for mod in ("weather_alert.py", "alert_designs.py", "quake.py", "alert_themes.py"):
    src = open(os.path.join(ROOT, "display", "modules", mod), encoding="utf-8").read()
    for p in re.findall(r'"((?:alert|candidates)/[\w.+-]+\.bdf)"', src):
        add(p, mod)
    for name in re.findall(r'_jpf\("([\w.+-]+)"\)', src):
        add(quake._jpf(name)[0], mod)

bad = {p: w for p, w in used.items() if p not in approved}
missing = {p: w for p, w in used.items() if not os.path.exists(os.path.join(ROOT, "assets", "fonts", p))}
print(f"{len(used)} fonts used by alert / EEW themes; {len(bad)} not approved; {len(missing)} missing")
for p, w in sorted(bad.items()):
    print("  NOT APPROVED", p, "—", ", ".join(sorted(w)))
for p, w in sorted(missing.items()):
    print("  MISSING", p, "—", ", ".join(sorted(w)))
sys.exit(1 if bad or missing else 0)
