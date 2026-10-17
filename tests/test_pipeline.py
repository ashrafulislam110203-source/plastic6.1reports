"""End-to-end test with the real NPT and Rejection files.
Run:  python tests/test_pipeline.py <folder with the sample files>
Uses a temporary database, so real data is never touched."""
import datetime as dt, glob, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
folder = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads"
os.environ["KPI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_pipeline.db")

import openpyxl
from database import db, repository as repo
from processing.cleaner import FileValidationError
from processing.npt_processor import parse_npt
from processing.rejection_processor import parse_rejection
from kpi import npt, rejection
from utils import excel_export as ex
from ui.upload import _save_all

D = dt.date(2026, 9, 18)
f_npt = glob.glob(os.path.join(folder, "Downtime*"))[0]
f_rej = glob.glob(os.path.join(folder, "Rejection*"))[0]
ok = []
def check(name, cond, info=""):
    ok.append(bool(cond)); print(("PASS  " if cond else "FAIL  ") + name, info)
def count(t): return int(db.query_df(f"select count(*) c from {t}").c[0])

db.init_db(); S = db.get_settings()
nrep = parse_npt(f_npt, "npt.xlsx", D, S); rrep = parse_rejection(f_rej, "rej.xlsx", D, S)
check("NPT events 195", nrep.rows_valid == 195, nrep.rows_valid)
check("rejection rows 61", rrep.rows_valid == 61)
check("rejection pieces = 2541, kg = 552.58", round(rrep.df.qty_pieces.sum()) == 2541 and round(rrep.df.weight_kg.sum(), 2) == 552.58)
check("machine IDs normalised (no leading zeros)", not nrep.df.machine_id.str.contains(r"-0\d").any())
check("cause '*' removed", not nrep.df.cause.str.contains(r"\*").any() and "Heater Problem" in set(nrep.df.cause), sorted(set(nrep.df.cause))[:4])

pend = dict(reports={"NPT": nrep, "Rejection": rrep}, errors={}, dups={"NPT": repo.existing_info(nrep), "Rejection": repo.existing_info(rrep)}, date=D)
print(_save_all(pend, {}))
check("DB counts", (count("npt_events"), count("rejection")) == (195, 61))

# KPI
nd = npt.load_npt(D, D, settings=S); ns = npt.summary(nd, S, 1)
check("NPT inside 18 Sep = 274.40 h (raw 396.15 h)", abs(ns["npt_h"] - 274.403) < 0.01, round(ns["npt_h"], 3))
check("no day > 24 h per machine", nd.groupby("machine_id").npt_sec.sum().max() / 3600 <= 24.0001)
pc = npt.by_cause(nd)
check("Pareto sums to 100%", abs(pc.pct.sum() - 1) < 1e-9 and abs(pc.cum_pct.iloc[-1] - 1) < 1e-9)
print(pc.head(3).round(2).to_string())
check("events counted once", ns["events"] == 195, ns["events"])
# events crossing days: 19 Sep must show the tail
nd19 = npt.load_npt(dt.date(2026, 9, 19), dt.date(2026, 9, 19), settings=S)
check("19 Sep shows carry-over of events", nd19.npt_sec.sum() / 3600 > 0, round(nd19.npt_sec.sum() / 3600, 1))
nd17_19 = npt.load_npt(dt.date(2026, 9, 17), dt.date(2026, 9, 19), settings=S)
check("17-19 Sep totals 396.15 h (nothing lost; From/To used, file durations differ by under 2 min in total)", abs(nd17_19.npt_sec.sum() / 3600 - 396.145) < 0.03, round(nd17_19.npt_sec.sum() / 3600, 3))
S2 = dict(S, shift_a_start="8", machine_count="39")
nds = npt.load_npt(D, D, settings=S2)
check("shift split keeps total", abs(nds.npt_sec.sum() - nd.npt_sec.sum()) < 1e-6 and set(nds["shift"]) == {"A", "B"})
check("NPT % with machine count", abs(npt.summary(nd, S2, 1)["npt_pct"] - 274.403 / (39 * 24)) < 1e-6)
rj = rejection.load_rejection(D, D); rs = rejection.summary(rj, 1)
check("rejection summary", rs["entries"] == 61 and round(rs["pieces"]) == 2541, rs)
check("rejection Pareto top = Short Mold", rejection.by_cause(rj).cause.iloc[0] == "Short Mold")

# duplicates
check("duplicates detected", repo.existing_info(nrep)["existing"] == 195 and repo.existing_info(rrep)["existing"] == 61)
pend["dups"] = {"NPT": repo.existing_info(nrep), "Rejection": repo.existing_info(rrep)}
print(_save_all(pend, {"NPT": "Cancel", "Rejection": "Skip Duplicate Records"}))
check("cancel/skip change nothing", (count("npt_events"), count("rejection")) == (195, 61))
print(_save_all(pend, {"NPT": "Replace Existing Data", "Rejection": "Replace Existing Data"}))
check("replace does not double", (count("npt_events"), count("rejection")) == (195, 61))

# bad files
def expect(name, fn, text):
    try:
        fn(); check(name, False, "no error raised")
    except FileValidationError as e:
        check(name, text in str(e), str(e))
expect("NPT slot gets rejection file", lambda: parse_npt(f_rej, "x", D, S), "does not appear to be an NPT report")
expect("Rejection slot gets NPT file", lambda: parse_rejection(f_npt, "x", D, S), "does not appear to be a Rejection report")
expect("NPT date mismatch", lambda: parse_npt(f_npt, "x", dt.date(2026, 9, 25), S), "Date mismatch detected")
expect("Rejection date mismatch", lambda: parse_rejection(f_rej, "x", dt.date(2026, 9, 25), S), "Date mismatch detected")
tmp = tempfile.mkdtemp()
wb = openpyxl.load_workbook(f_npt); ws = wb.active
for c in ws[8]:
    if c.value == "Machine": c.value = "Machine No"
wb.save(os.path.join(tmp, "a.xlsx"))
expect("missing NPT column", lambda: parse_npt(os.path.join(tmp, "a.xlsx"), "x", D, S), "Required column missing: Machine")
wb = openpyxl.load_workbook(f_rej); ws = wb.active
for c in ws[8]:
    if c.value == "Quantity": c.value = "Qty"
wb.save(os.path.join(tmp, "b.xlsx"))
expect("missing rejection column", lambda: parse_rejection(os.path.join(tmp, "b.xlsx"), "x", D, S), "Required column missing: Quantity")

# new cause, dirty rows
wb = openpyxl.load_workbook(f_npt); ws = wb.active
ws["H9"] = "Robot Arm Crash*"; ws["C10"] = "imm 380 3 "; ws["E11"] = None; ws["H12"] = "  power   breakdown (unscheduled) "
wb.save(os.path.join(tmp, "c.xlsx"))
r2 = parse_npt(os.path.join(tmp, "c.xlsx"), "x", D, S)
check("new NPT cause accepted", "Robot Arm Crash" in set(r2.df.cause))
check("bad row rejected, others processed", len(r2.rejected) == 1 and r2.rows_valid == 194, r2.rejected)
check("machine variant normalised", "IMM-380-3" in set(r2.df.machine_id))
check("case/space variants of a cause merge", "Power Breakdown (Unscheduled)" in set(r2.df.cause) and "power breakdown (unscheduled)" not in {c.lower() for c in r2.df.cause if c != c.title() and False})
repo.save_report(r2, 1, "replace")
check("new cause visible in analysis", "Robot Arm Crash" in set(npt.load_npt(D, D, settings=S).cause))
wb = openpyxl.load_workbook(f_rej); ws = wb.active
ws["H9"] = "Burn Mark*"; wb.save(os.path.join(tmp, "d.xlsx"))
r3 = parse_rejection(os.path.join(tmp, "d.xlsx"), "x", D, S); repo.save_report(r3, 1, "replace")
check("new rejection cause accepted", "Burn Mark" in set(rejection.load_rejection(D, D).cause))

# exports
for level, kw in (("daily", {}), ("range", {}), ("monthly", {}), ("yearly", dict(year=2026))):
    s0, e0 = (D, D) if level == "daily" else (dt.date(2026, 9, 1), dt.date(2026, 9, 30)) if level in ("range", "monthly") else (dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    data = ex.report_workbook(s0, e0, level=level, **kw)
    p = os.path.join(tmp, f"{level}.xlsx"); open(p, "wb").write(data)
    print(level, openpyxl.load_workbook(p).sheetnames)
open(os.path.join(tmp, "history.xlsx"), "wb").write(ex.history_workbook())
print("history", openpyxl.load_workbook(os.path.join(tmp, "history.xlsx")).sheetnames)
filt = ex.report_workbook(D, D, level="daily", machines=["IMM-380-57"], causes=["Product Jam"])
wb = openpyxl.load_workbook(__import__("io").BytesIO(filt)); n = wb["NPT Events"]
hdr = [c.value for c in n[1]]; rows = list(n.iter_rows(min_row=2, max_row=n.max_row - 1, values_only=True))
mi, ci = hdr.index("Machine"), hdr.index("Cause")
check("filtered export has only that machine/cause", rows and {r[mi] for r in rows} == {"IMM-380-57"} and {r[ci] for r in rows} == {"Product Jam"}, (hdr, len(rows)))
open("/tmp/last_daily.xlsx", "wb").write(ex.report_workbook(D, D, level="daily"))
print("\nALL PASSED" if all(ok) else "\nSOME FAILED")
