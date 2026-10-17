import datetime as dt

import streamlit as st

from database import db
from kpi import npt, rejection
from kpi.common import month_label
from utils import excel_export as ex
from .common import excel_button, no_data, show_table
from .overview import render_overview


def render():
    st.title("Yearly Analysis")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    year = st.selectbox("Year", list(range(hi.year, lo.year - 1, -1)))
    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    nd, rj, ns, rs = render_overview(start, end, monthly=True, year=year)
    if not nd.empty or not rj.empty:
        st.subheader("Month by month")
        t = ex.t_npt_trend(nd, True, year).merge(ex.t_rej_trend(rj, True, year), on="Month", suffixes=(" (NPT)", " (Rejection)"))
        show_table(t.drop(columns=[c for c in t.columns if c.startswith("Machines Affected")]),
                   dec_cols=["NPT (h)", "Rejected Pieces", "Rejected Weight (kg)"])
    excel_button("Export Yearly Report to Excel", lambda: ex.report_workbook(start, end, level="yearly", year=year), f"Yearly_KPI_{year}.xlsx", key="yr_x")
