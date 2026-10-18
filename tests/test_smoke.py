"""Smoke tests that run anywhere (CI included): every module imports, and every screen renders its samples.

    python -m pytest -q tests

No network, no hardware and no runtime state: BIGSCREEN_STATE points at an empty temp dir (conftest.py).
"""

import importlib
import pkgutil

import numpy as np
import pytest

import display.modules as dm
import display.modules.weather_alert  # noqa: F401  (alert_designs/_specials import it first, as the display does)

MODULES = sorted(m.name for m in pkgutil.iter_modules(dm.__path__))
BC = ["bc_naad", "bc_roads", "bc_quake", "bc_tsunami", "bc_fire"]


@pytest.mark.parametrize("name", MODULES)
def test_display_module_imports(name):
    importlib.import_module(f"display.modules.{name}")


@pytest.mark.parametrize("name", ["hub.app", "hub.config", "hub.scheduler", "hub.sync", "hub.weather.geo",
                                  "hub.weather.tracks", "hub.bc.common", "display.main", "display.rotation"])
def test_core_imports(name):
    importlib.import_module(name)


def _frame_ok(f, shape=(192, 448, 3)):
    assert isinstance(f, np.ndarray) and f.shape == shape and f.dtype == np.uint8
    assert f.any(), "blank frame"


def test_alert_themes_render(tmp_path):
    from display.modules.alert_themes import THEMES
    from display.modules.weather_alert import render_preview
    for tid in THEMES:
        _frame_ok(render_preview(tid, {"alerts": []}, 5.0, {"_data_dir": str(tmp_path)}))


@pytest.mark.parametrize("modname", BC)
def test_bc_themes_and_samples_render(modname, tmp_path):
    from display.modules import bc_kit
    mod = importlib.import_module(f"display.modules.{modname}")
    for tid in mod.THEMES:
        _frame_ok(mod.render_preview(tid, None, 5.0, {}, {}, str(tmp_path)))
    for sample in mod.SAMPLES:
        _frame_ok(mod.render_preview(mod.Screen.DEFAULT_THEME, sample, 5.0, {}, {}, str(tmp_path)))
        f, pages = bc_kit.render_half(mod.Screen, sample, 3.0, {}, {}, str(tmp_path))
        assert pages >= 1
        _frame_ok(f)


def test_fonts_catalog_covers_every_font():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    cat = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    files = {p.relative_to(root).as_posix() for p in root.rglob("*.bdf")}
    assert files <= set(cat), f"fonts missing from catalog.json: {sorted(files - set(cat))[:10]}"


def test_every_font_the_code_names_exists():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    fonts = root / "assets" / "fonts"
    missing = []
    for py in (root / "display").rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for m in re.finditer(r'"((?:alert|candidates)/[\w.+-]+\.bdf)"', src):
            if not (fonts / m.group(1)).exists():
                missing.append(f"{py.name}: {m.group(1)}")
    assert not missing, missing
