import calendar
import datetime as dt

import streamlit as st

from database import db
from utils import excel_export as ex
from .common import excel_button, no_data
from .overview import render_overview


def render():
    st.title("Monthly Analysis")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    c = st.columns(2)
    year = c[0].selectbox("Year", list(range(hi.year, lo.year - 1, -1)))
    month = c[1].selectbox("Month", list(range(1, 13)), index=hi.month - 1 if year == hi.year else 0,
                           format_func=lambda m: calendar.month_name[m])
    start = dt.date(year, month, 1)
    end = dt.date(year, month, calendar.monthrange(year, month)[1])
    render_overview(start, end)
    excel_button("Export Monthly Report to Excel", lambda: ex.report_workbook(start, end, level="monthly"), f"Monthly_KPI_{year}-{month:02d}.xlsx", key="mon_x")
