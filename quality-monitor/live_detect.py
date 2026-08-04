"""
Live CCTV camera viewer WITH detection and tracking.

This builds on live_camera.py. On the live video it now:
  - Detects cars/vehicles and people using the YOLO model.
  - Draws a labelled box around each one ("car", "person", "truck", ...).
  - Gives each object a stable tracking ID that stays with it as it moves
    (using the ByteTrack tracker from the supervision library).
  - Shows that ID on each box, e.g.  "car #7".
  - Still shows the current FPS, reconnects if the feed drops, and quits on Q.

How to run it (from inside the quality-monitor folder):
    ./venv/bin/python live_detect.py

To use a different camera, pass its link:
    ./venv/bin/python live_detect.py "rtsp://user:pass@192.168.1.9:554/stream1"

Note: the very first run downloads the YOLO model file (~6 MB) if it isn't
already here. That needs normal internet access.
"""

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
MODEL_NAME = "yolov8n.pt"

# The things we care about on an assembly line, by their YOLO class number:
#   0 = person, 2 = car, 3 = motorcycle, 5 = bus, 7 = truck
CLASSES_OF_INTEREST = [0, 2, 3, 5, 7]
VEHICLE_CLASSES = [2, 3, 5, 7]   # what counts as a "car/vehicle" in the station
PERSON_CLASS = [0]               # what counts as a "person" in the work area

# Ignore weak guesses below this confidence (0.0-1.0).
# Lowered so real cars are picked up more easily (fewer misses).
CONFIDENCE_THRESHOLD = 0.25

# To keep the computer's load light, we only RUN the AI this many times per
# minute. The video window still updates smoothly; only the heavy detection is
# throttled. 15/minute = look at the scene every 4 seconds.
DETECTIONS_PER_MINUTE = 15
DETECT_INTERVAL = 60.0 / DETECTIONS_PER_MINUTE

RECONNECT_DELAY = 3      # seconds to wait between (re)connect attempts
FEED_TIMEOUT = 5         # seconds of no frames before we treat the feed as dropped
WINDOW_NAME = "Quality Monitor - Detection + Tracking  (press Q to quit)"


def count_inside(detections, polygon):
    """
    Count how many of the given objects are INSIDE the polygon.

    We test the point where each object touches the ground (the bottom-centre
    of its box) - so a car counts when its wheels are in the zone and a worker
    when their feet are. Uses OpenCV's point-in-polygon test, which works the
    same across all library versions.
    """
    if len(detections) == 0:
        return 0
    contour = polygon.reshape((-1, 1, 2)).astype(np.int32)
    count = 0
    for x1, y1, x2, y2 in detections.xyxy:
        foot = (float((x1 + x2) / 2.0), float(y2))
        if cv2.pointPolygonTest(contour, foot, False) >= 0:
            count += 1
    return count


def ids_inside(detections, polygon):
    """Return the set of tracking IDs whose ground point is inside the polygon."""
    if len(detections) == 0:
        return set()
    contour = polygon.reshape((-1, 1, 2)).astype(np.int32)
    tracker_ids = detections.tracker_id
    found = set()
    for i, (x1, y1, x2, y2) in enumerate(detections.xyxy):
        foot = (float((x1 + x2) / 2.0), float(y2))
        if cv2.pointPolygonTest(contour, foot, False) >= 0:
            tid = tracker_ids[i] if tracker_ids is not None else i
            found.add(int(tid))
    return found


def log_report(report, area_id):
    """Print a car's final numbers and save them to the logbook database now."""
    station_name = f"Station {area_id}"
    print(f"\n=============  {station_name}: CAR FINISHED  =============")
    print(f"  Car ID                      : {report.get('car_track_id')}")
    print(f"  Cycle time (car in station) : {report['cycle_time']:.1f} s")
    print(f"  Hands-on time (total)       : {report['hands_on_time']:.1f} s")
    print(f"  Unique workers              : {report['unique_workers']}")
    print(f"  Separate on-sessions        : {report['on_sessions']}")
    for w in report.get("workers", []):
        print(f"    - worker {w['index']}: {w['hands_on']:.1f} s over {w['sessions']} session(s)")
    print("===================================================")

    try:
        db.save_car(report, station_name)
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


def load_model():
    """Load the YOLO model, with a clear message if it can't be found/downloaded."""
    try:
        print(f"Loading the YOLO model ({MODEL_NAME})...")
        model = YOLO(MODEL_NAME)
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


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE

    model = load_model()

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

    # --- Load the saved areas (drawn earlier with define_zones.py) ---
    zones = zc.load_areas()
    if zones is None:
        print("\nNOTE: No areas are saved yet (zones.json not found).")
        print("The live view will still run, but no stations/work areas will be shown.")
        print("To draw them, run:  .\\venv\\Scripts\\python define_zones.py\n")
    else:
        print(f"Loaded {len(zones['areas'])} area(s): "
              f"{', '.join('station ' + str(a['id']) for a in zones['areas'])}")

    # Per-area data is prepared once we know the video size (below).
    # Each entry: {"id", "station_pts", "work_pts", "monitor"}
    areas = []
    display_area_id = None      # which area's timer we show on screen (the first)
    last_report = None          # last finished car (for the on-screen banner)
    last_report_time = 0.0

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

            # --- Prepare all areas once, now that we know the video size ---
            if zones is not None and not areas:
                frame_h, frame_w = frame.shape[:2]
                saved_wh = zones["image_size"]
                for a in zones["areas"]:
                    areas.append({
                        "id": a["id"],
                        "station_pts": zc.scale_polygon(a[zc.STATION], saved_wh, (frame_w, frame_h)),
                        "work_pts": zc.scale_polygon(a[zc.WORK_AREA], saved_wh, (frame_w, frame_h)),
                        "monitor": StationMonitor(),
                        "cars": 0, "car_ids": set(), "worker_ids": set(),
                    })
                if areas:
                    display_area_id = areas[0]["id"]   # show the first area's timer

            now = time.time()

            # --- Run the AI only every DETECT_INTERVAL seconds (to save load) ---
            if now - last_detect_time >= DETECT_INTERVAL:
                last_detect_time = now

                # verbose=False keeps YOLO from printing a line for every frame.
                results = model(frame, verbose=False)[0]
                detections = sv.Detections.from_ultralytics(results)
                detections = detections[np.isin(detections.class_id, CLASSES_OF_INTEREST)]
                detections = detections[detections.confidence > CONFIDENCE_THRESHOLD]
                detections = tracker.update_with_detections(detections)

                # Build a label for each box: e.g. "car #7".
                labels = []
                for class_id, tracker_id in zip(detections.class_id, detections.tracker_id):
                    name = model.names[int(class_id)]
                    labels.append(f"{name} #{int(tracker_id)}")

                vehicles = detections[np.isin(detections.class_id, VEHICLE_CLASSES)]
                people = detections[np.isin(detections.class_id, PERSON_CLASS)]

                # For each area: check zones and drive its own timer.
                for a in areas:
                    a["car_ids"] = ids_inside(vehicles, a["station_pts"])
                    a["cars"] = len(a["car_ids"])
                    a["worker_ids"] = ids_inside(people, a["work_pts"])

                    report = a["monitor"].update(now, a["car_ids"], a["worker_ids"])
                    if report is not None:
                        log_report(report, a["id"])
                        if a["id"] == display_area_id:
                            last_report = report
                            last_report_time = now

            # --- Draw the most recent boxes and labels onto the current frame ---
            annotated = frame.copy()
            if detections is not None:
                annotated = box_annotator.annotate(scene=annotated, detections=detections)
                annotated = label_annotator.annotate(scene=annotated, detections=detections, labels=labels)

            # --- Draw every area (both shapes), coloured and numbered ---
            for a in areas:
                color = zc.color_for(a["id"])
                draw_zone(annotated, a["station_pts"], color, f"STATION {a['id']}", a["cars"])
                draw_zone(annotated, a["work_pts"], color, f"WORK {a['id']}", len(a["worker_ids"]))

            # --- Show the FIRST area's live timer in the top-left panel ---
            if display_area_id is not None:
                first = areas[0]
                readout = first["monitor"].live_readout()
                cv2.putText(annotated, f"STATION {first['id']}", (15, 75),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, zc.color_for(first["id"]), 2, cv2.LINE_AA)
                car_status = "YES" if first["cars"] > 0 else "no"
                worker_status = "YES" if len(first["worker_ids"]) > 0 else "no"
                cv2.putText(annotated, f"Car in station: {car_status}   Worker: {worker_status}",
                            (15, 103), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
                if readout is not None:
                    working = "WORKING" if readout["worker_on"] else "paused"
                    cv2.putText(annotated,
                                f"CYCLE: {readout['cycle_time']:.0f}s   HANDS-ON: {readout['hands_on_time']:.0f}s  [{working}]",
                                (15, 138), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
                    cv2.putText(annotated,
                                f"on-sessions: {readout['on_sessions']}   workers: {readout['workers_seen']}",
                                (15, 166), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1, cv2.LINE_AA)

                # Show the first area's last finished car for 10 seconds.
                if last_report is not None and now - last_report_time < 10:
                    y0 = annotated.shape[0] - 110
                    box = annotated.copy()
                    cv2.rectangle(box, (10, y0 - 10), (440, y0 + 95), (0, 0, 0), -1)
                    annotated = cv2.addWeighted(box, 0.55, annotated, 0.45, 0)
                    cv2.putText(annotated, f"LAST CAR (station {display_area_id}):", (20, y0 + 12),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
                    cv2.putText(annotated, f"cycle {last_report['cycle_time']:.0f}s   hands-on {last_report['hands_on_time']:.0f}s",
                                (20, y0 + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
                    cv2.putText(annotated, f"workers {last_report['unique_workers']}   sessions {last_report['on_sessions']}",
                                (20, y0 + 66), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)

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
