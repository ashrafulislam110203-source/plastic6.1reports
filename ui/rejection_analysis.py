import streamlit as st

from database import db
from kpi import rejection
from kpi.common import month_label
from utils import excel_export as ex
from .common import (dataframe, date_range_input, distinct, excel_button, line_filter, machine_filter, no_data,
                     plot, rej_cards, show_table)
from .overview import bar, pareto_chart


def render():
    st.title("Rejection Analysis")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    c = st.columns([2, 2, 2, 2, 2])
    with c[0]:
        start, end = date_range_input(key="rej_rng")
    with c[1]:
        machines = machine_filter("rej_m")
    with c[2]:
        lines = line_filter("rej_l")
    with c[3]:
        items = st.multiselect("Product", distinct("rejection", "item_name"), key="rej_i") or None
    with c[4]:
        causes = st.multiselect("Rejection cause", distinct("rejection", "cause"), key="rej_c") or None
    rj = rejection.load_rejection(start, end, machines, items, causes, lines=lines)
    days = (end - start).days + 1
    if rj.empty:
        st.info("No rejection data for this selection.")
        return
    rej_cards(rejection.summary(rj, days))
    st.subheader("Pareto by cause")
    p = rejection.by_cause(rj).rename(columns={"pieces": "qty"})
    plot(pareto_chart(p, "cause", "qty", "Rejection Pareto (pieces)", "Pieces"))
    show_table(ex.t_rej_pareto(rj), pct_cols=["% of Total Rejection", "Cumulative %"], dec_cols=["Rejected Pieces", "Rejected Weight (kg)"])
    a, b = st.columns(2)
    with a:
        st.subheader("Trend")
        d = rejection.by_date(rj)
        if len(d) > 1:
            plot(bar(d["date"], d.pieces, "Rejected pieces per day", color="#d9534f"))
        m = rejection.by_month(rj)
        if len(m) > 1:
            plot(bar(m["month"].map(month_label), m.pieces, "Rejected pieces per month", color="#d9534f"))
    with b:
        st.subheader("Machine-wise")
        mt = rejection.by_machine(rj)
        plot(bar(mt.machine_id, mt.pieces, "Rejected pieces by machine", color="#d9534f"))
    show_table(ex.t_rej_machine(rj), pct_cols=["% of Total Rejection"], dec_cols=["Rejected Pieces", "Rejected Weight (kg)"])
    bl = rejection.by_line(rj)
    if len(bl) > 1 or (len(bl) == 1 and bl.production_line.iloc[0] != "Unmapped"):
        st.subheader("By Production Line")
        plot(bar(bl.production_line, bl.pieces, "Rejected pieces by Production Line", color="#d9534f"))
        show_table(ex.t_rej_line(rj), pct_cols=["% of Total Rejection"], dec_cols=["Rejected Pieces", "Rejected Weight (kg)"])
    st.subheader("Product-wise")
    show_table(ex.t_rej_item(rj), pct_cols=["% of Total Rejection"], dec_cols=["Rejected Pieces", "Rejected Weight (kg)"])
    with st.expander("Rejection records"):
        dataframe(ex.t_rej_records(rj))
    st.caption("Rejection % is not shown in Version 1: production quantity is not used.")
    excel_button("Export Rejection analysis to Excel", lambda: ex.report_workbook(start, end, level="range", machines=machines, items=items, rej_causes=causes, lines=lines),
                 f"Rejection_Analysis_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx", key="rej_x")
