"""CAP 1.2 / CAP-CP parsing (Alert Ready messages and NTWC tsunami bulletins)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime


def _tag(e):
    return e.tag.split("}", 1)[-1]


def _kids(e, name):
    return [k for k in e if _tag(k) == name]


def _text(e, name):
    for k in e:
        if _tag(k) == name:
            return (k.text or "").strip()
    return ""


def ts(s):
    """CAP time (2026-10-16T18:00:52-00:00) → epoch seconds."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _pairs(e, name):
    out = {}
    for k in _kids(e, name):
        n, v = _text(k, "valueName"), _text(k, "value")
        if n:
            out.setdefault(n, []).append(v)
    return out


def parse(xml_bytes, prefer_lang="en"):
    """One CAP alert → dict (the preferred-language info block, all areas merged)."""
    root = ET.fromstring(xml_bytes)
    a = {
        "identifier": _text(root, "identifier"), "sender": _text(root, "sender"), "sent": ts(_text(root, "sent")),
        "sent_raw": _text(root, "sent"), "status": _text(root, "status"), "msg_type": _text(root, "msgType"),
        "scope": _text(root, "scope"), "source": _text(root, "source"), "note": _text(root, "note"),
        "codes": [(_k.text or "").strip() for _k in _kids(root, "code")],
        "references": [r.split(",") for r in _text(root, "references").split() if r.count(",") == 2],
        "incidents": _text(root, "incidents"),
    }
    infos = _kids(root, "info")
    info = next((i for i in infos if _text(i, "language").lower().startswith(prefer_lang)), infos[0] if infos else None)
    if info is None:
        return a
    ev_codes = _pairs(info, "eventCode")
    params = _pairs(info, "parameter")
    areas, polygons, circles, geocodes = [], [], [], []
    for ar in _kids(info, "area"):
        d = _text(ar, "areaDesc")
        if d and d not in areas:
            areas.append(d)
        for p in _kids(ar, "polygon"):
            ring = []
            for pt in (p.text or "").split():
                try:
                    la, lo = pt.split(",")
                    ring.append([float(lo), float(la)])
                except ValueError:
                    pass
            if len(ring) >= 3:
                polygons.append(ring)
        for cc in _kids(ar, "circle"):
            try:
                ctr, rad = (cc.text or "").split()
                la, lo = ctr.split(",")
                circles.append([float(lo), float(la), float(rad)])
            except ValueError:
                pass
        for g in _kids(ar, "geocode"):
            v = _text(g, "value")
            if v:
                geocodes.append(v)
    a.update({
        "language": _text(info, "language"), "category": _text(info, "category"), "event": _text(info, "event"),
        "event_code": (ev_codes.get("profile:CAP-CP:Event:0.4") or ev_codes.get("profile:CAP-CP:Event:0.3") or [""])[0],
        "event_codes": ev_codes, "response": _text(info, "responseType"), "urgency": _text(info, "urgency"),
        "severity": _text(info, "severity"), "certainty": _text(info, "certainty"), "audience": _text(info, "audience"),
        "effective": ts(_text(info, "effective")), "onset": ts(_text(info, "onset")), "expires": ts(_text(info, "expires")),
        "sender_name": _text(info, "senderName"), "headline": _text(info, "headline"),
        "description": _text(info, "description"), "instruction": _text(info, "instruction"), "web": _text(info, "web"),
        "contact": _text(info, "contact"), "params": params, "areas": areas, "polygons": polygons, "circles": circles,
        "geocodes": geocodes,
    })
    return a


def param(a, name, default=""):
    for k, v in (a.get("params") or {}).items():
        if k.endswith(name) and v:
            return v[0]
    return default
