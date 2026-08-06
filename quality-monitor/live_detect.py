r"""
Live car-stage tracker.

On the live video it detects cars, tracks each with a stable ID, checks which
parking STAGE each car is in (by area overlap), times how long it stays, and
saves each finished visit to the logbook. Shows the FPS, reconnects if the feed
drops, and quits on Q.

How to run it (from inside the quality-monitor folder):
    .\venv\Scripts\python live_detect.py

You can change the settings right on the command line (all optional):
    --confidence 0.20     how sure the AI must be (0-1)      [-c]
    --model yolov8l.pt    which model: yolov8n/s/m/l/x.pt    [-m]
    --overlap 0.70        how much of a car must be in a stage (0-1)  [-o]
    --rate 12             detections per minute              [-r]
    --off 30              seconds gone before "left"

Examples:
    .\venv\Scripts\python live_detect.py --confidence 0.20
    .\venv\Scripts\python live_detect.py -c 0.3 -m yolov8l.pt
    .\venv\Scripts\python live_detect.py --help

To use a different camera, put its link first (in quotes):
    .\venv\Scripts\python live_detect.py "rtsp://user:pass@10.0.0.9:554/..." -c 0.2

Note: the first run downloads the chosen model file if it isn't already here
(needs internet). Settings not given on the command line use the defaults near
the top of this file.
"""

import argparse
import sys
import time
import warnings

import cv2
import numpy as np
import supervision as sv
from ultralytics import YOLO

import zone_config as zc
import database as db
from timers import StationMonitor

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
DEFAULT_SOURCE = "rtsp://alahmadiab:A123456a@10.236.7.105:554/cam/realmonitor?channel=5&subtype=0"

# Which YOLO model to use. Bigger = stronger detection (better for a tilted
# camera, distant/partly-hidden people and cars) but slower. Because we only
# run detection 15x/minute, a bigger model is affordable here.
#   yolov8n.pt = nano   (fastest, weakest)
#   yolov8s.pt = small
#   yolov8m.pt = medium (recommended balance)
#   yolov8l.pt = large  (strongest we'd normally use; slowest)
MODEL_NAME = "yolov8m.pt"

# "How much of the car must be inside the stage to count as parked" (0.0-1.0).
# 0.6 = 60%. Overlap is measured by area, which suits an angled camera.
CAR_OVERLAP_THRESHOLD = 0.60

# This phase is CARS ONLY. YOLO vehicle classes:
#   2 = car, 3 = motorcycle, 5 = bus, 7 = truck
VEHICLE_CLASSES = [2, 3, 5, 7]

# Ignore weak guesses below this confidence (0.0-1.0).
# Lowered so real cars are picked up more easily (fewer misses).
CONFIDENCE_THRESHOLD = 0.25

# To keep the computer's load light (and let a stronger model run), we only RUN
# the AI this many times per minute. The video window still updates smoothly;
# only the heavy detection is throttled. 6/minute = look every 10 seconds.
DETECTIONS_PER_MINUTE = 6
DETECT_INTERVAL = 60.0 / DETECTIONS_PER_MINUTE

RECONNECT_DELAY = 3      # seconds to wait between (re)connect attempts
FEED_TIMEOUT = 5         # seconds of no frames before we treat the feed as dropped
WINDOW_NAME = "Quality Monitor - Detection + Tracking  (press Q to quit)"


def make_zone_mask(polygon, height, width):
    """
    Paint the zone as a solid shape on a blank image (1 = inside, 0 = outside).
    We build this once so the overlap test below is fast.
    """
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [polygon.reshape((-1, 1, 2)).astype(np.int32)], 1)
    return mask


def overlap_fraction(box, mask):
    """
    How much of an object's box sits inside the zone, from 0.0 (none) to 1.0
    (all of it). This is the "how much of the car is on the area" measure - it
    handles a tilted camera far better than a single foot point.
    """
    h, w = mask.shape
    x1 = max(0, int(box[0])); y1 = max(0, int(box[1]))
    x2 = min(w, int(box[2])); y2 = min(h, int(box[3]))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    region = mask[y1:y2, x1:x2]
    if region.size == 0:
        return 0.0
    return float(region.mean())   # mask is 0/1, so the mean IS the fraction inside


def inside_ids(detections, mask, threshold):
    """
    Return (set_of_tracking_ids, count) for objects that are at least
    `threshold` (e.g. 0.6 = 60%) inside the zone.
    """
    if len(detections) == 0:
        return set(), 0
    tracker_ids = detections.tracker_id
    found = set()
    for i, box in enumerate(detections.xyxy):
        if overlap_fraction(box, mask) >= threshold:
            tid = tracker_ids[i] if tracker_ids is not None else i
            found.add(int(tid))
    return found, len(found)


def mmss(seconds):
    """Format seconds as m:ss (e.g. 125 -> '2:05')."""
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def log_stage_visit(report, stage_id):
    """Print a car's stage dwell time and save it to the logbook database now."""
    stage_name = f"Stage {stage_id}"
    dwell = report["cycle_time"]
    print(f"\n=====  {stage_name}: CAR LEFT  =====")
    print(f"  Car ID     : {report.get('car_track_id')}")
    print(f"  Stayed for : {dwell:.1f} s  ({mmss(dwell)})")
    print("==================================")

    try:
        db.save_stage_visit(report, stage_name)
        print("  (saved to logbook.db)\n")
    except Exception as err:  # noqa: BLE001 - never let a save error crash the live view
        print(f"  WARNING: could not save to database: {err}\n")


def draw_zone(image, polygon, color_bgr, label, count):
    """Shade and outline a zone, and write its name + live count on it."""
    pts = polygon.reshape((-1, 1, 2)).astype(np.int32)

    # Light see-through shading so the video is still visible underneath.
    overlay = image.copy()
    cv2.fillPoly(overlay, [pts], color_bgr)
    cv2.addWeighted(overlay, 0.20, image, 0.80, 0, dst=image)

    # Solid outline.
    cv2.polylines(image, [pts], isClosed=True, color=color_bgr, thickness=2)

    # Label near the first corner.
    corner = tuple(polygon[0])
    cv2.putText(image, f"{label}: {count}", (corner[0], max(corner[1] - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_bgr, 2, cv2.LINE_AA)
    return image


def open_camera(source):
    """Try to open the camera. Returns an opened capture, or None on failure."""
    capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    try:
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except cv2.error:
        pass
    if not capture.isOpened():
        capture.release()
        return None
    return capture


def explain_connection_failure(source):
    """Print a clear, plain-English message about why connecting may have failed."""
    print("\n--------------------------------------------------------------")
    print("Could not connect to the camera.")
    print(f"  Link used: {source}")
    print("\nCommon reasons and what to check:")
    print("  1. Wrong link, username, or password  -> double-check every part of the link.")
    print("  2. This computer is not on the same network as the camera.")
    print("  3. The camera is switched off, rebooting, or unplugged.")
    print("  4. The camera's IP address (10.236.7.105) has changed.")
    print("  5. A firewall is blocking the connection (RTSP uses port 554).")
    print("--------------------------------------------------------------\n")


def load_model(model_name=MODEL_NAME):
    """Load the YOLO model, with a clear message if it can't be found/downloaded."""
    try:
        print(f"Loading the YOLO model ({model_name})...")
        model = YOLO(model_name)
        print("Model ready.")
        return model
    except Exception as err:  # noqa: BLE001 - surface the problem in plain words
        print("\n--------------------------------------------------------------")
        print("Could not load the detection model.")
        print(f"Reason: {err}")
        print("\nThe model file is downloaded automatically the first time, which")
        print("needs internet access. Run  ./venv/bin/python get_model.py  once")
        print("on a machine with normal internet, then try again.")
        print("--------------------------------------------------------------\n")
        raise SystemExit(1)


def parse_args():
    """Read optional settings from the command line (all have sensible defaults)."""
    p = argparse.ArgumentParser(
        description="Live car-stage tracker. All options are optional.")
    p.add_argument("source", nargs="?", default=DEFAULT_SOURCE,
                   help="camera link (defaults to the built-in one)")
    p.add_argument("--confidence", "-c", type=float, default=CONFIDENCE_THRESHOLD,
                   help=f"how sure YOLO must be, 0-1 (default {CONFIDENCE_THRESHOLD})")
    p.add_argument("--model", "-m", default=MODEL_NAME,
                   help=f"model file: yolov8n/s/m/l/x.pt (default {MODEL_NAME})")
    p.add_argument("--overlap", "-o", type=float, default=CAR_OVERLAP_THRESHOLD,
                   help=f"how much of a car must be in a stage, 0-1 (default {CAR_OVERLAP_THRESHOLD})")
    p.add_argument("--rate", "-r", type=float, default=DETECTIONS_PER_MINUTE,
                   help=f"detections per minute (default {DETECTIONS_PER_MINUTE})")
    p.add_argument("--off", type=float, default=None,
                   help="seconds a car must be gone before 'left' (default 20)")
    return p.parse_args()


def main():
    args = parse_args()
    source = args.source
    confidence = args.confidence
    overlap = args.overlap
    detect_interval = 60.0 / args.rate if args.rate > 0 else DETECT_INTERVAL
    monitor_kwargs = {} if args.off is None else {"car_off": args.off}

    print("Settings for this run:")
    print(f"  model      = {args.model}")
    print(f"  confidence = {confidence}")
    print(f"  overlap    = {overlap}  ({int(overlap * 100)}% of the car must be in a stage)")
    print(f"  rate       = {args.rate}/min  (look every {detect_interval:.0f}s)")
    if args.off is not None:
        print(f"  leave-check = {args.off}s")

    model = load_model(args.model)

    # Make sure the logbook database and its tables exist.
    db.init_db()
    print(f"Logbook database ready: {db.DB_FILE}")

    # The tracker. It hands out and remembers the stable ID numbers.
    # (ByteTrack is marked deprecated in this library version but works fine.)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tracker = sv.ByteTrack()

    # These draw the boxes and the text labels for us.
    box_annotator = sv.BoxAnnotator(thickness=2)
    label_annotator = sv.LabelAnnotator(text_scale=0.5, text_thickness=1)

    # --- Load the saved stages (drawn earlier with define_zones.py) ---
    zones = zc.load_stages()
    if zones is None:
        print("\nNOTE: No stages are saved yet (zones.json not found).")
        print("The live view will still run, but no parking stages will be shown.")
        print("To draw them, run:  .\\venv\\Scripts\\python define_zones.py\n")
    else:
        print(f"Loaded {len(zones['stages'])} stage(s): "
              f"{', '.join('stage ' + str(s['id']) for s in zones['stages'])}")

    # Per-stage data is prepared once we know the video size (below).
    # Each entry: {"id", "pts", "mask", "monitor", "cars", "car_ids"}
    stages = []

    print(f"\nConnecting to camera: {source}")
    print("A video window will open. Click it and press  Q  to quit.\n")

    capture = None
    fps = 0.0
    frame_count = 0
    fps_timer = time.time()
    last_good_frame = time.time()

    # Detection is throttled (see DETECT_INTERVAL). Between runs we keep and
    # re-draw the most recent results so the video still looks live.
    detections = None
    labels = []
    last_detect_time = 0.0

    try:
        while True:
            # --- (Re)connect if needed ---
            if capture is None:
                capture = open_camera(source)
                if capture is None:
                    explain_connection_failure(source)
                    print(f"Retrying in {RECONNECT_DELAY} seconds... (press Ctrl+C to stop)")
                    time.sleep(RECONNECT_DELAY)
                    continue
                print("Connected. Showing live video with detection + tracking.")
                last_good_frame = time.time()
                tracker.reset()  # start ID numbering fresh on a new connection

            # --- Read the next frame ---
            ok, frame = capture.read()
            if not ok or frame is None:
                if time.time() - last_good_frame > FEED_TIMEOUT:
                    print("Feed dropped - trying to reconnect...")
                    capture.release()
                    capture = None
                    time.sleep(RECONNECT_DELAY)
                continue
            last_good_frame = time.time()

            # --- Prepare all stages once, now that we know the video size ---
            if zones is not None and not stages:
                frame_h, frame_w = frame.shape[:2]
                saved_wh = zones["image_size"]
                for s in zones["stages"]:
                    pts = zc.scale_polygon(s[zc.STAGE], saved_wh, (frame_w, frame_h))
                    stages.append({
                        "id": s["id"],
                        "pts": pts,
                        "mask": make_zone_mask(pts, frame_h, frame_w),
                        "monitor": StationMonitor(**monitor_kwargs),
                        "cars": 0, "car_ids": set(),
                    })

            now = time.time()

            # --- Run the AI only every `detect_interval` seconds (to save load) ---
            if now - last_detect_time >= detect_interval:
                last_detect_time = now

                # verbose=False keeps YOLO from printing a line for every frame.
                results = model(frame, verbose=False)[0]
                detections = sv.Detections.from_ultralytics(results)
                detections = detections[np.isin(detections.class_id, VEHICLE_CLASSES)]
                detections = detections[detections.confidence > confidence]
                detections = tracker.update_with_detections(detections)

                # Build a label for each box: e.g. "car #7".
                labels = []
                for class_id, tracker_id in zip(detections.class_id, detections.tracker_id):
                    name = model.names[int(class_id)]
                    labels.append(f"{name} #{int(tracker_id)}")

                # For each stage: is a car parked here (60%+ overlap)? Time it.
                for a in stages:
                    a["car_ids"], a["cars"] = inside_ids(detections, a["mask"], overlap)
                    # No workers this phase, so pass an empty set of worker IDs.
                    report = a["monitor"].update(now, a["car_ids"], set())
                    if report is not None:
                        log_stage_visit(report, a["id"])

            # --- Draw the most recent boxes and labels onto the current frame ---
            annotated = frame.copy()
            if detections is not None:
                annotated = box_annotator.annotate(scene=annotated, detections=detections)
                annotated = label_annotator.annotate(scene=annotated, detections=detections, labels=labels)

            # --- Draw every stage, coloured and numbered, with its live dwell ---
            for a in stages:
                color = zc.color_for(a["id"])
                draw_zone(annotated, a["pts"], color, f"STAGE {a['id']}", a["cars"])
                readout = a["monitor"].live_readout()
                if readout is not None:   # a car is currently parked here
                    corner = tuple(a["pts"][0])
                    cv2.putText(annotated, f"{mmss(readout['cycle_time'])}",
                                (corner[0], min(corner[1] + 22, annotated.shape[0] - 10)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

            # --- FPS counter (updates about once per second) ---
            frame_count += 1
            elapsed = time.time() - fps_timer
            if elapsed >= 1.0:
                fps = frame_count / elapsed
                frame_count = 0
                fps_timer = time.time()

            n_objects = len(detections) if detections is not None else 0
            cv2.putText(
                annotated,
                f"FPS: {fps:4.1f}   objects: {n_objects}",
                (15, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            # --- Show it ---
            cv2.imshow(WINDOW_NAME, annotated)

            # --- Quit on Q or window close ---
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q")):
                print("\nQ pressed - closing.")
                break
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                print("\nWindow closed - stopping.")
                break

    except KeyboardInterrupt:
        print("\nStopped with Ctrl+C.")
    finally:
        if capture is not None:
            capture.release()
        cv2.destroyAllWindows()
        print("Camera released. Goodbye.")


if __name__ == "__main__":
    main()
