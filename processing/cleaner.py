"""Shared cleaning helpers and the ParsedReport container."""
import datetime as dt
import re
from dataclasses import dataclass, field

import pandas as pd


class FileValidationError(Exception):
    """Raised when a whole file cannot be used (wrong file, missing column, wrong date)."""


@dataclass
class ParsedReport:
    report_type: str                      # "Production" | "NPT" | "Rejection"
    file_name: str = ""
    report_date: dt.date | None = None    # single-day reports
    dates: list = field(default_factory=list)   # all dates found (production)
    df: pd.DataFrame = field(default_factory=pd.DataFrame)
    rows_read: int = 0
    rejected: list = field(default_factory=list)   # (location, reason)
    warnings: list = field(default_factory=list)   # plain text
    flags: list = field(default_factory=list)      # dicts: warn_date, machine_id, kind, message

    @property
    def rows_valid(self):
        return len(self.df)


def norm_header(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower() if s is not None else ""


def get_cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def to_float(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if v != v else float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_machine(x):
    """'IMM-380-03', 'imm 380 3', 'IMM-380-3 ' -> 'IMM-380-3'. Returns None if not recognised."""
    if x is None:
        return None
    m = re.search(r"IMM[\s\-_]*(\d+)[\s\-_]*(\d+)", str(x).upper())
    if not m:
        return None
    return f"IMM-{int(m.group(1))}-{int(m.group(2))}"


def machine_size(machine_id):
    m = re.match(r"IMM-(\d+)-", machine_id or "")
    return m.group(1) if m else None


def parse_position(mc_sl):
    m = re.match(r"\s*([A-Za-z]\d+)", str(mc_sl or ""))
    return m.group(1).upper() if m else None


_SMALL = {"or", "of", "and", "to", "in"}


def clean_cause(x):
    """Remove the trailing '*', extra spaces and fix capitalisation. New causes are accepted as they are."""
    if x is None:
        return None
    s = re.sub(r"\s+", " ", str(x)).strip().rstrip("*").strip()
    if not s:
        return None
    return " ".join(w if w.lower() in _SMALL else w[:1].upper() + w[1:] for w in s.split(" "))


_RANGE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*to\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.I)


def _mk(a, b, y, dayfirst):
    day, mon = (int(a), int(b)) if dayfirst else (int(b), int(a))
    try:
        return dt.date(int(y), mon, day)
    except ValueError:
        return None


def resolve_report_date(text, dayfirst, selected):
    """Read 'Date Range' text of a report and compare with the date chosen by the user.

    NPT reports write dd/mm/yyyy and Rejection reports write m/d/yyyy, so each caller
    says which order it expects; the other order is tried only to detect a match.
    Returns (date, warnings)."""
    m = _RANGE.search(str(text or ""))
    if not m:
        if selected:
            return selected, ["Date Range line not found in the file; the selected date was used."]
        raise FileValidationError("Date Range line not found in the file.")
    a, b, y1, c, d, y2 = m.groups()
    pref = (_mk(a, b, y1, dayfirst), _mk(c, d, y2, dayfirst))
    alt = (_mk(a, b, y1, not dayfirst), _mk(c, d, y2, not dayfirst))
    if pref[0] and pref[0] != pref[1]:
        raise FileValidationError(f"Report covers more than one day ({pref[0]:%d-%b-%Y} to {pref[1]:%d-%b-%Y}). "
                                  "Please download one day at a time.")
    rd = pref[0]
    warns = []
    if selected is None:
        if rd is None:
            raise FileValidationError("Could not read the report date.")
        return rd, warns
    if rd == selected:
        return rd, warns
    if alt[0] and alt[0] == alt[1] and alt[0] == selected:
        warns.append("Report date was read as day/month swapped; it matches the selected date.")
        return alt[0], warns
    shown = f"{rd:%d-%b-%Y}" if rd else str(text)
    raise FileValidationError(f"Date mismatch detected. The report is for {shown}, "
                              f"but the selected date is {selected:%d-%b-%Y}.")


def find_header_row(rows, must_have, any_of, limit=40):
    """Return (row_index, {normalised_header: col_index}) of the first row containing all
    of `must_have` and at least one of `any_of`."""
    for i, row in enumerate(rows[:limit]):
        names = [norm_header(c) for c in row]
        if all(m in names for m in must_have) and any(a in names for a in any_of):
            return i, {n: j for j, n in reversed(list(enumerate(names))) if n}
    return None, {}
