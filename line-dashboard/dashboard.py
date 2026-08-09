"""
Production-line performance dashboard.

A browser page that reads the logbook produced by the quality-monitor tracker
and shows, per production line (QC stage), how long cars stay - the cycle time -
and when it is faster or slower. It also lets you:

  - name each stage as a line (Setup),
  - choose which charts to show and average vs median (adjustable),
  - clean the data (ignore or permanently delete unreal sub-1-minute cycles),
  - edit or erase individual rows (Admin - PIN protected).

It reads/writes only the logbook file (logbook.db); it never touches the camera.

HOW TO RUN (from inside the line-dashboard folder):
    .\venv\Scripts\streamlit run dashboard.py

To protect the Admin tab, set a PIN before launching, e.g. in PowerShell:
    $env:DASHBOARD_PIN = "1234"
    .\venv\Scripts\streamlit run dashboard.py

The pure data functions below have no screen code so they can be tested on their
own (see test_dashboard.py).
"""

import io
import json
import os
import sqlite3
from pathlib import Path

import pandas as pd

DEFAULT_DB = Path(__file__).resolve().parent.parent / "quality-monitor" / "logbook.db"
NAMES_FILE = Path(__file__).resolve().parent / "line_names.json"

# Cycles shorter than this are treated as unreal (a car just passing, a blip).
DEFAULT_MIN_CYCLE_S = 60

PART_ORDER = [
    "Early (before 09)", "Morning (09-11)", "Late morning (11-13)",
    "Lunch (13-14)", "Afternoon (14-17)", "Evening (17-20)", "Night (20-06)",
]
EDITABLE_COLS = ["entered_at", "left_at", "stage", "car_id", "dwell_s"]


# ---------------------------------------------------------------------------
# Pure data helpers (no screen code - safe to import and test)
# ---------------------------------------------------------------------------
def part_of_day(hour):
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
    conn = sqlite3.connect(str(db_path))
    try:
        return pd.read_sql_query("SELECT * FROM stage_visits ORDER BY id", conn)
    finally:
        conn.close()


def enrich(df, names=None):
    df = df.copy()
    names = names or {}
    when = pd.to_datetime(df["left_at"], errors="coerce")
    df["left_dt"] = when
    df["date"] = when.dt.date.astype("string")
    df["hour"] = when.dt.hour
    df["part_of_day"] = when.dt.hour.map(part_of_day)
    df["line"] = df["stage"].map(lambda s: names.get(s, s))
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


# --- data editing / cleaning (write to the logbook) ---
def delete_visits(db_path, ids):
    """Permanently delete the given visit id numbers. Returns how many were removed."""
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        marks = ",".join("?" for _ in ids)
        cur = conn.execute(f"DELETE FROM stage_visits WHERE id IN ({marks})", ids)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def update_visit(db_path, visit_id, fields):
    """Update one visit row. `fields` is a dict of column -> new value."""
    fields = {k: v for k, v in fields.items() if k in EDITABLE_COLS}
    if not fields:
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur = conn.execute(f"UPDATE stage_visits SET {sets} WHERE id = ?",
                           list(fields.values()) + [int(visit_id)])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def delete_short(db_path, min_seconds):
    """Permanently delete visits shorter than min_seconds (unreal cycles)."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("DELETE FROM stage_visits WHERE dwell_s < ?", [float(min_seconds)])
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def build_excel(df):
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
    st.caption("Cycle time per line, read from the tracker's logbook.")

    # --- Sidebar: data source ---
    st.sidebar.header("Data source")
    db_path = st.sidebar.text_input("Logbook file (logbook.db)", value=str(DEFAULT_DB))
    if st.sidebar.button("🔄 Reload"):
        st.rerun()

    if not Path(db_path).exists():
        st.warning(f"Can't find the logbook at:\n\n`{db_path}`\n\n"
                   "Point the sidebar box at your `logbook.db` (e.g. `C:\\qm\\logbook.db`), "
                   "then press Reload.")
        st.stop()
    try:
        raw = load_visits(db_path)
    except Exception as err:  # noqa: BLE001
        st.error(f"Could not read the logbook: {err}")
        st.stop()
    if raw.empty:
        st.info("The logbook has no finished car visits yet.")
        st.stop()

    names = load_names()
    df = enrich(raw, names)

    # --- Sidebar: filters (data cleaning + scope) ---
    st.sidebar.header("Filters")
    min_cycle = st.sidebar.number_input(
        "Ignore cycles under (seconds)", min_value=0, value=DEFAULT_MIN_CYCLE_S, step=10,
        help="Short blips aren't real cycles. Default 60s hides anything under a minute.")
    lines = sorted(df["line"].unique())
    chosen = st.sidebar.multiselect("Lines", lines, default=lines)
    valid_dates = df["left_dt"].dropna()
    date_range = None
    if not valid_dates.empty:
        dmin, dmax = valid_dates.min().date(), valid_dates.max().date()
        date_range = st.sidebar.date_input("Date range", value=(dmin, dmax),
                                           min_value=dmin, max_value=dmax)

    # --- Sidebar: adjustable display ---
    st.sidebar.header("Display")
    stat_label = st.sidebar.radio("Show", ["Average", "Median"], horizontal=True)
    stat_col = "avg_s" if stat_label == "Average" else "median_s"
    all_sections = ["By line", "By time of day", "By hour", "Over time", "Summary table"]
    sections = st.sidebar.multiselect("Sections to show", all_sections, default=all_sections)

    # Apply filters -> the "view" every tab shares.
    view = df[df["line"].isin(chosen) & (df["dwell_s"] >= min_cycle)]
    if date_range and isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        lo, hi = date_range
        view = view[(view["left_dt"].dt.date >= lo) & (view["left_dt"].dt.date <= hi)]

    hidden = len(df) - len(df[df["dwell_s"] >= min_cycle])
    if min_cycle > 0 and hidden > 0:
        st.sidebar.caption(f"{hidden} visit(s) under {min_cycle}s are hidden from the charts.")

    tab_perf, tab_setup, tab_data, tab_admin = st.tabs(
        ["📊 Performance", "⚙️ Setup", "📁 Data & export", "🔒 Admin"])

    # ===== PERFORMANCE =====
    with tab_perf:
        if view.empty:
            st.info("No visits match the current filters.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Cars measured", f"{len(view):,}")
            c2.metric(f"{stat_label} cycle",
                      mmss(view["dwell_s"].mean() if stat_col == "avg_s" else view["dwell_s"].median()))
            c3.metric("Fastest", mmss(view["dwell_s"].min()))
            c4.metric("Slowest", mmss(view["dwell_s"].max()))

            if "By line" in sections:
                st.subheader(f"{stat_label} cycle time by line")
                st.caption("Taller bar = slower line. Compare stage load / balance.")
                st.bar_chart(by_line(view).set_index("line")[stat_col], y_label="seconds")

            if "By time of day" in sections:
                st.subheader("When is cycle time faster? (by part of day)")
                pv = by_part(view).pivot(index="part_of_day", columns="line", values=stat_col)
                st.bar_chart(pv.reindex(PART_ORDER).dropna(how="all"), y_label="seconds")

            if "By hour" in sections:
                st.subheader("By hour of day")
                pvh = by_hour(view).pivot(index="hour", columns="line", values=stat_col)
                st.bar_chart(pvh, y_label="seconds")

            if "Over time" in sections:
                st.subheader("Each car over time")
                st.caption("One point per car - watch for drift across the shift.")
                st.line_chart(view, x="left_dt", y="cycle_min", color="line",
                              y_label="cycle time (minutes)")

            if "Summary table" in sections:
                st.subheader("Per-line summary")
                st.dataframe(by_line(view), use_container_width=True, hide_index=True)

    # ===== SETUP =====
    with tab_setup:
        st.subheader("Name each stage as a production line")
        stages = sorted(raw["stage"].unique())
        new_names = {}
        with st.form("names_form"):
            for s in stages:
                new_names[s] = st.text_input(f"“{s}” is called:", value=names.get(s, s), key=f"nm_{s}")
            if st.form_submit_button("💾 Save names"):
                save_names(new_names)
                st.success("Saved. Every tab now uses these names.")
                st.rerun()

    # ===== DATA & EXPORT =====
    with tab_data:
        st.subheader("Every visit (after filters)")
        show = ["line", "stage", "car_id", "entered_at", "left_at",
                "part_of_day", "hour", "dwell_s", "cycle_min"]
        st.dataframe(view[[c for c in show if c in view.columns]],
                     use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download Excel (with summaries)",
                           data=build_excel(view), file_name="line_performance.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ===== ADMIN (edit / erase / clean) =====
    with tab_admin:
        pin = os.environ.get("DASHBOARD_PIN", "")
        unlocked = True
        if pin:
            entered = st.text_input("Admin PIN", type="password")
            unlocked = (entered == pin)
            if not unlocked:
                st.info("Enter the admin PIN to edit or delete data.")
        else:
            st.warning("No admin PIN is set — anyone with this page can edit/delete. "
                       "To protect it, set an environment variable **DASHBOARD_PIN** before launching.")

        if unlocked:
            st.subheader("Clean the data")
            st.write("Remove unreal short cycles permanently from the logbook.")
            colA, colB = st.columns([2, 3])
            cut = colA.number_input("Delete visits under (seconds)", min_value=1, value=60, step=10)
            n_short = int((raw["dwell_s"] < cut).sum())
            colB.caption(f"{n_short} visit(s) currently under {cut}s.")
            if st.button(f"🗑️ Permanently delete {n_short} short visit(s)", disabled=n_short == 0):
                removed = delete_short(db_path, cut)
                st.success(f"Deleted {removed} visit(s).")
                st.rerun()

            st.divider()
            st.subheader("Edit or erase individual rows")
            st.caption("Change a value in a cell, or tick a row and use the toolbar's 🗑 to remove it. "
                       "Then press Save. The `id` column can't be changed.")
            editor_df = raw[["id"] + EDITABLE_COLS].copy()
            edited = st.data_editor(editor_df, num_rows="dynamic", disabled=["id"],
                                    use_container_width=True, hide_index=True, key="editor")
            if st.button("💾 Save changes to the logbook"):
                orig = raw.set_index("id")
                edited_valid = edited.dropna(subset=["id"])
                kept = set(int(i) for i in edited_valid["id"])
                to_delete = set(int(i) for i in orig.index) - kept
                n_updated = 0
                for _, row in edited_valid.iterrows():
                    rid = int(row["id"])
                    if rid in orig.index:
                        o = orig.loc[rid]
                        changed = {c: row[c] for c in EDITABLE_COLS if str(row[c]) != str(o[c])}
                        if changed:
                            update_visit(db_path, rid, changed)
                            n_updated += 1
                n_deleted = delete_visits(db_path, to_delete)
                st.success(f"Saved: {n_updated} edited, {n_deleted} deleted.")
                st.rerun()


if __name__ == "__main__":
    render()
