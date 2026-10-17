"""Rejection calculations from the Rejection report (Quantity = thousand pieces, Weight = tons,
converted to pieces and kg when the file is read). There is no production quantity in Version 1,
so Rejection % cannot be calculated - absolute pieces and kg are shown."""
import numpy as np
import pandas as pd

from database import db
from .common import add_month, all_months, iso, with_machine_map

COLS = ["report_date", "machine_id", "item_name", "cause", "qty_pieces", "weight_kg"]


def load_rejection(start, end, machines=None, items=None, causes=None, lines=None):
    df = db.query_df("SELECT * FROM rejection WHERE report_date>=? AND report_date<=?", (iso(start), iso(end)))
    if df.empty:
        return df
    df["report_date"] = pd.to_datetime(df["report_date"]).dt.date
    if machines:
        df = df[df.machine_id.isin(machines)]
    if items:
        df = df[df.item_name.isin(items)]
    if causes:
        df = df[df.cause.isin(causes)]
    if lines:
        mapped = with_machine_map(df[["machine_id"]].drop_duplicates())
        keep = set(mapped[mapped.production_line.isin(lines)].machine_id)
        df = df[df.machine_id.isin(keep)]
    return df


def summary(rj, n_days):
    if rj.empty:
        return dict(pieces=0.0, kg=0.0, entries=0, machines=0, items=0, causes=0, per_day=0.0, top_cause=None)
    top = rj.groupby("cause").qty_pieces.sum().idxmax()
    return dict(pieces=rj.qty_pieces.sum(), kg=rj.weight_kg.sum(), entries=len(rj), machines=rj.machine_id.nunique(),
                items=rj.item_name.nunique(), causes=rj.cause.nunique(), per_day=rj.qty_pieces.sum() / max(n_days, 1), top_cause=top)


def _group(rj, key, extra=None):
    if rj.empty:
        return pd.DataFrame(columns=[key, "entries", "pieces", "kg", "pct", "cum_pct"])
    g = rj.groupby(key).agg(entries=("qty_pieces", "size"), pieces=("qty_pieces", "sum"), kg=("weight_kg", "sum")).reset_index()
    g = g.sort_values("pieces", ascending=False).reset_index(drop=True)
    g["pct"] = g.pieces / g.pieces.sum() if g.pieces.sum() else 0.0
    g["cum_pct"] = g.pct.cumsum()
    return g


def by_cause(rj):
    return _group(rj, "cause")


def by_machine(rj):
    g = _group(rj, "machine_id")
    if not g.empty:
        top = rj.groupby(["machine_id", "cause"]).qty_pieces.sum().reset_index().sort_values("qty_pieces", ascending=False).drop_duplicates("machine_id")
        g = g.merge(top[["machine_id", "cause"]].rename(columns={"cause": "top_cause"}), on="machine_id")
        g = with_machine_map(g)
    return g


def by_line(rj):
    """Rejected pieces grouped by Production Line (Machine Mapping). Unmapped machines appear as 'Unmapped'."""
    cols = ["production_line", "machines", "entries", "pieces", "kg", "pct"]
    if rj.empty:
        return pd.DataFrame(columns=cols)
    rj2 = with_machine_map(rj)
    g = rj2.groupby("production_line").agg(machines=("machine_id", "nunique"), entries=("qty_pieces", "size"),
                                            pieces=("qty_pieces", "sum"), kg=("weight_kg", "sum")).reset_index()
    g = g.sort_values("pieces", ascending=False).reset_index(drop=True)
    g["pct"] = g.pieces / g.pieces.sum() if g.pieces.sum() else 0.0
    return g[cols]


def by_item(rj):
    return _group(rj, "item_name")


def by_date(rj):
    if rj.empty:
        return pd.DataFrame(columns=["date", "entries", "machines", "pieces", "kg"])
    g = rj.groupby("report_date").agg(entries=("qty_pieces", "size"), machines=("machine_id", "nunique"),
                                      pieces=("qty_pieces", "sum"), kg=("weight_kg", "sum")).reset_index()
    return g.rename(columns={"report_date": "date"})


def by_month(rj, year=None):
    if rj.empty:
        g = pd.DataFrame(columns=["month", "entries", "machines", "pieces", "kg"])
    else:
        d = add_month(rj, "report_date")
        g = d.groupby("month").agg(entries=("qty_pieces", "size"), machines=("machine_id", "nunique"),
                                   pieces=("qty_pieces", "sum"), kg=("weight_kg", "sum")).reset_index()
    if year:
        g = pd.DataFrame({"month": all_months(year)}).merge(g, how="left", on="month").fillna(
            {"entries": 0, "machines": 0, "pieces": 0.0, "kg": 0.0})
    return g
