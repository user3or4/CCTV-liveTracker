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
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
DEFAULT_DB = _HERE.parent / "quality-monitor" / "logbook.db"
NAMES_FILE = _HERE / "line_names.json"
PREFS_FILE = _HERE / "prefs.json"          # remembered settings (breaks, off-time, display...)
CAR_NAMES_FILE = _HERE / "car_names.json"  # car model per line PER DAY (blank each new day)

# Cycles shorter than this are treated as unreal (a car just passing, a blip).
DEFAULT_MIN_CYCLE_S = 60

PART_ORDER = [
    "Early (before 09)", "Morning (09-11)", "Late morning (11-13)",
    "Lunch (13-14)", "Afternoon (14-17)", "Evening (17-20)", "Night (20-06)",
]
EDITABLE_COLS = ["camera", "entered_at", "left_at", "stage", "car_id", "dwell_s"]


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
    df["entered_dt"] = pd.to_datetime(df.get("entered_at"), errors="coerce")
    df["date"] = when.dt.date.astype("string")
    df["hour"] = when.dt.hour
    df["part_of_day"] = when.dt.hour.map(part_of_day)
    df["line"] = df["stage"].map(lambda s: names.get(s, s))
    df["cycle_min"] = (df["dwell_s"] / 60.0).round(2)
    return df


def window_overlap_seconds(enter, leave, start_t, end_t):
    """
    Seconds of a stay (enter..leave) that fall inside a DAILY window [start,end].
    If end <= start the window wraps past midnight (e.g. 16:00 -> 07:00 next day).
    Works for stays that span several days.
    """
    if pd.isna(enter) or pd.isna(leave) or leave <= enter:
        return 0.0
    if start_t == end_t:      # an empty/disabled window (not a 24-hour one)
        return 0.0
    total = 0.0
    day = enter.date() - timedelta(days=1)
    last = leave.date() + timedelta(days=1)
    while day <= last:
        ws = pd.Timestamp(datetime.combine(day, start_t))
        we = pd.Timestamp(datetime.combine(day, end_t))
        if we <= ws:                      # wraps midnight
            we = we + pd.Timedelta(days=1)
        ov = (min(leave, we) - max(enter, ws)).total_seconds()
        if ov > 0:
            total += ov
        day += timedelta(days=1)
    return total


def total_deduction_seconds(enter, leave, windows):
    """Sum overlap across every non-production window (breaks + off-time)."""
    return sum(window_overlap_seconds(enter, leave, s, e) for s, e in windows)


def apply_deductions(df, windows):
    """
    Add deducted_s (non-production time inside each stay: breaks + off-time) and
    adjusted_s (dwell minus that), so cycle time reflects real working time.
    `windows` is a list of (start_time, end_time) datetime.time pairs.
    """
    df = df.copy()
    df["deducted_s"] = [round(total_deduction_seconds(e, l, windows), 1)
                        for e, l in zip(df["entered_dt"], df["left_dt"])]
    df["adjusted_s"] = (df["dwell_s"] - df["deducted_s"]).clip(lower=0).round(1)
    df["adjusted_min"] = (df["adjusted_s"] / 60.0).round(2)
    return df


DEFAULT_PREFS = {
    "breaks_enabled": False,
    "breaks": [["12:00", "12:40"], ["15:00", "15:15"]],
    "offtime_enabled": True,
    "offtime": ["16:00", "07:00"],
    "min_cycle_s": DEFAULT_MIN_CYCLE_S,
    "stat": "Average",
    "sections": ["By line", "By time of day", "By hour", "Per car (each line)",
                 "Over time", "Summary table"],
    "autorefresh": False,
}


def load_prefs():
    prefs = dict(DEFAULT_PREFS)
    if PREFS_FILE.exists():
        try:
            prefs.update(json.loads(PREFS_FILE.read_text()))
        except Exception:  # noqa: BLE001
            pass
    return prefs


def save_prefs(prefs):
    try:
        PREFS_FILE.write_text(json.dumps(prefs, indent=2))
    except Exception:  # noqa: BLE001
        pass


def load_car_names():
    """{'YYYY-MM-DD': {line: car_model}} - a fresh (empty) sheet each new day."""
    if CAR_NAMES_FILE.exists():
        try:
            return json.loads(CAR_NAMES_FILE.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_car_names(data):
    CAR_NAMES_FILE.write_text(json.dumps(data, indent=2))


def _agg(df, group_cols, value_col="dwell_s"):
    g = (df.groupby(group_cols)[value_col]
           .agg(cars="count", avg_s="mean", median_s="median",
                fastest_s="min", slowest_s="max")
           .reset_index())
    for col in ("avg_s", "median_s"):
        g[col] = g[col].round(1)
    return g


def by_line(df, value_col="dwell_s"):
    return _agg(df, ["line"], value_col)


def by_hour(df, value_col="dwell_s"):
    return _agg(df, ["line", "hour"], value_col)


def by_part(df, value_col="dwell_s"):
    g = _agg(df, ["line", "part_of_day"], value_col)
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


def apply_car_names(df, car_names):
    """Attach the car model recorded for each row's (date, line)."""
    df = df.copy()
    df["car_model"] = [car_names.get(str(dt), {}).get(ln, "")
                       for dt, ln in zip(df["date"], df["line"])]
    return df


def build_excel(df, value_col="dwell_s"):
    """Export raw rows plus summaries. `value_col` is the cycle-time column the
    summaries are built on (dwell_s, or adjusted_s when time is subtracted)."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        cols = ["camera", "car_model", "stage", "line", "car_id", "entered_at", "left_at",
                "date", "hour", "part_of_day", "dwell_s", "cycle_min",
                "deducted_s", "adjusted_s", "adjusted_min"]
        df[[c for c in cols if c in df.columns]].to_excel(writer, sheet_name="Stage visits", index=False)
        by_line(df, value_col).to_excel(writer, sheet_name="By line", index=False)
        by_part(df, value_col).to_excel(writer, sheet_name="By time of day", index=False)
        by_hour(df, value_col).to_excel(writer, sheet_name="By hour", index=False)
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

    prefs = load_prefs()

    def _t(s):
        h, m = (int(x) for x in s.split(":"))
        return dtime(h, m)

    # --- Sidebar: data source ---
    st.sidebar.header("Data source")
    db_path = st.sidebar.text_input("Logbook file (logbook.db)", value=str(DEFAULT_DB))
    col_rl, col_ar = st.sidebar.columns([1, 2])
    if col_rl.button("🔄 Reload"):
        st.rerun()
    autorefresh = col_ar.checkbox("Auto every 1 min", value=prefs.get("autorefresh", False))
    if autorefresh:
        # Reload the whole page every 60s (picks up new data; prefs are restored).
        import streamlit.components.v1 as components
        components.html("<script>setTimeout(function(){window.parent.location.reload();},60000);</script>",
                        height=0)

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
    car_names = load_car_names()
    df = apply_car_names(enrich(raw, names), car_names)

    # --- Sidebar: filters (data cleaning + scope) ---
    st.sidebar.header("Filters")
    min_cycle = st.sidebar.number_input(
        "Ignore cycles under (seconds)", min_value=0, value=int(prefs.get("min_cycle_s", DEFAULT_MIN_CYCLE_S)),
        step=10, help="Short blips aren't real cycles. Default 60s hides anything under a minute.")

    cams = sorted(str(c) for c in df["camera"].dropna().unique()) if "camera" in df.columns else []
    chosen_cams = cams
    if len(cams) > 1:
        chosen_cams = st.sidebar.multiselect("Cameras / car models", cams, default=cams)

    lines = sorted(df["line"].unique())
    chosen = st.sidebar.multiselect("Lines", lines, default=lines)

    valid_dates = df["left_dt"].dropna()
    date_range = None
    if not valid_dates.empty:
        dmin, dmax = valid_dates.min().date(), valid_dates.max().date()
        date_range = st.sidebar.date_input("Date range", value=(dmin, dmax),
                                           min_value=dmin, max_value=dmax)
    from_hr, to_hr = st.sidebar.slider("Time of day (hours)", 0, 23, (0, 23),
                                       help="Keep only cars that left between these hours.")

    # --- Sidebar: non-production time to subtract from cycle time ---
    st.sidebar.header("Non-production time")
    offtime_on = st.sidebar.checkbox("Subtract OFF time (no production)",
                                     value=prefs.get("offtime_enabled", True))
    off = prefs.get("offtime", ["16:00", "07:00"])
    o1 = st.sidebar.time_input("Off from", value=_t(off[0]), key="o1")
    o2 = st.sidebar.time_input("Off until (next day)", value=_t(off[1]), key="o2")

    breaks_on = st.sidebar.checkbox("Subtract BREAK time",
                                    value=prefs.get("breaks_enabled", False))
    brk = prefs.get("breaks", [["12:00", "12:40"], ["15:00", "15:15"]])
    b1s = st.sidebar.time_input("Break 1 start", value=_t(brk[0][0]), key="b1s")
    b1e = st.sidebar.time_input("Break 1 end", value=_t(brk[0][1]), key="b1e")
    b2s = st.sidebar.time_input("Break 2 start", value=_t(brk[1][0]), key="b2s")
    b2e = st.sidebar.time_input("Break 2 end", value=_t(brk[1][1]), key="b2e")

    # --- Sidebar: adjustable display ---
    st.sidebar.header("Display")
    stat_label = st.sidebar.radio("Show", ["Average", "Median"], horizontal=True,
                                  index=0 if prefs.get("stat", "Average") == "Average" else 1)
    stat_col = "avg_s" if stat_label == "Average" else "median_s"
    all_sections = ["By line", "By time of day", "By hour", "Per car (each line)",
                    "Over time", "Summary table"]
    sections = st.sidebar.multiselect("Sections to show", all_sections,
                                      default=prefs.get("sections", all_sections))

    # Remember everything for next time (survives Reload and app restart).
    save_prefs({
        "breaks_enabled": breaks_on,
        "breaks": [[b1s.strftime("%H:%M"), b1e.strftime("%H:%M")],
                   [b2s.strftime("%H:%M"), b2e.strftime("%H:%M")]],
        "offtime_enabled": offtime_on,
        "offtime": [o1.strftime("%H:%M"), o2.strftime("%H:%M")],
        "min_cycle_s": int(min_cycle),
        "stat": stat_label,
        "sections": sections,
        "autorefresh": autorefresh,
    })

    # Apply filters -> the "view" every tab shares.
    view = df[df["line"].isin(chosen) & (df["dwell_s"] >= min_cycle)]
    if cams:
        view = view[view["camera"].astype("string").isin(chosen_cams)]
    if date_range and isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        lo, hi = date_range
        view = view[(view["left_dt"].dt.date >= lo) & (view["left_dt"].dt.date <= hi)]
    view = view[(view["hour"] >= from_hr) & (view["hour"] <= to_hr)]

    # Build the list of non-production windows to subtract, then adjust.
    windows = []
    if breaks_on:
        windows += [(b1s, b1e), (b2s, b2e)]
    if offtime_on:
        windows += [(o1, o2)]
    view = apply_deductions(view, windows)
    deducting = bool(windows)
    value_col = "adjusted_s" if deducting else "dwell_s"
    minutes_col = "adjusted_min" if deducting else "cycle_min"
    cycle_label = "cycle (adjusted)" if deducting else "cycle"

    hidden = len(df) - len(df[df["dwell_s"] >= min_cycle])
    if min_cycle > 0 and hidden > 0:
        st.sidebar.caption(f"{hidden} visit(s) under {min_cycle}s are hidden from the charts.")
    if deducting:
        st.sidebar.caption("Break / off-time is being subtracted from cycle time.")

    tab_perf, tab_setup, tab_data, tab_admin = st.tabs(
        ["📊 Performance", "⚙️ Setup", "📁 Data & export", "🔒 Admin"])

    # ===== PERFORMANCE =====
    with tab_perf:
        if view.empty:
            st.info("No visits match the current filters.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Cars measured", f"{len(view):,}")
            c2.metric(f"{stat_label} {cycle_label}",
                      mmss(view[value_col].mean() if stat_col == "avg_s" else view[value_col].median()))
            c3.metric("Fastest", mmss(view[value_col].min()))
            c4.metric("Slowest", mmss(view[value_col].max()))
            if deducting:
                st.caption("Cycle times below have break / off-time removed.")

            if "By line" in sections:
                st.subheader(f"{stat_label} {cycle_label} time by line")
                st.caption("Taller bar = slower line. Compare stage load / balance.")
                st.bar_chart(by_line(view, value_col).set_index("line")[stat_col], y_label="seconds")

            if "By time of day" in sections:
                st.subheader("When is cycle time faster? (by part of day)")
                pv = by_part(view, value_col).pivot(index="part_of_day", columns="line", values=stat_col)
                st.bar_chart(pv.reindex(PART_ORDER).dropna(how="all"), y_label="seconds")

            if "By hour" in sections:
                st.subheader("By hour of day")
                pvh = by_hour(view, value_col).pivot(index="hour", columns="line", values=stat_col)
                st.bar_chart(pvh, y_label="seconds")

            if "Per car (each line)" in sections:
                st.subheader("Each car, one bar per line")
                st.caption("One bar = one car, in the order it left. Spot the long and short ones at a glance.")
                for ln in sorted(view["line"].unique()):
                    d_ln = view[view["line"] == ln].sort_values("left_dt").reset_index(drop=True)
                    # Title includes today's car model for this line, if set.
                    model = ""
                    if "car_model" in d_ln.columns:
                        mvals = [m for m in d_ln["car_model"].unique() if m]
                        model = f" — {', '.join(mvals)}" if mvals else ""
                    st.markdown(f"**{ln}{model}**  ·  {len(d_ln)} cars")
                    bars = pd.DataFrame({
                        "car": [f"{i+1}" for i in range(len(d_ln))],
                        "minutes": d_ln[minutes_col].values,
                    }).set_index("car")
                    st.bar_chart(bars, y_label=f"{cycle_label} time (minutes)")

            if "Over time" in sections:
                st.subheader("Each car over time")
                st.caption("One point per car - watch for drift across the shift.")
                st.line_chart(view, x="left_dt", y=minutes_col, color="line",
                              y_label=f"{cycle_label} time (minutes)")

            if "Summary table" in sections:
                st.subheader("Per-line summary")
                st.dataframe(by_line(view, value_col), use_container_width=True, hide_index=True)

    # ===== SETUP =====
    with tab_setup:
        st.subheader("Name each stage as a production line")
        st.caption("This is the permanent line name (kept every day).")
        stages = sorted(raw["stage"].unique())
        new_names = {}
        with st.form("names_form"):
            for s in stages:
                new_names[s] = st.text_input(f"“{s}” is called:", value=names.get(s, s), key=f"nm_{s}")
            if st.form_submit_button("💾 Save line names"):
                save_names(new_names)
                st.success("Saved. Every tab now uses these names.")
                st.rerun()

        st.divider()
        today = datetime.now().strftime("%Y-%m-%d")
        st.subheader(f"Today's car model per line  ({today})")
        st.caption("Which car is running on each line today. Starts blank each new day, "
                   "so you enter it again — it's saved with today's data.")
        today_map = car_names.get(today, {})
        line_list = sorted(df["line"].unique())
        new_today = {}
        with st.form("car_today_form"):
            for ln in line_list:
                new_today[ln] = st.text_input(f"{ln}:", value=today_map.get(ln, ""),
                                              placeholder="e.g. Land Cruiser", key=f"car_{ln}")
            if st.form_submit_button("💾 Save today's cars"):
                data = load_car_names()
                data[today] = {ln: v.strip() for ln, v in new_today.items() if v.strip()}
                save_car_names(data)
                st.success("Saved today's car models.")
                st.rerun()

    # ===== DATA & EXPORT =====
    with tab_data:
        st.subheader("Every visit (after filters)")
        show = ["camera", "car_model", "line", "stage", "car_id", "entered_at", "left_at",
                "part_of_day", "hour", "dwell_s"]
        if deducting:
            show += ["deducted_s", "adjusted_s"]
        else:
            show += ["cycle_min"]
        st.dataframe(view[[c for c in show if c in view.columns]],
                     use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download Excel (with summaries)",
                           data=build_excel(view, value_col), file_name="line_performance.xlsx",
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
            edit_cols = [c for c in EDITABLE_COLS if c in raw.columns]
            editor_df = raw[["id"] + edit_cols].copy()
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
                        changed = {c: row[c] for c in edit_cols if str(row[c]) != str(o[c])}
                        if changed:
                            update_visit(db_path, rid, changed)
                            n_updated += 1
                n_deleted = delete_visits(db_path, to_delete)
                st.success(f"Saved: {n_updated} edited, {n_deleted} deleted.")
                st.rerun()


if __name__ == "__main__":
    render()
