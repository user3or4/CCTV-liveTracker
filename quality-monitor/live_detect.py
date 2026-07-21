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

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
DEFAULT_SOURCE = "rtsp://adminmed:A1b2c3d4@192.168.1.5:554/stream1"
MODEL_NAME = "yolov8n.pt"

# The things we care about on an assembly line, by their YOLO class number:
#   0 = person, 2 = car, 3 = motorcycle, 5 = bus, 7 = truck
CLASSES_OF_INTEREST = [0, 2, 3, 5, 7]

# Ignore weak guesses below this confidence (0.0-1.0).
CONFIDENCE_THRESHOLD = 0.35

RECONNECT_DELAY = 3      # seconds to wait between (re)connect attempts
FEED_TIMEOUT = 5         # seconds of no frames before we treat the feed as dropped
WINDOW_NAME = "Quality Monitor - Detection + Tracking  (press Q to quit)"


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
    print("  4. The camera's IP address (192.168.1.5) has changed.")
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

    # The tracker. It hands out and remembers the stable ID numbers.
    # (ByteTrack is marked deprecated in this library version but works fine.)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tracker = sv.ByteTrack()

    # These draw the boxes and the text labels for us.
    box_annotator = sv.BoxAnnotator(thickness=2)
    label_annotator = sv.LabelAnnotator(text_scale=0.5, text_thickness=1)

    print(f"\nConnecting to camera: {source}")
    print("A video window will open. Click it and press  Q  to quit.\n")

    capture = None
    fps = 0.0
    frame_count = 0
    fps_timer = time.time()
    last_good_frame = time.time()

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

            # --- Detect objects in this frame ---
            # verbose=False keeps YOLO from printing a line for every frame.
            results = model(frame, verbose=False)[0]
            detections = sv.Detections.from_ultralytics(results)

            # Keep only the classes we care about and confident-enough guesses.
            detections = detections[np.isin(detections.class_id, CLASSES_OF_INTEREST)]
            detections = detections[detections.confidence > CONFIDENCE_THRESHOLD]

            # --- Assign / update the stable tracking IDs ---
            detections = tracker.update_with_detections(detections)

            # --- Build a label for each box: e.g. "car #7" ---
            labels = []
            for class_id, tracker_id in zip(detections.class_id, detections.tracker_id):
                name = model.names[int(class_id)]
                labels.append(f"{name} #{int(tracker_id)}")

            # --- Draw boxes and labels onto the frame ---
            annotated = box_annotator.annotate(scene=frame.copy(), detections=detections)
            annotated = label_annotator.annotate(scene=annotated, detections=detections, labels=labels)

            # --- FPS counter (updates about once per second) ---
            frame_count += 1
            elapsed = time.time() - fps_timer
            if elapsed >= 1.0:
                fps = frame_count / elapsed
                frame_count = 0
                fps_timer = time.time()

            cv2.putText(
                annotated,
                f"FPS: {fps:4.1f}   objects: {len(detections)}",
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
