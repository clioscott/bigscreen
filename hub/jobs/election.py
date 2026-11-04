"""US election job: live results → data/election.json for the US Election screen.

Source: NBC News's results pages read their numbers from JSON (Associated Press data, NBC Decision Desk calls),
refreshed every 15 s. One request per office (Senate, House, governor) per run; no key. This is not a documented
API, so the job is defensive: a field that goes missing leaves that race blank rather than failing the run.

Poll closing times are not in the feed; ``CLOSE_ET`` holds each state's last closing (Eastern time).

An office whose fetch fails keeps its last results. With ``source`` = "auto", the seat counts come from civicAPI
(civicapi.org, free, no key) when NBC can't be read. civicAPI has no per-state lookup that works on election night
(its race search was down on 2026-11-03), so only the Senate and House seat totals fall back; the map keeps
NBC's last numbers and the screen says which source it is showing.

Settings (``modules.us_election``): ``enabled``, ``source`` ("nbc" | "auto"), ``refresh_interval_sec`` (60),
``season`` ("2026"), ``active_until`` (ISO date; after it the job stops fetching, so it never polls between
elections), ``civicapi_races`` (civicAPI's national summary race ids).
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

from hub import paths
from hub.bc import common
from hub.jobs import _harness
from hub.util import read_json

URL = "https://www.nbcnews.com/firecracker/api/v2/national-results/{season}-elections/{office}-results"
CIVIC = "https://civicapi.org/api/v2/race/{id}"
OFFICES = {"senate": "senate", "house": "house", "governor": "governor"}
SOURCES = {"nbc": "NBC News (AP data)", "auto": "NBC News, civicAPI seat counts if NBC fails"}
DEFAULTS = {"enabled": True, "source": "auto", "refresh_interval_sec": 60, "season": "2026", "active_until": "2026-11-12",
            "civicapi_races": {"senate": 87528, "house": 87524}}
PARTY = {"Dem": "D", "Rep": "R"}

# Each state's last polls closing, Eastern time (hours after midnight on election day; 25 = 1 am next day).
CLOSE_ET = {19: "GA IN KY SC VT VA", 19.5: "NC OH WV",
            20: "AL CT DE DC FL IL ME MD MA MS MO NH NJ OK PA RI TN", 20.5: "AR",
            21: "AZ CO IA KS LA MI MN NE NM NY ND SD TX WI WY", 22: "MT NV UT", 23: "CA ID OR WA",
            24: "HI", 25: "AK"}
CLOSE_HOUR = {st: h for h, sts in CLOSE_ET.items() for st in sts.split()}


def close_utc(st, day: date):
    """Epoch seconds of the state's last polls closing (Eastern = UTC-5 in November)."""
    h = CLOSE_HOUR.get(st)
    if h is None:
        return None
    t = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(hours=h + 5)
    return int(t.timestamp())


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def race(tip: dict, polls_closed, st, day):
    """One race, compact: phase, % in, candidates [[party, %, last name, won]] (D, R first), winner."""
    cands = []
    for c in tip.get("candidates") or []:
        p = c.get("party") or ""
        if p == "Oth" or (c.get("lastName") or "").strip().lower().startswith("total write"):
            continue
        cands.append([PARTY.get(p, "I"), round(_num(c.get("percentVote")), 1), (c.get("lastName") or "").strip(),
                      bool(c.get("isWinner")), int(_num(c.get("votes")))])
    if not cands:
        return None
    order = {"D": 0, "R": 1}
    cands.sort(key=lambda c: (order.get(c[0], 2), -c[1]))
    pin = round(_num(tip.get("percentIn")), 1)
    won = next((c[0] for c in cands if c[3]), None)
    votes = sum(c[4] for c in cands)
    if won:
        ph = "called"
    elif polls_closed is False and pin == 0 and votes == 0:
        ph = "open"
    elif pin == 0 or votes == 0:
        ph = "waiting"
    else:
        ph = "counting"
    lead = max(cands, key=lambda c: c[1])[0] if votes else None
    return {"ph": ph, "pin": pin, "w": won, "lead": lead, "c": [c[:4] for c in cands[:4]],
            "close": close_utc(st, day)}


def _bop(doc, office):
    try:
        b = doc["globalMarqueeInfo"]["desktop"]["balanceOfPower"]["results"][office]
        return {"d": int(b["dem"]["total"]), "r": int(b["rep"]["total"]), "i": int((b.get("ind") or {}).get("total") or 0)}
    except (KeyError, TypeError, ValueError):
        b = (doc.get("bopData") or {}).get("bar") or {}
        return {"d": int((b.get("dem") or {}).get("total") or 0), "r": int((b.get("rep") or {}).get("total") or 0),
                "i": int((b.get("ind") or {}).get("total") or 0)}


def statewide(doc, day):
    """Senate / governor: one race per state (the regular race; a special only when it's the state's only one)."""
    states = {}
    for key, r in (doc.get("mapData") or {}).items():
        st = key.split("~")[0]
        if len(st) != 2 or st == "US" or (st in states and "~" in key):
            continue
        rc = race(r.get("tooltip") or {}, r.get("pollsClosed"), st, day)
        if rc:
            states[st] = rc
    key = list(dict.fromkeys(b.get("stateAbbr") for b in doc.get("battlegroundRaces") or [] if b.get("stateAbbr")))
    return states, [k for k in key if k in states]


def house(doc, day):
    """House: per state, seats won / leading by party and the districts' combined count; plus key districts."""
    by_state, districts = {}, {}
    for r in (doc.get("mapData") or {}).values():
        st = r.get("stateCode")
        tip = r.get("tooltip") or {}
        if not st:
            continue
        rc = race(tip, r.get("pollsClosed"), st, day)
        s = by_state.setdefault(st, {"seats": 0, "won": {}, "lead": {}, "pin": 0.0, "phs": [], "close": close_utc(st, day)})
        s["seats"] += 1
        if not rc:
            continue
        s["phs"].append(rc["ph"])
        s["pin"] += rc["pin"]
        if rc["w"]:
            s["won"][rc["w"]] = s["won"].get(rc["w"], 0) + 1
        elif rc["lead"] and rc["ph"] == "counting":
            s["lead"][rc["lead"]] = s["lead"].get(rc["lead"], 0) + 1
        name = tip.get("raceName") or ""
        num = name.rsplit(" ", 1)[-1]
        districts[f"{st}-{'AL' if 'large' in name.lower() else num}"] = rc
    states = {}
    for st, s in by_state.items():
        phs = s.pop("phs")
        s["pin"] = round(s["pin"] / max(1, len(phs)), 1)
        s["ph"] = ("called" if phs and all(p == "called" for p in phs) else "open" if phs and all(p == "open" for p in phs)
                   else "waiting" if all(p in ("open", "waiting") for p in phs) else "counting")
        states[st] = s
    key = []
    for b in doc.get("battlegroundRaces") or []:
        st, n = b.get("stateAbbr"), b.get("district")
        k = f"{st}-{n}" if n not in (None, 0, "0") else f"{st}-AL"
        if k in districts and k not in key:
            key.append(k)
    return states, {k: districts[k] for k in key}, key


def run(cfg):
    st = common.settings(cfg, "us_election", DEFAULTS)
    if st.get("enabled") is False:
        common.write("election", {"disabled": True})
        return {"ok": True, "message": "US election screen off", "files": ["election.json"]}
    until = str(st.get("active_until") or "")
    if until and date.today().isoformat() > until:
        return {"ok": True, "message": f"idle: past active_until {until}"}
    docs, errors = {}, []
    for name, office in OFFICES.items():
        try:
            r = common.session.get(URL.format(season=st["season"], office=office), timeout=30,
                                   headers={"Accept": "application/json"})
            r.raise_for_status()
            docs[name] = r.json()
        except Exception as e:
            errors.append(f"{name}: {e}")
    prev = read_json(paths.DATA_DIR / "election.json", {}) or {}
    if not docs:
        if st.get("source") == "auto" and prev.get("senate"):
            return civic_fallback(st, prev, errors)
        raise RuntimeError("; ".join(errors))
    any_doc = next(iter(docs.values()))
    try:
        day = date.fromisoformat(any_doc.get("electionDate") or "")
    except ValueError:
        day = date.today()
    out = {"source": "AP via NBC", "as_of": any_doc.get("currentTime"), "election_date": day.isoformat(),
           "errors": errors, "bop": {}}
    for name in OFFICES:                       # an office that failed this time keeps its last results
        if name not in docs and prev.get(name):
            out[name] = prev[name]
            if (prev.get("bop") or {}).get(name):
                out["bop"][name] = prev["bop"][name]
    if "senate" in docs:
        states, key = statewide(docs["senate"], day)
        out["senate"] = {"states": states, "key": key}
        out["bop"]["senate"] = {**_bop(docs["senate"], "S"), "total": 100, "need": 51}
    if "house" in docs:
        states, races, key = house(docs["house"], day)
        out["house"] = {"states": states, "races": races, "key": key}
        out["bop"]["house"] = {**_bop(docs["house"], "H"), "total": 435, "need": 218}
    if "governor" in docs:
        states, key = statewide(docs["governor"], day)
        out["governor"] = {"states": states, "key": key}
        won = [r["w"] for r in states.values() if r["w"]]
        out["bop"]["governor"] = {"d": won.count("D"), "r": won.count("R"), "i": len(won) - won.count("D") - won.count("R"),
                                  "total": len(states), "need": len(states) // 2 + 1, "called_only": True}
    out["map_utc"] = time.time()
    common.write("election", out)
    n = sum(len((out.get(o) or {}).get("states") or {}) for o in OFFICES)
    return {"ok": True, "message": f"{n} state races" + (f"; failed: {'; '.join(errors)}" if errors else ""),
            "files": ["election.json"]}


def civic_seats(doc):
    """civicAPI national summary race → {"d", "r", "i"} seats (its candidates are the parties, with ``seats``)."""
    out = {"d": 0, "r": 0, "i": 0}
    for c in doc.get("candidates") or []:
        p = (c.get("party") or "").lower()
        k = "d" if p.startswith("democrat") else "r" if p.startswith("republican") else "i"
        out[k] += int(c.get("seats") or 0)
    return out


def civic_fallback(st, prev, errors):
    """NBC is down: keep the last map, refresh the Senate and House seat counts from civicAPI."""
    out = dict(prev)
    out["bop"] = dict(prev.get("bop") or {})
    got = []
    for office, rid in (st.get("civicapi_races") or {}).items():
        try:
            r = common.session.get(CIVIC.format(id=int(rid)), timeout=20)
            r.raise_for_status()
            seats = civic_seats(r.json())
        except Exception as e:
            errors.append(f"civicAPI {office}: {e}")
            continue
        out["bop"][office] = {**(out["bop"].get(office) or {}), **seats}
        got.append(office)
    if not got:
        raise RuntimeError("; ".join(errors))
    out.update(source="civicAPI seats", errors=errors, as_of=None)
    common.write("election", out)
    return {"ok": True, "message": f"NBC unavailable: seat counts from civicAPI ({', '.join(got)}); map from "
                                   f"{time.strftime('%H:%M', time.localtime(prev.get('map_utc') or 0))}",
            "files": ["election.json"]}


if __name__ == "__main__":
    _harness.main(run)
