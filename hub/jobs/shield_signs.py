"""The real highway signs the webcam captions use → data/shields/*.png (hub/shield_signs.py).

Each sign is fetched from Wikimedia Commons once and kept, so after the first run this only checks that the files
are there. The control panel's preview also renders a sign the moment it's picked; this catches the rest.
"""

from __future__ import annotations

from hub import shield_signs
from hub.jobs import _harness


def run(cfg):
    want = shield_signs.wanted(cfg)
    have, missing = 0, []
    for route, kind in sorted(want, key=str):
        if shield_signs.ensure(route, kind):
            have += 1
        else:
            missing.append(route)
    return {"ok": True, "signs": have, "no_sign": missing}


if __name__ == "__main__":
    _harness.main(run)
