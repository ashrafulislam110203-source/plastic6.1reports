import calendar

import pandas as pd

from database import db

TS = "%Y-%m-%d %H:%M:%S"


def _machine_map():
    """machine_id -> (machine_no, production_line). Cached the same 30s as every other read."""
    m = db.query_df("SELECT machine_id, machine_no, production_line FROM machines")
    return m.set_index("machine_id") if not m.empty else m


def with_machine_map(df):
    """Adds Machine No. / Production Line columns to any DataFrame with a machine_id column
    (used by both NPT and Rejection). A machine with no mapping yet shows as 'Unmapped' rather
    than being dropped or left blank."""
    if df.empty:
        df = df.copy()
        df["machine_no"] = pd.Series(dtype=object)
        df["production_line"] = pd.Series(dtype=object)
        return df
    m = _machine_map()
    df = df.copy()
    if m.empty:
        df["machine_no"] = None
        df["production_line"] = None
    else:
        df["machine_no"] = df.machine_id.map(m.machine_no)
        df["production_line"] = df.machine_id.map(m.production_line)
    df["production_line"] = df["production_line"].fillna("Unmapped")
    return df


def iso(d):
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def pareto(df, label, value):
    if df is None or df.empty:
        return pd.DataFrame(columns=[label, value, "pct", "cum_pct"])
    g = df.groupby(label)[value].sum().sort_values(ascending=False).reset_index()
    tot = g[value].sum()
    g["pct"] = g[value] / tot if tot else 0.0
    g["cum_pct"] = g.pct.cumsum()
    return g


def month_label(ym):
    y, m = ym.split("-")
    return f"{calendar.month_abbr[int(m)]} {y}"


def add_month(df, date_col="date"):
    df = df.copy()
    df["month"] = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m")
    return df


def all_months(year):
    return [f"{year}-{m:02d}" for m in range(1, 13)]
