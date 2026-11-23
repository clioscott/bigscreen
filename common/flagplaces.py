"""Which country and province / state / prefecture a webcam is in, for its caption's default flags.

Shared by the hub (the panel shows what "auto" picks) and the display. It reads only the webcam's own settings, in
this order: explicit ``country`` / ``region`` keys; the place text ("Toronto, ON", "Dublin, Ireland", "Osaka"); the
time zone (America/Vancouver is British Columbia, Asia/Tokyo is Japan); for Japan, the coordinates when they fall in
central Tokyo. Countries are lower-case ISO codes ("ca", "jp"); regions are upper-case province / state codes ("BC",
"MA") or Japanese prefecture numbers ("13" is Tokyo).
"""

from __future__ import annotations

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
        elif tail.upper() in CA_REGIONS and tz not in US_TZ:
            country, region = "ca", region or tail.upper()
        elif tail.upper() in US_REGIONS and tz not in CA_TZ:
            country, region = "us", region or tail.upper()
        elif tz in CA_TZ:
            country = "ca"
        elif tz in US_TZ:
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
    return country, region


def default_ids(cam: dict, known=None) -> list[str]:
    """The flag ids a webcam shows by default: its country's and its province's / state's / prefecture's, those that
    exist in ``known`` (a collection of ids; None: no check)."""
    country, region = locate(cam)
    ids = []
    if country:
        ids.append(country)
        if region:
            ids.append(f"{country}-{region.lower()}")
    return [i for i in ids if known is None or i in known]
