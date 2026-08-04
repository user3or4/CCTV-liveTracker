"""
Shared settings and helpers for the parking STAGES.

Focus for this phase: CARS only. You define several numbered stages (parking
spots 1, 2, 3, ...). Each stage is a single shape (mask) drawn around the
parking area. For every car that stops in a stage we measure how long it stays.

Stages are drawn once (with define_zones.py) and saved to zones.json, so they
don't have to be redrawn every time. This file reads and writes that file.
"""

import json
from pathlib import Path

import numpy as np

ZONES_FILE = Path(__file__).parent / "zones.json"

STAGE = "polygon"          # key for a stage's shape
STATION = "station"        # (old format keys, still understood on load)
WORK_AREA = "work_area"

# A colour per stage (Blue-Green-Red order, as OpenCV expects).
STAGE_PALETTE_BGR = [
    (0, 200, 255),    # orange
    (255, 180, 0),    # blue
    (0, 220, 0),      # green
    (200, 0, 255),    # magenta/pink
    (255, 255, 0),    # cyan
    (0, 120, 255),    # deep orange
    (180, 180, 0),    # teal
    (255, 0, 180),    # purple
]


def color_for(stage_id):
    """Return the drawing colour for a given stage number."""
    return STAGE_PALETTE_BGR[(int(stage_id) - 1) % len(STAGE_PALETTE_BGR)]


def save_stages(stages, image_wh):
    """
    Save all stages to zones.json.
    stages: list of dicts like {"id": 1, "polygon": [(x, y), ...]}.
    """
    data = {
        "image_size": [int(image_wh[0]), int(image_wh[1])],
        "stages": [
            {"id": int(s["id"]),
             STAGE: [[int(x), int(y)] for x, y in s[STAGE]]}
            for s in stages
        ],
    }
    ZONES_FILE.write_text(json.dumps(data, indent=2))
    return ZONES_FILE


def load_stages():
    """
    Load all saved stages as {"image_size": (w, h), "stages": [{id, polygon}, ...]}
    with numpy point arrays, or None if nothing has been drawn yet.

    Also understands the older formats (station+work_area pairs, or a single
    top-level station) and uses the station/first shape as the stage.
    """
    if not ZONES_FILE.exists():
        return None
    data = json.loads(ZONES_FILE.read_text())

    raw = []
    if "stages" in data:                                   # new car-only format
        raw = [(s["id"], s[STAGE]) for s in data["stages"]]
    elif "areas" in data:                                  # station+work pairs
        raw = [(a["id"], a[STATION]) for a in data["areas"]]
    elif STATION in data:                                  # oldest single zone
        raw = [(1, data[STATION])]

    stages = [{"id": int(i), STAGE: np.array(pts, dtype=np.int64)} for i, pts in raw]
    return {"image_size": tuple(data.get("image_size", (0, 0))), "stages": stages}


def scale_polygon(points, from_wh, to_wh):
    """
    If the live video is a different size than the snapshot the stages were
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
