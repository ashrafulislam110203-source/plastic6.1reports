"""Reads the Rejection report.

Real layout: title + 6 info lines (Date Range written m/d/yyyy), header row, then one row per
rejection entry: Sl, Section, Machine, Item, WIP, Quantity, Weight, Cause, Added By, Added Date.
Quantity is in thousand pieces and Weight is in tons (confirmed by the user); the conversion
factors are in Settings.
"""
import re

import openpyxl
import pandas as pd

from .cleaner import (FileValidationError, ParsedReport, clean_cause, find_header_row, get_cell,
                      norm_header, normalize_machine, resolve_report_date, to_float)
from .npt_processor import _dt, _s

NAMES = {"machine": "Machine", "item": "Item", "quantity": "Quantity", "cause": "Cause"}


def parse_rejection(file, file_name="", selected_date=None, settings=None):
    settings = settings or {}
    q_f = float(settings.get("rejection_qty_to_pieces", 1000) or 1000)
    w_f = float(settings.get("rejection_weight_to_kg", 1000) or 1000)
    rep = ParsedReport("Rejection", file_name)
    wb = openpyxl.load_workbook(file, data_only=True)
    rows, hdr_i, cols = None, None, {}
    for ws in wb.worksheets:
        r = list(ws.iter_rows(values_only=True))
        i, c = find_header_row(r, [], ["item", "quantity", "wip"])
        if i is not None and "from time" not in c:
            rows, hdr_i, cols = r, i, c
            break
    if rows is None:
        raise FileValidationError("Invalid file structure. This does not appear to be a Rejection report.")
    for k, label in NAMES.items():
        if k not in cols:
            raise FileValidationError(f"Required column missing: {label}")

    range_text = next((get_cell(r, 1) for r in rows[:hdr_i] if norm_header(get_cell(r, 0)).startswith("date range")), None)
    rep.report_date, w = resolve_report_date(range_text, False, selected_date)
    rep.warnings += w

    out, zero_w = [], 0
    for xl_row, row in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        m_raw = get_cell(row, cols["machine"])
        if (m_raw is None or str(m_raw).strip() == "") and norm_header(get_cell(row, 0)).startswith("total"):
            continue
        rep.rows_read += 1
        machine = normalize_machine(m_raw)
        if machine is None:
            rep.rejected.append((f"row {xl_row}", f"Machine ID not recognised: '{m_raw}'"))
            continue
        q = to_float(get_cell(row, cols["quantity"]))
        if q is None or q < 0:
            rep.rejected.append((f"row {xl_row}", "Quantity is blank, not a number, or negative"))
            continue
        wgt = to_float(get_cell(row, cols.get("weight")))
        if wgt is not None and wgt < 0:
            rep.rejected.append((f"row {xl_row}", "Weight is negative"))
            continue
        if not wgt:
            zero_w += 1
        item = str(get_cell(row, cols["item"]) or "").strip()
        m = re.search(r"\(([^()]*)\)\s*$", item)
        cause_raw = get_cell(row, cols["cause"])
        out.append(dict(
            report_date=rep.report_date.isoformat(), machine_id=machine, machine_raw=str(m_raw).strip(),
            item_name=re.sub(r"\s*\([^()]*\)\s*$", "", item).strip() or None, item_code=m.group(1) if m else None,
            wip_name=_s(get_cell(row, cols.get("wip"))), qty_raw=q, weight_raw=wgt,
            qty_pieces=round(q * q_f, 3), weight_kg=None if wgt is None else round(wgt * w_f, 3),
            cause_raw=None if cause_raw is None else str(cause_raw), cause=clean_cause(cause_raw) or "Cause Not Recorded",
            added_by=_s(get_cell(row, cols.get("added by"))),
            added_time=(lambda t: t.strftime("%Y-%m-%d %H:%M:%S") if t else None)(_dt(get_cell(row, cols.get("added date"))))))
    if zero_w:
        rep.warnings.append(f"{zero_w} rejection row(s) have zero or blank weight.")
    rep.df = pd.DataFrame(out)
    if rep.df.empty and not rep.rejected:
        raise FileValidationError("No rejection records found in the file.")
    return rep
