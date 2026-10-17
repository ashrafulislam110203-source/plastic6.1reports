"""Reads an uploaded Machine ID -> Machine No. -> Production Line mapping file (.xlsx/.xlsm/.csv).

Production Line names are never hard-coded, exactly like NPT/Rejection causes: whatever the user
writes in the file is accepted and remembered (Settings keeps the list of lines seen so far, used
only to fill the dropdown when editing the mapping by hand - it is never used to reject a value).
"""
import csv
import io

import openpyxl
import pandas as pd

from .cleaner import FileValidationError, ParsedReport, find_header_row, get_cell, norm_header, normalize_machine

NAMES = {"machine id": "Machine ID", "machine no": "Machine No.", "production line": "Production Line"}
MACHINE_NO_KEYS = ("machine no", "machine no.", "machine number", "machine number.", "mc no", "mc no.")
LINE_KEYS = ("production line", "line", "line name")


def _rows_from_csv(file_bytes):
    text = file_bytes.decode("utf-8-sig", errors="replace")
    return list(csv.reader(io.StringIO(text)))


def _rows_from_excel(file):
    wb = openpyxl.load_workbook(file, data_only=True)
    best = None
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        i, cols = find_header_row(rows, ["machine id"], MACHINE_NO_KEYS + LINE_KEYS)
        if i is not None:
            best = (rows, i, cols)
            break
    return best


def parse_machine_map(file, file_name=""):
    rep = ParsedReport("Machine Map", file_name)
    is_csv = file_name.lower().endswith(".csv")
    if is_csv:
        data = file.read() if hasattr(file, "read") else file
        rows = _rows_from_csv(data)
        hdr_i, cols = find_header_row(rows, ["machine id"], MACHINE_NO_KEYS + LINE_KEYS)
        if hdr_i is None:
            rows = None
    else:
        found = _rows_from_excel(file)
        rows, hdr_i, cols = found if found else (None, None, {})
    if rows is None:
        raise FileValidationError("Invalid file structure. This does not appear to be a Machine Mapping file: "
                                  "a header row with 'Machine ID', 'Machine No.' and 'Production Line' was not found.")
    no_col = next((cols[k] for k in MACHINE_NO_KEYS if k in cols), None)
    line_col = next((cols[k] for k in LINE_KEYS if k in cols), None)
    if no_col is None:
        raise FileValidationError("Required column missing: Machine No.")
    if line_col is None:
        raise FileValidationError("Required column missing: Production Line")

    out, seen = [], {}
    for xl_row, row in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        rep.rows_read += 1
        id_raw = get_cell(row, cols["machine id"])
        machine = normalize_machine(id_raw)
        if machine is None:
            rep.rejected.append((f"row {xl_row}", f"Machine ID not recognised: '{str(id_raw).strip() if id_raw else ''}'"))
            continue
        no = get_cell(row, no_col)
        no = str(no).strip() if no is not None and str(no).strip() else None
        line = get_cell(row, line_col)
        line = str(line).strip() if line is not None and str(line).strip() else None
        if no is None and line is None:
            rep.rejected.append((f"row {xl_row}", "Both Machine No. and Production Line are blank"))
            continue
        if machine in seen:
            rep.warnings.append(f"Row {xl_row}: {machine} was already mapped by row {seen[machine]} in this file; "
                                "the later row was used.")
        seen[machine] = xl_row
        out.append(dict(machine_id=machine, machine_no=no, production_line=line))
    df = pd.DataFrame(out)
    if not df.empty:
        df = df.drop_duplicates("machine_id", keep="last")
    rep.df = df
    if df.empty and not rep.rejected:
        raise FileValidationError("No mapping rows found in the file.")
    return rep
