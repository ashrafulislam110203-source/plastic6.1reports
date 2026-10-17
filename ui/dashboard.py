import streamlit as st

from database import db
from utils import excel_export as ex
from .common import date_range_input, excel_button, line_filter, machine_filter, no_data
from .overview import render_overview


def render():
    st.title("Dashboard")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        start, end = date_range_input()
    with c2:
        machines = machine_filter()
    with c3:
        lines = line_filter()
    render_overview(start, end, machines, lines=lines)
    excel_button("Export this period to Excel", lambda: ex.report_workbook(start, end, level="range", machines=machines, lines=lines),
                 f"KPI_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx", key="dash_x")
