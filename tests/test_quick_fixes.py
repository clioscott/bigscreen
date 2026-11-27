"""Data loader, config watcher, sync skip, map packs, feed health and Pi health."""

import json
import os
import time

import numpy as np


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# --- the Pi's data loader: a file rewritten with the same content is not parsed again ------------------------------
def test_loader_keeps_object_when_content_is_unchanged(tmp_path):
    from display.data_loader import DataLoader
    p = tmp_path / "calendar.json"
    p.write_text(json.dumps([{"id": 1}]))
    ld = DataLoader(str(tmp_path), check_interval=0.02)
    ld.get_data("calendar")
    assert _wait(lambda: ld.get_data("calendar") is not None)
    first, mt = ld.get_data("calendar"), ld.get_mtime("calendar")

    p.write_text(json.dumps([{"id": 1}]))                 # same bytes, new mtime
    os.utime(p, (mt + 60, mt + 60))
    assert _wait(lambda: ld.get_mtime("calendar") == mt + 60)   # still counts as fresh
    assert ld.get_data("calendar") is first                # … but it's the same object: nothing reprocesses

    p.write_text(json.dumps([{"id": 2}]))
    os.utime(p, (mt + 120, mt + 120))
    assert _wait(lambda: ld.get_data("calendar") == [{"id": 2}])
    assert ld.get_data("calendar") is not first


def test_loader_invalidate_reads_again(tmp_path):
    from display.data_loader import DataLoader
    p = tmp_path / "weather.json"
    p.write_text(json.dumps({"t": 1}))
    ld = DataLoader(str(tmp_path), check_interval=0.02)
    ld.get_data("weather")
    assert _wait(lambda: ld.get_data("weather") is not None)
    first = ld.get_data("weather")
    ld.invalidate("weather")
    assert _wait(lambda: ld.get_data("weather") is not None)
    assert ld.get_data("weather") == first and ld.get_data("weather") is not first


# --- the display's config: read by a watcher thread, taken in by poll() --------------------------------------------
def test_config_reloads_through_the_watcher(tmp_path, monkeypatch):
    from display import config
    monkeypatch.setattr(config, "WATCH_SEC", 0.02)
    p, fb = tmp_path / "config.json", tmp_path / "fallback.json"
    p.write_text(json.dumps({"revision": 1, "system": {}}))
    src = config.ConfigSource(str(p), fallback=str(fb))
    assert src.poll(time.time()) is True                   # the first load happens in poll itself
    assert src.effective().revision == 1
    assert json.loads(fb.read_text())["revision"] == 1     # the fallback copy for a start before the hub's sync
    gen = src.generation
    assert src.poll(time.time()) is False

    p.write_text(json.dumps({"revision": 2, "system": {}}))
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert _wait(lambda: src.poll(time.time()))
    assert src.effective().revision == 2 and src.generation == gen + 1
    assert _wait(lambda: json.loads(fb.read_text())["revision"] == 2)


def test_config_keeps_the_last_good_one_when_the_file_is_broken(tmp_path, monkeypatch):
    from display import config
    monkeypatch.setattr(config, "WATCH_SEC", 0.02)
    p, fb = tmp_path / "config.json", tmp_path / "fallback.json"
    p.write_text(json.dumps({"revision": 1}))
    src = config.ConfigSource(str(p), fallback=str(fb))
    src.poll(time.time())
    p.write_text("{not json")
    os.utime(p, (time.time() + 5, time.time() + 5))
    time.sleep(0.2)
    assert src.poll(time.time()) is False and src.effective().revision == 1
    p.write_text(json.dumps({"revision": 3}))
    os.utime(p, (time.time() + 10, time.time() + 10))
    assert _wait(lambda: src.poll(time.time()))
    assert src.effective().revision == 3


def test_config_starts_on_the_fallback_then_takes_the_synced_file(tmp_path, monkeypatch):
    from display import config
    monkeypatch.setattr(config, "WATCH_SEC", 0.02)
    p, fb = tmp_path / "config.json", tmp_path / "fallback.json"
    fb.write_text(json.dumps({"revision": 7}))
    src = config.ConfigSource(str(p), fallback=str(fb))
    src.poll(time.time())
    assert src.effective().revision == 7
    p.write_text(json.dumps({"revision": 8}))
    assert _wait(lambda: src.poll(time.time()))
    assert src.effective().revision == 8


# --- the hub's sync: nothing changed, nothing pushed ----------------------------------------------------------------
def test_sync_skips_a_push_when_nothing_changed(tmp_path, monkeypatch):
    from hub import paths, sync
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sync, "pi_target", lambda cfg=None: ("pi@example", "/dev/shm/x"))
    monkeypatch.setattr(sync, "_pushed", {"print": None, "t": 0.0})
    monkeypatch.setattr(sync, "last_result", {"at": None, "ok": None, "error": ""})
    runs = []

    class Done:
        returncode, stderr = 0, ""

    monkeypatch.setattr(sync.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or Done())
    (tmp_path / "a.json").write_text("{}")
    sync._push_once()
    assert len(runs) == 1 and sync.last_result["ok"] is True
    sync._push_once()                                      # nothing changed
    assert len(runs) == 1
    (tmp_path / "a.json").write_text('{"b": 1}')
    sync._push_once()
    assert len(runs) == 2
    (tmp_path / ".a.json.x.tmp").write_text("half")        # a file being written doesn't count
    sync._push_once()
    assert len(runs) == 2
    sync._pushed["t"] -= sync.RESYNC_SEC + 1               # … and a rebooted Pi is refilled within RESYNC_SEC
    sync._push_once()
    assert len(runs) == 3


def test_sync_pushes_again_after_a_failed_push(tmp_path, monkeypatch):
    from hub import paths, sync
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sync, "pi_target", lambda cfg=None: ("pi@example", "/dev/shm/x"))
    monkeypatch.setattr(sync, "_pushed", {"print": None, "t": 0.0})
    monkeypatch.setattr(sync, "last_result", {"at": None, "ok": None, "error": ""})
    codes, runs = [1, 0], []

    class Done:
        stderr = ""

        def __init__(self, rc):
            self.returncode = rc

    monkeypatch.setattr(sync.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or Done(codes.pop(0) if codes else 0))
    (tmp_path / "a.json").write_text("{}")
    sync._push_once()
    assert sync.last_result["ok"] is False
    sync._push_once()
    assert len(runs) == 2 and sync.last_result["ok"] is True


# --- map packs ------------------------------------------------------------------------------------------------------
def test_map_pack_opens_with_the_same_frames(tmp_path):
    from display.modules import weather_maps
    frames = np.arange(2 * 4 * 6 * 3, dtype=np.uint8)
    p = tmp_path / "t.bin"
    frames.tofile(p)
    got = weather_maps._open_pack(str(p))
    assert np.array_equal(np.asarray(got).reshape(2, 4, 6, 3), frames.reshape(2, 4, 6, 3))
    del got


# --- feed health ----------------------------------------------------------------------------------------------------
def test_a_job_that_partly_failed_is_degraded(tmp_path, monkeypatch):
    from hub import paths, scheduler
    monkeypatch.setattr(paths, "STATUS_PATH", tmp_path / "status.json")
    s = scheduler.Scheduler()
    now = time.time()
    s.status["news"].update(last_success=now, last_ok=True, detail={"degraded": "4 feed(s) failing"})
    s.status["weather"].update(last_success=now, last_ok=True, detail={"degraded": ""})
    s.status["finance"].update(last_success=now, last_ok=False, detail={"degraded": "2 symbol(s) stale"})
    snap = s.snapshot()
    assert snap["news"]["health"] == "degraded"
    assert snap["weather"]["health"] == "ok"
    assert snap["finance"]["health"] == "failing"          # failing outranks degraded


# --- Pi health ------------------------------------------------------------------------------------------------------
def test_pihealth_counts_each_sag_once(tmp_path, monkeypatch):
    from display import pihealth
    monkeypatch.setattr(pihealth, "_state", dict(pihealth._state, undervoltage_events=0, undervoltage_now=None,
                                                 undervoltage_last=None))
    alarm = tmp_path / "in0_lcrit_alarm"
    seq = ["0", "1", "1", "0", "1", "0"]
    for i, v in enumerate(seq):
        alarm.write_text(v + "\n")
        s = pihealth.sample(str(alarm), now=100.0 + i)
    assert s["undervoltage_events"] == 2 and s["undervoltage_now"] is False and s["undervoltage_last"] == 104.0


def test_pihealth_without_the_sensor(monkeypatch):
    from display import pihealth
    monkeypatch.setattr(pihealth, "_state", dict(pihealth._state, undervoltage_events=0, undervoltage_now=None))
    s = pihealth.sample(None)
    assert s["undervoltage_now"] is None and s["undervoltage_events"] == 0
