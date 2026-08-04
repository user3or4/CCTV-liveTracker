"""
Shared settings and helpers for the inspection areas.

You can define SEVERAL numbered areas (station 1, 2, 3, ...). Each area is a
pair of shapes:
    - "station"   : where the CAR sits for that station.
    - "work_area" : the space next to it where the WORKER stands.

Each area is timed on its own. The areas are drawn once (with define_zones.py)
and saved to zones.json, so they don't have to be redrawn every time. This
file just reads and writes that settings file.
"""

import json
from pathlib import Path

import numpy as np

ZONES_FILE = Path(__file__).parent / "zones.json"

STATION = "station"
WORK_AREA = "work_area"

# A colour per area (Blue-Green-Red order, as OpenCV expects). Area 1 uses the
# first colour, area 2 the second, and so on. Both shapes of an area share its
# colour so you can see they belong together.
AREA_PALETTE_BGR = [
    (0, 200, 255),    # orange
    (255, 180, 0),    # blue
    (0, 220, 0),      # green
    (200, 0, 255),    # magenta/pink
    (255, 255, 0),    # cyan
    (0, 120, 255),    # deep orange
    (180, 180, 0),    # teal
    (255, 0, 180),    # purple
]


def color_for(area_id):
    """Return the drawing colour for a given area number."""
    return AREA_PALETTE_BGR[(int(area_id) - 1) % len(AREA_PALETTE_BGR)]


def save_areas(areas, image_wh):
    """
    Save all areas to zones.json.

    areas: list of dicts, each like
        {"id": 1, "station": [(x,y), ...], "work_area": [(x,y), ...]}
    """
    data = {
        "image_size": [int(image_wh[0]), int(image_wh[1])],
        "areas": [
            {
                "id": int(a["id"]),
                STATION: [[int(x), int(y)] for x, y in a[STATION]],
                WORK_AREA: [[int(x), int(y)] for x, y in a[WORK_AREA]],
            }
            for a in areas
        ],
    }
    ZONES_FILE.write_text(json.dumps(data, indent=2))
    return ZONES_FILE


def load_areas():
    """
    Load all saved areas.

    Returns {"image_size": (w, h), "areas": [ {id, station, work_area}, ... ]}
    with numpy point arrays, or None if nothing has been drawn yet.
    Also understands the older single-zone file and treats it as area 1.
    """
    if not ZONES_FILE.exists():
        return None
    data = json.loads(ZONES_FILE.read_text())

    # Old format (one station + one work_area at the top level) -> area 1.
    if "areas" not in data and STATION in data:
        raw_areas = [{"id": 1, STATION: data[STATION], WORK_AREA: data[WORK_AREA]}]
    else:
        raw_areas = data.get("areas", [])

    areas = []
    for a in raw_areas:
        areas.append({
            "id": int(a["id"]),
            STATION: np.array(a[STATION], dtype=np.int64),
            WORK_AREA: np.array(a[WORK_AREA], dtype=np.int64),
        })
    return {
        "image_size": tuple(data.get("image_size", (0, 0))),
        "areas": areas,
    }


def scale_polygon(points, from_wh, to_wh):
    """
    If the live video is a different size than the snapshot the areas were
    drawn on, stretch the polygon to match so it still lines up.
    """
    if not from_wh or from_wh[0] == 0 or from_wh[1] == 0:
        return points
    if tuple(from_wh) == tuple(to_wh):
        return points
    sx = to_wh[0] / from_wh[0]
    sy = to_wh[1] / from_wh[1]
    scaled = points.astype(float).copy()
    scaled[:, 0] *= sx
    scaled[:, 1] *= sy
    return scaled.astype(np.int64)
