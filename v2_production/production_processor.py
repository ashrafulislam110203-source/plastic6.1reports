"""VERSION 2 (NOT ACTIVE in Version 1). Kept so Production / OEE can be added later."""
"""Reads the Injection Due (Working File) workbook.

Layout found in the real file:
  * two header rows: row 1 = column names and one DATE per day, row 2 = sub-headers
  * columns A..AQ are fixed product/order/machine columns
  * from column AR every day has a block of 6 columns:
        A-Good, A-Bad, B-Good, B-Bad, Total, Total Bad
    (Total and Total Bad are formulas - they are recalculated here, not trusted)
  * one row = one product order on one machine ("SM" column is the machine ID)
"""
import datetime as dt

import openpyxl
import pandas as pd
from openpyxl.utils import get_column_letter

from processing.cleaner import (FileValidationError, ParsedReport, get_cell, norm_header,
                      normalize_machine, parse_position, to_float)

STATIC = {"sm": "SM", "mc sl": "MC SL", "ct": "CT", "cv": "CV", "fg name": "FG Name",
          "fg code": "FG Code", "order name": "Order Name", "color": "Color", "weight": "Weight"}
REQUIRED = ["sm", "ct", "cv", "fg name"]
SUBS = {"a-good": "a_good", "a-bad": "a_bad", "b-good": "b_good", "b-bad": "b_bad"}


def _open_rows(file):
    wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    best = None
    for ws in wb.worksheets:
        head = list(ws.iter_rows(min_row=1, max_row=6, values_only=True))
        for i, row in enumerate(head):
            names = [norm_header(c) for c in row]
            if "sm" in names and "ct" in names:
                best = (ws, i)
                break
        if best:
            break
    if not best:
        raise FileValidationError("Invalid file structure. This does not appear to be an Injection Due / "
                                  "Production report (columns SM and CT were not found).")
    ws, hdr_i = best
    return list(ws.iter_rows(values_only=True)), hdr_i


def parse_production(file, file_name="", selected_date=None, all_dates=False, date_from=None):
    rep = ParsedReport("Production", file_name)
    rows, hdr_i = _open_rows(file)
    hdr = rows[hdr_i]
    sub = rows[hdr_i + 1] if hdr_i + 1 < len(rows) else ()

    # fixed columns: first occurrence before the first date column
    first_date = next((i for i, v in enumerate(hdr) if isinstance(v, (dt.datetime, dt.date))), len(hdr))
    cols = {}
    for i in range(first_date):
        k = norm_header(hdr[i])
        if k in STATIC and k not in cols:
            cols[k] = i
    for k in REQUIRED:
        if k not in cols:
            raise FileValidationError(f"Required column missing: {STATIC[k]}")

    # day blocks
    blocks, prev = [], None
    for i, v in enumerate(hdr):
        if not isinstance(v, (dt.datetime, dt.date)):
            continue
        d = v.date() if isinstance(v, dt.datetime) else v
        found = {}
        for j in range(i, min(i + 6, len(sub))):
            k = norm_header(sub[j])
            if k in SUBS and SUBS[k] not in found:
                found[SUBS[k]] = j
        letter = get_column_letter(i + 1)
        if len(found) < 4:
            rep.warnings.append(f"Column {letter}: date {d:%d-%b-%Y} skipped - shift columns not found.")
            continue
        if prev and d <= prev:
            msg = (f"Date header {d:%d-%b-%Y} in column {letter} is out of order (after {prev:%d-%b-%Y}); "
                   "that day was skipped. Please check the header for a typing mistake.")
            rep.warnings.append(msg)
            rep.flags.append(dict(warn_date=d.isoformat(), machine_id=None, kind="Date header problem", message=msg))
            continue
        if prev and (d - prev).days > 7:
            rep.warnings.append(f"{(d - prev).days - 1} days are missing between {prev:%d-%b-%Y} and {d:%d-%b-%Y}.")
        prev = d
        blocks.append((d, found))
    if not blocks:
        raise FileValidationError("Required column missing: daily date columns (A-Good / A-Bad / B-Good / B-Bad)")

    rep.dates = [b[0] for b in blocks]
    if all_dates:
        chosen = [b for b in blocks if date_from is None or b[0] >= date_from]
        rep.warnings.append("The Injection Due sheet is a rolling planning sheet: finished orders are removed over time, "
                            "so older days may be incomplete. Check the machine count of old days before trusting them.") if date_from is None else None
    else:
        chosen = [b for b in blocks if b[0] == selected_date]
        if not chosen:
            raise FileValidationError(
                f"Date mismatch detected. The file has no column for {selected_date:%d-%b-%Y} "
                f"(latest date in file: {blocks[-1][0]:%d-%b-%Y}).")

    out = []
    data_rows = list(enumerate(rows[hdr_i + 2:], start=hdr_i + 3))   # (excel row number, row)
    seen_rows = set()
    for d, found in chosen:
        for xl_row, row in data_rows:
            sm_raw = get_cell(row, cols["sm"])
            if sm_raw is None or str(sm_raw).strip() == "":
                continue
            vals = {k: to_float(get_cell(row, j)) for k, j in found.items()}
            if all(v in (None, 0.0) for v in vals.values()):
                continue                        # nothing produced that day on this row
            seen_rows.add((d, xl_row))
            where = f"{d:%d-%b-%Y} row {xl_row}"
            if any(v is not None and v < 0 for v in vals.values()):
                rep.rejected.append((where, "Negative production quantity"))
                continue
            machine = normalize_machine(sm_raw)
            if machine is None:
                rep.rejected.append((where, f"Machine ID not recognised: '{str(sm_raw).strip()}'"))
                continue
            ct = to_float(get_cell(row, cols["ct"]))
            cv = to_float(get_cell(row, cols["cv"]))
            name = get_cell(row, cols["fg name"])
            mc_sl = get_cell(row, cols.get("mc sl"))
            if ct is None or ct <= 0 or cv is None or cv <= 0:
                rep.flags.append(dict(warn_date=d.isoformat(), machine_id=machine, kind="Missing cycle time / cavity",
                                      message=f"{machine} on {d:%d-%b-%Y}: cycle time or cavity is blank for "
                                              f"'{name}' (row {xl_row}). Its pieces are left out of Performance."))
            elif cv < 1:
                rep.flags.append(dict(warn_date=d.isoformat(), machine_id=machine, kind="Cavity below 1",
                                      message=f"{machine} on {d:%d-%b-%Y}: cavity is {cv:g} for '{name}' "
                                              f"(row {xl_row}). Please check the value."))
            out.append(dict(
                prod_date=d.isoformat(), machine_id=machine, machine_raw=str(sm_raw).strip(),
                mc_sl=str(mc_sl).strip() if mc_sl is not None else None, position=parse_position(mc_sl),
                order_type=_s(get_cell(row, cols.get("order name"))),
                product_code=_s(get_cell(row, cols.get("fg code"))),
                product_name=_s(name), color=_s(get_cell(row, cols.get("color"))),
                cavity=cv, cycle_time_sec=ct, unit_weight_kg=to_float(get_cell(row, cols.get("weight"))),
                a_good=vals.get("a_good") or 0.0, a_bad=vals.get("a_bad") or 0.0,
                b_good=vals.get("b_good") or 0.0, b_bad=vals.get("b_bad") or 0.0,
                source_row=xl_row))
    rep.rows_read = len(seen_rows)
    rep.df = pd.DataFrame(out)
    if not all_dates:
        rep.report_date = selected_date
        if rep.df.empty:
            raise FileValidationError(f"No production quantities found for {selected_date:%d-%b-%Y}. "
                                      "The file may not be updated for that day yet.")
    elif rep.df.empty:
        raise FileValidationError("No production quantities found in the file.")
    return rep


def _s(v):
    if v is None:
        return None
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s or None
