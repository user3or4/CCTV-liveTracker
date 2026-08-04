r"""
Draw one or more numbered parking STAGES by clicking on a camera snapshot.

Focus: CARS only. For each stage you draw ONE shape around the parking spot.
After each one you're asked whether to add another. All stages are saved to
zones.json so you only draw them once.

HOW TO RUN (from inside the quality-monitor folder):
    .\venv\Scripts\python define_zones.py

CONTROLS while drawing a shape (also shown on screen):
    Left-click ....... add a corner point
    Right-click ...... undo the last point
    ENTER ............ finish the current shape
    R ................ clear the current shape and start it over
    Q or ESC ......... cancel everything (nothing saved)

Tip: click at least 3 corners per stage, going around the parking spot in order.
"""

import sys
import time

import cv2
import numpy as np

import zone_config as zc

DEFAULT_SOURCE = "rtsp://alahmadiab:A123456a@10.236.7.105:554/cam/realmonitor?channel=5&subtype=0"
WINDOW_NAME = "Define Stages - click the corners"


def grab_snapshot(source, tries=30):
    """Open the camera and grab one good frame to draw on."""
    print(f"Connecting to camera to take a snapshot:\n  {source}")
    capture = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        capture.release()
        return None
    frame = None
    for _ in range(tries):  # first few RTSP frames are often blank
        ok, f = capture.read()
        if ok and f is not None:
            frame = f
        if frame is not None:
            time.sleep(0.05)
    capture.release()
    return frame


class PolygonDrawer:
    """Lets the user click the corners of one stage on the snapshot."""

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

    def render(self, already):
        img = self.base.copy()

        # Show stages already finished (faint), so you can place the next ones.
        for s in already:
            pts = np.array(s[zc.STAGE], dtype=np.int32)
            cv2.polylines(img, [pts], True, zc.color_for(s["id"]), 1)

        if len(self.points) >= 3:
            overlay = img.copy()
            cv2.fillPoly(overlay, [np.array(self.points, dtype=np.int32)], self.color)
            img = cv2.addWeighted(overlay, 0.25, img, 0.75, 0)
        if self.points:
            for i in range(len(self.points) - 1):
                cv2.line(img, self.points[i], self.points[i + 1], self.color, 2)
            cv2.line(img, self.points[-1], self.mouse, self.color, 1)
            for p in self.points:
                cv2.circle(img, p, 5, self.color, -1)

        banner = img.copy()
        cv2.rectangle(banner, (0, 0), (img.shape[1], 70), (0, 0, 0), -1)
        img = cv2.addWeighted(banner, 0.6, img, 0.4, 0)
        cv2.putText(img, f"Draw: {self.title}   (points: {len(self.points)})",
                    (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img, "L-click=add  R-click=undo  ENTER=done  R=reset  Q=cancel",
                    (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
        return img

    def run(self, already):
        cv2.setMouseCallback(WINDOW_NAME, self.on_mouse)
        while True:
            cv2.imshow(WINDOW_NAME, self.render(already))
            key = cv2.waitKey(20) & 0xFF
            if key in (ord("q"), 27):
                return None
            if key in (ord("r"), ord("R")):
                self.points = []
            if key in (13, 10):  # ENTER
                if len(self.points) >= 3:
                    return self.points
                print("  Need at least 3 corners before finishing this stage.")


def ask_add_another(base_image, stages):
    """Between stages: A = add another, F = finish and save, Q = cancel."""
    while True:
        img = base_image.copy()
        for s in stages:
            pts = np.array(s[zc.STAGE], dtype=np.int32)
            cv2.polylines(img, [pts], True, zc.color_for(s["id"]), 2)
            c = tuple(np.array(s[zc.STAGE][0]))
            cv2.putText(img, f"{s['id']}", (c[0], max(c[1] - 8, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, zc.color_for(s["id"]), 2, cv2.LINE_AA)

        banner = img.copy()
        cv2.rectangle(banner, (0, 0), (img.shape[1], 70), (0, 0, 0), -1)
        img = cv2.addWeighted(banner, 0.6, img, 0.4, 0)
        cv2.putText(img, f"Saved {len(stages)} stage(s).",
                    (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(img, "Press  A = add another stage    F = finish & save    Q = cancel",
                    (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)

        cv2.imshow(WINDOW_NAME, img)
        key = cv2.waitKey(20) & 0xFF
        if key in (ord("a"), ord("A")):
            return "add"
        if key in (ord("f"), ord("F")):
            return "finish"
        if key in (ord("q"), 27):
            return "cancel"


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE

    frame = grab_snapshot(source)
    if frame is None:
        print("\nCould not get a picture from the camera.")
        print("Check the camera link/network, or pass a saved image instead:")
        print('    .\\venv\\Scripts\\python define_zones.py "C:\\path\\to\\snapshot.jpg"')
        if len(sys.argv) > 1:
            frame = cv2.imread(sys.argv[1])
        if frame is None:
            raise SystemExit(1)

    height, width = frame.shape[:2]
    print(f"Got a {width}x{height} snapshot. Opening the drawing window...\n")
    cv2.namedWindow(WINDOW_NAME)

    stages = []
    stage_id = 1
    while True:
        color = zc.color_for(stage_id)
        pts = PolygonDrawer(frame, f"STAGE {stage_id} (parking spot)", color).run(stages)
        if pts is None:
            cv2.destroyAllWindows()
            print("Cancelled. Nothing saved.")
            return

        stages.append({"id": stage_id, zc.STAGE: pts})
        print(f"  Stage {stage_id} captured.")

        choice = ask_add_another(frame, stages)
        if choice == "add":
            stage_id += 1
            continue
        if choice == "cancel":
            cv2.destroyAllWindows()
            print("Cancelled. Nothing saved.")
            return
        break  # finish

    cv2.destroyAllWindows()
    path = zc.save_stages(stages, (width, height))
    print(f"\nSaved {len(stages)} stage(s) to:\n  {path}")
    for s in stages:
        print(f"  Stage {s['id']}: {len(s[zc.STAGE])} corners")
    print("\nDone. Now run the live view:  .\\venv\\Scripts\\python live_detect.py")


if __name__ == "__main__":
    main()
