"""Which country and province / state / prefecture a webcam is in, for its caption's default flags.

Shared by the hub (the panel shows what "auto" picks) and the display. It reads only the webcam's own settings, in
this order: explicit ``country`` / ``region`` keys; the place text ("Toronto, ON", "Dublin, Ireland", "Osaka"); the
time zone (America/Vancouver is British Columbia, Asia/Tokyo is Japan); for Japan, the coordinates when they fall in
central Tokyo. Countries are lower-case ISO codes ("ca", "jp"); regions are upper-case province / state codes ("BC",
"MA") or Japanese prefecture numbers ("13" is Tokyo).
"""

from __future__ import annotations

import re
import unicodedata

CA_REGIONS = {"BC", "AB", "SK", "MB", "ON", "QC", "NB", "NS", "PE", "NL", "YT", "NT", "NU"}
US_REGIONS = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
              "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
              "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"}
CA_TZ = {"America/Vancouver": "BC", "America/Edmonton": "AB", "America/Regina": "SK", "America/Swift_Current": "SK",
         "America/Winnipeg": "MB", "America/Toronto": "ON", "America/Montreal": "QC", "America/Halifax": "NS",
         "America/Glace_Bay": "NS", "America/Moncton": "NB", "America/St_Johns": "NL", "America/Goose_Bay": "NL",
         "America/Whitehorse": "YT", "America/Dawson": "YT", "America/Yellowknife": "NT", "America/Inuvik": "NT",
         "America/Iqaluit": "NU", "America/Rankin_Inlet": "NU", "America/Cambridge_Bay": "NU"}
US_TZ = {"America/New_York": "", "America/Chicago": "", "America/Denver": "", "America/Los_Angeles": "",
         "America/Phoenix": "AZ", "America/Anchorage": "AK", "America/Juneau": "AK", "Pacific/Honolulu": "HI",
         "America/Boise": "ID", "America/Detroit": "MI"}
COUNTRY_TZ = {"Asia/Tokyo": "jp", "Europe/Dublin": "ie", "Europe/London": "gb", "Europe/Helsinki": "fi",
              "Europe/Oslo": "no", "Europe/Stockholm": "se", "Europe/Copenhagen": "dk", "Europe/Zurich": "ch",
              "Europe/Berlin": "de", "Europe/Madrid": "es", "Europe/Rome": "it", "Europe/Athens": "gr",
              "Europe/Paris": "fr", "Europe/Kyiv": "ua", "Europe/Kiev": "ua", "Asia/Shanghai": "cn",
              "Asia/Seoul": "kr", "Asia/Manila": "ph", "Asia/Bangkok": "th", "America/Mexico_City": "mx"}
COUNTRY_NAMES = {"canada": "ca", "usa": "us", "united states": "us", "japan": "jp", "ireland": "ie", "finland": "fi",
                 "norway": "no", "sweden": "se", "denmark": "dk", "switzerland": "ch", "germany": "de", "spain": "es",
                 "italy": "it", "greece": "gr", "france": "fr", "ukraine": "ua", "china": "cn", "south korea": "kr",
                 "korea": "kr", "philippines": "ph", "thailand": "th", "mexico": "mx", "uk": "gb",
                 "united kingdom": "gb", "england": "gb", "scotland": "gb", "wales": "gb", "northern ireland": "gb"}
# Japanese places named in the place text, and their prefectures
JP_PLACES = {"tokyo": "13", "shinjuku": "13", "shibuya": "13", "minato": "13", "chiyoda": "13", "koto": "13",
             "osaka": "27", "yokohama": "14", "kawasaki": "14", "kanagawa": "14", "sapporo": "01", "hokkaido": "01",
             "fukuoka": "40", "saitama": "11", "kyoto": "26", "nagoya": "23"}
TOKYO_WARDS = (35.52, 35.83, 139.56, 139.93)      # lat0, lat1, lon0, lon1: the 23 wards, roughly
KNOWN_COUNTRIES = set(COUNTRY_NAMES.values()) | set(COUNTRY_TZ.values()) | {"ca", "us"}


def _us_zone(tz: str) -> bool:
    return tz in US_TZ or tz.startswith(("America/Indiana/", "America/Kentucky/", "America/North_Dakota/"))


def locate(cam: dict) -> tuple[str, str]:
    """(country, region) for a webcam; either may be "" when it isn't known. Space webcams are nowhere."""
    if not isinstance(cam, dict) or cam.get("space"):
        return "", ""
    country = str(cam.get("country") or "").strip().lower()
    region = str(cam.get("region") or "").strip().upper()
    tz = str(cam.get("tz") or "")
    place = str(cam.get("place") or "")
    tail = place.rsplit(",", 1)[1].strip() if "," in place else ""
    if not country:
        if tail.lower() in COUNTRY_NAMES:
            country = COUNTRY_NAMES[tail.lower()]
        elif tail.upper() in CA_REGIONS and (not tz or tz in CA_TZ):   # "NL" in Amsterdam's zone is the Netherlands
            country, region = "ca", region or tail.upper()
        elif tail.upper() in US_REGIONS and (not tz or _us_zone(tz)):  # "DE" in Berlin's zone is Germany
            country, region = "us", region or tail.upper()
        elif len(tail) == 2 and tail.lower() in KNOWN_COUNTRIES:   # "Berlin, DE"
            country = tail.lower()
        elif tz in CA_TZ:
            country = "ca"
        elif _us_zone(tz):
            country = "us"
        elif tz in COUNTRY_TZ:
            country = COUNTRY_TZ[tz]
    if not region:
        if country == "ca" and tail.upper() in CA_REGIONS:
            region = tail.upper()
        elif country == "us" and tail.upper() in US_REGIONS:
            region = tail.upper()
        elif country == "ca":
            region = CA_TZ.get(tz, "")
        elif country == "us":
            region = US_TZ.get(tz, "")
        elif country == "jp":
            low = place.lower()
            region = next((p for name, p in JP_PLACES.items() if name in low), "")
            if not region:
                try:
                    lat, lon = float(cam.get("lat")), float(cam.get("lon"))
                    a, b, c, d = TOKYO_WARDS
                    if a <= lat <= b and c <= lon <= d:
                        region = "13"
                except (TypeError, ValueError):
                    pass
    if region.isdigit():                                  # Japanese prefectures are two digits ("1" is "01")
        region = region.zfill(2)
    return country, region


# countries whose own flag is left out: the province / state / prefecture flag (and the city's) says enough
REGION_ONLY = {"ca", "us", "jp"}
WITH_CITY = REGION_ONLY | {"ie"}          # where the city's flag follows (Dublin); elsewhere the country's flag alone


def _norm(name: str) -> str:
    """A place name for comparing: no accents, case, "(...)" notes, "-ku" / "City" / "Ward" or punctuation."""
    s = unicodedata.normalize("NFKD", name.split("(")[0])
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"(?: ku| city| ward)$", "", s).strip()


def city_id(cam: dict, country: str, region: str, known) -> str:
    """The city flag for a webcam's place ("Shibuya-ku", "Dublin, Ireland", "North Vancouver, BC"): a flag in the
    ``city`` group of ``known`` (the flag index: {id: entry}) in the same country and region whose name is the
    place's first part. "" when there's none (or ``known`` has no entries to look in)."""
    if not isinstance(known, dict):
        return ""
    head = _norm(str(cam.get("place") or "").split(",")[0])
    if not head:
        return ""
    hits = [fid for fid, e in known.items()
            if isinstance(e, dict) and e.get("group") == "city" and str(e.get("country") or "").lower() == country
            and str(e.get("region") or "").upper().zfill(2 if region.isdigit() else 0) == region
            and _norm(str(e.get("name") or "")) == head]
    hits.sort(key=lambda fid: "(" in str(known[fid].get("name") or ""))   # "Niagara Falls" before its "(logo)"
    return hits[0] if hits else ""


def default_ids(cam: dict, known=None) -> list[str]:
    """The flag ids a webcam shows by default, those that exist in ``known`` (a collection of ids, or the flag index
    {id: entry}, which also finds city flags; None: no check).

    Canada, the US and Japan: the province / state / prefecture flag and the city's when there is one, not the
    country's (the country's only when the region isn't known). Ireland: the country's and the city's (Dublin).
    Anywhere else: the country's flag alone."""
    country, region = locate(cam)
    if not country:
        return []
    ids = []
    if country in REGION_ONLY and region:
        ids.append(f"{country}-{region.lower()}")
        if known is not None and ids[0] not in known:   # no flag for that region: fall back to the country's
            ids = [country]
    else:
        ids.append(country)
    city = country in WITH_CITY and city_id(cam, country, region if country in REGION_ONLY else "", known)
    if city:
        ids.append(city)
    return [i for i in ids if known is None or i in known]
