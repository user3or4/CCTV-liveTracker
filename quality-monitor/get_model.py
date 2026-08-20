"""
Download the standard YOLOv8n model file (yolov8n.pt) used to detect
cars and people.

Run this once, from inside the quality-monitor folder:

    ./venv/bin/python get_model.py

It saves yolov8n.pt (~6 MB) into this folder. On a normal computer or
server this takes a few seconds. If your network blocks the download,
the script tells you clearly instead of failing silently.
"""

from pathlib import Path
from ultralytics import YOLO

# Match the model live_detect.py uses (medium = stronger detection).
# Match the model live_detect.py uses (medium segmentation = real car shapes).
MODEL_NAME = "yolov8m-seg.pt"


def main() -> None:
    target = Path(__file__).parent / MODEL_NAME

    if target.exists() and target.stat().st_size > 1_000_000:
        print(f"'{MODEL_NAME}' is already here ({target.stat().st_size / 1e6:.1f} MB). Nothing to do.")
        return

    print(f"Downloading '{MODEL_NAME}' ...")
    try:
        # Loading the model by name triggers the download if it's missing.
        model = YOLO(MODEL_NAME)
    except Exception as err:  # noqa: BLE001 - surface any download problem plainly
        print("\nCould not download the model.")
        print(f"Reason: {err}")
        print(
            "\nThis usually means the network is blocking the download site. "
            "Try again on a machine with normal internet access."
        )
        raise SystemExit(1)

    # Confirm the classes we care about are present.
    classes = set(model.names.values())
    for wanted in ("car", "person"):
        status = "yes" if wanted in classes else "MISSING"
        print(f"  can detect '{wanted}': {status}")

    print(f"\nDone. Model saved and ready ({target.name}).")


if __name__ == "__main__":
    main()
