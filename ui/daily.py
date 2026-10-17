import streamlit as st

from database import db
from utils import excel_export as ex
from .common import excel_button, no_data, show_table, dataframe
from .overview import render_overview
from kpi import npt


def render():
    st.title("Daily Analysis")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    day = st.date_input("Select date", hi, min_value=lo, max_value=hi, format="DD/MM/YYYY")
    nd, rj, ns, rs = render_overview(day, day)
    if not nd.empty:
        s = db.get_settings()
        sh = npt.by_shift(nd)
        if not sh.empty and (sh["shift"] != "All").any():
            st.subheader("Shift-wise NPT")
            show_table(sh.rename(columns={"shift": "Shift", "npt_h": "NPT (h)", "pct": "% of Total NPT"}), pct_cols=["% of Total NPT"], dec_cols=["NPT (h)"])
        else:
            st.caption("Shift-wise NPT: set 'Shift A start hour' in Settings to split NPT by shift. The Rejection report has no shift column.")
        st.subheader("NPT events of this day")
        dataframe(ex.t_npt_events(nd))
    if not rj.empty:
        st.subheader("Rejection records of this day")
        dataframe(ex.t_rej_records(rj))
    excel_button("Export Daily Report to Excel", lambda: ex.report_workbook(day, day, level="daily"), f"Daily_KPI_{day:%Y-%m-%d}.xlsx", key="daily_x")
