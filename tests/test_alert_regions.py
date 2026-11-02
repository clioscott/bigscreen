from display.modules.alert_brief import on_wall


def test_lower_mainland_regions_are_on_the_wall():
    assert on_wall(["Metro Vancouver - southwest including Richmond and Delta"])
    assert on_wall(["Metro Vancouver"])
    assert on_wall(["Fraser Valley - east including Chilliwack"])
    assert on_wall(["Howe Sound"])


def test_off_area_regions_are_dropped():
    assert not on_wall(["Duncan"])
    assert not on_wall(["Greater Victoria", "Southern Gulf Islands"])
    assert not on_wall([])


def test_mixed_alert_stays():
    assert on_wall(["Duncan", "Metro Vancouver - central including the City of Vancouver"])
