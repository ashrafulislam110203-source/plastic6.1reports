import pandas as pd
import streamlit as st

from database import db
from kpi import npt, rejection
from .common import dataframe, date_range_input, no_data, table_button


def render():
    st.title("Data Quality")
    st.caption("Suspicious data is shown here. Nothing is hidden or corrected.")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    start, end = date_range_input(default_days=7, key="dq_rng")
    s = db.get_settings()
    st.subheader("Checks on stored data")
    found = False
    ov = npt.overlaps(start, end)
    if not ov.empty:
        found = True
        st.warning(f"{len(ov)} NPT event(s) overlap another event on the same machine.")
        dataframe(ov.rename(columns={"machine_id": "Machine", "cause": "Cause", "start_time": "Start", "end_time": "End", "prev_end": "Previous event ends"}))
    long_h = float(s.get("long_npt_hours") or 24)
    ev = db.query_df("SELECT machine_id, cause, start_time, end_time, duration_sec FROM npt_events WHERE end_time>=? AND start_time<=? AND duration_sec>?",
                     (start.isoformat(), end.isoformat() + " 23:59:59", long_h * 3600))
    if not ev.empty:
        found = True
        ev["Duration (h)"] = ev.duration_sec / 3600
        st.warning(f"{len(ev)} NPT event(s) are longer than {long_h:g} h.")
        dataframe(ev.drop(columns=["duration_sec"]).rename(columns={"machine_id": "Machine", "cause": "Cause", "start_time": "Start", "end_time": "End"}))
    rj = rejection.load_rejection(start, end)
    if not rj.empty:
        z = rj[(rj.weight_kg.fillna(0) == 0)]
        if len(z):
            found = True
            st.warning(f"{len(z)} rejection record(s) have zero or blank weight.")
            dataframe(z[["report_date", "machine_id", "item_name", "cause", "qty_pieces", "weight_kg"]])
        both = set(npt.load_npt(start, end, settings=s).machine_id) ^ set(rj.machine_id)
        st.caption(f"Machines seen in only one of the two reports in this period: {len(both)} (normal when a machine has only NPT or only rejection).")
    if not found:
        st.success("No problems found in this period.")
    st.subheader("Warnings saved at upload time")
    up = db.query_df("SELECT w.warn_date AS Date, w.machine_id AS Machine, w.kind AS Type, w.message AS Details, "
                     "h.report_type AS Report, h.file_name AS File FROM data_warnings w LEFT JOIN upload_history h ON h.id=w.upload_id "
                     "ORDER BY w.id DESC LIMIT 500")
    dataframe(up)
    if not up.empty:
        table_button("Export warnings to Excel", {"Data Warnings": up}, "Data_Warnings.xlsx", "dq_x")
