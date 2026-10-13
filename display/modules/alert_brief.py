"""Turn an ECCC alert into a brief that can be read at a glance.

ECCC alert texts follow a fairly stable shape: a one-paragraph summary (what,
how much, when), timing / detail paragraphs, impact sentences ("Local utility
outages are possible"), advice in the imperative ("Allow extra time for
travel"), then boilerplate ("Please continue to monitor…", how to report).
The brief splits a text into those parts so the screen can show the essentials
first and hold on each one long enough to read, instead of scrolling a wall of
text. Amounts (mm, cm, km/h, °C…) are marked so they can be highlighted.
"""

from __future__ import annotations

import re
from datetime import datetime

AMOUNT = re.compile(
    r"(?:(?:up to|near|at least|over|gust(?:s|ing)? (?:of |to |up to )?)\s*)?"
    r"(?:minus |-)?\d+(?:[.,]\d+)?(?:\s*(?:to|-|–|and)\s*(?:minus |-)?\d+(?:[.,]\d+)?)?\s*"
    r"(?:mm|cm|km/h|kilometres per hour|°C|degrees(?: Celsius)?|millimetres|centimetres|metres|m\b|per cent|%)",
    re.I)
BOILER = re.compile(r"(^this (?:warning|watch|advisory|statement) is in effect|^- |please continue to monitor|to report severe weather|send an email|call 1-8|#\w+storm|"
                    r"social media|weather\.gc\.ca|for more information|public safety|ec\.gc\.ca)", re.I)
ADVICE = re.compile(r"^(allow|avoid|be |bring|check|consider|do not|don't|dress|drive|ensure|keep|limit|make sure|"
                    r"monitor|move|never|plan|postpone|prepare|protect|reduce|seek|secure|slow|stay|take|turn|use|"
                    r"visit|watch for|watch out|wear|if (?:you|outside|visibility|travel)|when |have |know )", re.I)
IMPACT = re.compile(r"\b(is possible|are possible|will likely|is likely|are likely|may |could |can cause|"
                    r"damage|outage|flood|pool|washout|visibility|hazardous|dangerous|slippery|treacherous|"
                    r"difficult|challenging|risk|threat|power)", re.I)
TIMEWORD = re.compile(r"\b(tonight|today|tomorrow|this (?:morning|afternoon|evening|weekend)|overnight|"
                      r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|beginning|starting|until|"
                      r"through|ending|continuing|by (?:the )?(?:morning|afternoon|evening|end)|"
                      r"\d{1,2}(?::\d\d)? ?(?:a\.m\.|p\.m\.|am|pm))\b", re.I)

HAZARDS = [
    ("tornado", ("tornado",)), ("thunder", ("thunderstorm", "lightning", "hail")),
    ("freezing", ("freezing rain", "freezing drizzle", "ice pellet", "flash freeze")),
    ("snow", ("snowfall", "snow squall", "blowing snow", "winter storm", "blizzard", "snow")),
    ("wind", ("wind", "gust", "hurricane", "tropical storm")), ("flood", ("storm surge", "flood", "streamflow", "high water")),
    ("heat", ("heat", "humidex")), ("cold", ("extreme cold", "cold", "wind chill")), ("frost", ("frost",)),
    ("fog", ("fog",)), ("smoke", ("smoke", "air quality", "dust")), ("rain", ("rainfall", "rain", "atmospheric river")),
]


def hazard_of(alert):
    name = (alert.get("name") or "").lower()
    for key, words in HAZARDS:
        if any(w in name for w in words):
            return key
    text = (alert.get("text") or "").lower()[:400]            # special weather statements: guess from the text
    for key, words in HAZARDS:
        if any(w in text for w in words):
            return key
    return "general"


def _sentences(paragraph):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", paragraph.strip())
    return [p.strip() for p in parts if p.strip()]


def spans(text):
    """[(chunk, highlighted)] with amounts highlighted."""
    out, pos = [], 0
    for m in AMOUNT.finditer(text):
        if is_motion(text, m.start()):
            continue
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out


def short_where(regions, limit=3):
    """'Metro Vancouver - central including …' ×3 → 'Metro Vancouver'; distinct areas joined."""
    heads = []
    for r in regions or []:
        h = r.split(" - ")[0].split(" including ")[0].strip()
        if h and h not in heads:
            heads.append(h)
    if not heads:
        return ""
    s = ", ".join(heads[:limit])
    return s + (f" +{len(heads) - limit}" if len(heads) > limit else "")


def is_motion(text, start):
    """True if the number at ``start`` is a storm's speed of travel ("moving east at 30 km/h"), not an amount."""
    return bool(re.search(r"\b(moving|travell?ing|speed)\b[^.]{0,24}$", text[max(0, start - 40):start], re.I))


def key_amount(text):
    for m in AMOUNT.finditer(text or ""):
        if not is_motion(text, m.start()):
            return re.sub(r"\s+", " ", m.group(0)).strip()
    return ""


def _when(ts, fmt="%a %H:%M"):
    return datetime.fromtimestamp(ts).strftime(fmt) if ts else ""


def brief(alert):
    """Structured, ordered content for one alert."""
    text = (alert.get("text") or "").replace("\r", "")
    main = text.split("###")[0]
    paras = [p.strip() for p in re.split(r"\n\s*\n", main) if p.strip()]
    paras = [re.sub(r"\s*\n\s*", " ", p) for p in paras]
    # web addresses can't be followed on a wall (and won't wrap): "at EmergencyInfoBC (https://…)" → "at EmergencyInfoBC"
    paras = [re.sub(r"\s*\(?\b(?:https?://|www\.)\S+?\)?(?=[\s.,;:]*(?:\s|$))", "", p).strip() for p in paras]
    paras = [p for p in paras if p]
    summary = paras[0] if paras else ""
    if summary.lower().startswith("this warning is in effect") and len(paras) > 1:
        summary = paras[1]                                   # "...due to the following hazard(s): - …"
    details, impacts, advice = [], [], []
    for p in paras:
        if p == summary:
            continue
        for s in _sentences(p):
            if BOILER.search(s):
                continue
            if ADVICE.search(s):
                advice.append(s)
            elif IMPACT.search(s) and not TIMEWORD.search(s[:40]):
                impacts.append(s)
            else:
                details.append(s)
    timing = next((s for s in _sentences(summary) + details if TIMEWORD.search(s)), "")
    regions = alert.get("regions") or []
    return {
        "hazard": hazard_of(alert),
        "kind": alert.get("type") or "statement",
        "title": (alert.get("name") or "weather alert").upper(),
        "status": alert.get("status") or "",
        "summary": summary,
        "timing": timing,
        "details": details,
        "impacts": impacts,
        "advice": advice,
        "amount": key_amount(summary) or key_amount(" ".join(details)) or key_amount(" ".join(impacts)),
        "where": short_where(regions),
        "cities": cities(regions),
        "areas": area_levels(regions),
        "regions": regions,
        "until": _when(alert.get("expires")),
        "issued": _when(alert.get("published")),
        "ends": _when(alert.get("event_end")),
        "test": bool(alert.get("test")),
    }


def cards(b, level="standard"):
    """Ordered (label, text) cards after the headline; level: brief | standard | full."""
    out = [("WHAT", b["summary"])]
    detail = " ".join(b["details"])
    if b["details"] and (level == "full" or (level == "standard" and len(detail.split()) <= 45)):
        out.append(("DETAILS", detail))                    # short enough to be worth the few seconds
    elif b["timing"] and b["timing"] not in b["summary"]:
        out.append(("WHEN", b["timing"]))
    if level != "brief" and b["impacts"]:
        out.append(("EXPECT", " ".join(b["impacts"])))
    if b["advice"]:
        out.append(("WHAT TO DO", " ".join(b["advice"])))
    if level == "full" and len(b["regions"]) > 1:
        out.append(("WHERE", "; ".join(b["regions"])))
    return [(l, t) for l, t in out if t]


SEVERITY_LEVEL = {"statement": 1, "advisory": 2, "watch": 3, "warning": 4}


def paragraphs(b, level="all"):
    """The whole message as styled paragraphs, most important first, for single-screen layouts.

    style: lead (summary, bold) · detail · impact (what to expect) · advice (what to do, bold) · regions.
    level "essentials" leaves out the detail paragraphs and the full region list."""
    out = [{"style": "lead", "tag": "WHAT", "text": b["summary"]}]
    if level != "essentials":
        for d in _group(b["details"]):
            out.append({"style": "detail", "tag": "DETAILS", "text": d})
    if b["impacts"]:
        out.append({"style": "impact", "tag": "EXPECT", "text": " ".join(b["impacts"])})
    if b["advice"]:
        out.append({"style": "advice", "tag": "DO", "text": " ".join(b["advice"])})
    if b.get("cities") or b["regions"]:                    # the area is never left out (it shrinks instead)
        out.append({"style": "regions", "tag": "AREA", "text": "; ".join((b.get("areas") or [[]])[0] or b["regions"])})
    return [p for p in out if p["text"]]


def _group(sentences, max_words=45):
    """Join detail sentences into a few readable paragraphs."""
    out, cur = [], []
    for s in sentences:
        cur.append(s)
        if sum(len(x.split()) for x in cur) >= max_words:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


_PLACE_PREFIX = re.compile(r"\b(?:the\s+)?(?:City|District|Township|Town|Village|Municipality|Resort Municipality|"
                           r"Corporation of the City|Regional District)\s+of\s+", re.I)


def cities(regions):
    """Place names from ECCC zone names, in order, without duplicates.

    'Metro Vancouver - central including the City of Vancouver, Burnaby and New Westminster'
        → ['Vancouver', 'Burnaby', 'New Westminster']
    A zone without an 'including' list ('Howe Sound') contributes its own name."""
    out = []
    for r in regions or []:
        if " including " in r:
            part = r.split(" including ", 1)[1]
        else:
            head, _, tail = r.partition(" - ")
            part = tail if tail and tail[:1].isupper() else head
        part = _PLACE_PREFIX.sub("", part)
        for name in re.split(r",\s*|\s+and\s+|\s*&\s*", part):
            name = name.strip(" .;")
            if name and name not in out:
                out.append(name)
    return out


# Cities that are always named, whatever the space (short form used when compacting).
KEY_CITIES = [("Vancouver", "Vancouver"), ("Richmond", "Richmond"), ("Burnaby", "Burnaby"), ("Delta", "Delta"),
              ("Surrey", "Surrey"), ("Coquitlam", "Coquitlam"), ("New Westminster", "New West"), ("Langley", "Langley")]


def _key_of(city):
    """The key-city short name a place counts as ('South Delta' → 'Delta'), or None. Only plain
    'Vancouver' is Vancouver — North and West Vancouver are their own cities."""
    for full, short in KEY_CITIES:
        if full == "Vancouver":
            if city == "Vancouver":
                return short
        elif full.lower() in city.lower() and not city.lower().startswith("port "):
            return short
    return None


def zones(regions):
    """[{label, head, part, cities}] — 'Metro Vancouver - North Shore including …' →
    label 'Metro Vancouver (North Shore)', cities ['West Vancouver', 'North Vancouver']."""
    out = []
    for r in regions or []:
        head, _, tail = r.partition(" - ")
        part = tail.split(" including ")[0].strip() if tail else ""
        label = f"{head} ({part[:1].upper() + part[1:]})" if part else head
        out.append({"label": label, "head": head, "part": part[:1].upper() + part[1:], "cities": cities([r])})
    return out


def area_levels(regions):
    """Ways to name the affected area, most detailed first; each is a list of items (one per line in a sidebar).

    0  every city
    1  each zone, with its key cities:   'Metro Vancouver (Central): Vancouver, Burnaby, New West'
    2  zones merged per area:            'Metro Vancouver (All Regions): Vancouver, Richmond, …'
    3  zone names only (strips that sit beside a fuller list)
    Key cities appear at every level but the last."""
    zs = zones(regions)
    if not zs:
        return [[], [], [], []]
    every = []
    for z in zs:
        for cty in z["cities"]:
            if cty not in every:
                every.append(cty)
    lvl1 = []
    for z in zs:
        keys = [k for k in (_key_of(cty) for cty in z["cities"]) if k]
        keys = list(dict.fromkeys(keys))
        lvl1.append(z["label"] + (": " + ", ".join(keys) if keys else ""))
    heads = list(dict.fromkeys(z["head"] for z in zs))
    lvl2, lvl3 = [], []
    for h in heads:
        mine = [z for z in zs if z["head"] == h]
        parts = [z["part"] for z in mine if z["part"]]
        if len(mine) >= 4:
            name = f"{h} (All Regions)"
        elif parts:
            name = f"{h} ({', '.join(parts)})"
        else:
            name = h
        keys = list(dict.fromkeys(k for z in mine for k in (_key_of(c) for c in z["cities"]) if k))
        lvl2.append(name + (": " + ", ".join(keys) if keys else ""))
        lvl3.append(name)
    return [every, lvl1, lvl2, lvl3]


def mentions(alert, place):
    """Does this alert cover ``place`` (e.g. the wall's home city)? Looks at zone names and the place list."""
    if not place:
        return False
    p = place.lower()
    for r in alert.get("regions") or []:
        if p in r.lower():
            return True
    return any(p == c.lower() or p in c.lower() for c in cities(alert.get("regions") or []))


# Hazard groups for the control panel's toggles, with the ECCC alerts each one covers.
# The ECCC data pages (hub/weather/alert_pages.py) shown after an alert's text and area map, by weather type.
HAZARD_PAGES = {
    "rain": ["radar", "precip"], "snow": ["radar", "snowfall", "snowlevel"], "freezing": ["radar", "fzra", "temp"],
    "wind": ["gust", "wind"], "thunder": ["radar", "tstorm"], "tornado": ["radar", "tstorm"],
    "flood": ["sealevel", "gust"], "heat": ["humidex", "temp"], "cold": ["temp", "wind"], "frost": ["temp"],
    "fog": ["fog", "satellite"], "smoke": ["smoke", "satellite"], "general": ["satellite", "radar"],
}

# The forecast variables (hub/weather/alert_data.py) each weather type's special page uses.
HAZARD_VARS = {
    "rain": ["precip", "pop", "temp"], "snow": ["snow", "snowlevel", "temp", "precip"], "freezing": ["fzra", "temp", "precip"],
    "wind": ["wind", "temp"], "thunder": ["tstorm", "pop", "precip"], "tornado": ["tstorm", "wind"],
    "flood": ["sealevel", "wind", "precip"], "heat": ["temp", "dew"], "cold": ["temp", "wind"], "frost": ["temp", "dew"],
    "fog": ["vis", "temp", "dew"], "smoke": ["smoke", "pm25", "wind"], "general": ["temp", "precip", "pop", "wind"],
}

HAZARD_INFO = [
    ("rain", "Rain", "rainfall warning, atmospheric river"),
    ("snow", "Snow & winter storms", "snowfall, snow squall, blowing snow, winter storm, blizzard warnings"),
    ("freezing", "Freezing rain & ice", "freezing rain, freezing drizzle, flash freeze, ice pellets"),
    ("wind", "Wind", "wind, arctic outflow, hurricane, tropical storm warnings"),
    ("thunder", "Thunderstorms & hail", "severe thunderstorm watches and warnings, hail"),
    ("tornado", "Tornadoes", "tornado watches and warnings"),
    ("flood", "Coastal flooding & storm surge", "storm surge, coastal flooding, high water"),
    ("heat", "Heat", "heat warnings, humidex"),
    ("cold", "Extreme cold", "extreme cold, wind chill"),
    ("frost", "Frost", "frost advisories"),
    ("fog", "Fog", "fog advisories, freezing fog"),
    ("smoke", "Air quality & smoke", "special air quality statements, smoke, dust"),
    ("general", "Other special weather statements", "special weather statements that don't fit a group above"),
]


# The wall's own names for ECCC's alert regions (chosen by the user, 2026-10): (name, short code), in display order.
# ECCC wording researched in RESEARCH.md; both eras are covered (one "Metro Vancouver" region until 2023-03,
# five sub-regions since), plus ECCC's NW/NE/SE/SW short forms and the coastal-flooding zones.
REGIONS = [("Richmond", "RICHMOND"), ("Vancouver", "VAN"), ("North Shore", "NORTH VAN"), ("Tri-Cities", "TRI-CITIES"),
           ("Surrey", "SURREY"), ("Howe Sound", "HOWE SOUND"), ("Sea to Sky", "SEA2SKY"), ("Abbotsford", "ABBY"),
           ("Chilliwack", "CHWK"), ("Sunshine Coast", "SUN COAST")]
METRO = ("Metro Vancouver", "METRO")
_METRO_PARTS = {"Richmond", "Vancouver", "North Shore", "Tri-Cities", "Surrey"}
_REGION_RULES = [   # (ECCC name prefix / text, lower case) → our region names
    ("metro vancouver - central", ["Vancouver"]), ("metro vancouver - north shore", ["North Shore"]),
    ("metro vancouver - northeast", ["Tri-Cities"]), ("metro vancouver - southeast", ["Surrey"]),
    ("metro vancouver - southwest", ["Richmond"]), ("metro vancouver - nw", ["North Shore"]), ("metro vancouver - ne", ["Tri-Cities"]),
    ("metro vancouver - se", ["Surrey"]), ("metro vancouver - sw", ["Richmond"]),
    ("coastline of metro vancouver incl. west vancouver", ["North Shore"]), ("coastline of metro vancouver incl. vancouver", ["Vancouver"]),
    ("boundary bay", ["Richmond"]), ("mud and semiahmoo bay", ["Surrey"]),
    ("howe sound", ["Howe Sound"]), ("shoreline of howe sound", ["Howe Sound"]),
    ("sea to sky", ["Sea to Sky"]), ("whistler", ["Sea to Sky"]),
    ("fraser valley - west", ["Abbotsford"]), ("central fraser valley", ["Abbotsford"]),
    ("fraser valley - central", ["Chilliwack"]), ("fraser valley - east", ["Chilliwack"]), ("eastern fraser valley", ["Chilliwack"]),
    ("sunshine coast", ["Sunshine Coast"]),
]


def region_labels(regions, home="Richmond"):
    """ECCC region strings → [(name, short)] in the wall's naming: the home region first, then display order;
    all five Metro Vancouver regions (or the pre-2023 single region) become "Metro Vancouver"."""
    names, other = set(), []
    for r in regions or []:
        low = " ".join(r.lower().split())
        if low == "metro vancouver":
            names |= _METRO_PARTS
            continue
        if low == "fraser valley":
            names |= {"Abbotsford", "Chilliwack"}
            continue
        hit = next((ours for key, ours in _REGION_RULES if low.startswith(key)), None)
        if hit:
            names.update(hit)
        elif r not in other:
            other.append(r)                                    # anything unknown keeps ECCC's own name
    out = []
    if _METRO_PARTS <= names:                                  # all five: one name (it includes the home region)
        out.append(METRO)
        names -= _METRO_PARTS
    ordered = [rg for rg in REGIONS if rg[0] in names]
    ordered.sort(key=lambda rg: rg[0] != home)                # otherwise the wall's own region leads
    return out + ordered + [(o, o.upper()) for o in other]


def area_levels(regions):              # noqa: F811 — the region names replace the city-by-city levels
    """[names, names, names, short codes] — e.g. ["Richmond", "Vancouver"] / ["RICHMOND", "VAN"]."""
    labels = region_labels(regions)
    names = [n for n, _ in labels]
    return [names, names, names, [s for _, s in labels]]
