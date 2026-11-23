"""The Space Map's panels: blocks of small text along the edges, each showing just the fields picked for it.

``map.panels`` is a list of panels, each {id, on, pos, title, fields, layout, labels, flags, font, opacity, colour,
accent}: ``pos`` one of the nine places (POSITIONS: top/middle/bottom × left/centre/right), the panels in one place
stacked in list order (from the top edge down, from the bottom edge up, or centred); ``fields`` the things to show
(FIELDS: a field is one row, or a few for the lists), ``layout`` "rows" (a table: names left, values right) or "line"
(everything on one line, for a strip along an edge). Nothing is shown for a field without data (a panel with nothing
left is skipped), so a panel can be as small as one number.

Data: data/iss.json (hub/jobs/iss.py: the orbit, crew, docked craft, space weather, launches, relays), NASA ISS Live
(hub/iss_live.py), the satellite layers (display/modules/mapsats.py) and the links worked out for this moment."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

POSITIONS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")
LAYOUTS = ("rows", "line")
PANEL_FONTS = {"tiny": ("tiny", 7), "small": ("small", 9)}
PANEL_DEFAULTS = {"on": True, "pos": "tr", "title": "", "fields": [], "layout": "rows", "labels": True, "flags": True,
                  "font": "tiny", "opacity": 0.72, "colour": "#d7dce4", "accent": "#6e87a0", "crew_names": "full"}
CREW_NAMES = ("full", "last", "role", "flags")      # the crew lists: full names, last names, role + last name, or
                                                    # just each country's flag and how many
ROLE_SHORT = {"Commander": "CMDR.", "Flight Engineer": "FE", "Pilot": "PLT.", "Payload Specialist": "PS",
              "Mission Specialist": "MS", "Spaceflight Participant": "SFP"}
UNITS = {"time": ("12h", "24h"), "speed": ("kmh", "mph", "kms"), "distance": ("km", "mi"), "temp": ("c", "f")}
UNIT_DEFAULTS = {"time": "12h", "speed": "kmh", "distance": "km", "temp": "c"}
MU = 398600.4418
R_E = 6378.137

# every field a panel can show: key -> (group, what the control panel calls it)
FIELDS = {
    # where it is
    "over": ("Where", "What it's over"), "coords": ("Where", "Latitude, longitude"),
    "alt": ("Where", "Altitude"), "speed": ("Where", "Speed"), "local_time": ("Where", "Local time below"),
    "zone": ("Where", "Time zone below"), "utc": ("Where", "UTC now"), "gmt": ("Where", "Station clock (GMT day/time)"),
    # orbit
    "sun_next": ("Orbit", "Next sunrise / sunset"), "sun_after": ("Orbit", "The one after"),
    "sun_state": ("Orbit", "In sunlight / in shadow"), "sun_day": ("Orbit", "Sunrises a day"),
    "period": ("Orbit", "Orbit period"), "incl": ("Orbit", "Inclination"), "apsides": ("Orbit", "Perigee / apogee"),
    "orbit_no": ("Orbit", "Orbit number"), "mass": ("Orbit", "Station mass"),
    "elements_age": ("Orbit", "Age of the orbit data"),
    # crew & missions
    "crew_count": ("Crew", "Crew aboard (count)"), "crew_flags": ("Crew", "Crew by country (flags)"),
    "crew": ("Crew", "Crew list with days up"), "crew_roles": ("Crew", "Crew list with roles"),
    "tg_crew": ("Crew", "Tiangong crew with days up"), "people": ("Crew", "People in space (total)"),
    "people_by": ("Crew", "People in space by station"),
    "expedition": ("Missions", "Expedition and day"), "expedition_ru": ("Missions", "Roscosmos expedition (ISS-NN)"),
    "vehicles": ("Missions", "Crew vehicles and days"), "shenzhou": ("Missions", "Shenzhou mission and day"),
    "docked": ("Missions", "Docked spacecraft"), "docked_ports": ("Missions", "Docked spacecraft with ports"),
    # comms
    "signal": ("Comms", "Signal (AOS / LOS) and for how long"), "nasa_via": ("Comms", "NASA link: relay and ground"),
    "ru_via": ("Comms", "Russian link"), "cn_via": ("Comms", "Tiangong's link"), "ku": ("Comms", "Ku-band video link"),
    # station systems (NASA ISS Live)
    "cabin_p": ("Systems", "Cabin pressure"), "cabin_t": ("Systems", "Cabin temperature"), "o2": ("Systems", "Oxygen"),
    "co2": ("Systems", "CO₂"), "crewlock": ("Systems", "Crewlock pressure"), "water": ("Systems", "Clean water tank"),
    "urine": ("Systems", "Urine tank"), "waste": ("Systems", "Waste water tank"), "upa": ("Systems", "Urine processor"),
    "gyros": ("Systems", "Gyroscopes online"), "mode": ("Systems", "Station mode"),
    "att_mode": ("Systems", "Attitude control"), "nav": ("Systems", "Navigation source"),
    "maneuver": ("Systems", "Maneuver under way"), "ru_mode": ("Systems", "Russian segment mode"),
    "ru_att": ("Systems", "Russian attitude control"), "ru_thrusters": ("Systems", "Russian thrusters"),
    "power_v": ("Systems", "Solar array voltage"), "power_a": ("Systems", "Solar array current"),
    "arrays": ("Systems", "Solar array (SARJ) angles"), "betas": ("Systems", "Array beta gimbals"),
    # space weather (NOAA SWPC)
    "kp": ("Space weather", "Kp index"), "wind": ("Space weather", "Solar wind speed"),
    "bz": ("Space weather", "Magnetic field (Bt / Bz)"), "xray": ("Space weather", "X-ray flux (flare class)"),
    "scales": ("Space weather", "NOAA scales (R / S / G)"),
    # launches (Launch Library 2)
    "launch_next": ("Launches", "Next launch with countdown"), "launches": ("Launches", "Next 3 launches"),
    "launches_5": ("Launches", "Next 5 launches"),
    # satellites
    "sat_counts": ("Satellites", "Satellites per layer"), "sats_total": ("Satellites", "Satellites on the map"),
    "tg_coords": ("Satellites", "Tiangong's position"), "relays_used": ("Satellites", "Relays in use"),
}
LIST_FIELDS = {"crew", "crew_roles", "tg_crew", "people_by", "vehicles", "shenzhou", "docked", "docked_ports",
               "launch_next", "launches", "launches_5", "sat_counts", "betas", "crew_flags", "over"}
GROUPS = list(dict.fromkeys(g for g, _l in FIELDS.values()))


def default_panels(ex=None):
    """The panels when none are set: the old extras' docked list and telemetry where they were, and what used to be
    the caption's second line (sun, local time below, crew, mission)."""
    ex = ex or {}
    tel = ex.get("telemetry") or {}
    dock = ex.get("docked") or {}
    rows = [r for r in tel.get("rows") or ["cabin_p", "cabin_t", "o2", "co2", "gyros", "water"] if r in FIELDS]
    return [
        {"id": "mission", "pos": dock.get("corner", "tl") if dock.get("corner") in POSITIONS else "tl",
         "on": dock.get("on", True) is not False, "title": "", "fields": ["expedition", "docked"]},
        {"id": "systems", "pos": tel.get("corner", "tr") if tel.get("corner") in POSITIONS else "tr",
         "on": tel.get("on", True) is not False, "title": "", "fields": rows},
        {"id": "crew", "pos": "ml", "on": True, "title": "CREW", "fields": ["crew"]},
        {"id": "sun", "pos": "br", "on": True, "title": "", "fields": ["sun_next", "local_time"]},
    ]


def panels_of(mapset, ex=None):
    got = (mapset or {}).get("panels")
    if not isinstance(got, list):
        got = default_panels(ex)
    out = []
    for i, p in enumerate(got):
        if isinstance(p, dict):
            q = {**PANEL_DEFAULTS, "id": f"p{i}", **p}
            if q["pos"] not in POSITIONS:
                q["pos"] = "tr"
            out.append(q)
    return out


def units_of(mapset):
    u = (mapset or {}).get("units")
    u = u if isinstance(u, dict) else {}
    return {k: (u.get(k) if u.get(k) in UNITS[k] else UNIT_DEFAULTS[k]) for k in UNITS}


# --- what the fields say ---------------------------------------------------------------------------------------
def _val(live, k):
    try:
        return float(live["values"][k]["v"]), True
    except (KeyError, TypeError, ValueError):
        return 0.0, False


def _cal(live, k):
    try:
        v = live["values"][k]["cal"]
        return str(v).strip() if v not in (None, "") else None
    except (KeyError, TypeError):
        return None


def _clock(t, tz=None, u=None):
    try:
        from zoneinfo import ZoneInfo
        lt = datetime.fromtimestamp(t, ZoneInfo(tz) if tz else timezone.utc)
    except Exception:
        return None, None
    s = lt.strftime("%I:%M %p").lstrip("0") if (u or {}).get("time") == "12h" else lt.strftime("%H:%M")
    return s, lt


def _countdown(sec):
    sec = max(0, int(sec))
    if sec >= 86400:
        return f"{sec // 86400}D {sec % 86400 // 3600}H"
    if sec >= 3600:
        return f"{sec // 3600}H{sec % 3600 // 60:02d}M"
    return f"{sec // 60}M{sec % 60:02d}S"


def _speed(kms, u):
    if u["speed"] == "kms":
        return f"{kms:.2f} KM/S"
    if u["speed"] == "mph":
        return f"{kms * 2236.94:,.0f} MPH"
    return f"{kms * 3600:,.0f} KM/H"


def _dist(km, u):
    return f"{km * 0.621371:,.0f} MI" if u["distance"] == "mi" else f"{km:,.0f} KM"


def _elements(data):
    """(inclination °, mean motion rev/day, eccentricity, rev number at epoch, epoch unix) from the TLE, or None."""
    try:
        l1, l2 = data["elements"]
        yy, day = int(l1[18:20]), float(l1[20:32])
        epoch = datetime(2000 + yy if yy < 57 else 1900 + yy, 1, 1, tzinfo=timezone.utc).timestamp() + (day - 1) * 86400
        return float(l2[8:16]), float(l2[52:63]), float("0." + l2[26:33].strip()), int(l2[63:68]), epoch
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _flag(code, on=True):
    if not on or not code:
        return ""
    from display.modules.flags import flag_char
    return flag_char(code)


def _days(t, since):
    try:
        return int((t - float(since)) // 86400)
    except (TypeError, ValueError):
        return None


def _short(name, n=18):
    name = str(name).upper()
    return name if len(name) <= n else name[:n - 1] + "."


def rows_for(key, ctx):
    """[(label, value)] (value None: the label is the whole row) for a field, [] when there's nothing to show."""
    d, live, t, u, fl = ctx["data"] or {}, ctx["live"], ctx["t"], ctx["units"], ctx["flags"]
    pos = ctx.get("pos")
    from display.modules import space
    if key == "over":
        row = space._at(d.get("over"), t)
        return [(str(row[1]).upper(), None)] if row else []
    if key == "coords":
        return [("POS", space.coords(*pos, "nsew", 2))] if pos else []
    if key == "alt":
        _x, ok = _val(live, "pos_x")
        if ok:
            r = math.sqrt(sum(_val(live, k)[0] ** 2 for k in ("pos_x", "pos_y", "pos_z")))
            return [("ALT", _dist(r - R_E, u))] if r > R_E else []
        e = _elements(d)
        if e:
            a = (MU / (e[1] * 2 * math.pi / 86400) ** 2) ** (1 / 3)
            return [("ALT", _dist(a - R_E, u))]
        return []
    if key == "speed":
        _x, ok = _val(live, "vel_x")
        if ok:
            v = math.sqrt(sum(_val(live, k)[0] ** 2 for k in ("vel_x", "vel_y", "vel_z"))) / 1000
            return [("SPEED", _speed(v, u))]
        e = _elements(d)
        if e:
            a = (MU / (e[1] * 2 * math.pi / 86400) ** 2) ** (1 / 3)
            return [("SPEED", _speed(math.sqrt(MU / a), u))]
        return []
    if key in ("local_time", "zone"):
        row = space._at(d.get("tz"), t)
        if not row:
            return []
        s, lt = _clock(t, row[1], u)
        if key == "zone":
            off = lt.utcoffset().total_seconds() / 3600 if lt else 0
            return [("ZONE", f"UTC{off:+g}")] if lt else []
        return [("BELOW", s)] if s else []
    if key == "utc":
        s, _lt = _clock(t, None, {"time": "24h"})
        return [("UTC", s)] if s else []
    if key == "gmt":
        g = _cal(live, "gmt")
        return [("GMT", g)] if g else []
    if key in ("sun_next", "sun_after"):
        evs = [e for e in d.get("events") or [] if e[0] > t]
        i = 0 if key == "sun_next" else 1
        if len(evs) <= i:
            return []
        return [(evs[i][1].upper(), "IN " + _countdown(evs[i][0] - t))]
    if key == "sun_state":
        tr = d.get("track") or []
        row = space._at(tr, t)
        return [("NOW", "IN SUNLIGHT" if row[3] else "IN SHADOW")] if row else []
    if key == "sun_day":
        e = _elements(d)
        return [("SUNRISES", f"{e[1]:.1f} A DAY")] if e else []
    if key in ("period", "incl", "apsides", "orbit_no", "elements_age"):
        e = _elements(d)
        if not e:
            return []
        inc, mm, ecc, rev, epoch = e
        if key == "period":
            return [("PERIOD", f"{1440 / mm:.1f} MIN")]
        if key == "incl":
            return [("INCL", f"{inc:.2f}°")]
        if key == "orbit_no":                            # (the TLE's count wraps at 100,000: the ISS passed it in 2016)
            n = rev + int((t - epoch) / 86400 * mm) + (100000 if epoch > 1_480_000_000 and rev < 100000 else 0)
            return [("ORBIT", f"NO. {n:,}")]
        if key == "elements_age":
            return [("TLE", f"{max(0, t - epoch) / 3600:.0f}H OLD")]
        a = (MU / (mm * 2 * math.pi / 86400) ** 2) ** (1 / 3)
        return [("PERI/APO", f"{a * (1 - ecc) - R_E:.0f}/{a * (1 + ecc) - R_E:.0f} KM" if u["distance"] == "km"
                 else f"{(a * (1 - ecc) - R_E) * .621:.0f}/{(a * (1 + ecc) - R_E) * .621:.0f} MI")]
    if key == "mass":
        v, ok = _val(live, "mass")
        return [("MASS", f"{v / 1000:,.0f} T" if u["distance"] == "km" else f"{v * 0.0011023:,.0f} TONS")] if ok and v > 0 else []
    ppl = [p for p in d.get("people") or [] if isinstance(p, dict)]
    iss_ppl = [p for p in ppl if p.get("iss")]
    tg_ppl = [p for p in ppl if not p.get("iss") and "shenzhou" in str(p.get("craft", "")).lower()]
    if key == "crew_count":
        c = d.get("crew") or {}
        n = len(iss_ppl) or c.get("count")
        return [("ABOARD", str(n))] if n else []
    if key == "crew_flags":
        c = d.get("crew") or {}
        by = c.get("by") or []
        return [(" ".join(f"{n}{_flag(code) or code.upper()}" for code, n in by), None)] if by else []
    if key in ("crew", "crew_roles", "tg_crew"):
        who = tg_ppl if key == "tg_crew" else iss_ppl
        how = ctx.get("crew_names") if ctx.get("crew_names") in CREW_NAMES else "full"
        if how == "flags":                                   # 3(ru) 3(us) 1(ca): one row
            by = {}
            for p in who:
                by[p.get("flag") or "??"] = by.get(p.get("flag") or "??", 0) + 1
            row = " ".join(f"{n}{_flag(c) or c.upper()}" for c, n in sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))
            return [(row, None)] if row else []
        out = []
        for p in who:
            parts = str(p["name"]).split()
            last = parts[-1] if parts else ""
            if p.get("flag") == "cn" and len(parts) >= 2:      # (Chinese names: the family name comes first)
                last = parts[0]
            name = _short(p["name"], 20) if how == "full" else _short(
                (ROLE_SHORT.get(p.get("role"), "") + " " + last).strip() if how == "role" else last, 20)
            if key == "crew_roles":
                role = {"Commander": "CDR", "Flight Engineer": "FE", "Pilot": "PLT", "Payload Specialist": "PS"}.get(
                    p.get("role"), _short(p.get("role") or "", 6))
                out.append((_flag(p.get("flag"), fl) + name, role))
            else:
                dd = _days(t, p.get("launched"))
                out.append((_flag(p.get("flag"), fl) + name, f"{dd}D" if dd is not None else ""))
        return out
    if key == "people":
        return [("IN SPACE", str(len(ppl)))] if ppl else []
    if key == "people_by":
        by = {}
        for p in ppl:
            k = "ISS" if p.get("iss") else ("TIANGONG" if "shenzhou" in str(p.get("craft", "")).lower()
                                             else _short(p.get("craft") or "?", 12))
            by[k] = by.get(k, 0) + 1
        return [(k, str(n)) for k, n in by.items()]
    m = d.get("mission") or {}
    if key == "expedition":
        if not m.get("name"):
            return []
        dd = _days(t, m.get("start"))
        return [(str(m["name"]).upper(), f"DAY {dd + 1}" if dd is not None else "")]
    if key == "expedition_ru":
        num = str(m.get("name") or "").split()[-1] if m.get("name") else ""
        return [(_flag("ru", fl) + "ROSCOSMOS", f"ISS-{num}")] if num.isdigit() else []
    if key in ("vehicles", "shenzhou"):
        crafts = {}
        for p in ppl:
            c = p.get("craft") or ""
            if not c or (key == "shenzhou") != ("shenzhou" in c.lower()):
                continue
            if key == "vehicles" and not p.get("iss"):
                continue
            crafts.setdefault(c, (p.get("flag"), p.get("launched")))
        flag_of = {"soyuz": "ru", "dragon": "us", "starliner": "us", "shenzhou": "cn"}
        out = []
        for c, (f, launched) in crafts.items():
            f = next((v for k, v in flag_of.items() if k in c.lower()), f)
            dd = _days(t, launched)
            out.append((_flag(f, fl) + _short(c, 18), f"DAY {dd + 1}" if dd is not None else ""))
        return out
    if key in ("docked", "docked_ports"):
        out = []
        for x in d.get("docked") or []:
            if not isinstance(x, dict):
                continue
            dd = _days(t, x.get("docked"))
            lab = _flag(x.get("flag"), fl) + _short(x.get("name") or "", 18)
            if key == "docked_ports":
                out.append((lab, _short(x.get("port") or "", 16)))
            else:
                out.append((lab, f"{dd}D" if dd is not None else ""))
        return out
    if key == "signal":
        aos = ctx.get("aos")
        if aos is None:
            return []
        since = None
        for ts, a in (live or {}).get("signal") or []:
            if ts <= t and bool(a) == aos:
                since = since if since is not None else ts
            elif ts <= t:
                since = None
        return [("SIGNAL", ("AOS" if aos else "LOS") + (f" {_countdown(t - since)}" if since else ""))]
    if key in ("nasa_via", "ru_via", "cn_via"):
        net = key[:-4]
        lk = next((l for l in ctx.get("links") or [] if l["net"] == net), None)
        if not lk:
            return []
        lab = {"nasa": "NASA", "ru": "RUSSIA", "cn": "TIANGONG"}[net]
        via = str(lk.get("via") or "").upper()
        return [(lab, via + (" > " + str(lk.get("ground")).upper() if lk.get("ground") and lk.get("ground") != lk.get("via") else ""))]
    if key == "ku":
        c = _cal(live, "ku_tx")
        return [("KU-BAND", "TX" if (c or "").upper() == "YES" else "OFF")] if c else []
    simple = {"cabin_p": ("CABIN", "{:.0f} MMHG"), "o2": ("O2", "{:.0f} MMHG"), "co2": ("CO2", "{:.1f} MMHG"),
              "crewlock": ("CREWLOCK", "{:.0f} MMHG"), "water": ("WATER", "{:.0f}%"), "urine": ("URINE", "{:.0f}%"),
              "waste": ("WASTE H2O", "{:.0f}%"), "gyros": ("GYROS", "{:.0f}/4 ONLINE")}
    if key in simple:
        v, ok = _val(live, key)
        lab, fmt = simple[key]
        return [(lab, fmt.format(v))] if ok else []
    if key == "cabin_t":
        v, ok = _val(live, "cabin_t")
        return [("TEMP", f"{v * 9 / 5 + 32:.0f} F" if u["temp"] == "f" else f"{v:.1f} C")] if ok else []
    texts = {"upa": "URINE PROC", "mode": "MODE", "att_mode": "ATTITUDE", "nav": "NAV", "ru_mode": "RS MODE",
             "ru_att": "RS ATTITUDE", "ru_thrusters": "RS THRUST"}
    if key in texts:
        c = _cal(live, key)
        if key == "att_mode" and c:
            c = c.replace("ATT CTRL", "").replace("CMG/THRUSTER ASSIST", "CMG+THR").strip()
        return [(texts[key], _short(c, 16))] if c else []
    if key == "maneuver":
        c = _cal(live, "maneuver")
        return [("MANEUVER", "YES" if (c or "").upper() in ("TRUE", "1", "YES") else "NO")] if c else []
    if key in ("power_v", "power_a"):
        ks = ("v_1a", "v_2a", "v_1b", "v_2b") if key == "power_v" else ("a_1a", "a_2a", "a_1b", "a_2b")
        vals = [v for v, ok in (_val(live, k) for k in ks) if ok]
        if not vals:
            return []
        return [("ARRAYS", f"{sum(vals) / len(vals):.0f} V")] if key == "power_v" else [("ARRAYS", f"{sum(vals):.0f} A")]
    if key == "arrays":
        p, ok1 = _val(live, "sarj_p")
        s, ok2 = _val(live, "sarj_s")
        return [("SARJ", f"P {p:.0f}  S {s:.0f}")] if ok1 and ok2 else []
    if key == "betas":
        got = [(n.upper(), _val(live, "beta_" + n)) for n in ("1a", "2a", "3a", "4a", "1b", "2b")]
        got = [(n, v) for n, (v, ok) in got if ok]
        return [("BETA " + " ".join(n for n, _v in got[i:i + 3]), " ".join(f"{v:.0f}" for _n, v in got[i:i + 3]))
                for i in range(0, len(got), 3)]
    w = d.get("spacewx") or {}
    if key == "kp" and w.get("kp") is not None:
        kp = float(w["kp"])
        word = "QUIET" if kp < 4 else "ACTIVE" if kp < 5 else f"STORM G{min(5, int(kp) - 4)}"
        return [("KP", f"{kp:.1f} {word}")]
    if key == "wind" and w.get("wind") is not None:
        return [("SOLAR WIND", f"{w['wind']:.0f} KM/S")]
    if key == "bz" and w.get("bz") is not None:
        return [("BT/BZ", f"{w.get('bt', 0):.0f}/{w['bz']:+.0f} NT")]
    if key == "xray" and w.get("xray"):
        f = float(w["xray"])
        cls = next(((c, f / b) for c, b in (("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7)) if f >= b), ("A", f / 1e-8))
        return [("X-RAY", f"{cls[0]}{cls[1]:.1f}")]
    if key == "scales" and w.get("scales"):
        s = w["scales"]
        return [("SCALES", f"R{s.get('R', 0)} S{s.get('S', 0)} G{s.get('G', 0)}")]
    if key in ("launch_next", "launches", "launches_5"):
        ls = [x for x in d.get("launches") or [] if isinstance(x, dict) and x.get("net", 0) > t - 1800]
        n = {"launch_next": 1, "launches": 3, "launches_5": 5}[key]
        out = []
        for x in ls[:n]:
            dt = x["net"] - t
            cd = ("T-" + _countdown(dt)) if dt > 0 else "LAUNCHED?"
            out.append((_flag(x.get("flag"), fl) + _short(x.get("name") or "", 22), cd))
            if key == "launch_next" and x.get("rocket"):
                out.append((_short(x["rocket"], 22), x.get("status", "").upper()))
        return out
    if key in ("sat_counts", "sats_total"):
        counts = ctx.get("sat_counts") or {}
        if key == "sats_total":
            return [("SATELLITES", f"{sum(counts.values()):,}")] if counts else []
        from common.satlayers import LAYERS
        return [(_flag(LAYERS[k]["flag"], fl) + LAYERS[k]["label"].upper(), f"{n:,}") for k, n in counts.items()]
    if key == "tg_coords":
        cp = ctx.get("css_pos")
        return [("TIANGONG", space.coords(*cp, "nsew", 1))] if cp else []
    if key == "relays_used":
        used = [str(l.get("via")).upper() for l in ctx.get("links") or [] if l.get("relay")]
        return [("RELAYS", " ".join(used))] if used else []
    return []


# --- drawing ---------------------------------------------------------------------------------------------------
_fonts: dict = {}


def _font(name):
    f = _fonts.get(name)
    if f is None:
        from display.gfx import graphics
        from display.modules.webcam_caption import FONTS
        f = _fonts[name] = graphics.Font()
        f.LoadFont(FONTS[name])
    return f


def text_w(s, font="tiny"):
    f = _font(font)
    return sum(max(0, f.CharacterWidth(ord(ch))) for ch in s)


def draw_text(img, x, y, s, rgb, font="tiny"):
    from display.gfx import Canvas, Color, graphics
    f = _font(font)
    cv = Canvas.__new__(Canvas)
    cv.width, cv.height, cv.buf = img.shape[1], img.shape[0], img
    graphics.DrawText(cv, f, x, y + f.baseline, Color(*rgb), s)


def _hex(s, d):
    from display.modules.mapsats import hex_rgb
    return hex_rgb(s, d)


def measure(p, ctx, max_w):
    """A panel's content and size: (lines, w, h) where lines are [(label, value, is_title)]; None when empty."""
    font, lh = PANEL_FONTS.get(p.get("font"), PANEL_FONTS["tiny"])
    labels = p.get("labels", True)
    rows = []
    for k in p.get("fields") or []:
        if k in FIELDS:
            try:
                got = rows_for(k, {**ctx, "flags": p.get("flags", True), "crew_names": p.get("crew_names")})
            except Exception:
                continue
            if not labels and k not in LIST_FIELDS:          # just the values ("757 MMHG", not "CABIN 757 MMHG")
                got = [(a if b is None else str(b), None) for a, b in got]
            rows += got
    if not rows:
        return None
    title = str(p.get("title") or "").upper()
    if p.get("layout") == "line":
        parts = [(a if b is None else (f"{a} {b}" if labels else str(b))) for a, b in rows]
        s = "  ".join(parts)
        if title:
            s = title + "  " + s
        while text_w(s, font) > max_w - 4 and len(s) > 4:
            s = s[:-2] + "."
        return {"lines": [(s, None, False)], "w": min(max_w, text_w(s, font) + 4), "h": lh + 2, "lh": lh, "font": font,
                "split": 0}
    vw = max((text_w(str(b), font) for a, b in rows if b is not None), default=0)
    room = max_w - 4                                             # (too wide: the names are cut, the values kept)
    fit = lambda a, n: a if text_w(a, font) <= n else next((a[:i] + "." for i in range(len(a) - 1, 0, -1)
                                                           if text_w(a[:i] + ".", font) <= n), a[:1])
    rows = [(fit(a, room - (vw + 3 if b is not None else 0)), b) for a, b in rows]
    lw = max((text_w(a, font) for a, b in rows if b is not None), default=0)
    wide = max((text_w(a, font) for a, b in rows if b is None), default=0)
    w = max(wide, lw + (3 + vw if vw else 0), text_w(title, font) if title else 0) + 4
    lines = ([(title, None, True)] if title else []) + [(a, b, False) for a, b in rows]
    return {"lines": lines, "w": min(max_w, w), "h": lh * len(lines) + 2, "lh": lh, "font": font, "split": lw}


def layout(panels, ctx, w, h, top=0, bottom=0, avoid=None):
    """Where each panel goes: [(panel, measured, x, y)], stacked per place; the top places start under ``top`` rows,
    the bottom ones end above the caption (its box, ``avoid``, where they'd overlap it; else ``bottom`` rows), and
    the middle ones are centred in what the top and bottom of their column leave."""
    by = {k: [] for k in POSITIONS}
    for p in panels:
        if p.get("on", True) and p.get("fields"):
            by[p["pos"]].append(p)
    col_w = max(40, int(w * 0.48))
    meas = {}
    for pos, ps in by.items():
        meas[pos] = [(p, m) for p in ps for m in [measure(p, ctx, w - 2 if pos[1] == "c" else col_w)] if m]
    xof = lambda pos, m: 0 if pos[1] == "l" else w - m["w"] if pos[1] == "r" else (w - m["w"]) // 2
    out = []
    for col in "lcr":
        y = top                                                  # the top, down from under a caption at the top
        for p, m in meas["t" + col]:
            out.append((p, m, xof("t" + col, m), y))
            y += m["h"] + 2
        upper = y
        y = h                                                    # the bottom, up from above the caption
        for p, m in reversed(meas["b" + col]):
            x = xof("b" + col, m)
            lim = h - (bottom if not avoid else 0)
            if avoid and x < avoid[2] and x + m["w"] > avoid[0]:
                lim = min(lim, int(avoid[1]) - 1)
            if min(y, lim) - m["h"] < upper:                     # (no room left between: left out)
                continue
            y = min(y, lim) - m["h"]
            out.append((p, m, x, y))
            y -= 2
        lower = min(y + 2, h - (bottom if not avoid else 0))
        ms = meas["m" + col]
        if ms:
            fits, total = [], -2
            for p, m in ms:                                      # (as many as fit between, in order)
                if total + 2 + m["h"] <= lower - upper:
                    fits.append((p, m))
                    total += 2 + m["h"]
            y = upper + max(0, (lower - upper - total) // 2)
            for p, m in fits:
                out.append((p, m, xof("m" + col, m), y))
                y += m["h"] + 2
    return out


def boxes(placed):
    return [(x, y, x + m["w"], y + m["h"]) for _p, m, x, y in placed]


def draw(img, placed):
    for p, m, x, y in placed:
        h, w = img.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + m["w"]), min(h, y + m["h"])
        if x1 <= x0 or y1 <= y0:
            continue
        op = max(0.0, min(1.0, float(p.get("opacity", 0.72))))
        img[y0:y1, x0:x1] = (img[y0:y1, x0:x1] * (1 - op)).astype(np.uint8)
        txt, acc = _hex(p.get("colour"), (215, 220, 228)), _hex(p.get("accent"), (110, 135, 160))
        for i, (a, b, is_title) in enumerate(m["lines"]):
            yy = y + 1 + m["lh"] * i
            if b is None:
                draw_text(img, x + 2, yy, a, acc if is_title else txt, m["font"])
            else:
                draw_text(img, x + 2, yy, a, acc, m["font"])
                draw_text(img, x + 2 + m["split"] + 3, yy, str(b), txt, m["font"])

