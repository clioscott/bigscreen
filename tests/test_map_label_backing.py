"""A map label's edge: a dark box sits evenly around its text (1 px either side of the letters); an outline
rings every letter in black."""

import numpy as np
import pytest

from display.modules import maplabels


def _margins(img, row_lo, row_hi):
    band = img[row_lo:row_hi]
    dark = np.where((band == 60).all(axis=2).any(axis=0))[0]          # the backing (200 * 0.3)
    ink = np.where((band == (255, 0, 255)).all(axis=2).any(axis=0))[0]
    return ink.min() - dark.min(), dark.max() - ink.max()


@pytest.mark.parametrize("text", ["ISS", "LOS", "TIANGONG 3 CREW", "AOS"])
def test_a_labels_backing_is_centred_on_its_text(text):
    img = np.full((20, 120, 3), 200, np.uint8)
    item = {"text": text, "rgb": (255, 0, 255), "edge": "box"}
    from display.modules.mappanels import text_w
    maplabels.draw(img, [(item, 10, 5, text, text_w(text) + 2)])
    left, right = _margins(img, 5, 5 + maplabels.LH)
    assert left == right == 1


def test_a_labels_outline_rings_every_letter_in_black():
    from display.modules.mappanels import grow, text_w
    img = np.full((20, 120, 3), 200, np.uint8)
    item = {"text": "ISS", "rgb": (255, 0, 255)}                     # (the default edge)
    maplabels.draw(img, [(item, 10, 5, "ISS", text_w("ISS") + 2)])
    ink = (img == (255, 0, 255)).all(axis=2)
    black = (img == 0).all(axis=2)
    assert ink.any() and (black == (grow(ink) & ~ink)).all()          # exactly a 1 px ring, nothing else darkened
    assert not ((img != 200).any(axis=2) & ~ink & ~black).any()
