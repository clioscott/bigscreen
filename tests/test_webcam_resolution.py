"""Webcams at the stream's own resolution (hub/webcams.py): every format a YouTube stream has, the detail each crop
needs (never more than one stream pixel per LED), the hi picture (whole frame or the crops' region) and consumers
rendered from it as frames are decoded."""

import collections
import json
import threading
import types

import numpy as np

from hub import webcams as wc

FMT = [  # what yt-dlp -J lists for a 4K live stream (video-only HLS), plus audio and a muxed one
    {"format_id": "233", "url": "https://m/a.m3u8", "vcodec": "none", "acodec": "mp4a", "width": None, "height": None},
    {"format_id": "231", "url": "https://m/480.m3u8", "vcodec": "avc1.4D401F", "acodec": "none", "width": 854,
     "height": 480, "fps": 30, "tbr": 1283},
    {"format_id": "232", "url": "https://m/720.m3u8", "vcodec": "avc1.4D401F", "acodec": "none", "width": 1280,
     "height": 720, "fps": 30, "tbr": 2448},
    {"format_id": "270", "url": "https://m/1080.m3u8", "vcodec": "avc1.640028", "acodec": "none", "width": 1920,
     "height": 1080, "fps": 30, "tbr": 4500},
    {"format_id": "625", "url": "https://m/2160.m3u8", "vcodec": "vp09.00.51.08", "acodec": "none", "width": 3840,
     "height": 2160, "fps": 30, "tbr": 20000},
    {"format_id": "bad", "url": "file:///etc/passwd", "vcodec": "avc1", "width": 1920, "height": 1080},
]


def test_every_resolution_a_stream_has_smallest_first():
    f = wc.formats_of({"formats": FMT})
    assert [x["h"] for x in f] == [480, 720, 1080, 2160]                  # no audio, nothing but network URLs
    assert wc.native_of(f)["w"] == 3840 and f[-1]["vcodec"] == "vp09"


def test_the_stream_picked_is_the_smallest_with_enough_detail_but_never_under_720p():
    f = wc.formats_of({"formats": FMT})
    assert wc.pick_format(f, 0)["h"] == 720                               # 480p is soft and blocky: 720p at least
    assert wc.pick_format(f, 0, min_h=0)["h"] == 480                      # (snapshots: small is fine)
    assert wc.pick_format(f, 1300)["h"] == 1080
    assert wc.pick_format(f, 9000)["h"] == 2160                           # more than there is: the biggest


def test_lookup_reads_yt_dlps_json(monkeypatch):
    info = {"title": "Shibuya", "live_status": "is_live", "formats": FMT}
    monkeypatch.setattr(wc, "_yt_dlp", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps(info),
                                                                              stderr=""))
    wc._resolved.clear()
    got = wc.lookup("https://www.youtube.com/watch?v=abcdefghijk")
    assert got["title"] == "Shibuya" and len(got["formats"]) == 4
    assert wc.resolve("youtube", "https://www.youtube.com/watch?v=abcdefghijk", need_w=1500)[0] == "https://m/1080.m3u8"
    wc._resolved.clear()


def test_a_crop_that_lo_covers_needs_no_hi_picture():
    assert wc.plan(1280, 720, [(448, 175, None)]) is None                 # full width: 640 px is plenty for 448
    assert wc.plan(1920, 1080, [(448, 175, None), (224, 134, None)]) is None


def test_a_zoomed_crop_gets_its_region_at_the_detail_it_needs():
    crop = {"x": 0.2, "y": 0.2, "w": 0.5}
    one = wc.plan(1920, 1080, [(448, 175, crop)])                         # one crop: cut and scaled by ffmpeg
    assert one["exact"] and (one["out_w"], one["out_h"]) == (448, 175)
    assert wc.covers(one, 1920, 1080, (448, 175, crop)) and not wc.covers(one, 1920, 1080, (448, 175, None))
    other = (224, 134, {"x": 0.25, "y": 0.25, "w": 0.3})
    p = wc.plan(1920, 1080, [(448, 175, crop), other])                    # two: their region, scaled here
    l, t, r, b = wc.crop_box(1920, 1080, 448, 175, crop)
    assert p["x"] <= l and p["y"] <= t and p["x"] + p["w"] >= r and p["y"] + p["h"] >= b   # the crop, and a margin
    assert p["w"] * p["h"] < 0.5 * 1920 * 1080                             # not the whole frame (each pixel costs)
    whole = wc.plan(1920, 1080, [(448, 175, crop), (448, 175, {"x": 0.0, "y": 0.0, "w": 0.6}),
                                 (448, 175, {"x": 0.4, "y": 0.5, "w": 0.6})])
    assert (whole["x"], whole["y"], whole["w"], whole["h"]) == (0, 0, 1920, 1080)   # crops all over: the frame
    assert abs(p["full_w"] - 448 / 0.5 * wc.OVERSAMPLE) < 4 or p["full_w"] >= 224 / 0.3 * wc.OVERSAMPLE - 4
    assert p["out_w"] <= 1920 and p["out_w"] * p["out_h"] <= wc.HI_BUDGET


def test_one_to_one_on_4k_gets_just_the_crops_region_at_full_detail():
    one = {"x": 0.4, "y": 0.4, "w": 448 / 3840}                            # one stream pixel per LED
    wants = [(448, 175, one), (448, 175, None), (224, 134, None)]          # + the other places' fit crops (lo)
    p = wc.plan(3840, 2160, wants)
    assert p["full_w"] == 3840 and p["out_w"] == p["w"]                     # not scaled: every stream pixel
    assert p["w"] < 3840 and p["out_w"] * p["out_h"] <= wc.HI_BUDGET        # just the region, within the budget
    assert wc.covers(p, 3840, 2160, wants[0]) and not wc.covers(p, 3840, 2160, wants[1])


def test_one_to_one_is_a_straight_copy_of_the_stream():
    rng = np.random.default_rng(3)
    src = rng.integers(0, 255, (2160, 3840, 3), np.uint8)
    one = {"x": 0.4, "y": 0.4, "w": 448 / 3840}
    p = wc.plan(3840, 2160, [(448, 175, one)])
    hi = np.ascontiguousarray(src[p["y"]:p["y"] + p["h"], p["x"]:p["x"] + p["w"]])
    out = wc.render_region(hi, p, 3840, 2160, 448, 175, one, {"sharpen": 0})
    l, t, r, b = wc.crop_box(3840, 2160, 448, 175, one)
    assert (out == src[t:b, l:r]).all()                                    # pixel for pixel


def test_a_region_renders_what_the_whole_frame_would():
    rng = np.random.default_rng(5)
    src = rng.integers(0, 255, (1080, 1920, 3), np.uint8)
    crop = {"x": 0.3, "y": 0.25, "w": 0.4}
    p = {"full_w": 1920, "x": 400, "y": 200, "w": 1200, "h": 700, "out_w": 1200, "out_h": 700}
    hi = np.ascontiguousarray(src[200:900, 400:1600])
    a = wc.render_region(hi, p, 1920, 1080, 448, 175, crop, {"sharpen": 0}).astype(int)
    b = wc.render(src, 448, 175, crop, {"sharpen": 0}).astype(int)
    assert np.abs(a - b).mean() < 1.0


def test_sharpening_is_on_for_video_by_default_and_off_for_the_grid():
    assert wc.sharpen_of({}) == wc.DEFAULT_SHARPEN and wc.sharpen_of(None) == 0
    assert wc.sharpen_of({"sharpen": 0}) == 0 and wc.sharpen_of({"sharpen": 9}) == 2
    img = np.zeros((90, 160, 3), np.uint8)
    img[:, 80:] = 200
    soft, sharp = wc.render(img, 80, 45, None, {"sharpen": 0}), wc.render(img, 80, 45, None, {"sharpen": 1.5})
    assert int(sharp[20].max()) >= int(soft[20].max()) and (sharp != soft).any()


def _feed_shell():
    f = wc.Feed.__new__(wc.Feed)
    f.kind, f.url, f.fps = "youtube", "u", 30
    f._lock = threading.Lock()
    f._cond = threading.Condition(f._lock)
    f.decoded = collections.deque(maxlen=181)
    f.consumers = {}
    f.source, f.hi = {"w": 1920, "h": 1080}, None
    f.error = ""
    return f


def test_a_new_consumer_starts_with_the_backlog_then_gets_every_frame():
    f = _feed_shell()
    lo = [np.full((360, 640, 3), i, np.uint8) for i in range(5)]
    for i, fr in enumerate(lo, 1):
        f.decoded.append((i, fr))
    c = f.consumer(448, 175)
    assert c.oldest() == 0 and c.after(0)[0] == 1 and c.after(5) is None   # seeded from lo
    f._render_consumers(np.full((360, 640, 3), 9, np.uint8), 6)
    assert c.after(5)[0] == 6 and int(c.after(5)[1][0, 0, 0]) == 9
    assert f.consumer(448, 175) is c and c.refs == 2                         # the same zone and crop: shared
    f.release(c)
    f.release(c)
    assert f.consumers == {}


def test_a_preview_uses_the_hi_render_only_for_its_current_draft():
    f = _feed_shell()
    d = f.consumer(448, 175, key=("preview", "t"), draft=True)
    f._render_consumers(np.zeros((360, 640, 3), np.uint8), 1)
    assert d.for_dseq(1) is not None
    d.set(448, 175, {"x": 0.1, "y": 0.1, "w": 0.5}, None)                  # the crop moved: re-render from lo
    assert d.for_dseq(1) is None and not d.settled()


def test_ffmpeg_gives_lo_and_the_hi_region_on_their_own_pipes():
    f = _feed_shell()
    f.decoded_t = 0.0
    f.source = {"w": 3840, "h": 2160}
    f.hi = wc.plan(3840, 2160, [(448, 175, {"x": 0.4, "y": 0.4, "w": 448 / 3840})])
    args = f._args("https://m/2160.m3u8", hi_fd=7, hw=True)
    graph = args[args.index("-filter_complex") + 1]
    assert "split=2" in graph and f"crop=w={f.hi['w']}:h={f.hi['h']}" in graph and "scale=640:-2" in graph
    assert "pipe:1" in args and "pipe:7" in args and args[args.index("-hwaccel") + 1] == "vaapi"
    f.hi = None
    plain = f._args("https://m/720.m3u8")
    assert "-filter_complex" not in plain and "-hwaccel" not in plain


def test_a_consumer_with_gaps_in_its_frames_still_plays_on():
    c = wc.Consumer(4, 4)
    for d in (1, 2, 3, 4, 10, 11, 12, 13):                          # 5-9 never rendered (lo late, a failed render)
        c.add(d, 0, d)
    seen, d = [], 0
    while (got := c.after(d)) is not None:
        d = got[0]
        seen.append(d)
    assert seen == [1, 2, 3, 4, 10, 11, 12, 13]
    assert c.for_dseq(11) == 11 and c.for_dseq(7) is None


def test_the_region_covers_a_crop_at_the_frames_edge():
    want = (448, 175, {"x": 0.83, "y": 0.5, "w": 0.17})
    p = wc.plan(3840, 2160, [want])
    assert p["x"] + p["w"] <= 3840 and wc.covers(p, 3840, 2160, want)


def test_a_failed_lookup_is_not_repeated_for_a_while(monkeypatch):
    calls = []

    def dead(*a, **k):
        calls.append(a)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="ERROR: This live event has ended.")
    monkeypatch.setattr(wc, "_yt_dlp", dead)
    url = "https://www.youtube.com/watch?v=deaddeaddea"
    wc._resolved.pop(url, None)
    wc._lookup_failed.pop(url, None)
    for _ in range(5):
        try:
            wc.lookup(url)
        except wc.SourceError:
            pass
    assert len(calls) == 1 and wc.cached(url) is None               # the panel's 4-s poll never asks YouTube
    wc._lookup_failed.pop(url, None)


def test_30fps_is_picked_over_60_at_the_same_size():
    f = wc.formats_of({"formats": [
        {"url": "https://m/1080p60", "vcodec": "avc1", "acodec": "none", "width": 1920, "height": 1080, "fps": 60},
        {"url": "https://m/1080p30", "vcodec": "avc1", "acodec": "none", "width": 1920, "height": 1080, "fps": 30}]})
    assert wc.pick_format(f, 1500)["url"] == "https://m/1080p30"
    assert wc.native_of(f)["fps"] == 60                                  # (the panel still says what it has)


def test_whole_pixels_makes_the_crop_exactly_k_times_the_viewport():
    snap = {"x": 600 / 1920, "y": 300 / 1080, "w": round(2 * 448 / 1920, 6), "snap": True}
    assert wc.crop_box(1920, 1080, 448, 175, snap) == (600, 300, 600 + 896, 300 + 350)
    assert wc.snap_k(snap, 640, 360, 448, 175) == 0                      # the 640-wide picture: cut as usual
    assert wc.need_of(1920, 1080, 448, 175, snap) >= 1920                  # the stream's own pixels, unscaled
    p = wc.plan(1920, 1080, [(448, 175, snap)])
    assert p["exact"] and (p["x"], p["y"], p["w"], p["h"]) == (600, 300, 896, 350) and p["out_w"] == 448
    assert wc.covers(p, 1920, 1080, (448, 175, snap))


def test_whole_pixels_is_the_plain_average_of_each_k_by_k_block():
    rng = np.random.default_rng(9)
    src = rng.integers(0, 255, (1080, 1920, 3), np.uint8)
    snap = {"x": 300 / 1920, "y": 300 / 1080, "w": round(3 * 448 / 1920, 6), "snap": True}
    p = wc.plan(1920, 1080, [(448, 175, snap), (224, 134, {**snap, "x": 0.6})])   # two crops: averaged here
    hi = np.ascontiguousarray(src[p["y"]:p["y"] + p["h"], p["x"]:p["x"] + p["w"]])   # its region, unscaled
    assert (p["out_w"], p["out_h"]) == (p["w"], p["h"])
    out = wc.render_region(hi, p, 1920, 1080, 448, 175, snap, {"sharpen": 0}).astype(int)
    block = src[300:300 + 525, 300:300 + 1344].reshape(175, 3, 448, 3, 3).mean(axis=(1, 3))
    assert np.abs(out - block).max() <= 1                                # each LED: its 3 x 3 pixels averaged


def test_a_snapped_crop_is_its_own_consumer():
    crop = {"x": 0.1, "y": 0.1, "w": 0.5}
    assert wc._key_of(448, 175, crop, {}) != wc._key_of(448, 175, {**crop, "snap": True}, {})


# --- 4K: a live stream's DASH formats ----------------------------------------------------------------------------
def _live_info():
    """What yt-dlp -J --extractor-args youtube:formats=incomplete lists for a live 4K stream: HLS up to 1080p, and the
    adaptive (DASH) formats, H.264 MP4 and VP9 WebM, up to 2160p."""
    fs = []
    for h, w in ((720, 1280), (1080, 1920)):
        fs.append({"url": f"https://m/{h}.m3u8", "vcodec": "avc1.4d", "acodec": "none", "width": w, "height": h,
                   "fps": 30, "protocol": "m3u8_native", "ext": "mp4"})
    for h, w in ((720, 1280), (1080, 1920), (1440, 2560), (2160, 3840)):
        fs.append({"url": f"https://v/{h}.webm?x=1", "vcodec": "vp9", "acodec": "none", "width": w, "height": h,
                   "fps": 30, "protocol": "https", "ext": "webm"})
        if h <= 1080:
            fs.append({"url": f"https://v/{h}.mp4?x=1", "vcodec": "avc1.4d", "acodec": "none", "width": w,
                       "height": h, "fps": 30, "protocol": "https", "ext": "mp4"})
    return {"live_status": "is_live", "formats": fs}


def test_a_live_streams_4k_comes_from_dash_beyond_what_hls_has():
    f = wc.formats_of(_live_info())
    assert [(x["h"], x["dash"]) for x in f] == [(720, False), (1080, False), (1440, True), (2160, True)]
    assert wc.native_of(f)["w"] == 3840
    assert [x["h"] for x in wc.playable(f, hw=False)] == [720, 1080]      # 4K VP9 only on the video engine
    not_live = wc.formats_of({**_live_info(), "live_status": "was_live"})   # (adaptive ones only for live streams)
    assert not any(x["dash"] for x in not_live)


def test_the_lookup_asks_for_dash_formats_and_resolve_never_gives_one(monkeypatch):
    seen = []

    def yt(*a, **k):
        seen.append(a)
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"title": "t", **_live_info()}), stderr="")
    monkeypatch.setattr(wc, "_yt_dlp", yt)
    wc._resolved.clear()
    try:
        url = "https://www.youtube.com/watch?v=abcdefghijk"
        assert wc.resolve("youtube", url, need_w=9000)[0] == "https://m/1080.m3u8"   # (plain ffmpeg: no DASH)
        assert "youtube:formats=incomplete" in seen[0]
    finally:
        wc._resolved.clear()


def test_4k_is_decoded_only_for_crops_that_need_more_than_1080p():
    f = wc.formats_of(_live_info())
    assert wc.choose_format(f, [(448, 175, None)], hw=True)["h"] == 720          # full width: plenty in 720p
    assert wc.choose_format(f, [(448, 175, {"x": 0.3, "y": 0.3, "w": 0.4})], hw=True)["h"] == 1080
    tight = (448, 175, {"x": 0.4, "y": 0.4, "w": 0.2})                            # 448 / 0.2 x 1.5 = 3360 wide
    assert wc.choose_format(f, [tight], hw=True)["h"] == 2160
    assert wc.choose_format(f, [tight], hw=False)["h"] == 1080                    # no video engine: HLS's best


def test_whole_pixels_crops_get_a_resolution_they_are_exact_on():
    f = wc.formats_of(_live_info())
    k4 = {"x": 0.03, "y": 0.0, "w": round(4 * 448 / 1920, 6), "snap": True}      # 4:1, set on the 1080p
    got = wc.choose_format(f, [(448, 130, k4)], hw=True)
    assert got["h"] == 1080 and wc.snap_k(k4, got["w"], got["h"], 448, 130) == 4   # not 720p (not exact there)
    k3 = {"x": 0.3, "y": 0.3, "w": round(3 * 448 / 3840, 6), "snap": True}       # 3:1, set on the 4K
    got = wc.choose_format(f, [(448, 130, k3)], hw=True)
    assert got["dash"] and wc.snap_k(k3, got["w"], got["h"], 448, 130) > 0


def test_a_dash_run_reads_stdin_and_crops_and_scales_on_the_video_engine():
    f = _feed_shell()
    f.source = {"w": 3840, "h": 2160, "dash": True}
    f.hi = wc.plan(3840, 2160, [(448, 175, {"x": 0.4, "y": 0.4, "w": 448 / 3840})])
    args = f._args("https://v/2160.webm?x=1", hi_fd=7, hw=True, piped="dash")
    assert args[args.index("-i") + 1] == "pipe:0" and args[args.index("-f") + 1] == "matroska"
    assert "pipe" in args[args.index("-protocol_whitelist") + 1].split(",")
    assert args[args.index("-hwaccel_output_format") + 1] == "vaapi"
    graph = args[args.index("-filter_complex") + 1]
    assert f"crop=w={f.hi['w']}:h={f.hi['h']}" in graph
    assert f"scale_vaapi=w=640:h=360" in graph and f"scale_vaapi=w={f.hi['out_w']}:h={f.hi['out_h']}" in graph
    assert graph.count("hwdownload") == 2 and "pipe:7" in args
    f.hi = None
    lo_only = f._args("https://v/2160.webm?x=1", hw=True, piped="dash")
    assert "-filter_complex" not in lo_only and "scale_vaapi=w=640:h=360" in lo_only[lo_only.index("-vf") + 1]


class _FakeProc:
    def __init__(self, stop_after):
        self.written, self.stop_after, self.closed = [], stop_after, False
        self.stdin = self

    def poll(self):
        done = [w for w in self.written if w.endswith(b"|")]
        return 0 if len(done) >= self.stop_after else None

    def write(self, b):                                                   # a raw pipe: takes 3 bytes at most
        b = bytes(b)
        if not self.written or self.written[-1].endswith(b"|"):
            self.written.append(b"")
        self.written[-1] += b[:3]
        return min(3, len(b))

    def close(self):
        self.closed = True


class _Clock:
    """A fake time module for the segment feeder: sleep() moves the clock on."""
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.01)


def _playlist(first_seq, t0, n, dur=5.0):
    return [wc.Seg((first_seq + i, t0 + i * dur, dur, f"https://h/{first_seq + i}.ts", False)) for i in range(n)]


def _feeder(monkeypatch, segs, now, seg=None, get=None):
    """A feed shell with a fake clock, playlist and segment fetch; returns (feed, run, clock)."""
    clock = _Clock(now)
    monkeypatch.setattr(wc, "time", clock)
    monkeypatch.setattr(wc, "get_playlist", lambda url: segs)

    def seg_get(url, n=None):
        if get:
            return get(url, n)
        return (f"d{n}" if n is not None else url.rsplit("/", 1)[1]).encode() + b"|"
    monkeypatch.setattr(wc, "seg_get", seg_get)
    f = _feed_shell()
    f._seg = seg or {"timing": "https://h/index.m3u8", "dash": None}
    f._next_seq = None
    f._restart = f._expired = f._seg_broken = f._dash_broken = False
    f._fed, f._seg_error = False, ""
    return f, {}, clock


def test_a_feed_starts_a_buffer_back_and_sends_every_segment_at_once(monkeypatch):
    now = 1_000_000.0
    f, run, clock = _feeder(monkeypatch, _playlist(100, now - 60, 12), now)   # the last minute of the stream
    p = _FakeProc(3)
    f._feed_segments(p, run)
    # START_BACK_SEC (15 s) back from the end of the newest (111): 109, 110, 111, all straight away
    assert p.written == [b"109.ts|", b"110.ts|", b"111.ts|"]
    assert clock.t - now < 0.1                                            # no waiting for any of them
    assert f._fed and f._next_seq == 112


def test_a_new_segment_goes_in_as_soon_as_it_is_listed(monkeypatch):
    now = 1_000_000.0
    segs = _playlist(100, now - 60, 12)
    f, run, clock = _feeder(monkeypatch, segs, now)
    f._next_seq = 112                                                     # caught up: 112 isn't listed yet
    calls = {"n": 0}

    def playlist(url):
        calls["n"] += 1
        return segs if calls["n"] < 3 else segs + _playlist(112, now - 0, 1)
    monkeypatch.setattr(wc, "get_playlist", playlist)
    p = _FakeProc(1)
    f._feed_segments(p, run)
    assert p.written == [b"112.ts|"] and clock.t - now < 5               # read again after ~1 s, sent at once


def test_a_break_in_the_streams_timeline_starts_a_new_run_on_it(monkeypatch):
    now = 1_000_000.0
    segs = _playlist(100, now - 60, 12)
    segs[10] = wc.Seg((110, now - 10, 5.0, "https://h/110.ts", True))     # the encoder restarted: a new timeline
    f, run, clock = _feeder(monkeypatch, segs, now)
    p = _FakeProc(9)
    f._feed_segments(p, run)
    assert p.written == [b"109.ts|"] and f._restart and f._next_seq == 110


def test_after_a_long_pause_a_feed_carries_on_from_the_next_segment_still_listed(monkeypatch):
    now = 1_000_000.0
    f, run, clock = _feeder(monkeypatch, _playlist(100, now - 60, 12), now)
    f._next_seq = 104                                                     # a restart for more detail: seamless
    p = _FakeProc(2)
    f._feed_segments(p, run)
    assert p.written == [b"104.ts|", b"105.ts|"]                          # nothing passed over
    f2, run2, _ = _feeder(monkeypatch, _playlist(100, now - 60, 12), now)
    f2._next_seq = 40                                                     # long gone from the playlist
    p2 = _FakeProc(1)
    f2._feed_segments(p2, run2)
    assert p2.written == [b"109.ts|"]                                     # starts a buffer back again


def test_an_expired_url_ends_the_run_and_looks_the_stream_up_again(monkeypatch):
    import urllib.error

    def gone(url, n):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    now = 1_000_000.0
    f, run, clock = _feeder(monkeypatch, _playlist(100, now - 60, 12), now, get=gone)
    p = _FakeProc(99)
    f._feed_segments(p, run)
    assert p.closed and p.written == [] and f._expired and f._restart and not f._fed


def test_undated_segments_play_the_same(monkeypatch):
    now = 1_000_000.0
    undated = [wc.Seg((s.seq, None, s.dur, s.url, False)) for s in _playlist(100, now - 60, 12)]
    f, run, clock = _feeder(monkeypatch, undated, now)
    p = _FakeProc(2)
    f._feed_segments(p, run)
    assert p.written == [b"109.ts|", b"110.ts|"] and not f._seg_broken


def test_an_empty_playlist_hands_the_stream_to_ffmpeg(monkeypatch):
    f, run, clock = _feeder(monkeypatch, [], 1_000_000.0)
    p = _FakeProc(1)
    f._feed_segments(p, run)
    assert f._seg_broken and f._restart and p.written == []


def test_dash_segments_are_fetched_by_number_and_checked_against_the_hls_numbering(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(wc, "seg_head", lambda url: 112)
    f, run, clock = _feeder(monkeypatch, _playlist(100, now - 60, 12), now,
                            seg={"timing": "t", "dash": "https://v/2160?x=1"})
    p = _FakeProc(1)
    f._feed_segments(p, run)
    assert p.written == [b"d109|"]
    monkeypatch.setattr(wc, "seg_head", lambda url: 400)                  # numbered differently: HLS instead
    f._next_seq, f._restart = None, False
    p2 = _FakeProc(1)
    f._feed_segments(p2, {})
    assert f._dash_broken and f._restart and p2.written == []


PLAYLIST = """#EXTM3U
#EXT-X-TARGETDURATION:5
#EXT-X-MEDIA-SEQUENCE:217038
#EXT-X-PROGRAM-DATE-TIME:2026-11-22T06:11:15.132+00:00
#EXTINF:5.0,
a.ts
#EXTINF:4.5,
https://x/b.ts
#EXT-X-DISCONTINUITY
#EXT-X-PROGRAM-DATE-TIME:2026-11-22T06:12:00.000+00:00
#EXTINF:5.0,
c.ts
"""


def test_the_playlist_dates_every_segment():
    import datetime
    segs = wc.parse_playlist(PLAYLIST, "https://m/hls/index.m3u8")
    t0 = datetime.datetime(2026, 11, 22, 6, 11, 15, 132000, tzinfo=datetime.timezone.utc).timestamp()
    assert [s.seq for s in segs] == [217038, 217039, 217040]
    assert segs[0].url == "https://m/hls/a.ts" and segs[1].url == "https://x/b.ts"
    assert abs(segs[1].pdt - (t0 + 5.0)) < 1e-6 and segs[1].dur == 4.5
    assert segs[2].disc and abs(segs[2].pdt - (t0 + 44.868)) < 1e-3


def test_every_frame_has_the_plain_header():
    arr = np.zeros((2, 3, 3), np.uint8)
    assert wc.pack(arr)[:4] == b"BSWC" and len(wc.pack(arr)) == 12 + 18


def test_a_blip_is_retried_but_an_answer_is_not(monkeypatch):
    import urllib.error
    monkeypatch.setattr(wc.time, "sleep", lambda s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("connection reset")
        return b"ok"
    assert wc._retrying(flaky) == b"ok" and len(calls) == 3
    calls.clear()

    def busy():
        calls.append(1)
        raise urllib.error.HTTPError("u", 503, "busy", {}, None)
    try:
        wc._retrying(busy)
    except urllib.error.HTTPError:
        pass
    assert len(calls) == wc.SEG_TRIES                                     # 5xx: tried again (then given up)
    calls.clear()

    def expired():
        calls.append(1)
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    try:
        wc._retrying(expired)
    except urllib.error.HTTPError:
        pass
    assert len(calls) == 1                                                # 403 is an answer: no retry


def test_a_webcam_can_ask_for_4k_whatever_its_crops_need():
    f = wc.formats_of(_live_info())
    full = [(448, 175, None)]
    assert wc.choose_format(f, full, hw=True)["h"] == 720                         # auto: what the crop needs
    assert wc.choose_format(f, full, hw=True, quality="max")["h"] == 2160
    assert wc.choose_format(f, full, hw=True, quality=1440)["h"] == 1440
    assert wc.choose_format(f, full, hw=True, quality=1080)["h"] == 1080
    assert wc.choose_format(f, full, hw=False, quality="max")["h"] == 1080        # no video engine: HLS's best
    assert wc.quality_of(["auto", 1080, "auto"]) == 1080 and wc.quality_of(["auto", 1440, "max"]) == "max"
    assert wc.quality_of(["auto"]) is None


def test_a_chosen_quality_cuts_every_crop_from_the_streams_own_pixels():
    assert wc.plan(3840, 2160, [(448, 175, None)]) is None                       # auto: the 640 px picture is plenty
    p = wc.plan(3840, 2160, [(448, 175, None)], force=True)
    assert p["exact"] and p["full_w"] == 3840 and (p["out_w"], p["out_h"]) == (448, 175)


def test_quality_is_checked_in_the_config():
    import pytest
    from hub import config
    base = {"source": {"type": "youtube", "url": "https://www.youtube.com/watch?v=abcdefghijk"}}
    for bad in ("4k", 0, True, 99999):
        with pytest.raises(config.ConfigError):
            config._validate_webcams({"cams": {"a": {**base, "quality": bad}}})
    for good in ("auto", "max", 2160, 1080):
        config._validate_webcams({"cams": {"a": {**base, "quality": good}}})


def test_whole_pixels_set_on_1080p_is_the_nearest_whole_multiple_on_1440p():
    k4 = {"x": 0.0, "y": 0.3, "w": round(4 * 448 / 1920, 6), "snap": True}         # 4:1, set on 1080p
    l, t, r, b = wc.crop_box(2560, 1440, 448, 175, k4)
    assert (r - l, b - t) == (5 * 448, 5 * 175)                                     # 5:1 on 1440p (it'd be 5.33)
    assert abs((l + r) / 2 - (0.0 + k4["w"] / 2) * 2560) <= 1                       # about the same centre
    assert wc.crop_box(640, 360, 448, 175, k4) != (0, 0, 448, 175)                  # the 640 picture: framing kept
    assert wc.snap_k(k4, 640, 360, 448, 175, loose=True) == 0


def test_whole_pixels_crops_that_disagree_get_the_most_detail_not_the_least():
    f = wc.formats_of(_live_info())
    on1080 = (448, 175, {"x": 0.0, "y": 0.3, "w": round(4 * 448 / 1920, 6), "snap": True})
    on1440 = (448, 175, {"x": 0.0, "y": 0.3, "w": round(5 * 448 / 2560, 6), "snap": True})
    got = wc.choose_format(f, [on1080, on1440], hw=True)
    assert got["h"] >= 1080                                                          # (was 720p: 2.5 px per LED)


def test_the_detail_picture_is_cut_for_the_zone_playing_not_every_saved_one(monkeypatch):
    f = _feed_shell()
    f.source = {"w": 2560, "h": 1440, "fps": 30, "vcodec": "vp9", "dash": True}
    saved = [(448, 134, None), (224, 134, {"x": 0, "y": 0, "w": 0.94}), (448, 192, None),
             (448, 175, {"x": 0.0, "y": 0.28, "w": 0.875, "snap": True})]
    monkeypatch.setattr(wc, "config_needs", lambda kind, url: saved)
    monkeypatch.setattr(wc, "config_quality", lambda kind, url: "max")
    c = f.consumer(448, 175, {"x": 0.0, "y": 0.28, "w": 0.875, "snap": True})
    live = f.live_wants()
    assert live == [c.want()]
    p = wc.plan(2560, 1440, live, force=True)
    assert p["exact"] and (p["w"], p["h"], p["out_w"], p["out_h"]) == (2240, 875, 448, 175)   # 5 x 5, exactly


def test_the_wall_gets_the_exact_cut_and_a_preview_of_another_crop_comes_from_lo():
    f = _feed_shell()
    wall = f.consumer(448, 175, {"x": 0.0, "y": 0.28, "w": 0.875, "snap": True})
    draft = f.consumer(448, 175, {"x": 0.0, "y": 0.30, "w": 0.875, "snap": True}, key=("preview", "t"), draft=True)
    draft.changed -= 10                                                   # held still
    assert f.live_wants() == [wall.want()]
    f.release(wall)
    assert f.live_wants() == [draft.want()]                               # not on the wall: the preview's turn


def test_hours_are_checked_in_the_config():
    import pytest
    from hub import config
    base = {"source": {"type": "youtube", "url": "https://www.youtube.com/watch?v=abcdefghijk"}}
    config._validate_webcams({"cams": {"a": {**base, "hours": {"from": "16:00", "to": "08:00"}}}})
    for bad in ({"from": "25:00", "to": "08:00"}, {"from": "16:00"}, "night"):
        with pytest.raises(config.ConfigError):
            config._validate_webcams({"cams": {"a": {**base, "hours": bad}}})
