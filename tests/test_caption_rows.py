"""The webcam caption as rows of pieces (display/modules/webcam_caption.py rows_of, _l_tag): sections, the name's
lines and separators per row, a size per piece, empty sections dropping with their separator, a row too wide
dropping its last pieces (or wrapping, or moving a piece to the next row), and captions from before rows."""

from datetime import datetime, timezone

import pytest

from display.gfx import Canvas, Font
from display.modules import webcam_caption as cap

_fonts = {}


def _font(p):
    f = _fonts.get(p)
    if f is None:
        f = _fonts[p] = Font()
        f.LoadFont(cap.FONTS.get(p, p))
    return f


NOW = datetime(2026, 11, 22, 9, 20, tzinfo=timezone.utc)


def _cam(rows=None, label="Downtown Vancouver", **ov):
    o = {"style": "tag", "font": "medium", "time_format": "24h", **ov}
    if rows is not None:
        o["rows"] = rows
    return {"label": label, "place": "Main/Terminal", "tz": "UTC", "overlay": o}


def _lines(cam, w=448, h=134):
    """The caption's lines as [[(kind, text), …], …] (what _l_tag draws)."""
    ov = cap.overlay_of(cam)
    ct = cap.Content(cam, ov, now_local=NOW)
    k = cap.Ctx(cap._Zone(Canvas(w, h), (0, 0, w, h)), (0, 0, w, h), ov, cap._font_loader(_font))
    k.head_kind = ct.headline_kind
    room = k.sized(10 ** 4) - 2 * k.pad - 1 - 5
    out = []
    for row in cap.rows_of(ov, ct):
        for ln in cap._fit_row(cap._pieces(k, ct, row), room, row.get("wrap")):
            out.append([(p.kind, p.text) for p in ln])
    return out


def sep(s="dot", pad=True, **kw):
    return {"k": "sep", "s": s, "pad": pad, **kw}


def test_each_row_draws_its_pieces_in_order_with_its_own_separators():
    rows = [{"items": [{"k": "live"}, {"k": "name"}]},
            {"items": [{"k": "time"}, sep("pipe"), {"k": "place"}, sep("em", pad=False), {"k": "date"}]}]
    got = _lines(_cam(rows))
    assert got[0] == [("live", "LIVE"), ("name", "Downtown Vancouver")]
    assert got[1] == [("time", "09:20"), ("sep", " | "), ("place", "Main/Terminal"), ("sep", "—"), ("date", "SUN 22 NOV")]


def test_an_empty_section_drops_with_the_separator_before_it_and_separators_never_lead_or_double():
    rows = [{"items": [sep(), {"k": "weather"}, sep(), {"k": "time"}, sep(), {"k": "credit"}, sep(), sep("pipe"),
                       {"k": "place"}, sep()]}]
    assert _lines(_cam(rows)) == [[("time", "09:20"), ("sep", " · "), ("place", "Main/Terminal")]]


def test_each_line_of_the_name_is_its_own_piece():
    rows = [{"items": [{"k": "name:2"}, sep("dash"), {"k": "name"}]}, {"items": [{"k": "name:3"}]}]
    got = _lines(_cam(rows, label="Peace Bridge\nCanadian Inspection Lanes"))
    assert got == [[("name:2", "Canadian Inspection Lanes"), ("sep", " - "), ("name", "Peace Bridge")]]   # no line 3


def test_a_piece_can_have_its_own_size():
    rows = [{"items": [{"k": "time", "size": "huge"}, sep("space"), {"k": "place", "size": "tiny"}]}]
    cam = _cam(rows)
    ov = cap.overlay_of(cam)
    ct = cap.Content(cam, ov, now_local=NOW)
    k = cap.Ctx(cap._Zone(Canvas(448, 134), (0, 0, 448, 134)), (0, 0, 448, 134), ov, cap._font_loader(_font))
    k.head_kind = ct.headline_kind
    ps = cap._pieces(k, ct, cap.rows_of(ov, ct)[0])
    assert ps[0].f.height == 20 and ps[2].f.height == 6                   # 10x20 and tom-thumb
    h, base = cap._line_metrics(ps)
    assert h == 20 and base == ps[0].f.baseline                            # one baseline for the line


def test_a_row_too_wide_drops_its_last_pieces_but_keeps_and_cuts_the_first():
    rows = [{"items": [{"k": "name"}, sep(), {"k": "place"}, sep(), {"k": "time"}]}]
    got = _lines(_cam(rows), w=120)
    assert [k for k, _ in got[0]] == ["name"] and got[0][0][1].startswith("Downtown")   # cut, never dropped


def test_a_wrapping_row_continues_on_the_next_line_without_a_separator_at_the_break():
    rows = [{"items": [{"k": "place"}, sep(), {"k": "time"}, sep(), {"k": "date"}, sep(), {"k": "credit"}],
             "wrap": True}]
    got = _lines(_cam(rows), w=120)
    assert len(got) >= 2 and all(ln[0][0] != "sep" and ln[-1][0] != "sep" for ln in got)


def test_a_piece_marked_spill_moves_to_the_next_row_when_its_own_is_too_full():
    cam = _cam([{"items": [{"k": "live"}, {"k": "name"}, sep(), {"k": "place", "spill": True}]},
                {"items": [{"k": "time"}]}])
    wide, narrow = _lines(cam), None
    assert [k for k, _ in wide[0]] == ["live", "name", "sep", "place"]
    ov = cap.overlay_of(cam)
    c = Canvas(152, 130)
    cap._l_tag(cap.Ctx(cap._Zone(c, (0, 0, 152, 130)), (0, 0, 152, 130), ov, cap._font_loader(_font)),
               cap.Content(cam, ov, now_local=NOW))
    assert (c.buf.sum(axis=2) > 0).any()                                   # drawn (the place leads the time's row)


def test_captions_from_before_rows_get_rows_that_draw_the_same():
    cam = _cam(None, show={"name": True, "place": True, "time": True, "live": True, "weather": False},
               place_beside=True, separator="space", lines="auto", order=["place", "time"])
    ov = cap.overlay_of(cam)
    rows = cap.legacy_rows(ov, cap.Content(cam, ov, now_local=NOW))
    assert rows[0]["items"] == [{"k": "live"}, {"k": "name"}, {"k": "sep", "s": "space", "pad": False},
                                {"k": "place", "spill": True}]
    assert rows[1]["wrap"] and [i["k"] for i in rows[1]["items"]] == ["time"]


def test_rows_are_checked_in_the_config():
    from hub import config
    good = [{"items": [{"k": "live"}, {"k": "name:2", "size": "large"}, sep("en"), {"k": "orbit"}], "wrap": True}]
    config._validate_rows("x", good)
    for bad in ([{"items": [{"k": "nope"}]}], [{"items": [{"k": "sep", "s": "tilde"}]}],
                [{"items": [{"k": "name", "size": "giant"}]}], [{"items": "name"}], [{"items": []}] * 9):
        with pytest.raises(config.ConfigError):
            config._validate_rows("x", bad)
