"""
Shared settings and helpers for the two zones we care about:

  - "station"   : the inspection-station area. We watch for a CAR inside it.
  - "work_area" : the slightly larger area where a worker stands. We watch
                  for a PERSON inside it.

The zones are drawn once (with define_zones.py) and saved to zones.json, so
they don't have to be redrawn every time. This file just reads and writes
that settings file.
"""

import json
from pathlib import Path

import numpy as np

ZONES_FILE = Path(__file__).parent / "zones.json"

STATION = "station"
WORK_AREA = "work_area"

# Colours used everywhere so the drawing tool and the live view match.
# (OpenCV uses Blue-Green-Red order, not Red-Green-Blue.)
STATION_COLOR_BGR = (0, 200, 255)     # orange  -> the station
WORK_AREA_COLOR_BGR = (255, 180, 0)   # blue    -> the worker area


def save_zones(station_points, work_area_points, image_wh):
    """Save both polygons (and the image size they were drawn on) to zones.json."""
    data = {
        "image_size": [int(image_wh[0]), int(image_wh[1])],
        STATION: [[int(x), int(y)] for x, y in station_points],
        WORK_AREA: [[int(x), int(y)] for x, y in work_area_points],
    }
    ZONES_FILE.write_text(json.dumps(data, indent=2))
    return ZONES_FILE


def load_zones():
    """
    Load the saved zones.

    Returns a dict with numpy point arrays and the image size, or None if the
    settings file doesn't exist yet.
    """
    if not ZONES_FILE.exists():
        return None
    data = json.loads(ZONES_FILE.read_text())
    return {
        "image_size": tuple(data.get("image_size", (0, 0))),
        STATION: np.array(data[STATION], dtype=np.int64),
        WORK_AREA: np.array(data[WORK_AREA], dtype=np.int64),
    }


def scale_polygon(points, from_wh, to_wh):
    """
    If the live video is a different size than the snapshot the zones were
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
