"""The real highway signs the webcam captions use → data/shields/*.png (hub/shield_signs.py).

Each sign is fetched from Wikimedia Commons once and kept, so after the first run this only checks that the files
are there. The control panel's preview also renders a sign the moment it's picked; this catches the rest.
"""

from __future__ import annotations

from hub import shield_signs
from hub.jobs import _harness


def run(cfg):
    want = shield_signs.wanted(cfg)
    have, missing, errors = 0, [], {}
    for route, kind in sorted(want, key=str):
        try:                                               # one bad route (Commons down, a broken SVG) skips only itself
            if shield_signs.ensure(route, kind):
                have += 1
            else:
                missing.append(route)
        except Exception as e:                             # noqa: BLE001
            errors[route] = f"{type(e).__name__}: {e}"[:200]
    return {"ok": not errors, "signs": have, "no_sign": missing, **({"errors": errors} if errors else {})}


if __name__ == "__main__":
    _harness.main(run)
