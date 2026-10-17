# Production KPI - Injection Moulding (Version 1: NPT + Rejection)

Upload the NPT (downtime) and Rejection reports from Smart Manufacturing every day. The app checks the files,
saves the records in a **local SQLite database file**, and shows NPT and Rejection analysis by day, month,
year, machine, cause and product. Everything can be exported to Excel.

No setup is required: the app creates `data/kpi.db` automatically the first time it runs, with no secrets,
connection strings, or external services to configure. If that file ever can't be created or opened (for
example a read-only filesystem), the app automatically falls back to an in-memory database instead, so it
still launches cleanly - that database only lasts for as long as this process keeps running, and the
sidebar shows a plain warning when this has happened.

**Important trade-off:** on Streamlit Community Cloud specifically, the app's own disk is temporary - it can
be wiped whenever the app restarts (after a period of inactivity, a redeploy, or routine maintenance), taking
`data/kpi.db` with it. This setup trades that persistence away for zero configuration. See "Keeping your data
safe" below for how to protect against it; if you want data that survives restarts automatically with no
extra steps, consider an external database again later (Supabase Postgres is a free option) - ask and this
can be put back.

Version 1 does NOT use the Injection Due report. There is no OEE, Availability, Performance, Quality or
Rejection % (Rejection % needs a production quantity). The old production/OEE code is kept, switched off,
in `v2_production/` so it can be added later.

## Run on your computer
    pip install -r requirements.txt
    streamlit run app.py
The database file lives at `data/kpi.db`. Change where it's stored with the environment variable
`KPI_DB_PATH`. Optional password: set `KPI_APP_PASSWORD` as an environment variable, or in
`.streamlit/secrets.toml` (see `.streamlit/secrets.toml.example`) - entirely optional either way.

## Deploy to Streamlit Community Cloud
Push this project to GitHub, then on share.streamlit.io: New app -> pick the repo -> main file `app.py` ->
Deploy. Nothing else is required. Add `KPI_APP_PASSWORD` under the app's Settings -> Secrets only if you
want the site locked behind a password.

## Every day
1. Download the NPT and Rejection reports from Smart Manufacturing.
2. Open **Upload Reports**, choose the report date, upload the files, click **PROCESS REPORTS**.
3. If data for that date already exists, choose Replace / Skip / Cancel.
4. Use Dashboard, Daily / Monthly / Yearly / Machine / NPT / Rejection Analysis. Export to Excel from any page.

## Bulk / date-range NPT upload
If Smart Manufacturing exported NPT for a range of days (10, 15, even 100+ days in one file), tick
**"NPT file covers a date range (bulk upload)"** on Upload Reports instead of picking a single date.
Each event is stored and replaced under its OWN day automatically, from its own From Time - no date needs
to be chosen, and days outside the file are never touched. Re-uploading a smaller range later (say, to
correct one week) only replaces the days that smaller file actually contains; everything else in the
database is untouched. Rejection upload stays single-day only. Rows dated before the database start date
(Settings) are skipped, not silently dropped - they are listed as rejected rows.

## Machine Mapping (Machine No. / Production Line)
Machines are created automatically the moment their ID is first seen in an upload - this page only attaches
a **Machine No.** (position, e.g. "A1") and a **Production Line** (e.g. "A Line") to them, on the
**Machine Mapping** page. Upload a file with Machine ID / Machine No. / Production Line columns, or edit the
table directly. A machine with no mapping yet shows as **"Unmapped"** everywhere (never hidden or dropped).
Production Line is never hard-coded, exactly like NPT/Rejection causes: any line name you use is accepted
and remembered automatically, ready to reuse next time. Filter any analysis page or export by Production
Line the same way you already filter by Machine.

## Pages
Dashboard, Upload Reports, Daily, Monthly, Yearly, Machine, Machine Mapping, NPT and Rejection Analysis,
Data History, Upload History, Data Quality, Excel Export & Backup, Settings.

## What the files mean (checked on your real files)
* NPT: one row per downtime event (machine, from, to, cause). Dates are dd/mm/yyyy.
  Events can cross midnight, so each event is cut at midnight: only the time inside each calendar day is counted.
  Example: the 18 Sep file shows 396.1 h in total, but only 274.4 h falls on 18 Sep.
  An event that continues on the next day is counted again on that day by design, and is never stored twice.
* Rejection: one row per rejection entry. Dates are m/d/yyyy. Quantity = thousand pieces (x1000 = pieces),
  Weight = tons (x1000 = kg). There is no shift column, so rejection cannot be split by shift.
* Machine IDs are normalised: IMM-380-03 and IMM-380-3 are the same machine.
* Causes are never hard-coded. A new cause is accepted automatically. The trailing "*", extra spaces and
  capital/small letters are cleaned. Original text is kept in the database (`cause_raw`).

## Numbers
    Total NPT (h)      = time inside the selected days (events cut at midnight)
    Occurrences        = number of NPT events
    Average / Maximum  = per event, inside the selected period
    NPT % of scheduled = NPT hours / (machines x 24 h x days)   (only if "Number of machines" is set in Settings)
    Rejected pieces    = Quantity x 1000            Rejected weight (kg) = Weight x 1000
    Pareto             = causes sorted by hours (NPT) or pieces (Rejection), with % and cumulative %
Shift-wise NPT: Shift A starts at 08:00 by default (A = 08:00-20:00, B = 20:00-08:00). Change it in Settings.
A night shift that passes midnight appears on two calendar dates.
Not included: MTBF / MTTR (needs a decision on which causes are breakdowns).

## Keeping your data safe
* Real data starts on the "Database start date" set in Settings (default 1 September 2026); earlier report
  dates are refused so old test uploads can't mix in with real records.
* **Excel Export & Backup -> Prepare a backup** downloads every stored record as one `.json.gz` file. On
  Streamlit Community Cloud, make this a habit after each day's upload - it's the only thing that protects
  you if the app's disk gets wiped. **Restore from backup** on the same page loads one back in (it also
  still reads whole-database `.db` file backups from an even older version of this app).
* **Settings -> Manage stored data** lets you delete a single day's report, or reset everything (a backup is
  made automatically first, before anything is deleted).
* `KPI_DEMO=1` (an environment variable) switches the whole app to a separate local file (`data/demo.db`),
  so you can practice without touching real data.
* The sidebar shows a warning whenever the app is running on a temporary in-memory database, so you always
  know if persistence is currently off.

## Data checks
Rows with a wrong machine ID, missing dates, or To Time before From Time are not saved and are listed (the rest of the
file is still saved). Overlapping events on one machine, very long events, and rejection records with zero weight are
shown in Data Quality. Nothing is corrected silently.

## Test with your real files
    python tests/test_pipeline.py <folder with the 2 sample files>
    python tests/test_append.py <folder>
    python tests/test_bulk_and_mapping.py <folder>
    python tests/test_ui.py <folder>
Each test uses its own temporary SQLite file, so they never touch real data.

## Structure (modular)
    app.py                 page menu, password screen, startup
    database/               db.py (SQLite connection, schema, in-memory fallback), repository.py (save /
                            replace / skip / delete / reset / backup / restore, upload history)
    processing/             cleaner.py, npt_processor.py, rejection_processor.py  (one file per report type)
    kpi/                    npt.py, rejection.py, common.py
    ui/                     one file per page
    utils/excel_export.py   all Excel workbooks
    v2_production/          NOT ACTIVE: Injection Due reader, OEE engine, production table (Version 2)
To add a report type later: add a processor in `processing/`, a KPI module in `kpi/`, a save branch in
`database/repository.py`, and pages in `ui/`.

## Files not needed right now
`Dockerfile` and `render.yaml` were set up for an earlier plan (hosting on Render with a persistent disk).
They're inactive and harmless to leave in the repo - safe to delete, or keep them for later if you ever want
a host where `data/kpi.db` survives restarts on its own without the manual backup habit above.
