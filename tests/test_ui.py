"""Opens every page with Streamlit's test runner against a temporary database (loads the real files first)."""
import datetime as dt, glob, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
folder = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads"
os.environ["KPI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_ui.db")

from database import db, repository as repo
from processing.npt_processor import parse_npt
from processing.rejection_processor import parse_rejection
from streamlit.testing.v1 import AppTest
db.init_db(); S = db.get_settings(); D = dt.date(2026, 9, 18)
def load():
    for r in (parse_npt(glob.glob(folder + "/Downtime*")[0], "n", D, S), parse_rejection(glob.glob(folder + "/Rejection*")[0], "r", D, S)):
        repo.save_report(r, repo.add_history(r.report_type, "f", D.isoformat(), 1, 1, 0, "Success", "None", 0), "new")
pages = ["Dashboard", "Upload Reports", "Daily Analysis", "Monthly Analysis", "Yearly Analysis", "Machine Analysis",
         "Machine Mapping", "NPT Analysis", "Rejection Analysis", "Data History", "Upload History", "Data Quality",
         "Excel Export & Backup", "Settings"]
def mapping():
    ids = sorted(db.query_df("SELECT machine_id FROM machines").machine_id)
    if ids:
        repo.set_machine_map([dict(machine_id=m, machine_no=f"P{i}", production_line=["A Line", "B Line"][i % 2])
                              for i, m in enumerate(ids[:len(ids)//2])])
bad = False
for label, prep in (("EMPTY DATABASE", lambda: None), ("WITH DATA", load),
                    ("WITH DATA + SHIFTS + MACHINE COUNT", lambda: (db.set_setting("shift_a_start", "8"), db.set_setting("machine_count", "39"))),
                    ("WITH PARTIAL MACHINE MAPPING", mapping)):
    prep(); print("---", label)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120).run()
    for p in pages:
        at.sidebar.radio[0].set_value(p).run()
        n = len(at.exception); bad |= bool(n)
        print(f"  {p:24s} exceptions={n}", [e.value for e in at.exception][:2])
print("UI OK" if not bad else "UI FAILED")
