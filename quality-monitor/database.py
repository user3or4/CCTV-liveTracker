"""
The logbook database (SQLite).

In plain words: this is a single file on your computer (logbook.db) that works
like a small, private spreadsheet-with-superpowers. There's no server, no
internet, and no separate program to install - Python has it built in. Every
time a car finishes, we write its row straight into that file and save it
immediately, so if the program closes (or the power blips) nothing already
recorded is lost.

We keep two linked tables:
  cars          - one row per finished car (the headline numbers).
  worker_times  - one row per worker on that car (their personal hands-on time),
                  numbered 1, 2, 3, ... within each car.

You can open logbook.db with free tools (like "DB Browser for SQLite"), or use
export_excel.py to turn it into an Excel file whenever you want.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_FILE = Path(__file__).parent / "logbook.db"


def connect(db_path=DB_FILE):
    """Open the database file (creates it if it doesn't exist yet)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=DB_FILE):
    """Create the tables the first time. Safe to call every run."""
    conn = connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cars (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at     TEXT,               -- date and time the car arrived
            finished_at    TEXT    NOT NULL,   -- date and time the car left
            station        TEXT    NOT NULL,   -- e.g. "Station 1"
            car_id         INTEGER,            -- the car's tracking number
            cycle_time_s   REAL    NOT NULL,   -- seconds car was in the station
            hands_on_s     REAL    NOT NULL,   -- total worker time (no double count)
            waiting_s      REAL,               -- cycle - hands-on = idle time (muda)
            on_sessions    INTEGER NOT NULL,   -- separate on/off worker sessions
            num_workers    INTEGER NOT NULL    -- how many workers were counted
        );

        CREATE TABLE IF NOT EXISTS worker_times (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            car_row_id    INTEGER NOT NULL,    -- links back to cars.id
            worker_no     INTEGER NOT NULL,    -- 1, 2, 3, ... within this car
            hands_on_s    REAL    NOT NULL,    -- this person's own hands-on time
            sessions      INTEGER NOT NULL,    -- this person's on/off count
            FOREIGN KEY (car_row_id) REFERENCES cars(id)
        );

        -- Car-only phase: one row each time a car leaves a parking stage.
        CREATE TABLE IF NOT EXISTS stage_visits (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            camera       TEXT,                 -- which camera / car model this run watched
            entered_at   TEXT,                 -- when the car arrived at the stage
            left_at      TEXT    NOT NULL,     -- when the car left the stage
            stage        TEXT    NOT NULL,     -- e.g. "Stage 1"
            car_id       INTEGER,              -- the car's tracking number
            dwell_s      REAL    NOT NULL      -- how long the car stayed (seconds)
        );
        """
    )
    # Add newer columns to an older logbook that predates them (keeps old data).
    cars_cols = [row[1] for row in conn.execute("PRAGMA table_info(cars)")]
    for col, decl in (("started_at", "TEXT"), ("waiting_s", "REAL")):
        if col not in cars_cols:
            conn.execute(f"ALTER TABLE cars ADD COLUMN {col} {decl}")
    visit_cols = [row[1] for row in conn.execute("PRAGMA table_info(stage_visits)")]
    if "camera" not in visit_cols:
        conn.execute("ALTER TABLE stage_visits ADD COLUMN camera TEXT")
    conn.commit()
    conn.close()


def save_car(report, station_name, db_path=DB_FILE, finished_at=None):
    """
    Save one finished car and its per-worker times, and commit immediately so
    the data is safe even if the program stops right after.

    report: the dict produced by timers.StationMonitor (has cycle_time,
            hands_on_time, on_sessions, car_track_id, workers=[...]).
    Returns the new row id in the cars table.
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    end_epoch = report.get("end_epoch")
    start_epoch = report.get("start_epoch")
    when = finished_at or (
        datetime.fromtimestamp(end_epoch).strftime(fmt) if end_epoch
        else datetime.now().strftime(fmt))
    started = datetime.fromtimestamp(start_epoch).strftime(fmt) if start_epoch else None
    # Idle time while the car sat with no one working on it (a muda signal).
    waiting = round(max(report["cycle_time"] - report["hands_on_time"], 0.0), 1)

    conn = connect(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO cars
               (started_at, finished_at, station, car_id, cycle_time_s, hands_on_s,
                waiting_s, on_sessions, num_workers)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (started, when, station_name, report.get("car_track_id"),
             report["cycle_time"], report["hands_on_time"], waiting,
             report["on_sessions"], report["unique_workers"]),
        )
        car_row_id = cur.lastrowid

        for w in report.get("workers", []):
            conn.execute(
                """INSERT INTO worker_times
                   (car_row_id, worker_no, hands_on_s, sessions)
                   VALUES (?, ?, ?, ?)""",
                (car_row_id, w["index"], w["hands_on"], w["sessions"]),
            )

        conn.commit()   # <-- saved to disk right now; nothing is lost on a crash
        return car_row_id
    finally:
        conn.close()


def save_stage_visit(report, stage_name, camera=None, db_path=DB_FILE, left_at=None):
    """
    Save one finished stage visit (a car that stayed in a parking stage) and
    commit immediately so nothing is lost if the program stops.

    report: the dict from timers.StationMonitor (cycle_time is the dwell time,
            plus car_track_id and start/end epochs).
    camera: which camera / car model this run was watching (for multi-camera setups).
    Returns the new row id.
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    end_epoch = report.get("end_epoch")
    start_epoch = report.get("start_epoch")
    left = left_at or (
        datetime.fromtimestamp(end_epoch).strftime(fmt) if end_epoch
        else datetime.now().strftime(fmt))
    entered = datetime.fromtimestamp(start_epoch).strftime(fmt) if start_epoch else None

    conn = connect(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO stage_visits (camera, entered_at, left_at, stage, car_id, dwell_s)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (camera, entered, left, stage_name, report.get("car_track_id"),
             report["cycle_time"]),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
