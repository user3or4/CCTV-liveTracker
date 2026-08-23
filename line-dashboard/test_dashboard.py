"""
Tests for the dashboard's data functions (no browser needed).

We build a tiny logbook with visits at different hours and lines, then check
the summaries and the Excel builder come out right.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd

import dashboard as d


def make_db(rows, camera="cam1"):
    """rows: list of (left_at, stage, car_id, dwell_s). Returns a temp db path."""
    path = Path(tempfile.mkdtemp()) / "logbook.db"
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE stage_visits(
        id INTEGER PRIMARY KEY AUTOINCREMENT, camera TEXT, entered_at TEXT, left_at TEXT,
        stage TEXT, car_id INTEGER, dwell_s REAL)""")
    for left, stage, cid, dwell in rows:
        conn.execute("INSERT INTO stage_visits(camera,entered_at,left_at,stage,car_id,dwell_s) "
                     "VALUES(?,?,?,?,?,?)", (camera, left, left, stage, cid, dwell))
    conn.commit()
    conn.close()
    return path


ROWS = [
    ("2026-08-04 08:30:00", "Stage 1", 1, 60),    # early
    ("2026-08-04 10:15:00", "Stage 1", 2, 90),    # morning
    ("2026-08-04 13:30:00", "Stage 1", 3, 200),   # lunch (slow)
    ("2026-08-04 15:00:00", "Stage 1", 4, 80),    # afternoon (fast)
    ("2026-08-04 10:45:00", "Stage 2", 5, 150),
]

db = make_db(ROWS)
raw = d.load_visits(db)
assert len(raw) == 5, len(raw)
assert "camera" in raw.columns and set(raw["camera"]) == {"cam1"}, raw.columns.tolist()
print("camera column present:", set(raw["camera"]))

# names mapping applies
names = {"Stage 1": "Line B", "Stage 2": "Line C"}
df = d.enrich(raw, names)
assert set(df["line"]) == {"Line B", "Line C"}, set(df["line"])
assert "part_of_day" in df.columns and "hour" in df.columns

# by_line
bl = d.by_line(df).set_index("line")
assert bl.loc["Line B", "cars"] == 4, bl.loc["Line B", "cars"]
assert bl.loc["Line B", "fastest_s"] == 60 and bl.loc["Line B", "slowest_s"] == 200

# by_part: lunch is the slowest part of day for Line B
bp = d.by_part(df)
lineb = bp[bp["line"] == "Line B"].set_index("part_of_day")["avg_s"]
assert lineb.get("Lunch (13-14)") == 200, lineb.to_dict()
assert lineb.get("Afternoon (14-17)") == 80, lineb.to_dict()
print("by time of day (Line B):", {k: v for k, v in lineb.dropna().items()})

# excel builder produces a real workbook with the expected sheets
xlsx = d.build_excel(df)
assert xlsx[:2] == b"PK", "not a valid xlsx"
tmp = Path(tempfile.mkdtemp()) / "out.xlsx"
tmp.write_bytes(xlsx)
sheets = pd.ExcelFile(tmp).sheet_names
assert sheets == ["Stage visits", "By line", "By time of day", "By hour"], sheets
print("excel sheets:", sheets)

os.remove(tmp)

# --- data cleaning / editing on a fresh db ---
db2 = make_db([
    ("2026-08-04 09:00:00", "Stage 1", 1, 30),    # unreal (<60s)
    ("2026-08-04 09:10:00", "Stage 1", 2, 45),    # unreal (<60s)
    ("2026-08-04 09:20:00", "Stage 1", 3, 120),   # real
    ("2026-08-04 09:30:00", "Stage 2", 4, 300),   # real
])

# delete_short removes only the sub-60s rows
removed = d.delete_short(db2, 60)
assert removed == 2, removed
left = d.load_visits(db2)
assert len(left) == 2 and left["dwell_s"].min() == 120, left["dwell_s"].tolist()
print("delete_short removed", removed, "-> remaining", left["dwell_s"].tolist())

# update_visit changes a field
rid = int(left.iloc[0]["id"])
d.update_visit(db2, rid, {"dwell_s": 999, "stage": "Stage X"})
row = d.load_visits(db2).set_index("id").loc[rid]
assert row["dwell_s"] == 999 and row["stage"] == "Stage X", row.to_dict()
print("update_visit ok ->", row["dwell_s"], row["stage"])

# delete_visits erases a specific row
other = int(left.iloc[1]["id"])
assert d.delete_visits(db2, [other]) == 1
assert other not in set(d.load_visits(db2)["id"])
print("delete_visits ok -> rows left:", len(d.load_visits(db2)))

os.remove(db)
os.remove(db2)

# --- break subtraction ---
from datetime import time as dtime

def one(enter, leave, dwell_s):
    return pd.DataFrame([{"id": 1, "camera": "c", "entered_at": enter, "left_at": leave,
                          "stage": "Stage 1", "car_id": 1, "dwell_s": dwell_s}])

# The user's example: 65-min stay, a 40-min break fully inside -> 25 min real.
b = d.apply_breaks(d.enrich(one("2026-08-04 12:00:00", "2026-08-04 13:05:00", 65 * 60)),
                   [(dtime(12, 10), dtime(12, 50)), (dtime(0, 0), dtime(0, 0))])
assert b.iloc[0]["break_s"] == 40 * 60, b.iloc[0]["break_s"]
assert b.iloc[0]["adjusted_s"] == 25 * 60, b.iloc[0]["adjusted_s"]
print(f"65min stay - 40min break = {b.iloc[0]['adjusted_s']/60:.0f}min adjusted  OK")

# Partial overlap: stay 12:30-13:30, break 12:00-13:00 -> 30 min overlap.
b = d.apply_breaks(d.enrich(one("2026-08-04 12:30:00", "2026-08-04 13:30:00", 60 * 60)),
                   [(dtime(12, 0), dtime(13, 0))])
assert b.iloc[0]["break_s"] == 30 * 60, b.iloc[0]["break_s"]
assert b.iloc[0]["adjusted_s"] == 30 * 60, b.iloc[0]["adjusted_s"]
print("partial overlap 30min  OK")

# No overlap: break outside the stay -> nothing subtracted.
b = d.apply_breaks(d.enrich(one("2026-08-04 09:00:00", "2026-08-04 09:30:00", 30 * 60)),
                   [(dtime(12, 0), dtime(12, 40))])
assert b.iloc[0]["break_s"] == 0 and b.iloc[0]["adjusted_s"] == 30 * 60
print("no-overlap break=0  OK")

# Two breaks both inside a long stay are both subtracted.
b = d.apply_breaks(d.enrich(one("2026-08-04 11:00:00", "2026-08-04 16:00:00", 5 * 3600)),
                   [(dtime(12, 0), dtime(12, 40)), (dtime(15, 0), dtime(15, 15))])
assert b.iloc[0]["break_s"] == (40 + 15) * 60, b.iloc[0]["break_s"]
print("two breaks summed  OK")

print("\nALL DASHBOARD DATA TESTS PASSED")
