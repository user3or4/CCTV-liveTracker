"""
Export the logbook database to an Excel file.

Run this any time you want a spreadsheet of everything recorded so far:

    .\venv\Scripts\python export_excel.py

It creates  logbook_export.xlsx  in this folder with two sheets:
    "Cars"          - one row per finished car (headline numbers).
    "Worker times"  - one row per worker per car (personal hands-on times).

You can also give it a name:
    .\venv\Scripts\python export_excel.py "August report.xlsx"
"""

import sys
from pathlib import Path

import pandas as pd

import database as db

DEFAULT_OUT = Path(__file__).parent / "logbook_export.xlsx"


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT

    if not db.DB_FILE.exists():
        print("No logbook yet - the database file 'logbook.db' does not exist.")
        print("Run the live view (live_detect.py) and let at least one car finish first.")
        raise SystemExit(1)

    conn = db.connect()
    cars = pd.read_sql_query("SELECT * FROM cars ORDER BY id", conn)
    workers = pd.read_sql_query("SELECT * FROM worker_times ORDER BY car_row_id, worker_no", conn)
    conn.close()

    if cars.empty:
        print("The logbook is empty - no finished cars recorded yet.")
        raise SystemExit(1)

    try:
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            cars.to_excel(writer, sheet_name="Cars", index=False)
            workers.to_excel(writer, sheet_name="Worker times", index=False)
    except ModuleNotFoundError:
        print("The Excel engine 'openpyxl' isn't installed. Install it once with:")
        print("    .\\venv\\Scripts\\pip install openpyxl")
        raise SystemExit(1)

    print(f"Exported {len(cars)} car(s) and {len(workers)} worker row(s) to:")
    print(f"  {out.resolve()}")


if __name__ == "__main__":
    main()
