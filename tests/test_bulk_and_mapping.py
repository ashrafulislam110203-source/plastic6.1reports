"""Tests the two newer features end to end, against a local SQLite file, using the real sample NPT
file (replicated across several days to build a synthetic "date range" export, the same way Smart
Manufacturing's own date-range export would look):

  1. Bulk NPT upload: one file covering many days, each event stored and replaced under its OWN
     day - re-uploading a shorter range only ever touches the days that range actually contains.
  2. Machine Mapping: Machine ID -> Machine No. -> Production Line, joined into NPT/Rejection
     analysis and exports, with machines that aren't mapped yet showing as 'Unmapped'.

Run:  python tests/test_bulk_and_mapping.py <folder with the sample files>
"""
import datetime as dt, glob, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
folder = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads"
os.environ["KPI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_bulk_and_mapping.db")

import openpyxl
import pandas as pd
from database import db, repository as repo
from processing.npt_processor import parse_npt
from kpi import npt

ok = []
def check(name, cond, info=""):
    ok.append(bool(cond)); print(("PASS  " if cond else "FAIL  ") + name, info)

db.init_db()
S = db.get_settings()
start = dt.date.fromisoformat(S["data_start_date"])

# ---------------------------------------------------------------- build a synthetic bulk file
src = glob.glob(os.path.join(folder, "Downtime*"))[0]
wb = openpyxl.load_workbook(src)
ws = wb.active
header_row = next(i for i, row in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True), start=1)
                  if any(str(c).strip().lower() == "machine" for c in row if c))
cols = [c.value for c in ws[header_row]]
from_i, to_i, sl_i = (cols.index(k) + 1 for k in ("From Time", "To Time", "Sl"))
real_events = [[c.value for c in r] for r in ws.iter_rows(min_row=header_row + 1, max_row=ws.max_row)
              if isinstance(r[from_i - 1].value, dt.datetime) and isinstance(r[to_i - 1].value, dt.datetime)]

def make_bulk_file(days, out_path, cause_override=None, n_events=None):
    nwb = openpyxl.load_workbook(src)
    nws = nwb.active
    nws.delete_rows(header_row + 1, nws.max_row - header_row)
    cause_i = cols.index("Cause") + 1
    out_row, sl = header_row + 1, 1
    for day in days:
        shift = (day - dt.date(2026, 9, 17)).days
        for rec in real_events[:n_events]:
            vals = list(rec)
            vals[sl_i - 1] = sl; sl += 1
            vals[from_i - 1] = vals[from_i - 1] + dt.timedelta(days=shift)
            vals[to_i - 1] = vals[to_i - 1] + dt.timedelta(days=shift)
            if cause_override:
                vals[cause_i - 1] = cause_override
            for j, v in enumerate(vals, start=1):
                nws.cell(row=out_row, column=j, value=v)
            out_row += 1
    for r in nws.iter_rows(min_row=1, max_row=header_row - 1):
        if r[0].value and str(r[0].value).strip().lower().startswith("date range"):
            r[1].value = f"{days[0]:%d/%m/%Y} to {days[-1]:%d/%m/%Y}"
    nwb.save(out_path)

tmp = tempfile.mkdtemp()
f1 = os.path.join(tmp, "bulk5.xlsx")
make_bulk_file([dt.date(2026, 10, d) for d in range(1, 6)], f1)

# ---------------------------------------------------------------- bulk parse + save
rep1 = parse_npt(f1, "bulk5.xlsx", None, S, bulk=True, data_start=start)
check("bulk parse found multiple days", len(rep1.dates) > 1, rep1.dates)
check("bulk rows all valid (clean synthetic file)", rep1.rows_valid == rep1.rows_read)
uid = repo.add_history("NPT", "bulk5.xlsx", f"{rep1.dates[0]} to {rep1.dates[-1]}", rep1.rows_read, rep1.rows_valid, 0, "Success", "No duplicate", 0)
n = repo.save_report(rep1, uid, "new")
check("bulk save wrote every row", n == rep1.rows_valid, n)

snap_before = {r.report_date: (r.n, round(r.s, 1)) for r in
              db.query_df("SELECT report_date, COUNT(*) n, SUM(duration_sec) s FROM npt_events GROUP BY report_date").itertuples()}

# ---------------------------------------------------------------- reupload a sub-range: only ITS OWN days must change
f2 = os.path.join(tmp, "bulk_resmall.xlsx")
make_bulk_file([dt.date(2026, 10, 2), dt.date(2026, 10, 3)], f2, cause_override="REUPLOAD TEST CAUSE", n_events=30)
rep2 = parse_npt(f2, "resmall.xlsx", None, S, bulk=True, data_start=start)
info = repo.existing_info(rep2)
check("existing_info lists the overlapping days", set(info["dates_existing"]) == {d.isoformat() for d in rep2.dates}, info["dates_existing"])
uid2 = repo.add_history("NPT", "resmall.xlsx", f"{rep2.dates[0]} to {rep2.dates[-1]}", rep2.rows_read, rep2.rows_valid, 0, "Success", "Replaced", 0)
repo.save_report(rep2, uid2, "replace")
affected = {d.isoformat() for d in rep2.dates}
snap_after = {r.report_date: (r.n, round(r.s, 1)) for r in
             db.query_df("SELECT report_date, COUNT(*) n, SUM(duration_sec) s FROM npt_events GROUP BY report_date").itertuples()}
untouched_ok = all(snap_after[d] == snap_before[d] for d in snap_before if d not in affected)
replaced_ok = all(snap_after[d] != snap_before[d] for d in affected)
check("replace touches ONLY the days it introduced, nothing else", untouched_ok and replaced_ok,
     {d: (snap_before[d], snap_after[d]) for d in affected})

# ---------------------------------------------------------------- skip mode is a true no-op on exact duplicates
# (own, separate database file: must reflect the ORIGINAL upload with nothing replaced yet, or re-inserting
#  rows that a later test replaced away would legitimately not be duplicates any more)
_main_db_path = db.DB_PATH
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "test_bulk_and_mapping_skip.db")
db.reset_pool()
db.init_db()
rep_a = parse_npt(f1, "bulk5.xlsx", None, S, bulk=True, data_start=start)
repo.save_report(rep_a, repo.add_history("NPT", "bulk5.xlsx", "x", rep_a.rows_read, rep_a.rows_valid, 0, "Success", "New", 0), "new")
n_before = db.query_df("SELECT COUNT(*) c FROM npt_events").c.iloc[0]
rep_b = parse_npt(f1, "bulk5.xlsx", None, S, bulk=True, data_start=start)   # the exact same file again
written = repo.save_report(rep_b, repo.add_history("NPT", "bulk5.xlsx", "x", rep_b.rows_read, rep_b.rows_valid, 0, "Success", "Skipped duplicates", 0), "skip")
n_after = db.query_df("SELECT COUNT(*) c FROM npt_events").c.iloc[0]
check("skip mode writes 0 rows on an exact duplicate bulk re-upload", written == 0 and n_before == n_after, (written, n_before, n_after))
db.DB_PATH = _main_db_path
db.reset_pool()

# ---------------------------------------------------------------- data_start_date is enforced in bulk mode too
f3 = os.path.join(tmp, "bulk_old.xlsx")
make_bulk_file([start - dt.timedelta(days=5), start - dt.timedelta(days=1), start + dt.timedelta(days=100)], f3, n_events=5)
rep4 = parse_npt(f3, "old.xlsx", None, S, bulk=True, data_start=start)
check("rows before the database start date are rejected, not silently dropped",
     len(rep4.rejected) == 10 and len(rep4.dates) == 1, (len(rep4.rejected), rep4.dates))

# ---------------------------------------------------------------- Daily / Monthly / Yearly all see the bulk data
oct_month = npt.by_month(npt.load_npt(dt.date(2026, 10, 1), dt.date(2026, 10, 31), settings=S), year=2026)
check("bulk-uploaded data appears in Monthly totals", oct_month[oct_month.month == "2026-10"].npt_h.iloc[0] > 0)

# ================================================================== Machine Mapping
machines = sorted(set(rep1.df.machine_id))
half = machines[:len(machines) // 2]
repo.set_machine_map([dict(machine_id=m, machine_no=f"POS-{i}", production_line=["A Line", "B Line", "C Line"][i % 3])
                      for i, m in enumerate(half)])

# whole originally-uploaded range (Oct 1-6), matching where `machines`/`half` were computed from
nd = npt.load_npt(dt.date(2026, 10, 1), dt.date(2026, 10, 6), settings=S)
bm = npt.by_machine(nd)
check("mapped machines show their Production Line", (bm.production_line != "Unmapped").sum() == len(set(bm.machine_id) & set(half)),
     (sorted(set(bm.machine_id) & set(half)), len(half)))
check("unmapped machines show as 'Unmapped', not dropped", (bm.production_line == "Unmapped").any())

bl = npt.by_line(nd)
check("by_line total equals overall total (nothing lost in the join)", abs(bl.total_h.sum() - nd.npt_sec.sum() / 3600) < 1e-6)

only_a = npt.load_npt(dt.date(2026, 10, 1), dt.date(2026, 10, 6), settings=S, lines=["A Line"])
expected_a = {m for i, m in enumerate(half) if i % 3 == 0} & set(nd.machine_id)
check("Production Line filter returns exactly that line's machines", set(only_a.machine_id) == expected_a, (set(only_a.machine_id), expected_a))

# remapping an existing machine must never touch its size/first_seen (set by real NPT data, not by mapping)
before_row = db.query_df("SELECT size, first_seen FROM machines WHERE machine_id=?", (half[0],)).iloc[0]
repo.set_machine_map([dict(machine_id=half[0], machine_no="RENAMED", production_line="New Line Name")])
after_row = db.query_df("SELECT size, first_seen, machine_no FROM machines WHERE machine_id=?", (half[0],)).iloc[0]
check("remapping never touches size/first_seen (only machine_no/production_line)",
     before_row["size"] == after_row["size"] and before_row.first_seen == after_row.first_seen and after_row.machine_no == "RENAMED",
     (dict(before_row), dict(after_row)))
check("a brand-new Production Line name is accepted and remembered automatically", "New Line Name" in repo.known_production_lines())

# ---------------------------------------------------------------- Excel export carries the mapping through
from utils import excel_export as ex
import io
data = ex.report_workbook(dt.date(2026, 10, 1), dt.date(2026, 10, 1), level="daily")
xl = openpyxl.load_workbook(io.BytesIO(data))
check("export includes a 'NPT by Production Line' sheet once mapping exists", "NPT by Production Line" in xl.sheetnames, xl.sheetnames)
hdr = [c.value for c in xl["Machine Summary"][1]]
check("Machine Summary sheet includes Machine No. / Production Line columns", "Machine No." in hdr and "Production Line" in hdr)

print("\nALL PASSED" if all(ok) else "\nSOME FAILED")
sys.exit(0 if all(ok) else 1)
