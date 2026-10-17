import streamlit as st

from database import db, repository as repo
from kpi import npt, rejection
from utils import excel_export as ex
from .common import (dataframe, date_range_input, excel_button, line_filter, machine_options, no_data, npt_cards,
                     plot, rej_cards, show_table)
from .overview import bar, pareto_chart


def render():
    st.title("Machine Analysis")
    lo, hi = db.date_bounds()
    if lo is None:
        no_data()
        return
    s = db.get_settings()
    c1, c2 = st.columns([2, 2])
    with c1:
        start, end = date_range_input(key="mc_rng")
    with c2:
        lines = line_filter("mc_l")
    days = (end - start).days + 1
    nd_all = npt.load_npt(start, end, settings=s, lines=lines)
    rj_all = rejection.load_rejection(start, end, lines=lines)
    st.subheader("All machines")
    t = ex.t_machine_summary(nd_all, rj_all)
    if t.empty:
        st.info("No data in this period.")
        return
    show_table(t, dec_cols=["NPT (h)", "Rejected Pieces", "Rejected Weight (kg)"], int_cols=["NPT Events", "Rejection Entries"])
    st.subheader("One machine")
    opts = machine_options()
    m = st.selectbox("Machine (SM)", opts)
    mp = repo.get_machine_map()
    row = mp[mp.machine_id == m]
    if not row.empty:
        mno, line = row.machine_no.iloc[0], row.production_line.iloc[0]
        if mno or line:
            st.caption(f"Machine No.: **{mno or '-'}**  |  Production Line: **{line or 'Unmapped'}**")
        else:
            st.caption("Not yet mapped - add it on the **Machine Mapping** page.")
    nd = nd_all[nd_all.machine_id == m]
    rj = rj_all[rj_all.machine_id == m] if not rj_all.empty else rj_all
    npt_cards(npt.summary(nd, s, days), s)
    rej_cards(rejection.summary(rj, days))
    a, b = st.columns(2)
    with a:
        c = npt.by_cause(nd)
        if not c.empty:
            plot(pareto_chart(c, "cause", "total_h", f"{m}: NPT causes (hours)", "Hours"))
        d = npt.by_date(nd)
        if len(d) > 1:
            plot(bar(d["date"], d.npt_h, f"{m}: NPT hours per day"))
    with b:
        p = rejection.by_cause(rj).rename(columns={"pieces": "qty"})
        if not p.empty:
            plot(pareto_chart(p, "cause", "qty", f"{m}: rejection causes (pieces)", "Pieces"))
        d = rejection.by_date(rj)
        if len(d) > 1:
            plot(bar(d["date"], d.pieces, f"{m}: rejected pieces per day", color="#d9534f"))
    st.subheader("Product history (rejection)")
    show_table(ex.t_rej_item(rj), pct_cols=["% of Total Rejection"], dec_cols=["Rejected Pieces", "Rejected Weight (kg)"])
    with st.expander("NPT history"):
        dataframe(ex.t_npt_events(nd))
    excel_button(f"Export {m} to Excel", lambda: ex.report_workbook(start, end, level="range", machines=[m]),
                 f"Machine_{m}_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx", key="mc_x")
