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
- **Each car, one bar per line** — one bar = one car, so at a glance you see
  which car took the longest and which the shortest (a line with 10 cars shows
  10 bars).
- **Each car over time** — watch for drift across a shift.
- **Excel download** — the filtered data plus summary sheets, for deeper analysis.

## Non-production time (real working cycle)

Set these once in the sidebar; they're subtracted from each car's cycle time so
you see the true working time:
- **OFF time (no production)** — an overnight window (default **16:00 → 07:00
  next day**), adjustable.
- **Break time** — up to two break windows.

Example: a car in the station 65 min with a 40-min break inside → shows 25 min.

## Per-day car model

In the **Setup** tab, "Today's car model per line" lets you type which car is
running on each line today (e.g. *Line B → Land Cruiser*). It **starts blank each
new day**, so you enter it again, and it's saved alongside that day's data and
shown in the per-car graph and the export.

## Adjustable & remembered

The sidebar lets you tailor the view — **Statistic** (Average/Median),
**Sections to show**, **Lines**, **Date range**, **Time of day (hours)**,
**Cameras**, and **Ignore cycles under (seconds)**. Your choices are **saved
automatically** and restored on Reload or restart. Tick **Auto every 1 min** to
have the page refresh itself once a minute.

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
