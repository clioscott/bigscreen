"""Map areas round 2: short labels, Core Metro Vancouver (corner-fit), city label sets,
undarkened basemaps for radar/totals maps, opaque radar. Idempotent.

    .venv/bin/python -m tools.maps_round2
"""

from hub import config

C = {   # name: (lat, lon)
    "Vancouver": (49.2827, -123.1207), "Burnaby": (49.2488, -122.9805), "Richmond": (49.1666, -123.1336),
    "Surrey": (49.1044, -122.8011), "Coquitlam": (49.2838, -122.7932), "Langley": (49.1044, -122.6604),
    "North Vancouver": (49.3200, -123.0724), "Delta": (49.0847, -123.0586), "Squamish": (49.7016, -123.1558),
    "Abbotsford": (49.0580, -122.2526), "Chilliwack": (49.1579, -121.9514), "Bellingham": (48.7596, -122.4881),
    "Nanaimo": (49.1659, -123.9401), "Victoria": (48.4284, -123.3656), "Seattle": (47.6062, -122.3321),
    "Whistler": (50.1163, -122.9574), "Kelowna": (49.8880, -119.4960), "Kamloops": (50.6745, -120.3273),
    "Portland": (45.5152, -122.6784), "Spokane": (47.6588, -117.4260), "Calgary": (51.0447, -114.0719),
    "Edmonton": (53.5461, -113.4938), "San Francisco": (37.7749, -122.4194), "Prince George": (53.9171, -122.7497),
}


def cities(*names):
    return [{"name": n, "lat": C[n][0], "lon": C[n][1]} for n in names]


LABELS = {
    "core_metro": cities("Vancouver", "North Vancouver", "Burnaby", "Richmond", "Coquitlam", "Surrey", "Delta",
                         "Langley"),
    "metro": cities("Vancouver", "Surrey", "Langley", "Abbotsford", "Squamish", "Bellingham", "Nanaimo"),
    "pnw": cities("Vancouver", "Victoria", "Seattle", "Whistler", "Kelowna", "Kamloops", "Portland", "Spokane"),
    "west": cities("Vancouver", "Seattle", "Portland", "Calgary", "Edmonton", "San Francisco", "Prince George"),
}


def main():
    cfg = config.load()
    wm = cfg["modules"]["weather_maps"]
    areas = wm["areas"]
    areas["metro"].update(label="Greater Metro Vancouver", short_label="GVRD")
    areas["pnw"].update(label="Pacific Northwest", short_label="PNW")
    areas["west"].update(label="Western North America", short_label="W.NA")
    areas.setdefault("core_metro", {
        "label": "Core Metro Vancouver", "short_label": "Metro Van",
        # West Vancouver's NW extent and the Township of Langley's SE corner at the US border (OSM admin
        # boundaries). The view is fitted so these latitudes touch the top and bottom edges exactly.
        "fit": {"nw": [49.4085, -123.2975], "se": [49.0021, -122.4593]},
        "carto": {"regional": {"enabled": True}, "municipal": {"enabled": True}, "motorways": {"enabled": True}},
    })
    for aid, a in areas.items():
        lab = a.get("labels") or {}
        if "cities" not in lab:
            lab = {"enabled": True, "font": "tom-thumb.bdf", "color": "#ffe65a", "dot": True,
                   "dot_color": "#ffe65a", "outline": True, "cities": LABELS.get(aid, a.get("markers") or [])}
        a["labels"] = lab
        a.pop("markers", None)

    for tid, t in wm["tracks"].items():
        radar_like = t.get("kind") == "accumulation" or any(l.get("type") == "radar" for l in t.get("layers", []))
        if radar_like:
            bm = t.setdefault("basemap", {})
            bm.update(brightness=1.0, contrast=1.0, saturation=1.0)
            for l in t.get("layers", []):
                if l.get("type") == "radar":
                    l.pop("opacity", None)
        t.pop("opacity", None)
        if "markers" in t:
            t["labels"] = t.pop("markers")
    config.save(cfg, reason="map areas round 2 (short labels, core metro, city labels, opaque radar)")
    print("saved r", config.load()["revision"])


if __name__ == "__main__":
    main()
