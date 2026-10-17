"""Alert Ready (NAAD) screen: public alerts for BC that aren't weather, earthquakes, tsunami, wildfire or roads
(Amber Alerts, civil emergencies, hazardous materials, evacuation orders, 911 outages, boil-water notices…).

Data: data/naad.json from hub/jobs/naad.py — {"items": [{event, code, severity, urgency, certainty, headline,
description, instruction, areas, polygons, sender, sent, expires, updates, immediate, …}]}.

Themes are styled after how emergency alerts actually reach people in Canada, never like the weather screen:

  alert_ready   the broadcast intrusion — red EMERGENCY ALERT / ALERTE D'URGENCE slab, attention flashes
  phone         the wireless alert on a phone, beside a map of the alert area
  teletype      a CAP message on an amber terminal: every field as a table, the text typed out
  bulletin      a provincial bulletin in BC blue and gold, with a fact column
"""

from __future__ import annotations

import time

from display.modules import bc_kit as k
from display.modules.bc_kit import BLACK, H, W, WHITE, Para

SEV = {"extreme": (255, 40, 40), "severe": (255, 128, 0), "moderate": (255, 205, 0), "minor": (90, 170, 255),
       "unknown": (175, 175, 185)}
HOME = (-123.0918, 49.1442)


def sev_col(item):
    return SEV.get((item.get("severity") or "unknown").lower(), SEV["unknown"])


def icon_of(item):
    s = f"{item.get('code', '')} {item.get('event', '')}".lower()
    for words, name in ((("amber", "child", "missing"), "child"), (("hazmat", "chemical", "biolog", "radiolog", "nuclear", "gas leak", "plume"), "hazmat"),
                        (("911", "telephone", "phone"), "phone"), (("power", "outage", "hydro"), "bolt"),
                        (("flood", "water", "boil", "dike", "dyke"), "drop"), (("evacuat",), "run"),
                        (("test",), "megaphone"), (("police", "shooter", "terror", "threat"), "shield"),
                        (("animal",), "warning-diamond")):
        if any(w in s for w in words):
            return name
    return "siren"


def title_of(item):
    ev = (item.get("event") or "Alert").strip()
    return ev[:1].upper() + ev[1:]


def _ladder(*steps):
    return [dict(zip(("head", "body", "do"), s)) for s in steps]


def _paras(item, head_col, body_col, do_col, upper=False):
    hd = item.get("headline") or ""
    ds = item.get("description") or ""
    ins = item.get("instruction") or ""
    if hd and ds.lower().startswith(hd.lower()[:40]):
        hd = ""                                              # many senders repeat the headline as the first line
    f = (lambda s: s.upper()) if upper else (lambda s: s)
    return [Para(f(hd), "head", head_col, 0), Para(f(ds), "body", body_col, 2), Para(f(ins), "do", do_col, 1)]


def _expires(item):
    e = item.get("expires")
    return f"until {k.day_hhmm(e)}" if e else ""


def _issued(item):
    return k.day_hhmm(item.get("sent") or item.get("effective"))


def _area_line(item):
    return " · ".join(item.get("areas") or [])


# ------------------------------------------------------------------------------------------------ alert_ready
RED, DRED = (205, 0, 0), (70, 0, 0)


def _alert_ready(s, c, item, now, el):
    """Broadcast intrusion: three seconds of attention flashes, then the red slab and the full message."""
    if el < 3.0:
        on = int(el * 4) % 2 == 0
        c.buf[:] = RED if on else (0, 0, 0)
        fg = WHITE if on else RED
        k.icon(c, "siren", W // 2 - 16, 30, fg, 32)
        k.text_c(c, k.font("h24"), W // 2, 92, fg, "EMERGENCY ALERT")
        k.text_c(c, k.font("h20r"), W // 2, 124, fg, "ALERTE D'URGENCE")
        return
    col = sev_col(item)

    def static():
        k.box(c, 0, 0, W, 29, RED)
        k.icon(c, "siren", 6, 6, WHITE, 16)
        k.text(c, k.font("h20"), 28, 4, WHITE, "EMERGENCY ALERT")
        k.text_r(c, k.font("h13r"), W - 6, 8, (255, 200, 200), "ALERTE D'URGENCE")
        # event row: name in the severity colour, severity chip, issuer and time on the right
        f = k.pick(("h18", "h16", "h14"), title_of(item).upper(), 250)
        x = 6
        x += k.icon(c, icon_of(item), x, 34, col, 16) + 6
        k.text(c, f, x, 33 + (18 - f.h) // 2, col, title_of(item).upper())
        sv = (item.get("severity") or "").upper()
        meta = f"{item.get('sender') or ''}  {_issued(item)}".strip()
        fm = k.font("hv10")
        k.text_r(c, fm, W - 6, 36, (200, 200, 205), meta)
        if sv:
            k.chip(c, k.font("m10b"), W - 6 - k.tw(fm, meta) - k.tw(k.font("m10b"), sv) - 14, 35, sv, col, BLACK, 3, 0)
        k.box(c, 0, 54, W, 1, DRED)
        # the message, fitted to the body
        lines, _ = k.fit(_paras(item, WHITE, (210, 210, 215), (255, 220, 90)), W - 16, 112,
                         _ladder(("h16", "lut14", "lutb14"), ("h14", "lut12", "lutb12"), ("lutb12", "hv10", "hvb10"),
                                 ("m10b", "m10", "m10b"), ("s8", "s8", "s8")))
        k.draw_fit(c, lines, 8, 59)
        # footer: where and until when
        k.box(c, 0, 175, W, 17, DRED)
        fa = k.font("hv10")
        exp = _expires(item)
        area = _area_line(item)
        k.text(c, k.font("hvb10"), 6, 179, (255, 160, 160), "AREA")
        aw = W - 46 - (k.tw(fa, exp) + 14 if exp else 0)
        ftxt = k.pick(("hv10", "s8"), area, aw)
        k.text(c, ftxt, 40, 179 + (fa.h - ftxt.h) // 2, WHITE, area if k.tw(ftxt, area) <= aw else _shorten(ftxt, area, aw))
        if exp:
            k.text_r(c, fa, W - 6, 179, (255, 200, 200), exp)

    s.static(c, ("ar", item["id"], item.get("updates")), static)
    # the red slab breathes; a white bar sweeps under it while the alert is immediate
    kb = k.pulse(now, 1.6, 0.75, 1.0)
    c.buf[0:29, :, 0] = (c.buf[0:29, :, 0].astype(float) * kb).astype("uint8")
    if item.get("immediate"):
        x = int((now * 160) % (W + 60)) - 60
        k.box(c, max(0, x), 29, 60 if x >= 0 else 60 + x, 2, WHITE)


def _shorten(f, s, w):
    """Area lists only: keep whole names that fit, then '+N more' (the full list is on other themes)."""
    parts = [p.strip() for p in s.split("·")]
    out = []
    for i, p in enumerate(parts):
        cand = " · ".join(out + [p])
        more = f"  +{len(parts) - i - 1} more" if i < len(parts) - 1 else ""
        if k.tw(f, cand + more) > w:
            return " · ".join(out) + f"  +{len(parts) - len(out)} more"
        out.append(p)
    return " · ".join(out)


# ------------------------------------------------------------------------------------------------ phone
def _crop_for(mp, item, w, h):
    """Map crop (sx, sy) of w×h centred on the alert's polygons (or home)."""
    pts = [mp.project(la, lo) for ring in item.get("polygons") or [] for lo, la in ring] or [mp.project(HOME[1], HOME[0])]
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
    return int(max(0, min(mp.w - w, cx - w / 2))), int(max(0, min(mp.h - h, cy - h / 2)))


def _round_card(c, x, y, w, h, col, r=5):
    k.box(c, x + r, y, w - 2 * r, h, col)
    k.box(c, x, y + r, w, h - 2 * r, col)
    for i in range(r):
        d = int(round(r - (r * r - (r - i) ** 2) ** 0.5))
        k.box(c, x + d, y + i, w - 2 * d, 1, col)
        k.box(c, x + d, y + h - 1 - i, w - 2 * d, 1, col)


def _phone(s, c, item, now, el):
    col = sev_col(item)
    mw = 150
    cw = W - mw - 12

    def static():
        k.gradient_v(c, 0, 0, W, H, (14, 14, 22), (4, 4, 8))
        # the notification card
        _round_card(c, 6, 6, cw, H - 12, (36, 36, 44), 6)
        k.box(c, 6, 26, cw, 1, (60, 60, 72))
        k.icon(c, icon_of(item), 14, 8, col, 16)
        k.text(c, k.font("lutb12"), 36, 9, (235, 235, 240), "Emergency alert")
        k.text_r(c, k.font("lut12"), 6 + cw - 10, 9, (150, 150, 165), _issued(item))
        t = f"{title_of(item)}"
        ft = k.pick(("h16", "h14", "lutb12"), t, cw - 20)
        k.text(c, ft, 14, 31, col, t)
        lines, _ = k.fit(_paras(item, WHITE, (205, 205, 215), (255, 225, 120)), cw - 22, H - 12 - 56 - 16,
                         _ladder(("lutb14", "lut14", "lutb14"), ("lutb12", "lut12", "lutb12"), ("hvb10", "hv10", "hvb10"),
                                 ("m10b", "m10", "m10b"), ("s8", "s8", "s8")))
        k.draw_fit(c, lines, 14, 33 + ft.h + 4)
        fs = k.font("hv10")
        foot = f"{item.get('sender') or ''}  {_expires(item)}".strip()
        k.text(c, k.pick(("hv10", "s8"), foot, cw - 20), 14, H - 6 - fs.h - 4, (140, 140, 155), foot)
        # the map of the area
        mp = k.Map.load(s.data_dir, "naad_lm")
        mx = W - mw - 4
        if mp:
            mh = 150
            sx, sy = _crop_for(mp, item, mw, mh)
            mp.blit(c, mx, 6, sx, sy, mw, mh)
            if item.get("polygons"):
                m = mp.poly_mask(item["polygons"])[sy:sy + mh, sx:sx + mw]
                s.state["mask"] = (m, mx, 6)
                k.tint(c, m, col, 0.35, mx, 6)
                k.paint(c, k.edge(m), col, mx, 6)
            hx, hy = mp.project(HOME[1], HOME[0])
            if 0 <= hx - sx < mw and 0 <= hy - sy < mh:
                k.icon_outlined(c, "house", mx + hx - sx - 8, 6 + hy - sy - 7, (255, 255, 255))
            k.frame(c, mx - 1, 5, mw + 2, mh + 2, (60, 60, 72))
            ay = 6 + mh + 4
        else:
            ay = 6
        k.fit_text(c, _area_line(item), mx, ay, mw, H - ay - 2, ("lutb12", "hv10", "s8"), (220, 220, 230))

    s.static(c, ("ph", item["id"], item.get("updates")), static)
    m = s.state.get("mask")
    if m is not None:
        k.paint(c, k.edge(m[0]), k.dim(col, k.pulse(now, 1.0, 0.3, 1.0)), m[1], m[2])


# ------------------------------------------------------------------------------------------------ teletype
AMB, AMB_D, AMB_DD = (255, 176, 0), (150, 96, 0), (60, 38, 0)


def _teletype(s, c, item, now, el):
    col = sev_col(item)
    rows = [("EVENT", title_of(item).upper()), ("SEVERITY", (item.get("severity") or "").upper()),
            ("URGENCY", (item.get("urgency") or "").upper()), ("CERTAINTY", (item.get("certainty") or "").upper()),
            ("ISSUER", (item.get("sender") or "").upper()), ("ISSUED", _issued(item)),
            ("EXPIRES", k.day_hhmm(item.get("expires")) or "-"), ("UPDATE", str(item.get("updates") or 0))]
    lx, lw = 4, 168
    tx = lx + lw + 8
    key = ("tt", item["id"], item.get("updates"))
    if key != s.state.get("tt_key"):
        fk, fv = k.font("m10"), k.font("m10b")
        paras = _paras(item, AMB, AMB, (255, 230, 150), upper=True)
        lines, _ = k.fit(paras, W - tx - 4, H - 22 - 4,
                         _ladder(("t13b", "t13", "t13b"), ("t12b", "t12", "t12b"), ("m10b", "m10", "m10b"), ("s8", "s8", "s8")))
        s.state.update(tt_key=key, tt_lines=lines, tt_total=sum(len(t[4]) for t in lines))

    def static():
        k.text(c, k.font("m13b"), 4, 2, AMB, "NAAD ALERT RECEIVED")
        k.text_r(c, k.font("m13"), W - 4, 2, AMB_D, "CAP-CP 1.2")
        k.box(c, 0, 17, W, 1, AMB_DD)
        fk, fv = k.font("m10"), k.font("m10b")
        y = 22
        for name, val in rows:
            k.text(c, fk, lx, y, AMB_D, name)
            vcol = col if name == "SEVERITY" else AMB
            for i, ln in enumerate(k.wrap(fv, val, lw - 62) or [""]):
                k.text(c, fv, lx + 62, y + i * 11, vcol, ln)
            y += 11 * max(1, len(k.wrap(fv, val, lw - 62))) + 3
        k.box(c, lx + lw + 2, 20, 1, H - 22, AMB_DD)
        k.text(c, fk, lx, y + 2, AMB_D, "AREA")
        k.fit_text(c, " / ".join(item.get("areas") or []).upper(), lx, y + 14, lw, H - y - 16, ("m10b", "s8"), AMB)

    s.static(c, key, static)
    # type the message out (~90 chars/s), then a blinking block cursor
    budget = int(el * 90)
    cx = cy = None
    for f, colr, dx, dy, ln in s.state["tt_lines"]:
        if budget <= 0:
            break
        part = ln[:budget]
        budget -= len(ln)
        k.text(c, f, tx + dx, 22 + dy, colr, part)
        cx, cy, ch = tx + dx + k.tw(f, part) + 1, 22 + dy, f.h
    if cx is not None and k.blink(now, 0.8):
        k.box(c, cx, cy, 6, ch, AMB)
    k.text_r(c, k.font("m10"), W - 4, H - 11, AMB_D, k.hhmm(now, True))


# ------------------------------------------------------------------------------------------------ bulletin
BLUE, BLUE_D, GOLD = (0, 51, 102), (0, 26, 56), (252, 186, 25)


def _bulletin(s, c, item, now, el):
    col = sev_col(item)
    sw = 118

    def static():
        k.box(c, 0, 0, sw, H, BLUE)
        k.box(c, sw, 0, W - sw, H, (4, 10, 22))
        k.box(c, sw, 0, 2, H, GOLD)
        k.icon(c, icon_of(item), (sw - 32) // 2, 8, GOLD, 32)
        y = 46
        for lab, val, vc in (("SEVERITY", item.get("severity"), col), ("URGENCY", item.get("urgency"), WHITE),
                             ("CERTAINTY", item.get("certainty"), WHITE), ("ISSUED", _issued(item), WHITE),
                             ("EXPIRES", k.day_hhmm(item.get("expires")) or "—", WHITE)):
            if not val:
                continue
            k.text(c, k.font("hvb10"), 8, y, GOLD, lab)
            k.text(c, k.pick(("lutb12", "hvb10"), str(val), sw - 14), 8, y + 11, vc, str(val))
            y += 27
        k.fit_text(c, item.get("sender") or "", 8, y + 2, sw - 14, H - y - 4, ("hv10", "s8"), (170, 190, 220))
        x0 = sw + 10
        t = title_of(item)
        ft = k.pick(("h20", "h18", "h16", "h14"), t, W - x0 - 8)
        k.text(c, ft, x0, 5, GOLD, t)
        hy = 5 + ft.h + 4
        k.box(c, x0, hy, W - x0 - 8, 1, (40, 60, 100))
        area = _area_line(item)
        fa = k.font("hv10")
        alines = k.wrap(fa, area, W - x0 - 8)[:2] if area else []
        body_h = H - hy - 6 - (len(alines) * (fa.h + 1) + 6 if alines else 0)
        lines, _ = k.fit(_paras(item, WHITE, (205, 215, 230), GOLD), W - x0 - 8, body_h,
                         _ladder(("lutb14", "lut14", "lutb14"), ("lutb12", "lut12", "lutb12"), ("hvb10", "hv10", "hvb10"),
                                 ("m10b", "m10", "m10b"), ("s8", "s8", "s8")))
        k.draw_fit(c, lines, x0, hy + 5)
        if alines:
            ay = H - 4 - len(alines) * (fa.h + 1)
            k.box(c, x0, ay - 4, W - x0 - 8, 1, (40, 60, 100))
            for i, ln in enumerate(alines):
                k.text(c, fa, x0, ay + i * (fa.h + 1), (170, 190, 220), ln)

    s.static(c, ("bu", item["id"], item.get("updates")), static)
    # a gold edge runs down the divider
    y = int((now * 60) % (H + 30)) - 30
    k.box(c, sw, max(0, y), 2, 30, WHITE)


# ------------------------------------------------------------------------------------------------ samples
SAMPLES = {
    "amber": {"label": "Amber Alert (Surrey)", "item": {
        "event": "Amber Alert", "code": "amber", "severity": "Severe", "urgency": "Immediate", "certainty": "Observed",
        "sender": "RCMP BC", "sent_rel": -420, "expires_rel": 6 * 3600, "immediate": True, "updates": 0,
        "headline": "Amber Alert issued by Surrey RCMP",
        "description": "Surrey RCMP are looking for Maya, 6, 3 ft 8 in, slim, long black hair, last seen in a pink jacket "
                       "and grey leggings. She is believed to be with a man, 35, driving a silver 2015 Honda CR-V, BC plate "
                       "RK4 21T, last seen heading east on Highway 1 near 176 Street at 2:40 p.m.",
        "instruction": "If you see the child or the vehicle, call 911 immediately. Do not approach.",
        "areas": ["Metro Vancouver", "Fraser Valley"],
        "polygons": [[[-123.30, 49.00], [-121.40, 49.00], [-121.40, 49.45], [-123.30, 49.45], [-123.30, 49.00]]]}},
    "hazmat": {"label": "Shelter in place (Port Moody)", "item": {
        "event": "Hazardous materials", "code": "hazmat", "severity": "Severe", "urgency": "Immediate", "certainty": "Likely",
        "sender": "City of Port Moody", "sent_rel": -900, "expires_rel": 3 * 3600, "immediate": True, "updates": 1,
        "headline": "Shelter in place: chemical release near Ioco Road",
        "description": "A chlorine leak at an industrial site on Ioco Road is producing a plume drifting east over Port Moody "
                       "and Belcarra. Fire crews are on scene.",
        "instruction": "Within 2 km of Ioco Road: go indoors now, close windows and doors, and turn off fans, furnaces and "
                       "air conditioning. Stay inside until told it is safe.",
        "areas": ["Port Moody", "Belcarra", "Anmore"],
        "polygons": [[[-122.90, 49.27], [-122.80, 49.26], [-122.79, 49.33], [-122.88, 49.35], [-122.92, 49.31], [-122.90, 49.27]]]}},
    "evacuation": {"label": "Flood evacuation order (Sumas)", "item": {
        "event": "Evacuation order", "code": "civilEmerg", "severity": "Extreme", "urgency": "Immediate", "certainty": "Observed",
        "sender": "City of Abbotsford", "sent_rel": -300, "expires_rel": 24 * 3600, "immediate": True, "updates": 0,
        "headline": "Evacuation order: Sumas Prairie",
        "description": "Rising water at the Sumas River and a breach of the Sumas dike put the Sumas Prairie at immediate risk "
                       "of flooding.",
        "instruction": "Leave now. Go north on No. 3 Road or Whatcom Road to Highway 1 and report to the reception centre at "
                       "Abbotsford Senior Secondary. Take pets and medication.",
        "areas": ["Abbotsford: Sumas Prairie", "Yarrow"],
        "polygons": [[[-122.30, 49.02], [-122.08, 49.02], [-122.04, 49.10], [-122.22, 49.12], [-122.30, 49.07], [-122.30, 49.02]]]}},
    "outage911": {"label": "911 outage (Metro Vancouver)", "item": {
        "event": "911 service", "code": "911Service", "severity": "Moderate", "urgency": "Immediate", "certainty": "Observed",
        "sender": "E-Comm 911", "sent_rel": -1500, "expires_rel": 2 * 3600, "immediate": False, "updates": 0,
        "headline": "911 service disruption in parts of Metro Vancouver",
        "description": "Some landline and cellular callers in Vancouver, Burnaby and New Westminster may not reach 911 because "
                       "of a network outage.",
        "instruction": "If 911 does not connect, call 604-717-3321 or go to the nearest fire hall or police station.",
        "areas": ["Vancouver", "Burnaby", "New Westminster"],
        "polygons": [[[-123.23, 49.20], [-122.89, 49.18], [-122.89, 49.30], [-123.23, 49.31], [-123.23, 49.20]]]}},
}

THEMES = {
    "alert_ready": {"label": "Alert Ready broadcast", "hint": "Attention flashes, then the red EMERGENCY ALERT slab and the whole message",
                    "draw": _alert_ready, "badge": (214, 9)},
    "phone": {"label": "Phone alert", "hint": "The wireless alert as a phone notification card, beside a map of the area",
              "draw": _phone, "badge": (W - 150 - 4 - 48, H - 22)},
    "teletype": {"label": "CAP teletype", "hint": "Amber terminal: every CAP field as a table, the message typed out",
                 "draw": _teletype, "badge": (W - 130, 4)},
    "bulletin": {"label": "Provincial bulletin", "hint": "BC blue and gold: a fact column, the title, the message and the areas",
                 "draw": _bulletin, "badge": (W - 44, 4)},
}


class Screen(k.BCScreen):
    PANEL, DATA, SETTINGS = "naad_alert", "naad", "naad_alerts"
    THEMES, SAMPLES, DEFAULT_THEME = THEMES, SAMPLES, "alert_ready"
    LABEL = "ALERT READY"

    def extra_pages(self, item):
        return ["phone"] if item.get("polygons") else ["teletype"]

    def half_spec(self, item, theme=None):
        col = sev_col(item)
        theme = theme if theme in THEMES else self.DEFAULT_THEME

        def hero_(c, x, y, mw, mh):
            k.icon(c, icon_of(item), x, y, col, 32)
            return 32, 32

        def map_(c, x, y, w, h, now):
            mp = k.Map.load(self.data_dir, "naad_lm")
            if not mp:
                return
            sx, sy = _crop_for(mp, item, w, h)
            mp.blit(c, x, y, sx, sy, min(w, mp.w - sx), min(h, mp.h - sy))
            key = ("half_mask", item.get("id"), sx, sy, w, h)
            m = self.state.get(key)
            if m is None:
                m = self.state[key] = mp.poly_mask(item.get("polygons") or [])[sy:sy + h, sx:sx + w]
            tint = AMB if theme == "teletype" else col
            k.tint(c, m, tint, 0.35, x, y)
            k.paint(c, k.edge(m), k.dim(tint, k.pulse(now, 1.0, 0.4, 1.0)), x, y)
            hx, hy = mp.project(HOME[1], HOME[0])
            if 0 <= hx - sx < w and 0 <= hy - sy < h:
                k.icon_outlined(c, "house", int(x + hx - sx) - 8, int(y + hy - sy) - 7, WHITE)

        spec = {"label": self.LABEL, "col": col, "chip": ((item.get("severity") or "").upper(), col), "hero": hero_,
                "title": title_of(item), "lines": [(_area_line(item), (220, 220, 230)), (_expires(item), (170, 170, 185))],
                "body": _paras(item, WHITE, (210, 210, 215), (255, 220, 90)), "map": map_ if item.get("polygons") else None}
        return HALF[theme](item, spec, col)


# ------------------------------------------------------------------------------------------------ half-width looks
def _half_alert_ready(item, spec, col):
    """The red EMERGENCY ALERT slab (breathing), the event in capitals, the area on a dark red footer."""
    def summary(c, x, y, w, h, now, el):
        k.icon(c, icon_of(item), x, y + 1, col, 16)
        ty = y + k.fit_text(c, title_of(item).upper(), x + 20, y, w - 20, 36, ("h16", "h14", "lutb12"), col) + 4
        meta = f"{item.get('sender') or ''}  {_issued(item)}".strip()
        ty += k.fit_text(c, meta, x, ty, w, 24, ("hv10", "s8"), (200, 200, 205)) + 3
        hd = item.get("headline") or ""
        if hd:
            k.fit_text(c, hd, x, ty, w, y + h - 34 - ty, ("lutb12", "hvb10", "s8"), WHITE)
        fy = y + h - 30
        k.box(c, x - 4, fy, w + 8, 33, DRED)
        k.text(c, k.font("hvb10"), x, fy + 3, (255, 160, 160), "AREA")
        k.fit_text(c, _area_line(item), x + 34, fy + 3, w - 34, 14, ("hv10", "s8"), WHITE)
        if _expires(item):
            k.text(c, k.font("hv10"), x, fy + 17, (255, 200, 200), _expires(item))

    spec.update(label="EMERGENCY ALERT", chip=("ALERTE D'URGENCE", (255, 200, 200)), summary=summary,
                look={"head": "slab", "head_bg": RED, "head_fg": WHITE, "pulse": True, "head_h": 15, "label_font": "h13",
                      "bg": BLACK, "ladder": {"head": "lutb12", "body": "lut12", "do": "lutb12", "small": "hv10"}})
    return spec


def _half_phone(item, spec, col):
    """A phone notification: the card, the app row, the event in the severity colour."""
    def summary(c, x, y, w, h, now, el):
        ft = k.pick(("h16", "h14", "lutb12"), title_of(item), w)
        k.text(c, ft, x, y, col, title_of(item))
        ty = y + ft.h + 4
        hd = item.get("headline") or ""
        if hd:
            ty += k.fit_text(c, hd, x, ty, w, 40, ("lutb12", "hvb10", "s8"), WHITE) + 4
        ty += k.fit_text(c, _area_line(item), x, ty, w, 26, ("hv10", "s8"), (200, 200, 215)) + 3
        foot = f"{item.get('sender') or ''}  {_expires(item)}".strip()
        k.fit_text(c, foot, x, y + h - 11, w, 11, ("hv10", "s8"), (140, 140, 155))

    spec["body"] = _paras(item, WHITE, (205, 205, 215), (255, 225, 120))
    spec.update(label="Emergency alert", chip=(_issued(item), (150, 150, 165)), summary=summary,
                look={"grad": ((14, 14, 22), (4, 4, 8)), "card": ((36, 36, 44), 5), "head": "line", "head_h": 14,
                      "head_fg": (235, 235, 240), "rule": (60, 60, 72), "label_font": "lutb12",
                      "dot_on": (235, 235, 240), "dot_off": (70, 70, 84), "map_frame": (60, 60, 72),
                      "ladder": {"head": "lutb12", "body": "lut12", "do": "lutb12", "small": "hv10"}})
    return spec


def _half_teletype(item, spec, col):
    """An amber terminal: the CAP fields as a table, then the message typed out in capitals."""
    rows = [("EVENT", title_of(item).upper()), ("SEVERITY", (item.get("severity") or "").upper()),
            ("URGENCY", (item.get("urgency") or "").upper()), ("CERTAINTY", (item.get("certainty") or "").upper()),
            ("ISSUER", (item.get("sender") or "").upper()), ("ISSUED", _issued(item)),
            ("EXPIRES", k.day_hhmm(item.get("expires")) or "-")]

    def summary(c, x, y, w, h, now, el):
        fk, fv = k.font("m10"), k.font("m10b")
        ty = y
        shown = int(el * 6) + 1                                   # the rows print one after another
        for i, (name, val) in enumerate(rows):
            if i >= shown:
                break
            k.text(c, fk, x, ty, AMB_D, name)
            vl = k.wrap(fv, val, w - 62) or [""]
            for j, ln in enumerate(vl[:2]):
                k.text(c, fv, x + 62, ty + j * 11, col if name == "SEVERITY" else AMB, ln)
            ty += 11 * min(2, len(vl)) + 2
        if shown > len(rows):
            k.text(c, fk, x, ty + 1, AMB_D, "AREA")
            k.fit_text(c, " / ".join(item.get("areas") or []).upper(), x + 62, ty + 1, w - 62, y + h - ty - 1, ("m10b", "s8"), AMB)
        if k.blink(now, 0.8):
            k.box(c, x + w - 6, y + h - 10, 6, 10, AMB)

    spec.update(label="NAAD ALERT RECEIVED", chip=("CAP-CP", AMB_D), summary=summary,
                look={"bg": BLACK, "head": "term", "head_fg": AMB, "rule": AMB, "label_font": "m10b", "recolour": AMB,
                      "upper": True, "type": 90, "dot_on": AMB, "dot_off": AMB_DD, "map_frame": AMB_DD,
                      "ladder": {"head": "t12b", "body": "t12", "do": "t12b", "small": "m10"}})
    spec["body"] = _paras(item, AMB, AMB, (255, 230, 150), upper=True)
    return spec


def _half_bulletin(item, spec, col):
    """BC blue and gold: a fact column beside the title, the message in white, the instructions in gold."""
    cw = 62

    def summary(c, x, y, w, h, now, el):
        k.box(c, x - 4, y - 2, cw, h + 5, BLUE)
        k.box(c, x - 4 + cw, y - 2, 1, h + 5, GOLD)
        k.icon(c, icon_of(item), x + (cw - 8 - 32) // 2, y + 2, GOLD, 32)
        fy = y + 40
        for lab, val, vc in (("SEVERITY", item.get("severity"), col), ("EXPIRES", k.day_hhmm(item.get("expires")) or "—", WHITE)):
            if not val:
                continue
            k.text(c, k.font("s8"), x, fy, GOLD, lab)
            k.fit_text(c, str(val), x, fy + 9, cw - 8, 24, ("hvb10", "s8"), vc)
            fy += 32
        tx = x + cw + 4
        tw_ = w - cw - 4
        ty = y + k.fit_text(c, title_of(item), tx, y, tw_, 40, ("h16", "h14", "lutb12", "hvb10"), GOLD) + 4
        k.box(c, tx, ty, tw_, 1, (40, 60, 100))
        ty += 4
        hd = item.get("headline") or ""
        if hd:
            ty += k.fit_text(c, hd, tx, ty, tw_, 40, ("lutb12", "hvb10", "s8"), WHITE) + 4
        k.fit_text(c, _area_line(item), tx, ty, tw_, y + h - ty, ("hv10", "s8"), (170, 190, 220))

    spec["body"] = _paras(item, WHITE, (205, 215, 230), GOLD)
    spec.update(label="PROVINCIAL ALERT", summary=summary,
                look={"bg": (4, 10, 22), "head": "bar", "head_bg": BLUE, "head_fg": GOLD, "rule": GOLD, "title_col": GOLD,
                      "dot_on": GOLD, "dot_off": (60, 80, 120),
                      "ladder": {"head": "lutb12", "body": "lut12", "do": "lutb12", "small": "hv10"}})
    return spec


HALF = {"alert_ready": _half_alert_ready, "phone": _half_phone, "teletype": _half_teletype, "bulletin": _half_bulletin}


def render_preview(theme, sample=None, at=4.0, settings=None, data=None, data_dir=None):
    return k.render_preview(Screen, theme, sample, at, settings, data, data_dir)
