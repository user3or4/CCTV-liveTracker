# Line Performance Dashboard

A browser dashboard that shows **cycle time per production line** from the data
the quality-monitor tracker records. It only **reads** the tracker's logbook
(`logbook.db`) — it never touches the camera or the tracker itself.

This is a separate goal from the tracker, so it lives in its own folder with its
own toolbox (virtual environment).

## What it shows

- **Average (or median) cycle time by line** — compare lines, spot the slow one.
- **When is cycle time faster?** — by part of the day (Morning / Lunch /
  Afternoon…) and by hour.
- **Each car over time** — watch for drift across a shift.
- **Excel download** — the filtered data plus summary sheets, for deeper analysis.

## Adjustable

The sidebar lets you tailor the view:
- **Statistic** — switch between Average and Median.
- **Sections to show** — tick only the charts you want.
- **Lines** and **Date range** — narrow the data.
- **Ignore cycles under (seconds)** — data cleaning: hides unreal short cycles
  (default 60s = anything under a minute is not shown in the charts).

## Admin (edit / erase / clean) — PIN protected

The **🔒 Admin** tab lets you fix the data:
- **Clean** — permanently delete visits under a chosen length (e.g. under 60s,
  which aren't real cycles).
- **Edit or erase rows** — change a value in a cell, or remove a row, then Save.

To protect this tab, set a PIN before launching (PowerShell):
```
$env:DASHBOARD_PIN = "1234"
.\venv\Scripts\streamlit run dashboard.py
```
If no PIN is set, the tab still works but shows a warning that it's unprotected.

## Setup (once)

From inside the `line-dashboard` folder:

```
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
```

## Run it

```
.\venv\Scripts\streamlit run dashboard.py
```

It opens in your web browser automatically. Leave the black terminal window open
while you use it; close it (or press Ctrl+C) to stop.

## First-time steps in the dashboard

1. **Point it at the logbook.** In the sidebar, set *Logbook file* to wherever
   your `logbook.db` is (e.g. `C:\qm\logbook.db`) and press **Reload**.
2. **Name your lines** (the “setup” you asked for). Open the **⚙️ Setup** tab and
   type a friendly name for each stage — e.g. *Stage 1 → “Line B”*. Press
   **Save names**. Those names are used everywhere afterwards.
3. **Read the performance.** The **📊 Performance** tab shows the charts; the
   **📁 Data & export** tab has the full table and the Excel download button.

The line names are stored in `line_names.json` in this folder, so you only set
them once.

## Note

The dashboard reads whatever is in the logbook at the moment you open or reload
it. Keep the tracker running to keep gathering data, and press **🔄 Reload** in
the sidebar to pull in the latest.
