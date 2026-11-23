"""The satellite layers the Space Map can show (hub/jobs/sats.py fetches them, display/modules/mapsats.py draws them).

Each: where CelesTrak lists it, which country runs it (its flag), how high it flies (sets how often positions are
worked out: low orbits move a pixel every few seconds), whether its satellites talk to each other by laser (the
trellis of links it can draw) and its look on the map."""

LAYERS = {
    "starlink": {"label": "Starlink", "query": "GROUP=starlink", "flag": "us", "orbit": "leo", "mesh": True,
                 "colour": "#9fb7ff", "note": "SpaceX, ~10,000 satellites with laser links between them"},
    "kuiper": {"label": "Amazon Leo", "query": "GROUP=kuiper", "flag": "us", "orbit": "leo", "mesh": True,
               "colour": "#ffb347", "note": "Amazon's network (was Project Kuiper)"},
    "lightspeed": {"label": "Telesat Lightspeed", "query": "NAME=LIGHTSPEED", "flag": "ca", "orbit": "leo",
                   "mesh": True, "colour": "#ff5d73", "note": "Canada's network: shows up as the first ones launch"},
    "qianfan": {"label": "Qianfan", "query": "GROUP=qianfan", "flag": "cn", "orbit": "leo", "mesh": False,
                "colour": "#ff6a4d", "note": "China's Spacesail (Thousand Sails) network"},
    "oneweb": {"label": "OneWeb", "query": "GROUP=oneweb", "flag": "gb", "orbit": "leo", "mesh": False,
               "colour": "#7fe0d0", "note": "Eutelsat OneWeb, 1,200 km up"},
    "iridium": {"label": "Iridium", "query": "GROUP=iridium-NEXT", "flag": "us", "orbit": "leo", "mesh": True,
                "colour": "#d9a6ff", "note": "66 satellites in six polar planes, linked by radio cross-links"},
    "gps": {"label": "GPS", "query": "GROUP=gps-ops", "flag": "us", "orbit": "meo", "mesh": False,
            "colour": "#7dff8a", "note": "the US navigation satellites, 20,200 km up"},
    "galileo": {"label": "Galileo", "query": "GROUP=galileo", "flag": "eu", "orbit": "meo", "mesh": False,
                "colour": "#5fa8ff", "note": "Europe's navigation satellites"},
    "glonass": {"label": "GLONASS", "query": "GROUP=glo-ops", "flag": "ru", "orbit": "meo", "mesh": False,
                "colour": "#ff8c8c", "note": "Russia's navigation satellites"},
    "beidou": {"label": "BeiDou", "query": "GROUP=beidou", "flag": "cn", "orbit": "meo", "mesh": False,
               "colour": "#ffd75a", "note": "China's navigation satellites"},
    "goes": {"label": "GOES", "query": "GROUP=goes", "flag": "us", "orbit": "geo", "mesh": False,
             "colour": "#ffe680", "note": "NOAA's weather satellites, parked over the Americas"},
}
STEP = {"leo": 30, "meo": 120, "geo": 600}          # s between worked-out positions (the display interpolates)
