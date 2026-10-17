import streamlit as st

from database import db
from kpi import npt
from kpi.common import month_label
from utils import excel_export as ex
from .common import (dataframe, date_range_input, distinct, excel_button, line_filter, machine_filter, no_data,
                     npt_cards, plot, show_table)
from .overview import bar, pareto_chart


def render():
    st.title("NPT Analysis")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    s = db.get_settings()
    c = st.columns([2, 2, 2, 2, 1])
    with c[0]:
        start, end = date_range_input(key="npt_rng")
    with c[1]:
        machines = machine_filter("npt_m")
    with c[2]:
        lines = line_filter("npt_l")
    with c[3]:
        causes = st.multiselect("NPT cause", distinct("npt_events", "cause"), key="npt_c") or None
    with c[4]:
        shift = st.selectbox("Shift", ["All", "A", "B"], help="Needs 'Shift A start hour' in Settings")
    if shift != "All" and not str(s.get("shift_a_start") or "").strip():
        st.warning("Shift filter needs 'Shift A start hour' in Settings. Showing all shifts.")
        shift = "All"
    nd = npt.load_npt(start, end, machines, causes, shift, s, lines=lines)
    days = (end - start).days + 1
    ns = npt.summary(nd, s, days)
    if nd.empty:
        st.info("No NPT data for this selection.")
        return
    npt_cards(ns, s)
    st.subheader("Pareto by cause")
    p = npt.by_cause(nd)
    plot(pareto_chart(p, "cause", "total_h", "NPT Pareto (hours)", "Hours"))
    show_table(ex.t_npt_pareto(nd), pct_cols=["% of Total NPT", "Cumulative %"], dec_cols=["Total NPT (h)", "Average Duration (min)", "Maximum Duration (min)"])
    a, b = st.columns(2)
    with a:
        st.subheader("Trend")
        d = npt.by_date(nd)
        if len(d) > 1:
            plot(bar(d["date"], d.npt_h, "NPT hours per day"))
        m = npt.by_month(nd)
        if len(m) > 1:
            plot(bar(m["month"].map(month_label), m.npt_h, "NPT hours per month"))
    with b:
        st.subheader("Machine-wise")
        mt = npt.by_machine(nd)
        plot(bar(mt.machine_id, mt.total_h, "NPT hours by machine"))
    show_table(ex.t_npt_machine(nd), pct_cols=["% of Total NPT"], dec_cols=["Total NPT (h)", "Average Duration (min)", "Maximum Duration (min)"])
    bl = npt.by_line(nd)
    if len(bl) > 1 or (len(bl) == 1 and bl.production_line.iloc[0] != "Unmapped"):
        st.subheader("By Production Line")
        plot(bar(bl.production_line, bl.total_h, "NPT hours by Production Line"))
        show_table(ex.t_npt_line(nd), pct_cols=["% of Total NPT"], dec_cols=["Total NPT (h)", "Average Duration (min)", "Maximum Duration (min)"])
    sh = npt.by_shift(nd)
    if (sh["shift"] != "All").any():
        st.subheader("Shift-wise")
        show_table(sh.rename(columns={"shift": "Shift", "npt_h": "NPT (h)", "pct": "% of Total NPT"}), pct_cols=["% of Total NPT"], dec_cols=["NPT (h)"])
    with st.expander("NPT events (split by day)"):
        dataframe(ex.t_npt_events(nd))
    st.caption("MTBF / MTTR are not shown: they need a decision on which causes count as breakdowns.")
    excel_button("Export NPT analysis to Excel", lambda: ex.report_workbook(start, end, level="range", machines=machines, causes=causes, shift=shift, lines=lines),
                 f"NPT_Analysis_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx", key="npt_x")
