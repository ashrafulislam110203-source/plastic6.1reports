"""VERSION 2 (NOT ACTIVE in Version 1). Production / OEE calculations kept for later.
Needs the production table (see schema.py) and a machine-mapping decision first."""
"""KPI calculations. Everything is calculated from the stored records, so a corrected
re-upload changes the KPIs automatically.

Formulas (see README):
  Good = A-Good + B-Good              Bad = A-Bad + B-Bad         Total = Good + Bad
  Ideal time (h) of a product row = Total x CycleTime / Cavity / 3600
  NPT (h) of a machine-day        = event time inside that calendar day (events split at midnight)
  Run time (h)   = max(Planned - NPT, 0)          Planned = 22 h (Settings)
  Availability   = Run time / Planned
  Performance    = Ideal time / Run time          (cavity is inside Ideal time; NOT capped)
  Quality        = Good / Total
  OEE            = Availability x Performance x Quality
  Utilisation    = Run time / Scheduled time (24 h)
Plant level uses sums, not averages of percentages.
"""
import numpy as np
import pandas as pd

from database import db

NPT_COLS = ["date", "machine_id", "cause", "npt_sec", "event_start", "event_end"]


def _iso(d):
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _f(settings, key, default):
    try:
        return float(settings.get(key) or default)
    except ValueError:
        return float(default)


# ---------------------------------------------------------------- loading
def load_production(start, end, machines=None, settings=None):
    settings = settings or db.get_settings()
    planned = _f(settings, "planned_hours", 22)
    df = db.query_df("SELECT * FROM production WHERE prod_date>=? AND prod_date<=? ORDER BY prod_date, machine_id",
                     (_iso(start), _iso(end)))
    if df.empty:
        return df
    df["prod_date"] = pd.to_datetime(df["prod_date"]).dt.date
    if machines:
        df = df[df.machine_id.isin(machines)].copy()
    for c in ["a_good", "a_bad", "b_good", "b_bad"]:
        df[c] = df[c].fillna(0.0)
    df["good"] = df.a_good + df.b_good
    df["bad"] = df.a_bad + df.b_bad
    df["total"] = df.good + df.bad
    ok = (df.cycle_time_sec > 0) & (df.cavity > 0)
    df["ideal_h"] = np.where(ok, df.total * df.cycle_time_sec / df.cavity / 3600.0, 0.0)
    df["pcs_no_ct"] = np.where(ok, 0.0, df.total)
    df["good_kg"] = df.good * df.unit_weight_kg
    df["row_over_capacity"] = df.ideal_h > planned
    return df


def npt_daily(start, end, machines=None):
    """NPT events cut at midnight -> one row per (day, event)."""
    ws = pd.Timestamp(start)
    we = pd.Timestamp(end) + pd.Timedelta(days=1)
    ev = db.query_df("SELECT * FROM npt_events WHERE end_time>? AND start_time<?",
                     (ws.strftime("%Y-%m-%d %H:%M:%S"), we.strftime("%Y-%m-%d %H:%M:%S")))
    if ev.empty:
        return pd.DataFrame(columns=NPT_COLS)
    if machines:
        ev = ev[ev.machine_id.isin(machines)]
    ev["s"] = pd.to_datetime(ev.start_time)
    ev["e"] = pd.to_datetime(ev.end_time)
    out = []
    for r in ev.itertuples():
        d = max(r.s.normalize(), ws)
        stop = min(r.e, we)
        while d < stop:
            nxt = d + pd.Timedelta(days=1)
            sec = (min(r.e, nxt) - max(r.s, d)).total_seconds()
            if sec > 0:
                out.append((d.date(), r.machine_id, r.cause, sec, r.s, r.e))
            d = nxt
    return pd.DataFrame(out, columns=NPT_COLS)


def load_rejection(start, end, machines=None):
    df = db.query_df("SELECT * FROM rejection WHERE report_date>=? AND report_date<=?", (_iso(start), _iso(end)))
    if df.empty:
        return df
    df["report_date"] = pd.to_datetime(df["report_date"]).dt.date
    if machines:
        df = df[df.machine_id.isin(machines)]
    return df


# ---------------------------------------------------------------- machine-day KPI
MD_COLS = ["date", "machine_id", "good", "bad", "total", "ideal_h", "npt_h", "planned_h", "run_h",
           "availability", "performance", "quality", "oee", "utilisation", "rows", "rows_over", "pcs_no_ct", "flag_text"]


def machine_day(start, end, machines=None, settings=None, prod=None, npt=None):
    settings = settings or db.get_settings()
    planned = _f(settings, "planned_hours", 22)
    sched = _f(settings, "scheduled_hours", 24)
    if prod is None:
        prod = load_production(start, end, machines, settings)
    if npt is None:
        npt = npt_daily(start, end, machines)
    if prod.empty and npt.empty:
        return pd.DataFrame(columns=MD_COLS)
    if not prod.empty:
        p = prod.groupby(["prod_date", "machine_id"]).agg(
            good=("good", "sum"), bad=("bad", "sum"), total=("total", "sum"), ideal_h=("ideal_h", "sum"),
            pcs_no_ct=("pcs_no_ct", "sum"), rows=("id", "count"), rows_over=("row_over_capacity", "sum")).reset_index()
        p = p.rename(columns={"prod_date": "date"})
    else:
        p = pd.DataFrame(columns=["date", "machine_id", "good", "bad", "total", "ideal_h", "pcs_no_ct", "rows", "rows_over"])
    if not npt.empty:
        n = npt.groupby(["date", "machine_id"]).npt_sec.sum().div(3600).rename("npt_h").reset_index()
    else:
        n = pd.DataFrame(columns=["date", "machine_id", "npt_h"])
    m = p.merge(n, how="outer", on=["date", "machine_id"])
    for c in ["good", "bad", "total", "ideal_h", "pcs_no_ct", "rows", "rows_over", "npt_h"]:
        m[c] = m[c].fillna(0.0).astype(float)
    m["planned_h"] = planned
    m["run_h"] = (planned - m.npt_h).clip(lower=0)
    m["availability"] = m.run_h / planned
    m["performance"] = np.where((m.run_h > 0) & (m.ideal_h > 0), m.ideal_h / m.run_h.replace(0, np.nan), np.nan)
    m["quality"] = np.where(m.total > 0, m.good / m.total.replace(0, np.nan), np.nan)
    m["oee"] = m.availability * m.performance * m.quality
    m["utilisation"] = m.run_h / sched
    m = m.sort_values(["date", "machine_id"]).reset_index(drop=True)
    m["flag_text"] = quality_flags(m, settings, as_column=True)
    return m[MD_COLS]


def quality_flags(m, settings=None, as_column=False):
    """Data-quality warnings for machine-days. Nothing is hidden or corrected."""
    settings = settings or db.get_settings()
    warn = _f(settings, "perf_warn_pct", 100) / 100.0
    planned = _f(settings, "planned_hours", 22)
    rows = []
    for r in m.itertuples():
        f = []
        dstr = f"{r.date:%d-%b-%Y}"
        if r.total > 0 and r.run_h <= 0:
            f.append(("Production with no run time",
                      f"Machine {r.machine_id} on {dstr}: {r.total:,.0f} pieces recorded but NPT ({r.npt_h:.1f} h) "
                      f"covers the whole planned time ({planned:g} h)."))
        elif r.performance == r.performance and r.performance > warn:
            f.append(("Performance above limit",
                      f"Abnormal Performance {r.performance:.0%} for Machine {r.machine_id} on {dstr}: pieces need "
                      f"{r.ideal_h:.1f} h at standard cycle time / cavity, but run time is {r.run_h:.1f} h "
                      f"(NPT {r.npt_h:.1f} h)."))
        if r.ideal_h > planned:
            f.append(("Production above machine capacity",
                      f"Machine {r.machine_id} on {dstr}: pieces need {r.ideal_h:.1f} h, more than the whole planned "
                      f"time of {planned:g} h, even with zero NPT. Check machine mapping or cycle time / cavity."))
        if r.rows_over > 0:
            f.append(("Product row above one machine's capacity",
                      f"Machine {r.machine_id} on {dstr}: {int(r.rows_over)} product row(s) alone need more than "
                      f"{planned:g} h (row may contain several machines)."))
        if r.pcs_no_ct > 0:
            f.append(("Missing cycle time / cavity",
                      f"Machine {r.machine_id} on {dstr}: {r.pcs_no_ct:,.0f} pieces have no cycle time or cavity; "
                      "they are left out of Performance."))
        rows.append(f)
    if as_column:
        return ["; ".join(k for k, _ in f) for f in rows]
    out = [dict(date=r.date, machine_id=r.machine_id, kind=k, message=msg)
           for r, f in zip(m.itertuples(), rows) for k, msg in f]
    return pd.DataFrame(out, columns=["date", "machine_id", "kind", "message"])


# ---------------------------------------------------------------- plant level
KPI_COLS = ["good", "bad", "total", "rejection_pct", "npt_h", "npt_pct", "availability", "performance",
            "quality", "oee", "utilisation", "machines", "flagged_machine_days"]


def plant_kpi(m):
    """One dict for the machine-day rows given (weighted by hours and pieces)."""
    if m.empty:
        return {k: np.nan for k in KPI_COLS}
    prod = m[m.total > 0]
    planned, run = m.planned_h.sum(), m.run_h.sum()
    run_prod = prod[prod.run_h > 0]
    perf = run_prod.ideal_h.sum() / run_prod.run_h.sum() if run_prod.run_h.sum() > 0 and run_prod.ideal_h.sum() > 0 else np.nan
    tot = m.total.sum()
    qual = m.good.sum() / tot if tot > 0 else np.nan
    avail = run / planned if planned else np.nan
    return dict(good=m.good.sum(), bad=m.bad.sum(), total=tot, rejection_pct=(m.bad.sum() / tot if tot > 0 else np.nan),
                npt_h=m.npt_h.sum(), npt_pct=m.npt_h.sum() / planned if planned else np.nan,
                availability=avail, performance=perf, quality=qual, oee=avail * perf * qual,
                utilisation=m.utilisation.mean(), machines=m.machine_id.nunique(),
                flagged_machine_days=int((m["flag_text"] != "").sum()))


def daily_kpi(m):
    rows = []
    for d, g in m.groupby("date"):
        k = plant_kpi(g)
        k["date"] = d
        rows.append(k)
    return pd.DataFrame(rows, columns=["date"] + KPI_COLS)


def machine_kpi(m):
    rows = []
    for mid, g in m.groupby("machine_id"):
        k = plant_kpi(g)
        k["machine_id"] = mid
        k["days"] = g.date.nunique()
        rows.append(k)
    cols = ["machine_id", "days"] + [c for c in KPI_COLS if c not in ("machines",)]
    return pd.DataFrame(rows, columns=cols + ["machines"]).drop(columns=["machines"])


# ---------------------------------------------------------------- analysis tables
def pareto(df, label, value):
    if df is None or df.empty:
        return pd.DataFrame(columns=[label, value, "pct", "cum_pct"])
    g = df.groupby(label)[value].sum().sort_values(ascending=False).reset_index()
    tot = g[value].sum()
    g["pct"] = g[value] / tot if tot else 0.0
    g["cum_pct"] = g.pct.cumsum()
    return g


def npt_by_cause(nd):
    cols = ["cause", "occurrences", "total_h", "pct", "avg_min", "max_min"]
    if nd is None or nd.empty:
        return pd.DataFrame(columns=cols)
    g = nd.groupby("cause").agg(occurrences=("npt_sec", "size"), total_h=("npt_sec", lambda x: x.sum() / 3600),
                                avg_min=("npt_sec", lambda x: x.mean() / 60), max_min=("npt_sec", lambda x: x.max() / 60)).reset_index()
    g["pct"] = g.total_h / g.total_h.sum()
    return g.sort_values("total_h", ascending=False)[cols].reset_index(drop=True)


def rejection_reconciliation(prod, rej):
    """Injection Due 'Bad' pieces vs Rejection report pieces per day (information only)."""
    if prod is None or prod.empty:
        return pd.DataFrame(columns=["date", "injection_due_bad", "rejection_report_pieces", "difference", "difference_pct"])
    a = prod.groupby("prod_date").bad.sum().rename("injection_due_bad")
    b = rej.groupby("report_date").qty_pieces.sum().rename("rejection_report_pieces") if rej is not None and not rej.empty else pd.Series(dtype=float, name="rejection_report_pieces")
    r = pd.concat([a, b], axis=1).fillna(0.0).reset_index().rename(columns={"index": "date"})
    r["difference"] = r.rejection_report_pieces - r.injection_due_bad
    r["difference_pct"] = np.where(r.injection_due_bad > 0, r["difference"] / r.injection_due_bad.replace(0, np.nan), np.nan)
    return r
