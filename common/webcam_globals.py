"""Settings that apply to every webcam at once (the Webcams page's "For every webcam" card).

``modules.webcams.global`` holds five groups, each with ``on`` and its values:

    colour   {"on", "sharpen", "brightness", "contrast", "saturation"}
    sec      {"on", "value"}                      how long each webcam shows in the list
    caption  {"on", ...CAPTION_KEYS}              where the caption sits and how it looks (not what it says)
    flags    {"on", "show", "corner", "size", "opacity"}   where the flags sit (not which flags)
    quality  {"on", "up_to": 1080 | 1440 | 2160}  the source resolution: the stream's best up to that

A group that is on replaces those values on every webcam, whatever the webcam has stored, except a webcam that keeps
its own for that group (``cams.<id>.own`` lists the groups). Nothing stored on a webcam is changed: switch a group
off and each webcam is back on its own settings.

Used by the display (display/modules/webcam.py) and the hub (hub/app.py) alike, so both see the same webcam.
"""

GROUPS = ("colour", "sec", "caption", "flags", "quality")
COLOUR_KEYS = ("sharpen", "brightness", "contrast", "saturation")
CAPTION_KEYS = ("style", "corner", "when", "font", "accent", "text", "bg", "opacity", "time_format", "fade",
                "show_sec", "every_min", "separator", "uppercase")
FLAG_KEYS = ("corner", "size", "opacity")
UP_TO = (1080, 1440, 2160)
UP_TO_PREFIX = "upto"         # an effective webcam's quality is "upto1440": hub/webcams.py cap_of()


def settings(webcams_cfg) -> dict:
    """modules.webcams.global as a dict of groups (empty when there is none)."""
    g = (webcams_cfg or {}).get("global") if isinstance(webcams_cfg, dict) else None
    return g if isinstance(g, dict) else {}


def applies(g, group, cam=None) -> bool:
    """Is this group set for every webcam, and does this webcam follow it?"""
    grp = (g or {}).get(group)
    if not isinstance(grp, dict) or not grp.get("on"):
        return False
    own = cam.get("own") if isinstance(cam, dict) else None
    return not (isinstance(own, (list, tuple)) and group in own)


def effective(cam, g):
    """The webcam as the wall should play it: its own settings with every global group that applies laid over them.
    The same object when nothing applies (so caches keyed on it keep working)."""
    if not isinstance(cam, dict) or not g:
        return cam
    out = None

    def copy():
        nonlocal out
        if out is None:
            out = dict(cam)
        return out

    if applies(g, "colour", cam):
        col = dict(cam.get("colour") or {})
        col.update({k: g["colour"][k] for k in COLOUR_KEYS if g["colour"].get(k) is not None})
        copy()["colour"] = col
    cap = {k: g["caption"][k] for k in CAPTION_KEYS if g["caption"].get(k) is not None} if applies(g, "caption", cam) else {}
    fl = None
    if applies(g, "flags", cam):
        fl = {k: g["flags"][k] for k in FLAG_KEYS if g["flags"].get(k) is not None}
        fl["on"] = bool(g["flags"].get("show", True))
    if cap or fl is not None:
        def lay(ov):
            ov = dict(ov or {})
            ov.update(cap)
            if fl is not None:
                ov["flags"] = {**(ov.get("flags") if isinstance(ov.get("flags"), dict) else {}), **fl}
            return ov
        o = copy()
        o["overlay"] = lay(cam.get("overlay"))
        per = cam.get("overlays")
        if isinstance(per, dict):                             # a size with a caption of its own follows too
            o["overlays"] = {k: lay(v) if isinstance(v, dict) else v for k, v in per.items()}
    if applies(g, "quality", cam):
        try:
            cap_h = int(g["quality"].get("up_to") or 0)
        except (TypeError, ValueError):
            cap_h = 0
        if cap_h >= 144:
            copy()["quality"] = f"{UP_TO_PREFIX}{cap_h}"
    return out if out is not None else cam


def cams_of(webcams_cfg) -> dict:
    """Every webcam, effective: {id: webcam}."""
    cams = (webcams_cfg or {}).get("cams") if isinstance(webcams_cfg, dict) else None
    if not isinstance(cams, dict):
        return {}
    g = settings(webcams_cfg)
    return {cid: effective(cam, g) for cid, cam in cams.items()} if g else cams


def seconds(webcams_cfg, cam_id, own_sec):
    """How long this webcam shows in the list: the global length when that is on and the webcam follows it, else
    its own (``own_sec``, as the list has it)."""
    g = settings(webcams_cfg)
    cam = ((webcams_cfg or {}).get("cams") or {}).get(cam_id) if isinstance(webcams_cfg, dict) else None
    if applies(g, "sec", cam):
        v = g["sec"].get("value")
        if v is not None:
            return v
    return own_sec
