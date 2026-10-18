"""Bridges now: the job's tiles, the display grid in every shape, and config validation."""

import json
import time

import numpy as np
import pytest

from display.modules import bridges as mod
from hub.jobs import bridges as job


def _data(tmp_path, n=8, off=(6,)):
    (tmp_path / "bridges").mkdir()
    cams = []
    for i in range(n):
        on = i not in off
        tiles = {kind: f"c{100 + i}_123_{kind}.bin" for kind in "sl"} if on else None
        cams.append({"key": f"b{i}", "label": f"Bridge {i}", "on": on, "updated": time.time() - 60 * (i + 1),
                     "period": 300, "cam": 100 + i, **({"tiles": tiles} if on else {"why": "camera off"})})
        if on:
            for kind, (w, h) in (("s", job.TILE_S), ("l", job.TILE_L)):
                np.full((h, w, 3), 40 + i * 20, np.uint8).tofile(tmp_path / "bridges" / tiles[kind])
    (tmp_path / "bridges.json").write_text(json.dumps({"tile_s": job.TILE_S, "tile_l": job.TILE_L, "cams": cams}))
    return str(tmp_path)


@pytest.mark.parametrize("size,panel", [((448, 192), "bridges_wall"), ((448, 134), "bridges_full"),
                                        ((223, 134), "bridges"), ((224, 134), "bridges")])
def test_grid_renders_every_shape(tmp_path, size, panel):
    img = mod.render_preview(_data(tmp_path), size, panel)
    assert img.shape == (size[1], size[0], 3) and img.any()


def test_off_camera_gets_a_dark_tile_and_tiles_keep_their_images(tmp_path):
    img = mod.render_preview(_data(tmp_path), (448, 134), "bridges_full")
    tw, th = job.TILE_S
    cell = lambda i: img[(i // 4) * (th + 1) + 20:(i // 4) * (th + 1) + 40, (i % 4) * (tw + 1) + 30:(i % 4) * (tw + 1) + 60]
    assert abs(int(cell(0).mean()) - 40) < 3                     # bridge 0's image (value 40) is there
    assert cell(6).mean() < 40                                   # bridge 6 is off: dark tile (its image would be 160)


def test_side_grid_pages_and_reports_a_pass(tmp_path):
    class Cfg:
        modules = {"bridges": {"page_sec": 0.05}}

    p = mod.BridgesPanel(Cfg(), _data(tmp_path))
    p.page_sec = 0.05                                            # config can't go below 1 s; fast pages for the test
    from display.gfx import Canvas
    c = Canvas(223, 134)
    results = []
    for _ in range(30):
        results.append(p.draw(c, (0, 0, 223, 134), {"panel": "bridges"}, not results))
        time.sleep(0.01)
    assert "done" in results                                     # 2 pages of 4 went by


def test_tile_crop_fills_the_tile():
    from PIL import Image
    img = Image.new("RGB", (800, 468), (200, 10, 10))
    for size in (job.TILE_S, job.TILE_L):
        t = job._tile(img, size)
        assert t.shape == (size[1], size[0], 3) and t[..., 0].min() > 150


def _cfg(br):
    return {"system": {}, "layout": {}, "modules": {"bridges": br}}


def test_config_validation():
    from hub import config
    config.validate(_cfg({"cams": job.DEFAULT_CAMS}))
    config.validate(_cfg({}))
    for bad in ({"cams": "x"}, {"cams": [{"label": "", "ids": [1]}]}, {"cams": [{"label": "A", "ids": []}]},
                {"cams": [{"label": "A", "ids": ["7"]}]}, {"cams": [{"label": "A", "ids": [1]}] * 13},
                {"cams": [{"label": "A", "ids": [True]}]}, {"page_sec": 0}, {"page_sec": "8"}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg(bad))


def test_new_json_waits_for_its_tiles(tmp_path):
    """bridges.json can reach the Pi before the tiles it names (two rsync passes): keep the old set meanwhile."""
    d = _data(tmp_path)

    class Cfg:
        modules = {}

    p = mod.BridgesPanel(Cfg(), d)
    p._load(time.time())
    assert len(p.tiles) == 14                                    # 7 cameras on × 2 sizes
    meta = json.loads((tmp_path / "bridges.json").read_text())
    meta["cams"][0]["tiles"] = {"s": "c100_999_s.bin", "l": "c100_999_l.bin"}     # a newer image, not synced yet
    meta["cams"][0]["label"] = "Renamed"
    (tmp_path / "bridges.json").write_text(json.dumps(meta))
    import os
    os.utime(tmp_path / "bridges.json", (time.time() + 5,) * 2)
    p._checked = 0
    p._load(time.time())
    assert p.meta["cams"][0]["label"] == "Bridge 0"              # still the old set
    for kind, (w, h) in (("s", job.TILE_S), ("l", job.TILE_L)):
        np.full((h, w, 3), 99, np.uint8).tofile(tmp_path / "bridges" / f"c100_999_{kind}.bin")
    p._checked = 0
    p._load(time.time())
    assert p.meta["cams"][0]["label"] == "Renamed" and p.tiles["c100_999_s.bin"][0, 0, 0] == 99


def test_each_side_pages_on_its_own_and_zero_page_sec_is_safe(tmp_path):
    class Cfg:
        modules = {"bridges": {"page_sec": 0}}

    p = mod.BridgesPanel(Cfg(), _data(tmp_path))
    assert p.page_sec >= 1.0
    from display.gfx import Canvas
    c = Canvas(448, 134)
    p.draw(c, (0, 0, 224, 134), {"panel": "bridges"}, True)
    t_left = p.started[("bridges", 0)]
    time.sleep(0.02)
    p.draw(c, (225, 0, 223, 134), {"panel": "bridges"}, True)
    assert p.started[("bridges", 0)] == t_left and ("bridges", 225) in p.started


def test_failed_download_keeps_only_the_same_cameras_tile(tmp_path, monkeypatch):
    from hub import paths
    out = tmp_path / "bridges"
    out.mkdir()
    monkeypatch.setattr(job, "OUT", out)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    for kind in "sl":
        (out / f"c18_100_{kind}.bin").write_bytes(b"x")
    (tmp_path / "bridges.json").write_text(json.dumps({"cams": [
        {"key": "b0", "label": "Lions Gate", "on": True, "cam": 18, "updated": 100, "at": 1,
         "tiles": {"s": "c18_100_s.bin", "l": "c18_100_l.bin"}}]}))
    listing = [{"id": 18, "is_on": True, "last_update_modified": "2026-10-18T13:00:00-07:00"},
               {"id": 72, "is_on": True, "last_update_modified": "2026-10-18T13:00:00-07:00"}]

    class Resp:
        def __init__(self, data=None):
            self.data = data

        def json(self):
            return self.data

        def raise_for_status(self):
            if self.data is None:
                raise OSError("image fetch failed")

    monkeypatch.setattr(job.common.session, "get", lambda url, timeout=0: Resp(listing if "api/webcams" in url else None))
    cfg = {"modules": {"bridges": {"cams": [{"label": "Lions Gate", "ids": [18]}, {"label": "Ironworkers", "ids": [72]}]}}}
    job.run(cfg)
    got = json.loads((tmp_path / "bridges.json").read_text())["cams"]
    assert got[0]["on"] and got[0]["tiles"]["s"] == "c18_100_s.bin" and got[0]["updated"] == 100   # its old image + age
    assert not got[1]["on"] and got[1]["why"] == "no image"     # a new camera never shows another one's tile
