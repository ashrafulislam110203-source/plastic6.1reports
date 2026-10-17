import pandas as pd
import streamlit as st

from database import db
from kpi import npt, rejection
from utils import excel_export as ex
from .common import (dataframe, date_range_input, distinct, line_filter, machine_filter, no_data, table_button)


def render():
    st.title("Data History")
    st.caption("The stored records. Old data is never deleted automatically.")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    kind = st.radio("Report type", ["NPT events", "Rejection records"], horizontal=True)
    c = st.columns([2, 2, 2, 2])
    with c[0]:
        start, end = date_range_input(key="h_rng")
    with c[1]:
        machines = machine_filter("h_m")
    with c[2]:
        lines = line_filter("h_l")
    if kind == "NPT events":
        with c[3]:
            causes = st.multiselect("NPT cause", distinct("npt_events", "cause"), key="h_c") or None
        nd = npt.load_npt(start, end, machines, causes, lines=lines)
        t = ex.t_npt_events(nd)
        st.write(f"{len(t):,} row(s) (events are split by calendar day)")
        dataframe(t)
        if not t.empty:
            table_button("Export filtered data to Excel", {"NPT Events": t}, f"NPT_Data_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx", "h_x1")
    else:
        with c[3]:
            causes = st.multiselect("Rejection cause", distinct("rejection", "cause"), key="h_c2") or None
        rj = rejection.load_rejection(start, end, machines, None, causes, lines=lines)
        t = ex.t_rej_records(rj)
        st.write(f"{len(t):,} row(s)")
        dataframe(t)
        if not t.empty:
            table_button("Export filtered data to Excel", {"Rejection": t}, f"Rejection_Data_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx", "h_x2")
