"""NPT (downtime) calculations from the stored events.

An event is cut at midnight (calendar day) so that a downtime that starts on one day and ends on
the next is counted in the right days. If a shift start hour is set in Settings, events are also cut
at the shift changes (A = 12 h from that hour, B = the other 12 h). A night shift that passes
midnight therefore appears on two calendar dates.
"""
import numpy as np
import pandas as pd

from database import db
from .common import TS, add_month, all_months, with_machine_map

COLS = ["date", "machine_id", "cause", "shift", "npt_sec", "event_start", "event_end"]
EV = ["machine_id", "event_start", "event_end"]


def _shift_a(settings):
    v = str(settings.get("shift_a_start") or "").strip()
    return int(float(v)) % 24 if v != "" else None


def _next_boundary(t, a):
    nb = t.normalize() + pd.Timedelta(days=1)
    if a is not None:
        for h in (a, (a + 12) % 24):
            c = t.normalize() + pd.Timedelta(hours=h)
            if c <= t:
                c += pd.Timedelta(days=1)
            nb = min(nb, c)
    return nb


def _shift(t, a):
    if a is None:
        return "All"
    return "A" if (t.hour - a) % 24 < 12 else "B"


def load_npt(start, end, machines=None, causes=None, shift=None, settings=None, lines=None):
    """One row per (event, calendar day, shift). `lines` filters by Production Line (Machine Mapping)."""
    settings = settings or db.get_settings()
    a = _shift_a(settings)
    ws, we = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1)
    ev = db.query_df("SELECT machine_id, cause, start_time, end_time FROM npt_events WHERE end_time>? AND start_time<?",
                     (ws.strftime(TS), we.strftime(TS)))
    if ev.empty:
        return pd.DataFrame(columns=COLS)
    if machines:
        ev = ev[ev.machine_id.isin(machines)]
    if causes:
        ev = ev[ev.cause.isin(causes)]
    if lines:
        mapped = with_machine_map(ev[["machine_id"]].drop_duplicates())
        keep = set(mapped[mapped.production_line.isin(lines)].machine_id)
        ev = ev[ev.machine_id.isin(keep)]
    ev = ev.assign(s=pd.to_datetime(ev.start_time), e=pd.to_datetime(ev.end_time))
    out = []
    for r in ev.itertuples():
        t, stop = max(r.s, ws), min(r.e, we)
        while t < stop:
            nb = min(_next_boundary(t, a), stop)
            sec = (nb - t).total_seconds()
            if sec > 0:
                out.append((t.date(), r.machine_id, r.cause, _shift(t, a), sec, r.s, r.e))
            t = nb
    df = pd.DataFrame(out, columns=COLS)
    if shift and shift != "All" and not df.empty:
        df = df[df["shift"] == shift]
    return df


def _events(nd):
    """Duration of each event inside the selected period."""
    return nd.groupby(["cause"] + EV).npt_sec.sum().reset_index()


def summary(nd, settings, n_days):
    if nd.empty:
        return dict(npt_h=0.0, events=0, machines=0, causes=0, avg_min=np.nan, max_min=np.nan,
                    per_day_h=0.0, npt_pct=np.nan, top_cause=None)
    e = nd.groupby(EV).npt_sec.sum()
    npt_h = nd.npt_sec.sum() / 3600
    mc = str(settings.get("machine_count") or "").strip()
    sched = float(settings.get("scheduled_hours") or 24)
    pct = npt_h / (float(mc) * sched * n_days) if mc and float(mc) > 0 and n_days > 0 else np.nan
    top = nd.groupby("cause").npt_sec.sum().idxmax()
    return dict(npt_h=npt_h, events=len(e), machines=nd.machine_id.nunique(), causes=nd.cause.nunique(),
                avg_min=e.mean() / 60, max_min=e.max() / 60, per_day_h=npt_h / max(n_days, 1), npt_pct=pct, top_cause=top)


def by_cause(nd):
    cols = ["cause", "occurrences", "total_h", "pct", "cum_pct", "avg_min", "max_min"]
    if nd.empty:
        return pd.DataFrame(columns=cols)
    e = _events(nd)
    g = e.groupby("cause").npt_sec.agg(occurrences="size", total_h=lambda x: x.sum() / 3600,
                                       avg_min=lambda x: x.mean() / 60, max_min=lambda x: x.max() / 60).reset_index()
    g = g.sort_values("total_h", ascending=False).reset_index(drop=True)
    g["pct"] = g.total_h / g.total_h.sum()
    g["cum_pct"] = g.pct.cumsum()
    return g[cols]


def by_machine(nd):
    cols = ["machine_id", "machine_no", "production_line", "occurrences", "total_h", "pct", "avg_min", "max_min", "top_cause"]
    if nd.empty:
        return pd.DataFrame(columns=cols)
    e = nd.groupby(["machine_id", "cause"] + ["event_start", "event_end"]).npt_sec.sum().reset_index()
    g = e.groupby("machine_id").npt_sec.agg(occurrences="size", total_h=lambda x: x.sum() / 3600,
                                            avg_min=lambda x: x.mean() / 60, max_min=lambda x: x.max() / 60).reset_index()
    top = nd.groupby(["machine_id", "cause"]).npt_sec.sum().reset_index().sort_values("npt_sec", ascending=False).drop_duplicates("machine_id")
    g = g.merge(top[["machine_id", "cause"]].rename(columns={"cause": "top_cause"}), on="machine_id")
    g = with_machine_map(g)
    g = g.sort_values("total_h", ascending=False).reset_index(drop=True)
    g["pct"] = g.total_h / g.total_h.sum()
    return g[cols]


def by_line(nd):
    """NPT hours grouped by Production Line (Machine Mapping). Unmapped machines appear as 'Unmapped'."""
    cols = ["production_line", "machines", "occurrences", "total_h", "pct", "avg_min", "max_min"]
    if nd.empty:
        return pd.DataFrame(columns=cols)
    nd2 = with_machine_map(nd)
    e = nd2.groupby(["production_line", "cause"] + EV).npt_sec.sum().reset_index()
    g = e.groupby("production_line").agg(machines=("machine_id", "nunique"),
                                         occurrences=("npt_sec", "size"), total_h=("npt_sec", lambda x: x.sum() / 3600),
                                         avg_min=("npt_sec", lambda x: x.mean() / 60), max_min=("npt_sec", lambda x: x.max() / 60)).reset_index()
    g = g.sort_values("total_h", ascending=False).reset_index(drop=True)
    g["pct"] = g.total_h / g.total_h.sum()
    return g[cols]


def by_date(nd):
    cols = ["date", "events", "machines", "npt_h"]
    if nd.empty:
        return pd.DataFrame(columns=cols)
    g = nd.groupby("date").apply(lambda x: pd.Series(dict(
        events=len(x.groupby(EV)), machines=x.machine_id.nunique(), npt_h=x.npt_sec.sum() / 3600)), include_groups=False).reset_index()
    return g[cols]


def by_month(nd, year=None):
    cols = ["month", "events", "machines", "npt_h"]
    if nd.empty:
        g = pd.DataFrame(columns=cols)
    else:
        d = add_month(nd)
        g = d.groupby("month").apply(lambda x: pd.Series(dict(
            events=len(x.groupby(EV)), machines=x.machine_id.nunique(), npt_h=x.npt_sec.sum() / 3600)), include_groups=False).reset_index()
    if year:
        g = pd.DataFrame({"month": all_months(year)}).merge(g, how="left", on="month").fillna({"events": 0, "machines": 0, "npt_h": 0.0})
    return g[cols]


def by_shift(nd):
    if nd.empty:
        return pd.DataFrame(columns=["shift", "npt_h", "pct"])
    g = nd.groupby("shift").npt_sec.sum().div(3600).rename("npt_h").reset_index()
    g["pct"] = g.npt_h / g.npt_h.sum()
    return g


def overlaps(start, end):
    """Events of the same machine that overlap in time (data check)."""
    ws, we = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1)
    ev = db.query_df("SELECT machine_id, cause, start_time, end_time FROM npt_events WHERE end_time>? AND start_time<? "
                     "ORDER BY machine_id, start_time", (ws.strftime(TS), we.strftime(TS)))
    if ev.empty:
        return ev
    ev["prev_end"] = ev.groupby("machine_id").end_time.shift()
    return ev[ev.start_time < ev.prev_end]
