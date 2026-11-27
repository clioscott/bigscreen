"""Clarity lab: the hub's clip maths (hub/clarity.py) and the Pi's looping clip screen (display/modules/clip.py)."""

import json

import numpy as np
import pytest

from hub import clarity


def test_levels_match_the_led_library():
    level, avail, codes = clarity.levels(85)
    assert level[0] == 0 and level[9] == 0 and level[10] >= 1           # the darkest codes are black on the wall
    assert avail[0] == 0 and avail[-1] == 168 and len(avail) < 169      # the top is 168 of 255, and some are skipped
    assert all(level[c] == lv for c, lv in zip(codes, avail))           # each level's code gives that level
    assert clarity.levels(100)[1][-1] == 255


def test_dither_averages_to_the_light_wanted():
    level = clarity.levels(85)[0]
    a = np.full((64, 64, 3), 14 / 255, np.float32)                       # between black and the first level
    plain = (a * 255 + 0.5).astype(np.uint8)
    out = clarity.dither(a, 85, np.random.default_rng(1))
    want = 2047 * clarity._curve(np.float64(14) * 85 / 255) / 8.0
    assert abs(level[out].mean() - want) < 0.03                          # dithered: right on average
    assert abs(level[plain].mean() - want) > 0.2                         # as now: every pixel rounded down
    assert set(np.unique(level[out]).tolist()) == {1, 2}                 # only the two levels around the target


def test_finish_without_dither_is_plain_rounding():
    a = np.linspace(0, 1, 12, dtype=np.float32).reshape(2, 2, 3)
    out = clarity.finish(a, {"sharpen": 0, "dither": False}, 85, np.random.default_rng(0))
    assert np.array_equal(out, (a * 255 + 0.5).astype(np.uint8))


def test_linear_box_keeps_a_bright_point_brighter_than_plain_averaging():
    f = np.zeros((4, 4, 3), np.uint8)
    f[0, 0] = 255                                                        # one lit pixel of four in the first block
    out = clarity.linear_box(f, 2, 2)
    assert out.shape == (2, 2, 3)
    assert out[0, 0, 0] > 0.5                                            # a quarter of the light reads as ~54 % grey
    assert out[1, 1, 0] == 0
    # a fractional ratio and an enlargement both come out the right size
    big = np.full((7, 9, 3), 128, np.uint8)
    assert clarity.linear_box(big, 4, 3).shape == (3, 4, 3)
    assert clarity.linear_box(big, 18, 14).shape == (14, 18, 3)
    assert abs(float(clarity.linear_box(big, 4, 3).mean()) - 128 / 255) < 0.004


def test_sharpen_leaves_flat_areas_and_raises_edges():
    a = np.full((6, 6, 3), 0.5, np.float32)
    assert np.allclose(clarity.sharpen(a, 0.25), a)
    a[:, 3:] = 0.7
    s = clarity.sharpen(a, 0.25)
    assert s[2, 3, 0] > 0.7 and s[2, 2, 0] < 0.5
    assert clarity.sharpen(a, 0) is a


def test_versions_are_checked_against_the_stream():
    v = clarity.clean_version({"height": "best", "fps": 60, "scaling": "linear", "sharpen": "0.25", "dither": 1}, {720, 1080})
    assert v == {"height": 1080, "fps": 60, "scaling": "linear", "sharpen": 0.25, "brightness": 1.0, "contrast": 1.0,
                 "saturation": 1.0, "dither": True}
    assert clarity.clean_version({}, {720})["scaling"] == "current"
    assert clarity.clean_version({"contrast": 9, "saturation": None}, {720})["contrast"] == 2.0      # kept in range
    for bad in ({"height": 2160}, {"fps": 24}, {"scaling": "cubic"}, {"sharpen": "lots"}, {"brightness": "x"}):
        with pytest.raises(ValueError):
            clarity.clean_version(bad, {720, 1080})
    assert "1080p" in clarity.describe(v) and "dithered" in clarity.describe(v)
    assert "contrast 120%" in clarity.describe(dict(v, contrast=1.2))


def test_colour_matches_the_walls_own_maths():
    from hub import webcams as wc
    rng = np.random.default_rng(3)
    u8 = rng.integers(0, 256, (6, 7, 3), dtype=np.uint8)
    for b, c, s in ((1.2, 1.0, 1.0), (1.0, 1.3, 1.0), (1.0, 1.0, 0.4), (0.9, 1.15, 1.6)):
        mine = clarity.finish(u8.astype(np.float32) / 255.0, {"brightness": b, "contrast": c, "saturation": s}, 85, rng)
        assert np.abs(mine.astype(int) - wc.adjust(u8, b, c, s).astype(int)).max() <= 1
    a = u8.astype(np.float32) / 255.0
    assert clarity.adjust(a) is a


def test_render_takes_scaled_frames_of_either_kind():
    frames = np.random.default_rng(5).integers(0, 256, (3, 4, 6, 3), dtype=np.uint8)
    v = clarity.clean_version({"height": 720, "sharpen": 0}, {720})
    assert np.array_equal(clarity.render(frames, v, 85, 1), frames)                       # nothing asked: untouched
    as_float = (frames.astype(np.float32) / 255.0).astype(np.float16)
    assert np.abs(clarity.render(as_float, v, 85, 1).astype(int) - frames.astype(int)).max() <= 1
    sharp = clarity.render(frames, dict(v, sharpen=0.5), 85, 1)
    assert sharp.shape == frames.shape and not np.array_equal(sharp, frames)


def _session_spec(tmp_path, monkeypatch, **kw):
    from hub import webcams as wc
    monkeypatch.setattr(clarity, "WORK", tmp_path / "work")
    monkeypatch.setattr(clarity, "SESSION", tmp_path / "work" / "session")
    monkeypatch.setattr(clarity, "CLIPS", tmp_path / "clips")
    monkeypatch.setattr(clarity, "STATE", tmp_path / "state.json")
    segs = [wc.Seg((100 + i, None, 5.0, f"u{i}", False)) for i in range(10)]
    monkeypatch.setattr(wc, "get_playlist", lambda url: segs)
    fmt = {"h": 720, "w": 1280, "fps": 30.0, "url": "hls", "dash": False}
    v = clarity.clean_version({"height": 720, "sharpen": 0}, {720})
    spec = {"cam": "c", "label": "C", "url": "yt", "mode": "split", "crop": None, "seconds": 5, "a": v, "b": dict(v, sharpen=0.5),
            "blind": False, "new_loop": False, "zone": [448, 175], "brightness": 85, "formats": {"a": fmt, "b": fmt},
            "numbering": fmt}
    spec.update(kw)
    return spec


def test_a_session_keeps_its_loop_until_a_new_one_is_asked_for(tmp_path, monkeypatch):
    spec = _session_spec(tmp_path, monkeypatch)
    first = clarity.session_for(spec)
    assert first["seqs"] == [107] and clarity.session_for(spec)["recorded"] == first["recorded"]
    assert clarity.session_for(dict(spec, seconds=10))["seqs"] == [106, 107]             # another length
    again = clarity.session_for(dict(spec, seconds=10, url="other"))                    # another webcam
    assert again["url"] == "other"
    fresh = clarity.session_for(dict(spec, seconds=10, url="other", new_loop=True))
    assert fresh["recorded"] >= again["recorded"] and fresh["files"] == {}


def test_build_downloads_once_and_reuses_scaled_frames(tmp_path, monkeypatch):
    spec = _session_spec(tmp_path, monkeypatch)
    calls = {"segments": 0, "ffmpeg": 0}

    def fake_segments(fmt, seqs):
        calls["segments"] += 1
        return b"video", "mpegts"

    class FakeProc:
        def __init__(self, args, stdout=None, stderr=None):
            import io
            calls["ffmpeg"] += 1
            n = int(args[args.index("-frames:v") + 1])
            self.stdout = io.BytesIO(bytes(n * 175 * 223 * 3))

        def poll(self): return 0
        def wait(self): return 0
        def kill(self): pass

    monkeypatch.setattr(clarity, "segments", fake_segments)
    monkeypatch.setattr(clarity.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(clarity.wc, "hw_decode", lambda: False)
    meta = clarity.build(spec)
    assert (meta["mode"], meta["n"], meta["w"], meta["h"], meta["fps"]) == ("split", 150, 448, 175, 30)
    assert calls == {"segments": 1, "ffmpeg": 1}                        # both halves: one download, one decode
    assert (tmp_path / "clips" / "clarity.bin").stat().st_size == 150 * 175 * 448 * 3

    clarity.build(dict(spec, b=dict(spec["b"], sharpen=1.0, contrast=1.2)))               # a colour change
    assert calls == {"segments": 1, "ffmpeg": 1}                        # … decodes nothing

    meta = clarity.build(dict(spec, mode="full"))                       # the whole zone, one version
    assert (meta["mode"], meta["w"], meta["left"], meta["right"]) == ("full", 448, "A", "A")
    assert calls["segments"] == 1 and calls["ffmpeg"] == 2              # same loop, new size

    clarity.build(dict(spec, new_loop=True))
    assert calls["segments"] == 2


def test_format_for_prefers_the_frame_rate_asked_for():
    fmts = [{"h": 1080, "fps": 30.0, "audio": True}, {"h": 1080, "fps": 60.0, "audio": True}, {"h": 720, "fps": 30.0, "audio": True}]
    assert clarity.format_for(fmts, 1080, 60)["fps"] == 60
    assert clarity.format_for(fmts, 1080, 30)["fps"] == 30
    with pytest.raises(ValueError):
        clarity.format_for(fmts, 1440, 30)


def test_compose_puts_a_left_and_b_right_at_the_faster_rate():
    a = np.zeros((30, 5, 223, 3), np.uint8)                              # 1 s at 30
    for i in range(30):
        a[i] = i
    b = np.full((60, 5, 223, 3), 200, np.uint8)                          # 1 s at 60
    out, fps = clarity.compose(a, b, 30, 60, 448)
    assert fps == 60 and out.shape == (60, 5, 448, 3)
    assert (out[:, :, 223:225] == 0).all()                               # the black line between the halves
    assert (out[:, :, 225:] == 200).all()
    assert [int(out[i, 0, 0, 0]) for i in (0, 1, 2, 3, 59)] == [0, 0, 1, 1, 29]   # each 30 fps frame shows twice
    assert clarity.half_width(448) == 223


def test_pick_segments_stays_behind_the_newest(monkeypatch):
    from hub import webcams as wc
    segs = [wc.Seg((100 + i, None, 5.0, f"u{i}", False)) for i in range(10)]
    monkeypatch.setattr(wc, "get_playlist", lambda url: segs)
    assert clarity.pick_segments({"url": "x"}, 5) == [107]
    assert clarity.pick_segments({"url": "x"}, 10) == [106, 107]


# --- the Pi's side ----------------------------------------------------------------------------------------------
def _write_clip(d, name, frames, fps=30):
    (d / "clips").mkdir(exist_ok=True)
    frames.tofile(d / "clips" / f"{name}.bin")
    (d / "clips" / f"{name}.json").write_text(json.dumps({"n": frames.shape[0], "h": frames.shape[1], "w": frames.shape[2], "fps": fps}))


def test_clip_screen_loops_its_frames(tmp_path):
    from display.gfx import Canvas
    from display.modules import clip, webcam
    frames = np.zeros((3, 4, 6, 3), np.uint8)
    for i in range(3):
        frames[i] = 50 * (i + 1)
    _write_clip(tmp_path, "t", frames, fps=10)
    store = clip.ClipStore(str(tmp_path), watch=False)
    store.scan()
    panel = clip.ClipPanel(store)
    c = Canvas(6, 4)
    seen = []
    try:
        for k in range(7):
            webcam.CLOCK["tick"] = 1000.0 + k * 0.1
            assert panel.draw(c, (0, 0, 6, 4), {"panel": "clip_tall", "clip": "t"}, k == 0) is None
            seen.append(int(c.buf[0, 0, 0]))
    finally:
        webcam.CLOCK["tick"] = None
        store.clips.clear()
    assert seen == [50, 100, 150, 50, 100, 150, 50]


def test_clip_store_waits_for_matching_files_and_forgets_deleted_ones(tmp_path):
    from display.modules import clip
    frames = np.zeros((2, 4, 6, 3), np.uint8)
    _write_clip(tmp_path, "t", frames)
    (tmp_path / "clips" / "t.json").write_text(json.dumps({"n": 5, "h": 4, "w": 6, "fps": 30}))   # from another build
    store = clip.ClipStore(str(tmp_path), watch=False)
    store.scan()
    assert store.get("t") is None
    _write_clip(tmp_path, "t", frames)
    store._seen.clear()
    store.scan()
    assert store.get("t")[1].shape == (2, 4, 6, 3)
    store.clips.clear()                                   # (drop the frames before deleting the file)
    (tmp_path / "clips" / "t.json").unlink()
    store.scan()
    assert store.get("t") is None


def test_a_missing_clip_ends_the_screen(tmp_path):
    from display.gfx import Canvas
    from display.modules import clip, webcam
    panel = clip.ClipPanel(clip.ClipStore(str(tmp_path), watch=False))
    c = Canvas(6, 4)
    try:
        webcam.CLOCK["tick"] = 500.0
        assert panel.draw(c, (0, 0, 6, 4), {"clip": "none"}, True) is None
        webcam.CLOCK["tick"] = 520.0
        assert panel.draw(c, (0, 0, 6, 4), {"clip": "none"}, False) == "skip"     # ends a held play too
    finally:
        webcam.CLOCK["tick"] = None
