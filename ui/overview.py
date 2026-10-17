"""One reusable view: NPT + Rejection cards, trends, Pareto charts and machine tables."""
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from database import db
from kpi import npt, rejection
from kpi.common import month_label
from utils import excel_export as ex
from .common import npt_cards, plot, rej_cards, show_table


def pareto_chart(p, label, value, title, unit):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_bar(x=p[label], y=p[value], name=unit, secondary_y=False)
    fig.add_scatter(x=p[label], y=p.cum_pct * 100, name="Cumulative %", mode="lines+markers", secondary_y=True)
    fig.update_yaxes(title_text=unit, secondary_y=False)
    fig.update_yaxes(title_text="Cumulative %", range=[0, 105], secondary_y=True)
    fig.update_layout(title=title, height=400, margin=dict(t=50, b=10), legend=dict(orientation="h"))
    return fig


def bar(x, y, title, goal=None, color=None):
    fig = go.Figure(go.Bar(x=x, y=y, marker_color=color))
    if goal:
        fig.add_hline(y=goal, line_dash="dash", annotation_text="goal")
    fig.update_layout(title=title, height=330, margin=dict(t=50, b=10))
    return fig


def _goal(s, key):
    try:
        return float(s.get(key) or 0) or None
    except ValueError:
        return None


def trend_charts(nd, rj, s, monthly=False, year=None):
    n = npt.by_month(nd, year) if monthly else npt.by_date(nd)
    r = rejection.by_month(rj, year) if monthly else rejection.by_date(rj)
    a, b = st.columns(2)
    with a:
        if not n.empty:
            x = n["month"].map(month_label) if monthly else n["date"]
            plot(bar(x, n.npt_h, "NPT hours per " + ("month" if monthly else "day"),
                     None if monthly else _goal(s, "npt_goal_h_per_day")))
        else:
            st.info("No NPT data in this selection.")
    with b:
        if not r.empty:
            x = r["month"].map(month_label) if monthly else r["date"]
            plot(bar(x, r.pieces, "Rejected pieces per " + ("month" if monthly else "day"),
                     None if monthly else _goal(s, "rejection_goal_pieces_per_day"), "#d9534f"))
        else:
            st.info("No rejection data in this selection.")


def pareto_row(nd, rj):
    a, b = st.columns(2)
    with a:
        c = npt.by_cause(nd)
        if not c.empty:
            plot(pareto_chart(c, "cause", "total_h", "NPT Pareto (hours)", "Hours"))
    with b:
        p = rejection.by_cause(rj).rename(columns={"pieces": "qty"})
        if not p.empty:
            plot(pareto_chart(p, "cause", "qty", "Rejection Pareto (pieces)", "Pieces"))


def machine_tables(nd, rj):
    a, b = st.columns(2)
    with a:
        st.subheader("NPT by machine")
        t = ex.t_npt_machine(nd)
        if not t.empty:
            show_table(t, pct_cols=["% of Total NPT"], dec_cols=["Total NPT (h)", "Average Duration (min)", "Maximum Duration (min)"])
    with b:
        st.subheader("Rejection by machine")
        t = ex.t_rej_machine(rj)
        if not t.empty:
            show_table(t, pct_cols=["% of Total Rejection"], dec_cols=["Rejected Pieces", "Rejected Weight (kg)"])


def render_overview(start, end, machines=None, monthly=False, year=None, settings=None, lines=None):
    """Draws the whole overview and returns (nd, rj) for export buttons."""
    s = settings or db.get_settings()
    nd = npt.load_npt(start, end, machines, settings=s, lines=lines)
    rj = rejection.load_rejection(start, end, machines, lines=lines)
    days = (end - start).days + 1
    ns, rs = npt.summary(nd, s, days), rejection.summary(rj, days)
    if nd.empty and rj.empty:
        st.info("No data stored for this selection.")
        return nd, rj, ns, rs
    if nd.empty:
        st.warning("No NPT data stored for this selection.")
    if rj.empty:
        st.warning("No Rejection data stored for this selection.")
    st.subheader("NPT")
    npt_cards(ns, s)
    st.subheader("Rejection")
    rej_cards(rs)
    st.subheader("Trends")
    if start != end or monthly:
        trend_charts(nd, rj, s, monthly, year)
    st.subheader("Pareto")
    pareto_row(nd, rj)
    machine_tables(nd, rj)
    return nd, rj, ns, rs
