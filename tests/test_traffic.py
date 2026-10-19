"""Traffic cams: still-image webcam sources (DriveBC), image age in captions, the Webcams page's grouping."""

import io
import time

import numpy as np
import pytest

from hub import traffic, webcams


def _jpeg(w=800, h=468, colour=(30, 60, 90), strip=(250, 250, 250)):
    from PIL import Image
    a = np.zeros((h, w, 3), np.uint8)
    a[:] = colour
    a[int(h * 0.95):] = strip                                     # DriveBC's caption strip
    out = io.BytesIO()
    Image.fromarray(a).save(out, "JPEG", quality=95)
    return out.getvalue()


class _Resp:
    def __init__(self, body, last_modified="Sun, 18 Oct 2026 07:00:00 GMT", meta=None):
        self.content, self.headers = body, ({"Last-Modified": last_modified} if last_modified else {})
        self.meta = meta

    def raise_for_status(self):
        pass

    def json(self):
        return self.meta


def _fake_get(image, meta):
    calls = []

    def get(url, *a, **k):
        calls.append(url)
        return _Resp(None, meta=meta) if "/api/webcams/" in url else _Resp(image, last_modified=None)
    return get, calls


def test_drivebc_source_checks():
    assert webcams.check_source({"type": "drivebc", "id": 18}) == ("drivebc", "https://www.drivebc.ca/images/18.jpg")
    assert webcams.check_source({"type": "image", "url": "https://x.example/cam.jpg"})[0] == "image"
    for bad in ({"type": "drivebc"}, {"type": "drivebc", "id": "18"}, {"type": "drivebc", "id": True},
                {"type": "image", "url": "rtsp://x/cam"}):
        with pytest.raises(webcams.SourceError):
            webcams.check_source(bad)


def test_fetch_still_crops_drivebcs_caption_and_takes_the_time_from_drivebc(monkeypatch):
    import requests
    get, calls = _fake_get(_jpeg(), {"is_on": True, "last_update_modified": "2026-10-18T13:29:17-07:00"})
    monkeypatch.setattr(requests, "get", get)
    frame, at = webcams.fetch_still("drivebc", "https://www.drivebc.ca/images/18.jpg")
    assert frame.shape[1] == webcams.WORK_W and frame.shape[0] % 2 == 0
    assert frame[-3:].mean() < 120                                # the white strip is gone
    from datetime import datetime
    assert at == datetime.fromisoformat("2026-10-18T13:29:17-07:00").timestamp()
    frame2, at2 = webcams.fetch_still("drivebc", "https://www.drivebc.ca/images/18.jpg", known_at=at)
    assert frame2 is None and at2 == at and calls[-1].endswith("/api/webcams/18/")   # same picture: no download


def test_offline_drivebc_camera_is_an_error(monkeypatch):
    import requests
    get, _ = _fake_get(_jpeg(), {"is_on": False, "last_update_modified": "2026-10-01T05:00:00-07:00"})
    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(webcams.SourceError, match="offline"):
        webcams.fetch_still("drivebc", "https://www.drivebc.ca/images/720.jpg")


def test_image_sources_use_last_modified(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(_jpeg()))
    _, at = webcams.fetch_still("image", "https://x.example/cam.jpg")
    from datetime import datetime, timezone
    assert at == datetime(2026, 10, 18, 7, 0, tzinfo=timezone.utc).timestamp()


def test_still_feed_publishes_every_second_with_the_image_age(monkeypatch):
    import requests
    get, _ = _fake_get(_jpeg(), {"is_on": True, "last_update_modified": "2026-10-18T13:29:17-07:00"})
    monkeypatch.setattr(requests, "get", get)
    f = webcams.feed({"type": "drivebc", "id": 999}, fps=15)
    f.attach()
    try:
        seq, frame = f.wait(0, 5)
        assert frame is not None and f.state == "live" and f.fps == 1 and f.image_t
        seq2, _ = f.wait(seq, 3)
        assert seq2 > seq                                           # republished, so viewers know it's alive
        assert f.describe()["still"] is True
    finally:
        f.detach()
        f.last_view = 0
        f._stop.set()


def test_image_time_is_exact_to_the_second():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from display.modules import webcam_caption as cap
    taken = datetime(2026, 10, 18, 13, 29, 17, tzinfo=ZoneInfo("America/Vancouver")).timestamp()
    cam = {"label": "Lions Gate", "tz": "America/Vancouver"}
    show = {"show": {"age": True}}
    assert dict(cap.Content(cam, cap.overlay_of({"overlay": show}), extra={"taken": taken}).bits())["age"] == "taken 13:29:17"
    twelve = cap.overlay_of({"overlay": {**show, "time_format": "12h"}})
    assert dict(cap.Content(cam, twelve, extra={"taken": taken}).bits())["age"] == "taken 1:29:17 PM"
    rel = cap.overlay_of({"overlay": {**show, "taken_format": "relative"}})
    assert dict(cap.Content(cam, rel, extra={"taken": time.time() - 400}).bits())["age"] == "6 min ago"
    both = cap.overlay_of({"overlay": {**show, "taken_format": "both"}})
    assert dict(cap.Content(cam, both, extra={"taken": taken}).bits())["age"].startswith("taken 13:29:17 · ")
    assert [cap.age_text(s) for s in (5, 300, 3600, 4500)] == ["just now", "5 min ago", "1 h ago", "1 h 15 min ago"]


def test_the_pi_asks_for_drivebc_cameras_by_id():
    from display.modules import webcam as wc
    q = wc.stream_query({"source": {"type": "drivebc", "id": 18}}, "full", 448, 134)
    assert q["type"] == "drivebc" and q["id"] == 18
    q = wc.stream_query({"source": {"type": "image", "url": "https://x/c.jpg", "refresh_sec": 120}}, "full", 448, 134)
    assert q["refresh"] == 120


def _cam(cid, lat, lon, name="Cam", orientation="N", on=True):
    return {"id": cid, "name": name, "caption": f"{name} caption", "location": {"coordinates": [lon, lat]},
            "orientation": orientation, "is_on": on, "marked_stale": False}


def test_traffic_grouping_by_crossing_and_city():
    raw = [_cam(18, 49.315, -123.138, "LGB North End 1", "S"), _cam(20, 49.316, -123.139, "LGB North End 2", "N"),
           _cam(292, 49.218, -122.81, "Port Mann Bridge - W", "W"), _cam(5000, 49.17, -123.13, "Richmond cam"),
           _cam(6000, 55.0, -125.0, "Far away")]                      # outside the area: ignored
    d = traffic.build(raw)
    assert d["count"] == 4
    lg = next(x for x in d["crossings"] if x["short"] == "Lions Gate")
    assert [c["id"] for c in lg["cams"]] == [18, 20] and lg["cams"][0]["look"] == "south"
    cities = {c["city"]: [x["id"] for x in c["cams"]] for c in d["cities"]}
    assert 5000 in cities["Richmond"] and 18 in cities["West Vancouver"]


def test_drivebc_webcam_validates():
    from hub import config
    cfg = {"system": {}, "layout": {}, "modules": {"webcams": {"cams": {"lg": {"label": "Lions Gate", "source": {"type": "drivebc", "id": 18}}}}}}
    config.validate(cfg)
    cfg["modules"]["webcams"]["cams"]["lg"]["source"] = {"type": "drivebc"}
    with pytest.raises(config.ConfigError):
        config.validate(cfg)
