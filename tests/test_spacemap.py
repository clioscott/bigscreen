"""Satellites Overhead's basemaps (hub/spacemap.py): drawn for exactly the wall's view, read back by the display."""

import io

import numpy as np
import pytest
from PIL import Image

from display.modules import issmap
from hub import spacemap

HOME = (45.0, -100.0)          # (a made-up home)


def _view(km, w=448, h=175, home=HOME):
    return issmap.OverheadView(w, h, **issmap.overhead_view(home, w, h, issmap.overhead_of(
        {"overhead": {"radius_km": km}}))["_overhead"])


def test_a_basemap_drawn_by_the_hub_is_the_one_the_display_reads_for_that_view(tmp_path, monkeypatch):
    v = _view(300)
    ln = np.zeros((v.h, v.w), np.uint8)
    ln[5, 7] = issmap.OV_COUNTRY
    lt = np.full((v.h, v.w), 9, np.uint8)
    monkeypatch.setattr(spacemap, "lines", lambda view: ln)
    monkeypatch.setattr(spacemap, "lights", lambda view, timeout=30: lt)
    assert spacemap.ensure(v, tmp_path / "issmap").startswith("drawn")
    assert spacemap.ensure(v, tmp_path / "issmap") == "kept"                  # (the same view: not drawn again)
    monkeypatch.setattr(issmap, "DATA_DIR", str(tmp_path))
    issmap._obases.clear()
    ob = issmap.overhead_base(v)
    assert ob["lines"][5, 7] == issmap.OV_COUNTRY and (ob["lights"] == 9).all()


def test_the_lines_alone_when_the_lights_cant_be_fetched_and_they_are_tried_again_later(tmp_path, monkeypatch):
    v = _view(300)
    monkeypatch.setattr(spacemap, "lines", lambda view: np.zeros((view.h, view.w), np.uint8))

    def fail(view, timeout=30):
        raise OSError("GIBS is down")

    monkeypatch.setattr(spacemap, "lights", fail)
    assert "no lights" in spacemap.ensure(v, tmp_path, now=1000.0)
    assert spacemap.ensure(v, tmp_path, now=2000.0) == "kept"
    assert spacemap.ensure(v, tmp_path, now=1000.0 + spacemap.LIGHTS_RETRY + 1).startswith("drawn")
    monkeypatch.setattr(issmap, "DATA_DIR", str(tmp_path.parent))
    issmap.OVERHEAD_DIRS[:] = [str(tmp_path)]
    try:
        issmap._obases.clear()
        assert issmap.overhead_base(v)["lights"] is None
    finally:
        issmap.OVERHEAD_DIRS.clear()


def test_views_too_wide_for_carto_are_left_to_the_displays_own_lines():
    assert spacemap.drawable(_view(300))
    assert not spacemap.drawable(_view(8000, h=40, home=(0.0, 0.0)))         # (the world round: 1:110m will do)
    assert not spacemap.drawable(_view(1500, home=(78.0, 15.0)))             # (past Mercator's 85°)
    assert spacemap.ensure(_view(1500, home=(78.0, 15.0)), "/nowhere") == "too wide"


def test_the_lights_are_sampled_at_the_views_pixels_keeping_small_towns(monkeypatch):
    v = _view(100)
    west, south, east, north = spacemap.edges(v)
    got = {}

    class R:
        headers = {"content-type": "image/png"}

        def __init__(self, params):
            W, H = params["WIDTH"], params["HEIGHT"]
            img = np.zeros((H, W), np.uint8)
            img[H // 2, W // 2] = 200                                         # (one bright dot, in the middle)
            b = io.BytesIO()
            Image.fromarray(img).save(b, "PNG")
            self.content = b.getvalue()

        def raise_for_status(self):
            pass

    def get(url, headers=None, timeout=None, params=None):
        got.update(params)
        return R(params)

    monkeypatch.setattr("requests.get", get)
    out = spacemap.lights(v)
    assert out.shape == (v.h, v.w) and out.max() == 200 and (out > 0).sum() <= 4
    ys, xs = np.nonzero(out)
    assert abs(xs.mean() - (v.w - 1) / 2) <= 1.5 and abs(ys.mean() - (v.h - 1) / 2) <= 3
    s, w_, n, e = map(float, got["BBOX"].split(","))
    assert (s, w_, n, e) == pytest.approx((south, west, north, east), abs=1e-5)
    assert got["WIDTH"] >= 2 * v.w and got["CRS"] == "EPSG:4326"


def test_one_basemap_per_size_and_old_zooms_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(spacemap.paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(spacemap, "DRAFTS", tmp_path / "drafts")
    monkeypatch.setattr(spacemap, "lines", lambda view: np.zeros((view.h, view.w), np.uint8))
    monkeypatch.setattr(spacemap, "lights", lambda view, timeout=30: None)
    (tmp_path / "issmap").mkdir()
    (tmp_path / "issmap" / "ov_1x1_1_0_0.bin").write_bytes(b"old")
    cfg = {"modules": {"weather_alerts": {"home_lonlat": [HOME[1], HOME[0]]},
                       "sats_overhead": {"map": {"overhead": {"radius_km": 400}}}}}
    out = spacemap.run(cfg)
    assert len(out) == 5 and all(s.startswith("drawn") for s in out.values())   # (left, right, full, tall, wall)
    names = sorted(p.name for p in (tmp_path / "issmap").glob("ov_*.bin"))
    assert len(names) == 5 and "ov_1x1_1_0_0.bin" not in names
    assert spacemap.run({"modules": {}}) == {"skipped": "Satellites Overhead isn't set up"}


def test_with_the_date_line_in_view_each_side_is_drawn_on_the_same_pixel_grid(monkeypatch):
    from hub.weather import carto
    v = _view(600, home=(52.0, 179.0))
    ps = spacemap.parts(v)
    assert len(ps) == 2 and ps[0]["i1"] == ps[1]["i0"] and ps[1]["i1"] == v.w
    px_deg = 360.0 / v.W                                   # (the line runs through a column's middle)
    assert ps[0]["east"] == pytest.approx(180.0, abs=px_deg) and ps[1]["west"] == pytest.approx(-180.0, abs=px_deg)
    assert spacemap.drawable(v)
    seen = []

    def overlay(gv, ov, mfp, group="over", fine_max_w=None):
        seen.append((gv.w, gv.center))
        out = np.zeros((gv.h, gv.w, 4), np.uint8)
        x, y = gv.project(-179.5 if gv.center[1] < 0 else 179.5, 52.0)   # (a point either side of the line)
        out[int(y), int(x)] = (2, 0, 0, 255)
        return out

    monkeypatch.setattr(carto, "render_overlay", overlay)
    ln = spacemap.lines(v)
    assert [w for w, _c in seen] == [p["i1"] - p["i0"] for p in ps]
    ys, xs = np.nonzero(ln == 2)
    for lon in (179.5, -179.5):
        px = (float(v.wx(lon)) - v.left()) % v.W
        assert np.abs(xs - px).min() <= 0.5 and np.abs(ys - float(v.y(52.0))).min() <= 0.5
