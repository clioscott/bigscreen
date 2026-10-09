"""Fetch OSM river *areas* (natural=water + water=river, waterway=riverbank; ways and multipolygon
relations) for a bbox via the Overpass API and save them as GeoJSON polygons next to the other
OSM extracts, so carto.py can trace river shorelines. v1's extract dropped these relations.

    .venv/bin/python -m tools.fetch_river_areas [south west north east] [name]
"""

import json
import sys

import requests
from shapely.geometry import LineString, mapping
from shapely.ops import linemerge, polygonize, unary_union

from hub.weather.carto import OSM_DIR

OVERPASS = "https://overpass-api.de/api/interpreter"


def main():
    s, w, n, e = (float(v) for v in sys.argv[1:5]) if len(sys.argv) >= 5 else (48.85, -123.75, 49.75, -121.4)
    name = sys.argv[5] if len(sys.argv) >= 6 else "lower_mainland"
    q = f"""[out:json][timeout:180];
(
  way["natural"="water"]["water"~"^(river|canal|oxbow|tidal_channel)$"]({s},{w},{n},{e});
  relation["natural"="water"]["water"~"^(river|canal|oxbow|tidal_channel)$"]({s},{w},{n},{e});
  way["waterway"="riverbank"]({s},{w},{n},{e});
  relation["waterway"="riverbank"]({s},{w},{n},{e});
);
out geom;"""
    r = requests.post(OVERPASS, data={"data": q}, timeout=240,
                      headers={"User-Agent": "BigScreen/2.0 (personal LED weather display; one-off extract)"})
    r.raise_for_status()
    feats = []
    for el in r.json().get("elements", []):
        tags = el.get("tags", {})
        rings = []
        if el["type"] == "way" and el.get("geometry"):
            pts = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if len(pts) >= 4 and pts[0] == pts[-1]:
                rings = [pts]
        elif el["type"] == "relation":
            outer = [LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
                     for m in el.get("members", []) if m.get("role") == "outer" and m.get("geometry")
                     and len(m["geometry"]) >= 2]
            if outer:
                polys = list(polygonize(linemerge(unary_union(outer))))
                rings = [list(p.exterior.coords) for p in polys]
        for ring in rings:
            feats.append({"type": "Feature", "properties": {"natural": "water", "water": tags.get("water", "river"),
                                                            "name": tags.get("name", "")},
                          "geometry": {"type": "Polygon", "coordinates": [ring]}})
    out = OSM_DIR / f"{name}_river_areas.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"wrote {len(feats)} river-area polygons to {out}")


if __name__ == "__main__":
    main()
