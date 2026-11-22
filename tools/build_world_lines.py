"""Build assets/maps/world_110m.json, the ISS maps' coastlines and country borders (display/modules/issmap.py), from
Natural Earth's 1:110m coastline and land boundary lines (public domain).

    python -m tools.build_world_lines COAST.zip BORDERS.zip [OUT]

(the zips: ne_110m_coastline.zip and ne_110m_admin_0_boundary_lines_land.zip from naturalearthdata.com; needs pyshp).
Output: {"coast": [[lon, lat, lon, lat, …], …], "borders": [...]}, degrees to 0.1 (a pixel on the wall is ~0.8)."""

import io
import json
import sys
import zipfile
from pathlib import Path


def lines(zpath):
    import shapefile
    with zipfile.ZipFile(zpath) as z:
        base = next(n[:-4] for n in z.namelist() if n.endswith(".shp"))
        r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), shx=io.BytesIO(z.read(base + ".shx")),
                             dbf=io.BytesIO(z.read(base + ".dbf")))
        out = []
        for s in r.iterShapes():
            parts = list(s.parts) + [len(s.points)]
            for a, b in zip(parts, parts[1:]):
                flat, last = [], None
                for lon, lat in s.points[a:b]:
                    p = (round(lon, 1), round(lat, 1))
                    if p != last:
                        flat += p
                        last = p
                if len(flat) >= 4:
                    out.append(flat)
        return out


def main(argv):
    coast, borders = argv[0], argv[1]
    out = Path(argv[2]) if len(argv) > 2 else Path(__file__).resolve().parent.parent / "assets/maps/world_110m.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"source": "Natural Earth 1:110m (public domain)", "coast": lines(coast), "borders": lines(borders)}
    out.write_text(json.dumps(data, separators=(",", ":")))
    print(out, out.stat().st_size, "bytes,", sum(len(l) // 2 for l in data["coast"]), "coast points,",
          sum(len(l) // 2 for l in data["borders"]), "border points")


if __name__ == "__main__":
    main(sys.argv[1:])
