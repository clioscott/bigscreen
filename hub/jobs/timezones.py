"""Time zone map: US/Canada offsets for a year from the newest tzdata, plus map rasters → data/tzmap.json

Also data/tzmap/*.bin (drawn by display/modules/tzmap.py), and a check for rule changes.

Each run (every 6 h by default) checks PyPI for a newer tzdata and GitHub for newer boundaries. When the rules
version changes, the zones whose offsets differ from the previous version are recorded (``rule_updates``, shown on
the control panel). The rasters are rebuilt only when the boundaries, the wall's layout or the rasteriser change.
"""

from __future__ import annotations

import time

import numpy as np

from hub import paths, timezones as tz
from hub.jobs import _harness
from hub.util import atomic_write_json, read_json

OUT = paths.DATA_DIR / "tzmap"
STATE = tz.CACHE / "state.json"
HORIZON_DAYS = 400
CLUSTER_GAP = 26 * 3600            # transitions closer than this are one night's clock change


def zone_sizes(cfg) -> dict:
    """Each place the screen can play, as on the wall (display/main.py: ticker + 1 px above, 17 px strip below)."""
    L = cfg.get("layout") or {}
    w, h = int(L.get("width", 448)), int(L.get("height", 192))
    main_h = h - 17 - (int(L.get("top_bar_height", 44)) + 1)
    cal_w = int(w * float(L.get("calendar_split_ratio", 0.66)))
    out = {}
    for zw, zh in ((w, h), (w, main_h), (cal_w, main_h), (w - cal_w - 1, main_h)):
        out[f"{zw}x{zh}"] = (zw, zh)
    return out


def _west_to_east(pack, ids):
    def lon(z):
        ring = pack["zones"][z][0][0] if pack["zones"].get(z) else [[0, 0]]
        return sum(p[0] for p in ring) / len(ring)
    return sorted((z for z in ids if pack["zones"].get(z)), key=lon)


def _at(sched, t):
    cur = sched[0]
    for s in sched:
        if s[0] <= t:
            cur = s
        else:
            break
    return cur


def build_states(scheds, zones, t0):
    """Every distinct moment's offsets: [{from, offs, abbrs}] (zones in ``zones`` order)."""
    times = sorted({s[0] for z in zones for s in scheds[z][1:]})
    out = []
    for t in [int(t0)] + times:
        rows = [_at(scheds[z], t) for z in zones]
        out.append({"from": t, "offs": [r[1] for r in rows], "abbrs": [r[2] for r in rows]})
    return out


def build_changes(states, zone_objs):
    """One night's transitions as one change: [{start, end, changed (zone indices whose offset changes), new (zones
    whose offset afterwards differs from a year before — rules new this year)}]."""
    out = []
    for i in range(1, len(states)):
        t = states[i]["from"]
        if out and t - out[-1]["end"] <= CLUSTER_GAP:
            out[-1]["end"] = t
        else:
            out.append({"start": t, "end": t, "before": i - 1})
    for c in out:
        before = states[c.pop("before")]
        after = next(s for s in reversed(states) if s["from"] <= c["end"])
        c["changed"] = [j for j, (a, b) in enumerate(zip(before["offs"], after["offs"])) if a != b]
        later = c["end"] + 7 * 86400
        c["new"] = [j for j, z in enumerate(zone_objs)
                    if tz._offset(z, later)[0] != tz._offset(z, later - 364 * 86400)[0]]
    return out


def run(cfg):
    tz.CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    st = read_json(STATE, {}) or {}
    errors = []
    check = tz.check_tzdata()
    if check["error"]:
        errors.append(check["error"])
    root, version, source = tz.rules_root()
    ids = tz.zone_ids(root)
    try:
        tag = tz.latest_boundaries_tag()
    except Exception as e:
        tag = None
        errors.append(f"GitHub: {e}")
    _harness.progress(1, 4, "boundaries")
    pack, err = tz.boundaries(set(ids), tag)
    if err:
        errors.append(err)
    zones = _west_to_east(pack, ids)
    zone_objs = [tz.zone(root, z) for z in zones]
    now = time.time()
    t0 = now - 86400
    until = now + HORIZON_DAYS * 86400
    scheds = {z: tz.schedule(o, t0, until) for z, o in zip(zones, zone_objs)}
    _harness.progress(2, 4, "rules")

    updates = st.get("rule_updates") or []
    prev = st.get("rules_version")
    if prev and prev != version:
        diff = tz.rule_diff(st.get("rules_sched") or {}, scheds, now, min(until, st.get("rules_until") or until))
        updates.insert(0, {"at": now, "from": prev, "to": version, "changes": diff})
    if st.get("boundaries") and st["boundaries"] != pack["tag"]:
        updates.insert(0, {"at": now, "boundaries_from": st["boundaries"], "boundaries_to": pack["tag"], "changes": []})
    st.update(rules_version=version, rules_sched=scheds, rules_until=until, boundaries=pack["tag"], rule_updates=updates[:12])

    states = build_states(scheds, zones, t0)
    changes = build_changes(states, zone_objs)

    sizes = zone_sizes(cfg)
    sig = [tz.RASTER_VERSION, pack["tag"], sorted(sizes), zones]
    old = read_json(paths.DATA_DIR / "tzmap.json", {}) or {}
    maps = old.get("maps") or {}
    if st.get("raster_sig") != sig or not all((OUT / m.get("idx", "-")).exists() for m in maps.values()) \
            or set(maps) != set(sizes):
        geo = tz.geodata()
        maps = {}
        for key, (zw, zh) in sizes.items():
            idx, lines, frames, rect = tz.rasterize(zw, zh, pack, zones, geo)
            for kind, arr in (("idx", idx), ("lines", lines)):
                tmp = OUT / f"{key}.{kind}.bin.tmp"
                arr.tofile(tmp)
                tmp.replace(OUT / f"{key}.{kind}.bin")
            maps[key] = {"key": key, "zone": [zw, zh], "rect": rect, "size": [rect[2], rect[3]], "frames": frames,
                         "idx": f"{key}.idx.bin", "lines": f"{key}.lines.bin",
                         "crc": {"idx": tz.crc(idx), "lines": tz.crc(lines)}}
        st["raster_sig"] = sig
        for p in OUT.iterdir():
            if not any(p.name in (m["idx"], m["lines"]) for m in maps.values()):
                p.unlink(missing_ok=True)
    _harness.progress(3, 4, "labels")

    # where each offset's time goes on the map, per distinct set of offsets
    idxs = {k: np.fromfile(OUT / m["idx"], np.uint8).reshape(m["size"][1], m["size"][0]) for k, m in maps.items()}
    memo = {}
    for s in states:
        key = tuple(s["offs"])
        if key not in memo:
            memo[key] = {k: tz.anchors(a, s["offs"]) for k, a in idxs.items()}
        s["anchors"] = memo[key]

    atomic_write_json(STATE, st)
    atomic_write_json(paths.DATA_DIR / "tzmap.json", {
        "generated_at": now, "rules": {"version": version, "source": source, "latest": check.get("latest"),
                                        "checked_at": now, "error": check["error"]},
        "boundaries": pack["tag"], "zones": [{"id": z, "area": tz.area(pack["zones"][z])} for z in zones], "states": states, "changes": changes,
        "maps": maps, "rule_updates": updates[:12]})
    return {"ok": True, "tzdata": version, "source": source, "boundaries": pack["tag"], "zones": len(zones),
            "changes": len(changes), "errors": errors[:3]}


if __name__ == "__main__":
    _harness.main(run)
