"""Weather maps job: keep the radar archive current, then re-render tracks that are due.

Runs every few minutes. Each track re-renders when its refresh_min has passed
or its settings changed. The radar archive is updated once per run for every
area used by a radar or accumulation track, keeping as many hours as the
longest window needs (up to 48 h).
"""

import hashlib
import json
import time

from hub import paths
from hub.jobs import _harness
from hub.util import atomic_write_json, read_json
from hub.weather import forecast, geo, radar, tracks

STATE = paths.CACHE_DIR / "maps_state.json"


def _sig(track, area, cfg):
    return hashlib.sha1(json.dumps([track, area, tracks.zone_sizes(cfg),
                                    cfg["modules"].get("weather_maps", {}).get("palettes")],
                                   sort_keys=True).encode()).hexdigest()


def run(cfg):
    wm = cfg["modules"].get("weather_maps", {})
    areas, all_tracks = wm.get("areas", {}), wm.get("tracks", {})
    enabled = {tid: t for tid, t in all_tracks.items() if t.get("enabled", True) and t.get("area") in areas}
    state = read_json(STATE, {}) or {}
    now = time.time()
    notes, errors = [], []

    # 1. radar archive per area (full-width grid), long enough for every track that uses it
    need = {}
    for t in enabled.values():
        uses_radar = t.get("kind") == "accumulation" or any(l.get("type") == "radar" for l in t.get("layers", []))
        if uses_radar:
            hours = float(t.get("window_hours", 24)) if t.get("kind") == "accumulation" \
                else float(t.get("window_min", 180)) / 60
            need[t["area"]] = max(need.get(t["area"], 0), min(48.0, hours))
    new_radar = set()
    steps, step = len(need) + len(enabled), 0
    for aid, hours in need.items():
        _harness.progress(step, steps, f"radar archive · {areas[aid].get('short_label') or aid}")
        step += 1
        area = dict(areas[aid], id=aid)
        _, arch_view, _ = tracks.views_for(area, "full", cfg)
        added, errs = radar.update_archive(arch_view, hours)
        if added:
            new_radar.add(aid)
        errors += errs[:2]
        times = radar.archive_times(arch_view)
        span = (times[-1] - times[0]).total_seconds() / 3600 if len(times) > 1 else 0
        notes.append(f"{aid}: +{added} radar frames, {span:.1f}/{hours:g} h archived")
        print(f"archive {aid}: +{added} frames, {span:.1f}/{hours:g} h", flush=True)

    # 2. render due tracks
    rendered = []
    run_id = None
    if any(t.get("kind") == "forecast" for t in enabled.values()):
        try:
            run_id = forecast.run_times()[0].isoformat()      # a new HRDPS run re-renders forecast maps
        except Exception as e:
            errors.append(f"HRDPS forecast times: {e}"[:160])
    for tid, t in enabled.items():
        step += 1
        st = state.get(tid, {})
        sig = _sig(t, areas[t["area"]], cfg)
        due = (now - st.get("at", 0) >= float(t.get("refresh_min", 10)) * 60 - 20
               or st.get("sig") != sig
               or (t["area"] in new_radar and t.get("kind") == "accumulation")
               or (t.get("kind") == "forecast" and run_id and st.get("run") != run_id))
        if not due:
            continue
        _harness.progress(step - 1, steps, f"rendering {t.get('label') or tid}")
        print(f"rendering {tid} ({t.get('label') or tid}) …", flush=True)
        t0 = time.time()
        try:
            res = tracks.render_track(tid, t, cfg)
        except Exception as e:
            res = {"ok": False, "message": f"{type(e).__name__}: {e}"}
        res["seconds"] = round(time.time() - t0, 1)
        state[tid] = {"at": now if res.get("ok") else st.get("at", 0), "sig": sig if res.get("ok") else st.get("sig"),
                      "last": res, "tried": now, "run": run_id if res.get("ok") else st.get("run")}
        (rendered if res.get("ok") else errors).append(f"{tid}: {res['message']}")
        print(f"  {tid}: {'ok' if res.get('ok') else 'FAILED'} in {res['seconds']}s — {res['message']}", flush=True)
        atomic_write_json(STATE, state, indent=1)

    _harness.progress(steps, steps, "cleaning up")
    # drop packs for tracks that no longer exist
    for p in list(tracks.ANIM_DIR.glob("*.json")):
        if p.stem not in enabled:
            p.unlink(missing_ok=True)
            (tracks.ANIM_DIR / f"{p.stem}.bin").unlink(missing_ok=True)
            state.pop(p.stem, None)
    atomic_write_json(STATE, state, indent=1)
    tracks.prune_wms_cache()
    geo.prune_cache()

    return {"ok": not errors or bool(rendered),
            "message": (f"rendered {len(rendered)} track(s)" if rendered else "tracks up to date")
                       + (f"; {len(errors)} problem(s)" if errors else ""),
            "rendered": rendered, "errors": errors[:8], "archive": notes,
            "files": ["anim/"]}


if __name__ == "__main__":
    _harness.main(run)
