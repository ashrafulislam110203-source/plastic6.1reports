"""Reads the Downtime (NPT) report.

Real layout: title + 6 info lines (Date Range written dd/mm/yyyy), header row, then one row
per downtime event: Sl, Section, Machine, From Time, To Time, Duration, Duration (In Second),
Cause, Cause Added By, Cause Added Date, Done By.
Events can start the day before and end the day after the report date, so the KPI layer
splits every event at midnight.
"""
import datetime as dt

import openpyxl
import pandas as pd

from .cleaner import (FileValidationError, ParsedReport, clean_cause, find_header_row, get_cell,
                      norm_header, normalize_machine, resolve_report_date, to_float)

NAMES = {"machine": "Machine", "from time": "From Time", "to time": "To Time", "cause": "Cause"}


def _dt(v):
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    if v is None or str(v).strip() == "":
        return None
    t = pd.to_datetime(str(v), dayfirst=True, errors="coerce")
    return None if pd.isna(t) else t.to_pydatetime()


def parse_npt(file, file_name="", selected_date=None, settings=None, bulk=False, data_start=None):
    """bulk=True: the file covers many days (a Smart Manufacturing date-range export). selected_date is not
    required; instead, each row's own From Time decides which calendar day it is stored and replaced under
    (rep.dates holds every distinct day found; rep.report_date is the earliest one, for display/history).
    data_start, when given, silently-rejects (not hard-fails) rows dated before it, same as the single-day
    upload's date picker already does via its min_value."""
    settings = settings or {}
    rep = ParsedReport("NPT", file_name)
    wb = openpyxl.load_workbook(file, data_only=True)
    rows, hdr_i, cols = None, None, {}
    for ws in wb.worksheets:
        r = list(ws.iter_rows(values_only=True))
        i, c = find_header_row(r, [], ["from time", "to time", "duration", "duration (in second)"])
        if i is not None:
            rows, hdr_i, cols = r, i, c
            break
    if rows is None:
        raise FileValidationError("Invalid file structure. This does not appear to be an NPT report.")
    for k, label in NAMES.items():
        if k not in cols:
            raise FileValidationError(f"Required column missing: {label}")

    range_text = next((get_cell(r, 1) for r in rows[:hdr_i] if norm_header(get_cell(r, 0)).startswith("date range")), None)
    if bulk:
        rep.report_date = None             # filled in below, once every row's own date is known
    else:
        rep.report_date, w = resolve_report_date(range_text, True, selected_date)
        rep.warnings += w

    long_h = float(settings.get("long_npt_hours", 24) or 24)
    out = []
    for xl_row, row in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        m_raw = get_cell(row, cols["machine"])
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        if m_raw is None or str(m_raw).strip() == "":
            if norm_header(get_cell(row, 0)).startswith("total"):
                continue
            rep.rejected.append((f"row {xl_row}", "Machine is blank"))
            rep.rows_read += 1
            continue
        rep.rows_read += 1
        machine = normalize_machine(m_raw)
        if machine is None:
            rep.rejected.append((f"row {xl_row}", f"Machine ID not recognised: '{str(m_raw).strip()}'"))
            continue
        start, end = _dt(get_cell(row, cols["from time"])), _dt(get_cell(row, cols["to time"]))
        if start is None or end is None:
            rep.rejected.append((f"row {xl_row}", "From Time or To Time is blank / not a date"))
            continue
        if end < start:
            rep.rejected.append((f"row {xl_row}", "To Time is earlier than From Time"))
            continue
        row_date = start.date()
        if bulk and data_start and row_date < data_start:
            rep.rejected.append((f"row {xl_row}, {row_date:%d-%b-%Y}",
                                 f"Before the database start date ({data_start:%d-%b-%Y})"))
            continue
        cause_raw = get_cell(row, cols["cause"])
        cause = clean_cause(cause_raw) or "Cause Not Recorded"
        dur = (end - start).total_seconds()
        given = to_float(get_cell(row, cols.get("duration (in second)")))
        if given is not None and abs(given - dur) > 2:
            rep.warnings.append(f"Row {xl_row}: Duration in file ({given:.0f}s) differs from From/To ({dur:.0f}s); From/To was used.")
        if dur / 3600 > long_h:
            rep.flags.append(dict(warn_date=start.date().isoformat(), machine_id=machine, kind="Very long NPT event",
                                  message=f"{machine}: one NPT event of {dur/3600:.1f} h ({start:%d-%b-%Y %H:%M} to "
                                          f"{end:%d-%b-%Y %H:%M}), cause '{cause}'."))
        out.append(dict(machine_id=machine, machine_raw=str(m_raw).strip(),
                        start_time=start.strftime("%Y-%m-%d %H:%M:%S"), end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                        duration_sec=dur, cause_raw=None if cause_raw is None else str(cause_raw), cause=cause,
                        added_by=_s(get_cell(row, cols.get("cause added by"))),
                        added_time=_ts(get_cell(row, cols.get("cause added date"))),
                        report_date=(row_date if bulk else rep.report_date).isoformat()))
    df = pd.DataFrame(out)
    if not df.empty:
        n0 = len(df)
        df = df.drop_duplicates(["machine_id", "start_time", "end_time"])
        if len(df) < n0:
            rep.warnings.append(f"{n0 - len(df)} duplicate row(s) inside the file were ignored.")
        if bulk:
            rep.dates = sorted(pd.to_datetime(df.report_date).dt.date.unique())
            rep.report_date = rep.dates[0]
            span = (rep.dates[-1] - rep.dates[0]).days + 1
            if span > len(rep.dates):
                rep.warnings.append(f"The file covers {span} calendar days ({rep.dates[0]:%d-%b-%Y} to "
                                    f"{rep.dates[-1]:%d-%b-%Y}) but only {len(rep.dates)} of them have events; "
                                    "the others may already be zero-NPT days, or missing from the export.")
        else:
            rep.dates = [rep.report_date]
            day0 = dt.datetime.combine(rep.report_date, dt.time())
            day1 = day0 + dt.timedelta(days=1)
            s = pd.to_datetime(df.start_time)
            e = pd.to_datetime(df.end_time)
            if not ((s < day1) & (e > day0)).any():
                raise FileValidationError(f"Date mismatch detected. No NPT event overlaps {rep.report_date:%d-%b-%Y}.")
    rep.df = df
    if df.empty and not rep.rejected:
        raise FileValidationError("No NPT events found in the file.")
    return rep


def _s(v):
    return None if v is None or str(v).strip() == "" else str(v).strip()


def _ts(v):
    t = _dt(v)
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else None
