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
        cams.append({"key": f"b{i}", "label": f"Bridge {i}", "on": on, "updated": time.time() - 60 * (i + 1),
                     "period": 300, **({} if on else {"why": "camera off"})})
        if on:
            for kind, (w, h) in (("s", job.TILE_S), ("l", job.TILE_L)):
                np.full((h, w, 3), 40 + i * 20, np.uint8).tofile(tmp_path / "bridges" / f"b{i}_{kind}.bin")
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
                {"cams": [{"label": "A", "ids": ["7"]}]}, {"cams": [{"label": "A", "ids": [1]}] * 13}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg(bad))
