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


# --- PR #17 review regressions ----------------------------------------------------------------------------------
def test_camera_api_down_still_gets_the_image(monkeypatch):
    import requests

    def get(url, *a, **k):
        if "/api/webcams/" in url:
            raise OSError("DriveBC API down")
        return _Resp(_jpeg(), last_modified=None)
    monkeypatch.setattr(requests, "get", get)
    frame, at = webcams.fetch_still("drivebc", "https://www.drivebc.ca/images/18.jpg")
    assert frame is not None and abs(at - time.time()) < 5


def test_thumbnails_skip_the_camera_api_and_remember_failures(monkeypatch):
    import requests
    from hub import app as hub_app
    calls = []

    def get(url, *a, **k):
        calls.append(url)
        if "/images/77.jpg" in url:
            raise OSError("no such camera")
        return _Resp(_jpeg(), last_modified=None)
    monkeypatch.setattr(requests, "get", get)
    hub_app._dbc_thumbs.clear()
    hub_app._dbc_thumb_failed.clear()
    r = hub_app.webcams_drivebc_thumb(18)
    assert r.media_type == "image/jpeg" and not any("/api/webcams/" in u for u in calls)
    from fastapi import HTTPException
    for _ in range(2):
        with pytest.raises(HTTPException):
            hub_app.webcams_drivebc_thumb(77)
    assert sum("/images/77.jpg" in u for u in calls) == 1           # not retried within the minute


def test_probe_reports_a_bad_still_instead_of_failing(monkeypatch):
    import requests
    from hub import app as hub_app
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("bad host")))
    r = hub_app.webcams_probe({"source": {"type": "image", "url": "https://nope.example/x.jpg"}})
    assert r["ok"] is False and "bad host" in r["error"]


def test_refresh_validation_and_safe_pi_cast():
    from hub import config
    from display.modules import webcam as wc
    base = {"label": "x", "source": {"type": "image", "url": "https://x/c.jpg"}}
    for bad in ("soon", 5, 9999):
        cfg = {"system": {}, "layout": {}, "modules": {"webcams": {"cams": {"a": {**base, "source": {**base["source"], "refresh_sec": bad}}}}}}
        with pytest.raises(config.ConfigError):
            config.validate(cfg)
    assert "refresh" not in wc.stream_query({"source": {**base["source"], "refresh_sec": "soon"}}, "full", 448, 134)


def test_still_feeds_have_no_pacer_and_take_a_new_refresh(monkeypatch):
    import threading
    import requests
    get, _ = _fake_get(_jpeg(), {"is_on": True, "last_update_modified": "2026-10-18T13:29:17-07:00"})
    monkeypatch.setattr(requests, "get", get)
    before = {t.name for t in threading.enumerate()}
    src = {"type": "image", "url": "https://x.example/cam2.jpg", "refresh_sec": 60}
    f = webcams.feed(src)
    try:
        assert not ({t.name for t in threading.enumerate()} - before) & {"webcam-pacer"}
        assert webcams.feed({**src, "refresh_sec": 300}) is f and f.refresh == 300
    finally:
        f.last_view = 0
        f._stop.set()


# --- directions checked by eye, home cities, City of Vancouver cameras ----------------------------------------------
def test_wrong_directions_are_corrected_and_turning_cameras_marked():
    steveston = {"id": 29, "orientation": "S", "caption": "Highway 99 at Steveston interchange, looking south.*"}
    f = traffic.fix(steveston)
    assert f["orientation"] == "N" and f["caption"] == "Highway 99 at Steveston interchange, looking north."
    assert f["turns"] and f["corrected"] and steveston["orientation"] == "S"          # the original is untouched
    unsure = traffic.fix({"id": 573, "orientation": "N", "caption": "Hwy 17 at Hwy 91 Connector, looking north."})
    assert unsure["orientation"] == "" and "looking" not in unsure["caption"]
    plain = {"id": 18, "orientation": "S", "caption": "Lions Gate Bridge north end, looking south."}
    assert traffic.fix(plain) is plain
    c = traffic.camera({**steveston, "name": "Steveston", "location": {"coordinates": [-123.0947, 49.1376]}})
    assert c["look"] == "north" and c["turns"] and c["city"] == "Richmond"


def test_cameras_by_the_river_land_in_the_right_city():
    where = {(49.2083, -123.1304): "Vancouver",          # Oak St at 70 Ave (north end of the Oak St Bridge)
             (49.1818, -123.1030): "Richmond",           # Hwy 99 at Shell Rd
             (49.1710, -122.9511): "Delta",              # Annacis Island
             (49.1763, -122.9617): "Richmond",           # Hwy 91/91A interchange
             (49.1832, -122.9571): "New Westminster",    # Queensborough
             (49.1956, -122.8922): "Surrey",             # Tannery Rd (stal̕əw̓asəm Bridge's south end)
             (49.2343, -122.8785): "Coquitlam",          # Brunette
             (49.3150, -123.1380): "West Vancouver"}     # Lions Gate north end (nearest centre)
    assert {k: traffic.city_of(*k) for k in where} == where


def test_home_cities_come_first():
    raw = [_cam(1, 49.05, -122.30, "Abbotsford cam"), _cam(2, 49.25, -122.98, "Burnaby cam"),
           _cam(3, 49.26, -123.11, "Vancouver cam"), _cam(4, 49.17, -123.13, "Richmond cam")]
    assert [c["city"] for c in traffic.build(raw)["cities"]] == ["Vancouver", "Richmond", "Burnaby", "Abbotsford"]


def test_every_crossing_has_its_cameras_once():
    for name, short, ids, note in traffic.CROSSINGS:
        assert ids and len(ids) == len(set(ids)), name
    names = [x[0] for x in traffic.CROSSINGS]
    for want in ("George Massey", "Oak Street", "Knight Street", "Hwy 91", "Queensborough", "Alex Fraser", "stal̕əw̓asəm",
                 "Port Mann", "Ironworkers", "Lions Gate"):
        assert any(want in n for n in names), want


def test_crossing_labels_are_drawable():
    from hub.jobs import drivebc
    raw = [_cam(609, 49.1956, -122.8922, "Tannery Road - N")]
    x = traffic.build(raw, drivebc.fold)["crossings"][0]
    assert x["short"] == "stal̕əw̓asəm" and x["label"] == "stalewasem" and x["note"]


def test_city_of_vancouver_pages_become_image_cameras():
    row = {"name": "Cambie Bridge", "url": "https://trafficcams.vancouver.ca/cambiebridge.htm",
           "geo_point_2d": {"lat": 49.271, "lon": -123.115}}
    html = ('<img src="cameraimages/CambieBridgeNorth.jpg" alt="Cambie Bridge - North" width="720">'
            '<img src="cameraimages/CambieBridgeSouth.jpg" alt="Cambie Bridge - South">')
    v = traffic.cov_views(row, html)
    assert [c["name"] for c in v] == ["Cambie Bridge - North", "Cambie Bridge - South"]
    assert v[0]["source"] == {"type": "image", "url": "https://trafficcams.vancouver.ca/cameraimages/CambieBridgeNorth.jpg",
                              "refresh_sec": 120}
    assert v[0]["orientation"] == "" and v[0]["credit"] == "City of Vancouver" and v[0]["thumb"] == "/api/webcams/city/cov-CambieBridgeNorth.jpg"
    import json
    pages = {traffic.COV_LIST: json.dumps([row, {"name": "x", "url": "https://elsewhere.example/a.htm"}]), row["url"]: html}
    assert len(traffic.cov_cameras(pages.__getitem__)) == 2                    # only the City's own pages are read


def test_fixing_twice_changes_nothing():
    once = traffic.fix({"id": 29, "orientation": "S", "caption": "Highway 99 at Steveston interchange, looking south.*"})
    assert traffic.fix(once) == once and once["turns"]


def test_city_crawl_backs_off_and_keeps_a_good_list(monkeypatch, tmp_path):
    from hub import app as hub_app
    monkeypatch.setattr(hub_app, "CITY_CACHE", tmp_path / "city.json")
    full = [{"id": f"cov-{i}", "source": {"url": f"https://x/{i}.jpg"}} for i in range(100)]
    monkeypatch.setattr(hub_app, "_city", {"at": {"Vancouver": 1.0}, "lists": {"Vancouver": list(full)}, "tried": 0.0,
                                           "urls": {}, "loaded": True})
    monkeypatch.setattr(traffic, "CITY_SOURCES", {"Vancouver": lambda get: full[:50],          # lost half its pages
                                                  "Richmond": lambda get: (_ for _ in ()).throw(OSError("down"))})
    started = []
    monkeypatch.setattr(hub_app.threading, "Thread",
                        lambda target, **k: started.append(target) or type("T", (), {"start": lambda s: target(*k.get("args", ()))})())
    hub_app._city_lists()
    hub_app._city_lists()                                                    # within the hour: no second crawl
    assert len(started) == 1 and len(hub_app._city["lists"]["Vancouver"]) == 100 and not hub_app._city_lock.locked()
    assert hub_app._city["at"] == {"Vancouver": 1.0}                        # neither city counts as freshly crawled


def test_old_vancouver_cache_is_reused_and_richmond_crawled(monkeypatch, tmp_path):
    import json
    from hub import app as hub_app
    old = [{"id": "cov-A", "source": {"url": "https://trafficcams.vancouver.ca/cameraimages/A.jpg"}}]
    (tmp_path / "cov.json").write_text(json.dumps({"at": 5.0, "list": old}))
    monkeypatch.setattr(hub_app, "CITY_CACHE", tmp_path / "city.json")
    monkeypatch.setattr(hub_app, "COV_CACHE", tmp_path / "cov.json")
    monkeypatch.setattr(hub_app, "_city", {"at": {}, "lists": {}, "tried": 0.0, "urls": {}, "loaded": False})
    rmd = [{"id": "rmd-x_eb", "source": {"url": "https://www.richmond.ca/trafficcam/x_eb.jpg"}}]
    monkeypatch.setattr(traffic, "CITY_SOURCES", {"Vancouver": lambda get: old, "Richmond": lambda get: rmd})
    monkeypatch.setattr(hub_app.threading, "Thread", lambda target, **k: type("T", (), {"start": lambda s: target(*k.get("args", ()))})())
    lists = hub_app._city_lists()
    assert set(lists) == {"Vancouver", "Richmond"} and set(hub_app._city["at"]) == {"Vancouver", "Richmond"}
    assert hub_app._city["urls"]["rmd-x_eb"].endswith("/x_eb.jpg")
    assert json.loads((tmp_path / "city.json").read_text())["lists"]["Richmond"] == rmd
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        hub_app.webcams_city_thumb("rmd-not-listed")                         # never an arbitrary URL


def test_richmond_map_and_pages_become_image_cameras():
    page = ("""<script> L.marker([49.162741, -123.145126], { alt: 'x', icon: VideoIcon }).addTo(mymap)
               .bindPopup("<h6>7100 block at Granville Avenue</h6><a href='IntersectionDetails.aspx?id=2753'>Live</a>");</script>
               <script> L.marker([49.17, -123.13], {}).addTo(mymap)
               .bindPopup("<h6>Garden City Road &amp; Williams Road</h6><a href='IntersectionDetails.aspx?id=9'>Live</a>");</script>""")
    m = traffic.richmond_markers(page)
    assert m[0] == (49.162741, -123.145126, "7100 block at Granville Avenue", traffic.RMD_BASE + "IntersectionDetails.aspx?id=2753")
    assert m[1][2] == "Garden City Road & Williams Road"
    html = ('<img id="a" onerror="imageNotFound(this)" src="https://www.richmond.ca/trafficcam/vdd_granville_7100_eb.jpg" '
            'alt="Live Camera Image: 7100 block at Granville Avenue Eastbound" />'
            '<img src="https://www.richmond.ca/trafficcam/vdd_granville_7100_nb.jpg" alt="Live Camera Image: 7100 block at Granville Avenue" />'
            '<img src="https://elsewhere.example/trafficcam/z.jpg" alt="Live Camera Image: z" />')
    v = traffic.richmond_views(49.16, -123.14, "7100 block at Granville Avenue", html)
    assert [c["name"] for c in v] == ["7100 block at Granville Avenue - Eastbound", "7100 block at Granville Avenue - Northbound"]
    assert v[0]["id"] == "rmd-vdd_granville_7100_eb" and v[0]["thumb"] == "/api/webcams/city/rmd-vdd_granville_7100_eb.jpg"
    assert v[0]["source"]["type"] == "image" and v[0]["credit"] == "City of Richmond" and v[0]["orientation"] == ""
    pages = {traffic.RMD_BASE: page, m[0][3]: html, m[1][3]: ""}
    assert len(traffic.richmond_cameras(pages.__getitem__)) == 2


def test_only_stale_cities_are_crawled(monkeypatch, tmp_path):
    import time as _t
    from hub import app as hub_app
    monkeypatch.setattr(hub_app, "CITY_CACHE", tmp_path / "city.json")
    van = [{"id": "cov-A", "source": {"url": "https://x/A.jpg"}}]
    monkeypatch.setattr(hub_app, "_city", {"at": {"Vancouver": _t.time()}, "lists": {"Vancouver": van}, "tried": 0.0,
                                           "urls": {}, "loaded": True})
    crawled = []
    monkeypatch.setattr(traffic, "CITY_SOURCES", {"Vancouver": lambda get: crawled.append("V") or van,
                                                  "Richmond": lambda get: crawled.append("R") or []})
    monkeypatch.setattr(hub_app.threading, "Thread",
                        lambda target, args=(), **k: type("T", (), {"start": lambda s: target(*args)})())
    hub_app._city_lists()
    assert crawled == ["R"]                                                  # Vancouver is fresh: left alone


def test_richmond_parsing_is_robust():
    page = ("""L.marker([49.1, -123.1], { icon: StaticCameraIcon }).addTo(mymap);
               L.marker([49.2, -123.2], {}).addTo(mymap).bindPopup("<h6>B</h6><a href='IntersectionDetails.aspx?id=2'>x</a>");""")
    assert traffic.richmond_markers(page) == [(49.2, -123.2, "B", traffic.RMD_BASE + "IntersectionDetails.aspx?id=2")]
    html = '<img alt="Live Camera Image: B Westbound" class="x" src="https://www.richmond.ca/trafficcam/b_wb.jpg">'
    assert [c["name"] for c in traffic.richmond_views(49.2, -123.2, "B", html)] == ["B - Westbound"]
