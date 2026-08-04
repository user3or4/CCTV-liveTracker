# Quality Monitor

A computer-vision quality-monitoring system for a car assembly line.

This is the **project setup stage only**. No features have been built yet — this
folder contains the foundation (tools installed, model prepared) that everything
else will be built on top of.

---

## What's in this project

| Piece | What it is, in plain words |
|-------|-----------------------------|
| `venv/` | A private "toolbox" for this project. It keeps all the software libraries below in one isolated place so they never clash with anything else on the computer. |
| `requirements.txt` | A shopping list of the exact software libraries (and versions) this project uses. Anyone can recreate the same setup from this file. |
| `get_model.py` | A tiny helper that downloads the AI model file (see below). |
| `yolov8n.pt` | The AI model file — the "trained brain" that recognises objects like cars and people in an image. *(Downloaded separately — see "The AI model" below.)* |

---

## The software libraries (what each one is for)

- **opencv-python** — The **eyes**. It reads video and images: opening a camera
  feed, grabbing individual frames, resizing, and drawing boxes/labels on the
  picture. Anything to do with handling the raw video happens here.

- **ultralytics (YOLO)** — The **brain**. This is the object-detection engine.
  Give it a picture and it tells you what's in it and where — "there's a car
  here, a person there" — by drawing a box around each thing it finds.

- **supervision** — The **helper / organiser**. It takes the raw detections
  from YOLO and makes them useful: counting objects, tracking the same car
  across frames, checking whether something is inside a defined zone, and
  tidying up the on-screen annotations. It saves us writing a lot of plumbing.

- **streamlit** — The **dashboard**. It turns our program into a simple web page
  you open in a browser — with the video, buttons, and live numbers — so you and
  the team can use the system without touching any code.

- **pandas + openpyxl** — The **exporter**. Together they read the logbook and
  write it out as an Excel spreadsheet whenever you want one.

Put simply: **OpenCV sees the video → YOLO recognises what's in it →
Supervision counts and tracks it → Streamlit shows it all on a screen.**

---

## The logbook (`logbook.db`)

Every time a car finishes at a station, its numbers are saved **immediately**
into a small local database file called `logbook.db` (using SQLite — built into
Python, no server or internet needed). Nothing is lost if the program stops.

- Run the live view:            `.\venv\Scripts\python live_detect.py`
- Turn it into Excel any time:  `.\venv\Scripts\python export_excel.py`

The Excel file has a **Cars** sheet (one row per car) and a **Worker times**
sheet (each worker's own hands-on time, numbered 1, 2, 3… per car).

### Current phase: cars only (parking-stage dwell)

The **Stage visits** sheet has one row each time a car leaves a parking stage:

| Column | Plain meaning | Used for |
|--------|---------------|----------|
| `entered_at` / `left_at` | when the car arrived / left the stage | throughput, timing |
| `stage` | which parking stage (e.g. "Stage 1") | **line balancing** by stage |
| `car_id` | the car's tracking number | tracing a car through a stage |
| `dwell_s` | how long the car stayed in the stage (seconds) | **cycle / stage time** |

This is deliberately **raw data** — one honest row per car per stage, no
averaging — so it can feed cycle-time analysis and stage-load / line-balancing
comparisons (and muda / muri / mura studies) later.

*(The worker "hands-on" phase is deferred; if that data already exists it is
kept in separate **Cars** / **Worker times** sheets.)*

---

## The AI model (`yolov8n.pt`)

The system recognises objects using a standard, pre-trained model called
**YOLOv8n** ("n" = *nano*, the smallest and fastest version — good for a first
build). It already knows 80 everyday object types out of the box, including the
two we care about most: **`car`** and **`person`** (also `truck` and `bus`,
which are handy for a vehicle line).

### How to get the model file

The first time you run any detection, the `ultralytics` library **downloads
this file automatically** — you usually don't have to do anything.

To download it ahead of time (recommended, so the first run is instant), just
run this once from inside the project folder:

```bash
./venv/bin/python get_model.py
```

That will place `yolov8n.pt` (about 6 MB) in this folder.

> **Note for this cloud setup:** the model file was **not** downloaded here,
> because this particular build environment blocks the download websites
> (GitHub and Hugging Face) for security reasons. On a normal computer or
> server the command above will fetch it in a few seconds. Everything else is
> installed and confirmed working.

---

## How to run things (quick reference)

From inside the `quality-monitor` folder:

- **Recreate this setup on another machine:**
  ```bash
  python3 -m venv venv
  ./venv/bin/pip install -r requirements.txt
  ```
- **Download the model:** `./venv/bin/python get_model.py`
- **(Later) run the dashboard:** `./venv/bin/streamlit run app.py`
  *(there is no `app.py` yet — that's a future step)*
