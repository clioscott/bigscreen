"""The live mirror is fetched from the Pi at 1x and enlarged on the hub (the Pi's 3x encode dimmed the wall)."""
import io

import numpy as np
from PIL import Image


def _png(arr):
    bio = io.BytesIO()
    Image.fromarray(arr, "RGB").save(bio, "PNG")
    return bio.getvalue()


def test_upscale_is_nearest_neighbour():
    from hub.app import upscale_png
    arr = np.zeros((4, 6, 3), np.uint8)
    arr[1, 2] = (255, 10, 20)
    out = np.asarray(Image.open(io.BytesIO(upscale_png(_png(arr), 3))))
    assert out.shape == (12, 18, 3)
    assert (out[3:6, 6:9] == (255, 10, 20)).all()
    assert out[2, 6].sum() == 0 and out[6, 6].sum() == 0


def test_scale_one_passes_through():
    from hub.app import upscale_png
    data = _png(np.full((2, 2, 3), 7, np.uint8))
    assert upscale_png(data, 1) is data


def test_hub_asks_the_pi_for_1x(monkeypatch):
    from hub import app as hub_app
    asked = []

    class R:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = _png(np.zeros((2, 3, 3), np.uint8))

    def get(url, timeout):
        asked.append(url)
        return R()

    monkeypatch.setattr(hub_app.requests, "get", get)
    monkeypatch.setattr(hub_app.pi, "display_url", lambda path: "http://pi:8765" + path)
    resp = hub_app.display_frame(scale=3)
    assert asked and asked[0].endswith("/frame.png?scale=1")
    assert Image.open(io.BytesIO(resp.body)).size == (9, 6)
