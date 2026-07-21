"""
Live CCTV camera viewer.

What this does, in plain words:
  - Connects to one live camera over the network.
  - Opens a window showing the live video.
  - Shows the current frames-per-second (FPS) in the corner, so you can see
    whether the computer is keeping up with the camera.
  - If the feed drops for a moment, it keeps trying to reconnect on its own.
  - Press the  Q  key (with the video window in focus) to close it cleanly.

How to run it (from inside the quality-monitor folder):
    ./venv/bin/python live_camera.py

To use a different camera, pass its link:
    ./venv/bin/python live_camera.py "rtsp://user:pass@192.168.1.9:554/stream1"
"""

import sys
import time

import cv2

# ---------------------------------------------------------------------------
# The camera link. This is your camera. You can also pass a different one on
# the command line (see the instructions at the top of this file).
# ---------------------------------------------------------------------------
DEFAULT_SOURCE = "rtsp://adminmed:A1b2c3d4@192.168.1.5:554/stream1"

# How long to wait, in seconds, between attempts to (re)connect.
RECONNECT_DELAY = 3
# If we can't grab a frame for this many seconds in a row, treat the feed as
# dropped and reconnect.
FEED_TIMEOUT = 5
WINDOW_NAME = "Quality Monitor - Live Camera  (press Q to quit)"


def open_camera(source):
    """Try to open the camera. Returns an opened capture, or None on failure."""
    # FFMPEG is the backend that speaks RTSP (the CCTV streaming language).
    capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    # Keep the buffer small so we always show the freshest frame, not a backlog.
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


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE

    print(f"Connecting to camera: {source}")
    print("A video window will open. Click it and press  Q  to quit.\n")

    capture = None
    # FPS measurement state.
    fps = 0.0
    frame_count = 0
    fps_timer = time.time()
    # Tracks the last time we successfully read a frame (for drop detection).
    last_good_frame = time.time()

    try:
        while True:
            # --- (Re)connect if we don't have a working camera ---
            if capture is None:
                capture = open_camera(source)
                if capture is None:
                    explain_connection_failure(source)
                    print(f"Retrying in {RECONNECT_DELAY} seconds... (press Ctrl+C to stop)")
                    time.sleep(RECONNECT_DELAY)
                    continue
                print("Connected. Showing live video.")
                last_good_frame = time.time()

            # --- Read the next frame ---
            ok, frame = capture.read()

            if not ok or frame is None:
                # Feed hiccup. If it's been down too long, reconnect.
                if time.time() - last_good_frame > FEED_TIMEOUT:
                    print("Feed dropped - trying to reconnect...")
                    capture.release()
                    capture = None
                    time.sleep(RECONNECT_DELAY)
                continue

            last_good_frame = time.time()

            # --- Work out the current FPS (updated about once per second) ---
            frame_count += 1
            elapsed = time.time() - fps_timer
            if elapsed >= 1.0:
                fps = frame_count / elapsed
                frame_count = 0
                fps_timer = time.time()

            # --- Draw the FPS number on the picture ---
            cv2.putText(
                frame,
                f"FPS: {fps:4.1f}",
                (15, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            # --- Show it ---
            cv2.imshow(WINDOW_NAME, frame)

            # --- Quit on Q (or if the window is closed) ---
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q") or key == ord("Q"):
                print("\nQ pressed - closing.")
                break
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                print("\nWindow closed - stopping.")
                break

    except KeyboardInterrupt:
        print("\nStopped with Ctrl+C.")
    finally:
        # Always clean up, no matter how we exit.
        if capture is not None:
            capture.release()
        cv2.destroyAllWindows()
        print("Camera released. Goodbye.")


if __name__ == "__main__":
    main()
