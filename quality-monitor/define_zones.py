"""
Draw the two zones by clicking on a snapshot from the camera.

You draw two shapes, one after the other:
  1) STATION   - the inspection-station area (we watch for a CAR here).
  2) WORK AREA - the slightly larger area where the worker stands
                 (we watch for a PERSON here).

Both shapes are saved to zones.json so you only draw them once.

HOW TO RUN (from inside the quality-monitor folder):
    .\venv\Scripts\python define_zones.py

CONTROLS (shown on screen too):
    Left-click ....... add a corner point
    Right-click ...... undo the last point
    ENTER ............ finish the current shape and move on
    R ................ clear the current shape and start it over
    Q or ESC ......... quit without saving

Tip: click at least 3 corners to make a shape. Go around the area in order
(like connecting dots); the shape closes itself automatically.
"""

import sys
import time

import cv2
import numpy as np

import zone_config as zc

DEFAULT_SOURCE = "rtsp://alahmadiab:A123456a@10.236.7.105:554/cam/realmonitor?channel=5&subtype=0"
WINDOW_NAME = "Define Zones - click the corners"


def grab_snapshot(source, tries=30):
    """Open the camera and grab one good frame to draw on."""
    print(f"Connecting to camera to take a snapshot:\n  {source}")
    capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        capture.release()
        return None

    frame = None
    # The first few RTSP frames are often blank; grab a handful.
    for _ in range(tries):
        ok, f = capture.read()
        if ok and f is not None:
            frame = f
        if frame is not None:
            time.sleep(0.05)
    capture.release()
    return frame


# ---------------------------------------------------------------------------
# Interactive drawing of a single polygon
# ---------------------------------------------------------------------------
class PolygonDrawer:
    def __init__(self, base_image, title, color_bgr):
        self.base = base_image
        self.title = title
        self.color = color_bgr
        self.points = []
        self.mouse = (0, 0)

    def on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN and self.points:
            self.points.pop()
        elif event == cv2.EVENT_MOUSEMOVE:
            self.mouse = (x, y)

    def render(self):
        img = self.base.copy()

        # Shade the shape so far.
        if len(self.points) >= 3:
            overlay = img.copy()
            cv2.fillPoly(overlay, [np.array(self.points, dtype=np.int32)], self.color)
            img = cv2.addWeighted(overlay, 0.25, img, 0.75, 0)

        # Draw the edges drawn so far, plus a "rubber band" to the cursor.
        if self.points:
            for i in range(len(self.points) - 1):
                cv2.line(img, self.points[i], self.points[i + 1], self.color, 2)
            cv2.line(img, self.points[-1], self.mouse, self.color, 1)
            for p in self.points:
                cv2.circle(img, p, 5, self.color, -1)

        # Instruction banner across the top.
        banner = img.copy()
        cv2.rectangle(banner, (0, 0), (img.shape[1], 70), (0, 0, 0), -1)
        img = cv2.addWeighted(banner, 0.6, img, 0.4, 0)
        cv2.putText(img, f"Draw: {self.title}   (points: {len(self.points)})",
                    (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img, "L-click=add   R-click=undo   ENTER=done   R=reset   Q=quit",
                    (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
        return img

    def run(self):
        """Returns the list of points, or None if the user quit."""
        cv2.setMouseCallback(WINDOW_NAME, self.on_mouse)
        while True:
            cv2.imshow(WINDOW_NAME, self.render())
            key = cv2.waitKey(20) & 0xFF
            if key in (ord("q"), 27):            # Q or ESC
                return None
            if key in (ord("r"), ord("R")):      # reset this shape
                self.points = []
            if key in (13, 10):                  # ENTER
                if len(self.points) >= 3:
                    return self.points
                print("  Need at least 3 corners before finishing this shape.")


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE

    frame = grab_snapshot(source)
    if frame is None:
        print("\nCould not get a picture from the camera.")
        print("Check the camera link/network (the live viewer's checklist applies),")
        print("or pass a saved image instead:")
        print('    .\\venv\\Scripts\\python define_zones.py "C:\\path\\to\\snapshot.jpg"')
        # Allow using a saved image file as the source too.
        if len(sys.argv) > 1:
            img = cv2.imread(sys.argv[1])
            if img is not None:
                frame = img
        if frame is None:
            raise SystemExit(1)

    height, width = frame.shape[:2]
    print(f"Got a {width}x{height} snapshot. Opening the drawing window...\n")

    cv2.namedWindow(WINDOW_NAME)

    # Shape 1: the station.
    station = PolygonDrawer(frame, "STATION (car inspection area)", zc.STATION_COLOR_BGR).run()
    if station is None:
        cv2.destroyAllWindows()
        print("Cancelled. Nothing saved.")
        return

    # Shape 2: the work area.
    work_area = PolygonDrawer(frame, "WORK AREA (where the worker stands)", zc.WORK_AREA_COLOR_BGR).run()
    if work_area is None:
        cv2.destroyAllWindows()
        print("Cancelled. Nothing saved.")
        return

    cv2.destroyAllWindows()

    path = zc.save_zones(station, work_area, (width, height))
    print("\nSaved both zones to:")
    print(f"  {path}")
    print(f"  station  : {len(station)} corners")
    print(f"  work_area: {len(work_area)} corners")
    print("\nDone. Now run the live view:  .\\venv\\Scripts\\python live_detect.py")


if __name__ == "__main__":
    main()
