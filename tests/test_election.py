"""US election: the job's reading of NBC's results JSON, and the screen drawing every view from it."""

from datetime import date

from display.gfx import Canvas
from display.modules.us_election import ElectionScreen
from hub.jobs import election

DAY = date(2026, 11, 3)


def cand(last, party, pct, votes, win=False):
    return {"lastName": last, "party": party, "percentVote": pct, "votes": votes, "isWinner": win}


def tip(cands, pin, name="Georgia"):
    return {"raceName": name, "percentIn": pin, "candidates": cands}


def doc(map_data, battle=(), bop=None):
    d = {"currentTime": "2026-11-04T01:00:00Z", "electionDate": "2026-11-03", "mapData": map_data,
         "battlegroundRaces": list(battle)}
    if bop:
        d["globalMarqueeInfo"] = {"desktop": {"balanceOfPower": {"results": bop}}}
    return d


def test_race_phases():
    ga = election.race(tip([cand("Collins", "Rep", 36.3, 900), cand("Ossoff", "Dem", 63.7, 1600, True),
                            cand("Total Write-ins", "Oth", 0, 0)], 54.7), True, "GA", DAY)
    assert ga["ph"] == "called" and ga["w"] == "D" and [c[0] for c in ga["c"]] == ["D", "R"]
    tx = election.race(tip([cand("Paxton", "Rep", 0, 0), cand("Talarico", "Dem", 0, 0)], 0), False, "TX", DAY)
    assert tx["ph"] == "open" and tx["close"] == 1793757600          # 9 pm Eastern (west Texas)
    me = election.race(tip([cand("Collins", "Rep", 0, 0), cand("Jackson", "Dem", 0, 0)], 0), True, "ME", DAY)
    assert me["ph"] == "waiting" and me["lead"] is None
    nc = election.race(tip([cand("Whatley", "Rep", 51.0, 510), cand("Cooper", "Dem", 49.0, 490)], 12), True, "NC", DAY)
    assert nc["ph"] == "counting" and nc["lead"] == "R"


def test_close_time_is_eastern():
    # Georgia's polls close at 7 pm Eastern = 00:00 UTC the next day
    assert election.close_utc("GA", DAY) == 1793750400
    assert election.close_utc("ZZ", DAY) is None


def test_statewide_and_house():
    sen = doc({"GA": {"pollsClosed": True, "tooltip": tip([cand("Ossoff", "Dem", 60, 600), cand("Collins", "Rep", 40, 400)], 30)},
               "OH~P": {"pollsClosed": True, "tooltip": tip([cand("Brown", "Dem", 55, 55), cand("Husted", "Rep", 45, 45)], 5)},
               "US": {"tooltip": {}}},
              battle=[{"stateAbbr": "GA"}, {"stateAbbr": "GA"}, {"stateAbbr": "ZZ"}],
              bop={"S": {"dem": {"total": 40}, "rep": {"total": 31}, "ind": {"total": 0}}})
    states, key = election.statewide(sen, DAY)
    assert set(states) == {"GA", "OH"} and key == ["GA"]
    assert election._bop(sen, "S") == {"d": 40, "r": 31, "i": 0}
    house = doc({"1": {"stateCode": "OH", "pollsClosed": True,
                       "tooltip": tip([cand("A", "Dem", 60, 60, True), cand("B", "Rep", 40, 40)], 80, "Ohio District 1")},
                 "2": {"stateCode": "OH", "pollsClosed": True,
                       "tooltip": tip([cand("C", "Dem", 30, 30), cand("D", "Rep", 70, 70)], 20, "Ohio District 2")},
                 "3": {"stateCode": "WY", "pollsClosed": False,
                       "tooltip": tip([cand("E", "Dem", 0, 0), cand("F", "Rep", 0, 0)], 0, "Wyoming At Large")}},
                battle=[{"stateAbbr": "OH", "district": 2}, {"stateAbbr": "WY", "district": 0}])
    hs, races, key = election.house(house, DAY)
    assert hs["OH"]["seats"] == 2 and hs["OH"]["won"] == {"D": 1} and hs["OH"]["lead"] == {"R": 1}
    assert hs["OH"]["ph"] == "counting" and hs["WY"]["ph"] == "open"
    assert key == ["OH-2", "WY-AL"] and races["OH-2"]["lead"] == "R"


def sample():
    def st(ph, d, r, pin, w=None):
        return {"ph": ph, "pin": pin, "w": w, "lead": "D" if d >= r else "R", "c": [["D", d, "X", w == "D"], ["R", r, "Y", w == "R"]],
                "close": 1793760000}
    states = {"GA": st("called", 64, 36, 55, "D"), "TX": st("open", 0, 0, 0), "ME": st("waiting", 0, 0, 0),
              "NC": st("counting", 49, 51, 20), "IL": st("called", 0, 0, 0, "D")}
    return {"updated_utc": 2e9, "source": "AP via NBC News",
            "bop": {"senate": {"d": 41, "r": 31, "i": 0, "total": 100, "need": 51},
                    "house": {"d": 30, "r": 15, "i": 0, "total": 435, "need": 218},
                    "governor": {"d": 4, "r": 0, "i": 0, "total": 36, "need": 19, "called_only": True}},
            "senate": {"states": states, "key": ["NC", "GA", "TX", "ME"]},
            "governor": {"states": states, "key": ["GA"]},
            "house": {"states": {"OH": {"seats": 15, "won": {"D": 2}, "lead": {"R": 4}, "pin": 30, "ph": "counting", "close": 1},
                                 "WY": {"seats": 1, "won": {}, "lead": {}, "pin": 0, "ph": "open", "close": 1}},
                      "races": {"OH-9": st("counting", 46, 54, 16)}, "key": ["OH-9"]}}


class Loader:
    def __init__(self, data):
        self.data = data

    def get_data(self, name):
        return self.data if name == "election" else None


def test_screen_draws_every_view_and_cycles():
    scr = ElectionScreen({}, Loader(sample()))
    c = Canvas(448, 192)
    entry = {"election_view": "cycle", "cycle_sec": 10}
    assert scr.draw(c, (0, 0, 448, 175), entry, True, now=100.0) is None
    seen = {scr.view_i}
    results = [scr.draw(c, (0, 0, 448, 175), entry, False, now=100.0 + 10 * k) for k in range(1, 4)]
    seen |= {scr.view_i}
    assert results == [None, None, "done"]               # one pass = Senate → House → governors
    assert c.buf[:175].any() and not c.buf[175:].any()     # draws above the clock strip only
    held = ElectionScreen({}, Loader(sample()))
    held.draw(c, (0, 0, 448, 175), {"election_view": "house", "cycle_sec": 10}, True, now=0.0)
    assert held.draw(c, (0, 0, 448, 175), {"election_view": "house", "cycle_sec": 10}, False, now=10.0) == "done"


def test_screen_without_data():
    c = Canvas(448, 192)
    ElectionScreen({}, Loader(None)).draw(c, (0, 0, 448, 175), {}, True, now=0.0)
    assert c.buf.any()


def test_civic_seats_and_fallback(tmp_path, monkeypatch):
    doc = {"candidates": [{"party": "Democratic", "seats": 41}, {"party": "Republican", "seats": 31},
                          {"party": "Independent", "seats": 1}, {"party": "Libertarian", "seats": 0}]}
    assert election.civic_seats(doc) == {"d": 41, "r": 31, "i": 1}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return doc
    written = {}
    monkeypatch.setattr(election.common.session, "get", lambda url, timeout=0: R())
    monkeypatch.setattr(election.common, "write", lambda name, payload: written.update(payload))
    prev = {"source": "AP via NBC", "map_utc": 1.0, "senate": {"states": {"GA": {}}}, "bop": {"senate": {"d": 1, "r": 1, "total": 100, "need": 51}}}
    res = election.civic_fallback(election.DEFAULTS, prev, ["senate: down"])
    assert res["ok"] and written["source"] == "civicAPI seats"
    assert written["bop"]["senate"] == {"d": 41, "r": 31, "i": 1, "total": 100, "need": 51}
    assert written["senate"] == prev["senate"]                  # the map keeps NBC's last numbers


def test_live_changes_flash_call_and_ticker():
    import copy
    a = sample()
    b = copy.deepcopy(a)
    b["updated_utc"] = a["updated_utc"] + 20
    b["senate"]["states"]["NC"].update(ph="called", w="R", c=[["D", 48, "Cooper", False], ["R", 52, "Whatley", True]])
    b["senate"]["states"]["TX"].update(ph="waiting")                     # polls closed
    b["bop"]["senate"]["r"] += 1
    state = {"d": a}

    class L2:
        def get_data(self, name):
            return state["d"]
    scr = ElectionScreen({}, L2())
    c = Canvas(448, 192)
    entry = {"election_view": "senate", "cycle_sec": 60}
    scr.draw(c, (0, 0, 448, 175), entry, True, now=0.0)
    assert not scr.ticker_active()                                       # the first data is no news
    state["d"] = b
    scr.draw(c, (0, 0, 448, 175), entry, False, now=1.0)
    assert scr._flash["senate"]["NC"][0] == "call" and scr._flash["senate"]["TX"][0] == "close"
    assert all(line[0][0].strip().count(":") == 1 for line in scr._news)       # each line led by the time seen
    heads = [line[1][0] for line in scr._news]                           # the governor sample shares these states
    assert scr.ticker_active() and heads.count("CALLED") == 2 and "POLLS CLOSED" in heads and "SENATE" in heads
    assert "Whatley" in "".join(t for t, _ in scr._news[0])
    before = c.buf[175:].copy()
    assert scr.draw_ticker(c, 90, 178, 280, 14, now=3.0)
    assert (c.buf[175:] != before).any()
    # once everything has scrolled through, the status text comes back
    t, shown = 3.0, 0
    while scr.draw_ticker(c, 90, 178, 280, 14, now=t) and shown < 50:
        t += 60.0                                                         # each line scrolls through in under a minute
        shown += 1
    assert shown == 4 and not scr.ticker_active()


def test_house_state_changes_are_not_calls():
    import copy
    a = sample()
    b = copy.deepcopy(a)
    b["updated_utc"] = a["updated_utc"] + 20
    b["house"]["states"]["OH"].update(won={"D": 3}, lead={"R": 5}, pin=40)
    b["house"]["races"]["OH-9"].update(ph="called", w="R", c=[["D", 46, "Kaptur", False], ["R", 54, "Merrin", True]])
    state = {"d": a}

    class L3:
        def get_data(self, name):
            return state["d"]
    scr = ElectionScreen({}, L3())
    c = Canvas(448, 192)
    scr.draw(c, (0, 0, 448, 175), {"election_view": "house"}, True, now=0.0)
    state["d"] = b
    scr.draw(c, (0, 0, 448, 175), {"election_view": "house"}, False, now=1.0)
    assert scr._flash["house"]["OH"][0] == "update"                      # seats moving, not a call
    calls = [line for line in scr._news if line[1][0] == "CALLED"]
    assert len(calls) == 1 and "OH-9 House" in "".join(t for t, _ in calls[0])
