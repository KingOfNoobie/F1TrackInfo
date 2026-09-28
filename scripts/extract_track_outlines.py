from __future__ import annotations

import json
import math
import re
from pathlib import Path

SRC = Path(r"D:\HA-config\custom_components\f1_sensor\track_map_static_geometry.py")
OUT = Path(r"d:\F1TrackInfo\frontend\data\track-outlines.json")


def normalize(points: list[tuple[int, int]], rotation: float) -> list[list[float]]:
    rad = math.radians(rotation)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    rotated = [(x * cos_a - y * sin_a, x * sin_a + y * cos_a) for x, y in points]
    xs = [p[0] for p in rotated]
    ys = [p[1] for p in rotated]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    width = max(maxx - minx, 1)
    height = max(maxy - miny, 1)
    pad = 40
    size = 1000
    scale = (size - 2 * pad) / max(width, height)
    ox = (size - width * scale) / 2
    oy = (size - height * scale) / 2
    return [
        [round(ox + (x - minx) * scale, 1), round(oy + (y - miny) * scale, 1)]
        for x, y in rotated
    ]


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    start = text.index("STATIC_TRACK_GEOMETRIES")
    text = text[start:]
    entries: dict[str, dict] = {}

    for match in re.finditer(r'"circuit_id":\s*"([^"]+)"', text):
        circuit_id = match.group(1)
        window = text[match.start() : match.start() + 12000]
        rot_match = re.search(r'"rotation":\s*([-\d.]+)', window)
        aliases_match = re.search(r'"aliases":\s*\(([^)]*)\)', window)
        points_match = re.search(r'"points":\s*\(', window)
        if not rot_match or not points_match:
            print("skip", circuit_id)
            continue
        # points block until provenance
        points_start = match.start() + points_match.end()
        provenance = text.find('"provenance"', points_start)
        points_block = text[points_start:provenance]
        points = [
            (int(a), int(b))
            for a, b in re.findall(r"\(([-\d]+),\s*([-\d]+)\)", points_block)
        ]
        if len(points) < 10:
            print("few points", circuit_id, len(points))
            continue
        aliases = re.findall(r'"([^"]+)"', aliases_match.group(1) if aliases_match else "")
        entries[circuit_id] = {
            "aliases": aliases,
            "points": normalize(points, float(rot_match.group(1))),
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(entries, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(entries)} circuits -> {OUT} ({OUT.stat().st_size} bytes)")
    print(", ".join(sorted(entries)))


if __name__ == "__main__":
    main()
