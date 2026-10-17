import datetime as dt

import streamlit as st

from database import db, repository as repo
from utils import excel_export as ex
from .common import excel_button, no_data


def render():
    st.title("Excel Export & Backup")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    st.subheader("Daily report")
    day = st.date_input("Date", hi, min_value=lo, max_value=hi, key="ex_day", format="DD/MM/YYYY")
    excel_button("Export Daily Report to Excel", lambda: ex.report_workbook(day, day, level="daily"), f"Daily_KPI_{day:%Y-%m-%d}.xlsx", "e1")
    st.subheader("Date range report")
    c = st.columns(2)
    a = c[0].date_input("From", max(lo, hi - dt.timedelta(days=29)), min_value=lo, max_value=hi, key="ex_a", format="DD/MM/YYYY")
    b = c[1].date_input("To", hi, min_value=lo, max_value=hi, key="ex_b", format="DD/MM/YYYY")
    if a <= b:
        excel_button("Export date range to Excel", lambda: ex.report_workbook(a, b, level="range"), f"KPI_{a:%Y-%m-%d}_to_{b:%Y-%m-%d}.xlsx", "e2")
    st.caption("Monthly, yearly, machine, NPT and Rejection workbooks have their own Export button on their pages, using the filters you chose.")
    st.subheader("All historical data")
    excel_button("Export All Historical Data", lambda: ex.history_workbook(), "All_Historical_Data.xlsx", "e3")

    st.subheader("Database backup")
    st.caption("A full copy of every stored record, as a .json.gz file. Keep this somewhere safe - "
               "it is the only way back if the database is ever emptied by mistake.")
    if st.button("Prepare a backup"):
        data, name = repo.backup_db("manual")
        st.session_state["_backup"] = (data, name)
    if "_backup" in st.session_state:
        data, name = st.session_state["_backup"]
        st.download_button("Download database backup", data, file_name=name, mime="application/gzip")

    st.subheader("Restore from backup")
    st.caption("Upload a backup file created above to bring records back - for example after the free hosting "
               "reset your data, or to undo a mistake.")
    up = st.file_uploader("Backup file (.json.gz)", type=["gz", "json"], key="restore_file")
    if up is not None:
        st.warning("This REPLACES everything currently stored with the content of this backup file. This cannot be undone.")
        if st.checkbox("I understand this will replace all current data.", key="restore_confirm"):
            if st.button("Restore now", type="primary"):
                try:
                    counts = repo.restore_db(up.getvalue())
                except Exception as e:
                    st.error(f"This file could not be read as a backup: {e}")
                else:
                    st.success("Restored: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v))
                    st.session_state.pop("_backup", None)
                    st.rerun()
