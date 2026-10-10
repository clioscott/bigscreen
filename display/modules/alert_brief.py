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


def key_amount(text):
    m = AMOUNT.search(text or "")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


def _when(ts, fmt="%a %H:%M"):
    return datetime.fromtimestamp(ts).strftime(fmt) if ts else ""


def brief(alert):
    """Structured, ordered content for one alert."""
    text = (alert.get("text") or "").replace("\r", "")
    main = text.split("###")[0]
    paras = [p.strip() for p in re.split(r"\n\s*\n", main) if p.strip()]
    paras = [re.sub(r"\s*\n\s*", " ", p) for p in paras]
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
