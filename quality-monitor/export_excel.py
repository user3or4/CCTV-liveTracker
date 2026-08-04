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

    try:
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            # This phase: one row per car per parking stage.
            stage_visits.to_excel(writer, sheet_name="Stage visits", index=False)
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
