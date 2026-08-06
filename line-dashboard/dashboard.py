"""
Production-line performance dashboard.

A browser page that reads the logbook produced by the quality-monitor tracker
and shows, per production line (QC stage), how long cars stay - the cycle time -
and when it is faster or slower (by hour, by part of the day).

It does NOT touch the tracker or the camera. It only READS the logbook file
(logbook.db) and shows the numbers. You do the one-time "setup" here too:
give each stage a friendly line name (e.g. Stage 1 -> "Line B").

HOW TO RUN (from inside the line-dashboard folder):
    .\venv\Scripts\streamlit run dashboard.py

It opens automatically in your web browser. Leave the black terminal window
open while you use it; close it (or press Ctrl+C) to stop.

The pure data functions below are kept free of any screen code so they can be
tested on their own (see test_dashboard.py).
"""

import io
import json
import sqlite3
from pathlib import Path

import pandas as pd

# Where the logbook usually lives, relative to this folder. You can point at a
# different file in the dashboard's sidebar (e.g. C:\qm\logbook.db).
DEFAULT_DB = Path(__file__).resolve().parent.parent / "quality-monitor" / "logbook.db"
NAMES_FILE = Path(__file__).resolve().parent / "line_names.json"

PART_ORDER = [
    "Early (before 09)", "Morning (09-11)", "Late morning (11-13)",
    "Lunch (13-14)", "Afternoon (14-17)", "Evening (17-20)", "Night (20-06)",
]


# ---------------------------------------------------------------------------
# Pure data helpers (no screen code - safe to import and test)
# ---------------------------------------------------------------------------
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


def load_visits(db_path):
    """Read all stage visits from the logbook. Returns an empty frame if none."""
    conn = sqlite3.connect(str(db_path))
    try:
        df = pd.read_sql_query("SELECT * FROM stage_visits ORDER BY id", conn)
    finally:
        conn.close()
    return df


def enrich(df, names=None):
    """Add friendly line name and time-of-day columns; returns a new frame."""
    df = df.copy()
    names = names or {}
    when = pd.to_datetime(df["left_at"], errors="coerce")
    df["left_dt"] = when
    df["date"] = when.dt.date.astype("string")
    df["hour"] = when.dt.hour
    df["part_of_day"] = when.dt.hour.map(part_of_day)
    # Friendly line name (falls back to the raw stage name if unmapped).
    df["line"] = df["stage"].map(lambda s: names.get(s, s))
    # Cycle time in minutes is easier to read on charts.
    df["cycle_min"] = (df["dwell_s"] / 60.0).round(2)
    return df


def _agg(df, group_cols):
    g = (df.groupby(group_cols)["dwell_s"]
           .agg(cars="count", avg_s="mean", median_s="median",
                fastest_s="min", slowest_s="max")
           .reset_index())
    for col in ("avg_s", "median_s"):
        g[col] = g[col].round(1)
    return g


def by_line(df):
    return _agg(df, ["line"])


def by_hour(df):
    return _agg(df, ["line", "hour"])


def by_part(df):
    g = _agg(df, ["line", "part_of_day"])
    g["part_of_day"] = pd.Categorical(g["part_of_day"], categories=PART_ORDER, ordered=True)
    return g.sort_values(["line", "part_of_day"])


def load_names():
    if NAMES_FILE.exists():
        return json.loads(NAMES_FILE.read_text())
    return {}


def save_names(names):
    NAMES_FILE.write_text(json.dumps(names, indent=2))


def build_excel(df):
    """Build an Excel workbook (in memory) with the raw + summary sheets."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        cols = ["stage", "line", "car_id", "entered_at", "left_at",
                "date", "hour", "part_of_day", "dwell_s", "cycle_min"]
        df[[c for c in cols if c in df.columns]].to_excel(writer, sheet_name="Stage visits", index=False)
        by_line(df).to_excel(writer, sheet_name="By line", index=False)
        by_part(df).to_excel(writer, sheet_name="By time of day", index=False)
        by_hour(df).to_excel(writer, sheet_name="By hour", index=False)
    buffer.seek(0)
    return buffer.getvalue()


def mmss(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# The dashboard screen (only runs when launched with `streamlit run`)
# ---------------------------------------------------------------------------
def render():
    import streamlit as st

    st.set_page_config(page_title="Line Performance", page_icon="🚗", layout="wide")
    st.title("🚗 Production-line performance")
    st.caption("Cycle time per line, read live from the tracker's logbook.")

    # --- Sidebar: where is the logbook, and which data to show ---
    st.sidebar.header("Data source")
    db_path = st.sidebar.text_input("Logbook file (logbook.db)", value=str(DEFAULT_DB))
    if st.sidebar.button("🔄 Reload"):
        st.rerun()

    if not Path(db_path).exists():
        st.warning(
            f"Can't find the logbook at:\n\n`{db_path}`\n\n"
            "Point the box in the sidebar at your `logbook.db` "
            "(for example `C:\\qm\\logbook.db`), then press Reload."
        )
        st.stop()

    try:
        raw = load_visits(db_path)
    except Exception as err:  # noqa: BLE001
        st.error(f"Could not read the logbook: {err}")
        st.stop()

    if raw.empty:
        st.info("The logbook has no finished car visits yet. "
                "Run the tracker and let a car pass through a stage first.")
        st.stop()

    names = load_names()
    df = enrich(raw, names)

    tab_perf, tab_setup, tab_data = st.tabs(
        ["📊 Performance", "⚙️ Setup (name the lines)", "📁 Data & export"])

    # ===== SETUP TAB =====
    with tab_setup:
        st.subheader("Give each stage a friendly line name")
        st.write("These names are used everywhere in the dashboard and the export.")
        stages = sorted(raw["stage"].unique())
        new_names = {}
        with st.form("names_form"):
            for s in stages:
                new_names[s] = st.text_input(f"“{s}” is called:", value=names.get(s, s), key=f"nm_{s}")
            if st.form_submit_button("💾 Save names"):
                save_names(new_names)
                st.success("Saved. The other tabs now use these names.")
                st.rerun()

    # --- Filters (shared by the performance + data tabs) ---
    st.sidebar.header("Filters")
    lines = sorted(df["line"].unique())
    chosen = st.sidebar.multiselect("Lines", lines, default=lines)
    valid_dates = df["left_dt"].dropna()
    if not valid_dates.empty:
        dmin, dmax = valid_dates.min().date(), valid_dates.max().date()
        date_range = st.sidebar.date_input("Date range", value=(dmin, dmax),
                                           min_value=dmin, max_value=dmax)
    else:
        date_range = None

    view = df[df["line"].isin(chosen)]
    if date_range and isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        lo, hi = date_range
        view = view[(view["left_dt"].dt.date >= lo) & (view["left_dt"].dt.date <= hi)]

    # ===== PERFORMANCE TAB =====
    with tab_perf:
        if view.empty:
            st.info("No visits match the current filters.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Cars measured", f"{len(view):,}")
            c2.metric("Avg cycle time", mmss(view["dwell_s"].mean()))
            c3.metric("Fastest", mmss(view["dwell_s"].min()))
            c4.metric("Slowest", mmss(view["dwell_s"].max()))

            st.subheader("Average cycle time by line")
            st.caption("Taller bar = slower line. Compare stage load / balance.")
            per_line = by_line(view).set_index("line")["avg_s"]
            st.bar_chart(per_line, y_label="avg cycle (seconds)")

            st.subheader("When is cycle time faster? (by time of day)")
            bp = by_part(view)
            pivot = bp.pivot(index="part_of_day", columns="line", values="avg_s")
            pivot = pivot.reindex(PART_ORDER).dropna(how="all")
            st.bar_chart(pivot, y_label="avg cycle (seconds)")

            st.subheader("By hour of day")
            bh = by_hour(view)
            pivot_h = bh.pivot(index="hour", columns="line", values="avg_s")
            st.bar_chart(pivot_h, y_label="avg cycle (seconds)")

            st.subheader("Each car over time")
            st.caption("One dot/line per car visit - watch for drift across the shift.")
            st.line_chart(view, x="left_dt", y="cycle_min", color="line",
                          y_label="cycle time (minutes)")

            st.subheader("Per-line summary")
            st.dataframe(by_line(view), use_container_width=True, hide_index=True)

    # ===== DATA TAB =====
    with tab_data:
        st.subheader("Every recorded visit")
        show_cols = ["line", "stage", "car_id", "entered_at", "left_at",
                     "part_of_day", "hour", "dwell_s", "cycle_min"]
        st.dataframe(view[[c for c in show_cols if c in view.columns]],
                     use_container_width=True, hide_index=True)
        st.download_button(
            "⬇️ Download Excel (with summaries)",
            data=build_excel(view),
            file_name="line_performance.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    render()
