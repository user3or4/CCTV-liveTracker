r"""
Export the logbook database to an Excel file.

Run this any time you want a spreadsheet of everything recorded so far:

    .\venv\Scripts\python export_excel.py

By default it writes a NEW file stamped with the date and time, e.g.
    logbook_export_2026-08-04_1530.xlsx
so it never clashes with a file you already have open in Excel.

You can also give it your own name:
    .\venv\Scripts\python export_excel.py "August report.xlsx"

The file has two sheets:
    "Cars"          - one row per finished car (headline numbers).
    "Worker times"  - one row per worker per car (personal hands-on times).
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

import database as db


def default_name():
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    return Path(__file__).parent / f"logbook_export_{stamp}.xlsx"


def part_of_day(hour):
    """Group an hour (0-23) into an easy-to-read part of the working day."""
    if hour < 9:
        return "Early (before 09)"
    if hour < 11:
        return "Morning (09-11)"
    if hour < 13:
        return "Late morning (11-13)"
    if hour < 14:
        return "Lunch (13-14)"
    if hour < 17:
        return "Afternoon (14-17)"
    if hour < 20:
        return "Evening (17-20)"
    return "Night (20-06)"


def add_time_columns(df):
    """Add date / hour / part-of-day columns based on when the car left."""
    df = df.copy()
    when = pd.to_datetime(df["left_at"], errors="coerce")
    df.insert(df.columns.get_loc("left_at") + 1, "date", when.dt.date.astype("string"))
    df.insert(df.columns.get_loc("date") + 1, "hour", when.dt.hour)
    df.insert(df.columns.get_loc("hour") + 1, "part_of_day", when.dt.hour.map(part_of_day))
    return df


def summarise(df):
    """
    Build three summary tables of dwell time (seconds):
      by stage, by part of day (per stage), and by exact hour (per stage).
    Each shows how many cars and the average / fastest / slowest dwell.
    """
    # Keep the camera in every summary so two cameras don't blur together.
    cam = ["camera"] if "camera" in df.columns else []

    def agg(group_cols):
        g = (df.groupby(cam + group_cols)["dwell_s"]
               .agg(cars="count", avg_dwell_s="mean",
                    fastest_s="min", slowest_s="max")
               .reset_index())
        g["avg_dwell_s"] = g["avg_dwell_s"].round(1)
        return g

    by_stage = agg(["stage"])
    by_part = agg(["stage", "part_of_day"])
    by_hour = agg(["stage", "hour"])
    return by_hour, by_part, by_stage


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else default_name()

    if not db.DB_FILE.exists():
        print("No logbook yet - the database file 'logbook.db' does not exist.")
        print("Run the live view (live_detect.py) and let at least one car finish first.")
        raise SystemExit(1)

    conn = db.connect()
    stage_visits = pd.read_sql_query("SELECT * FROM stage_visits ORDER BY id", conn)
    cars = pd.read_sql_query("SELECT * FROM cars ORDER BY id", conn)
    workers = pd.read_sql_query("SELECT * FROM worker_times ORDER BY car_row_id, worker_no", conn)
    conn.close()

    if stage_visits.empty and cars.empty:
        print("The logbook is empty - no finished cars recorded yet.")
        print("Run the live view and let at least one car pass through a stage first.")
        raise SystemExit(1)

    # Add helpful time columns and build the time-of-day summaries.
    by_hour = by_part = by_stage = None
    if not stage_visits.empty:
        stage_visits = add_time_columns(stage_visits)
        by_hour, by_part, by_stage = summarise(stage_visits)

    try:
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            # One row per car per parking stage (now with date/hour columns).
            stage_visits.to_excel(writer, sheet_name="Stage visits", index=False)
            if by_stage is not None:
                by_stage.to_excel(writer, sheet_name="By stage", index=False)
                by_part.to_excel(writer, sheet_name="By time of day", index=False)
                by_hour.to_excel(writer, sheet_name="By hour", index=False)
            # Older worker-phase data, only if any exists.
            if not cars.empty:
                cars.to_excel(writer, sheet_name="Cars", index=False)
                workers.to_excel(writer, sheet_name="Worker times", index=False)
    except ModuleNotFoundError:
        print("The Excel engine 'openpyxl' isn't installed. Install it once with:")
        print("    .\\venv\\Scripts\\pip install openpyxl")
        raise SystemExit(1)
    except PermissionError:
        print("Could not write the Excel file - it looks like it's already OPEN.")
        print(f"  File: {out}")
        print("Close it in Excel and run this again (or it will use a new timestamped")
        print("name automatically next time).")
        raise SystemExit(1)

    print(f"Exported {len(stage_visits)} stage visit(s) to:")
    print(f"  {out.resolve()}")


if __name__ == "__main__":
    main()
