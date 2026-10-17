"""Saving parsed reports (new / replace / skip), upload history, delete, backup and restore.

Everything that changes data runs in ONE database transaction: it is saved completely or not
at all, so a failed save can never leave a day half-deleted or half-written.
"""
import datetime as dt
import gzip
import json
import math
import os
import tempfile

import pandas as pd

from processing.cleaner import machine_size
from . import db

NPT_COLS = ["machine_id", "machine_raw", "start_time", "end_time", "duration_sec", "cause_raw", "cause",
            "added_by", "added_time", "report_date"]
# every row carries its OWN report_date (the calendar day its NPT event started on), computed by the
# parser - this is what makes single-day uploads, bulk/date-range uploads and overnight-carry-in events
# all delete/replace correctly on their own day, with no special-casing needed between the two upload modes.
# When replacing/upserting an existing NPT event, report_date is deliberately NOT overwritten: an
# overnight event that appears in both day N's and day N+1's report files keeps whichever report
# first introduced it, so replacing day N+1 alone never deletes an event that day N's data depends on.
NPT_UPSERT_COLS = [c for c in NPT_COLS if c != "report_date"]
REJ_COLS = ["report_date", "machine_id", "machine_raw", "item_name", "item_code", "wip_name", "qty_raw",
            "weight_raw", "qty_pieces", "weight_kg", "cause_raw", "cause", "added_by", "added_time"]
BACKUP_FORMAT = "kpi-backup"


class AlreadyExists(Exception):
    """The date was saved by someone else between checking and saving. Nothing was written."""


def _clean(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v.item() if hasattr(v, "item") else v


def _records(df, cols, upload_id):
    if df is None or df.empty:
        return []
    return [tuple(_clean(r[c]) for c in cols) + (upload_id,) for r in df.to_dict("records")]


def _in_clause(n):
    return "(" + ",".join(["?"] * n) + ")"


def _lock(cur, key):
    """No-op here: every read and write already goes through db.run(), which holds the single
    shared connection's own lock for its whole duration, so two saves can never interleave anyway.
    Kept as a function (rather than removing every call site) so this file reads the same way
    regardless of which database backend is underneath it."""


# ---------------------------------------------------------------- duplicates check (read only)
def existing_info(rep):
    """How much of this report is already in the database? -> dict(existing, total, detail)"""
    db.init_db()
    df = rep.df
    if df.empty:
        return dict(existing=0, total=0, detail="")
    if rep.report_type == "NPT":
        lo = (pd.to_datetime(df.start_time).min() - pd.Timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        hi = (pd.to_datetime(df.start_time).max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        q = db.query_df("SELECT machine_id, start_time, end_time FROM npt_events WHERE start_time BETWEEN ? AND ?",
                        (lo, hi), cache=False)
        have = set(zip(q.machine_id, q.start_time, q.end_time))
        n = sum((a, b, c) in have for a, b, c in zip(df.machine_id, df.start_time, df.end_time))
        days = sorted(set(df.report_date.dropna()))
        d_existing = []
        if days:
            dq = db.query_df(f"SELECT DISTINCT report_date FROM npt_events WHERE report_date IN {_in_clause(len(days))}",
                             days, cache=False)
            d_existing = sorted(dq.report_date) if not dq.empty else []
        detail = f"{n} of {len(df)} events are already stored"
        if len(days) > 1:
            detail += f" ({len(d_existing)} of {len(days)} day(s) in this file already have some data)"
        return dict(existing=n, total=len(df), detail=detail, dates_total=days, dates_existing=d_existing)
    q = db.query_df("SELECT COUNT(*) n FROM rejection WHERE report_date=?", (rep.report_date.isoformat(),), cache=False)
    n = int(q.n.iloc[0])
    return dict(existing=n, total=len(df), detail=f"{n} records already stored for {rep.report_date:%d-%b-%Y}")


def add_history(rep_type, file_name, report_date, total, valid, rejected, status, dup_status, errors, message=""):
    db.init_db()
    return db.execute(
        "INSERT INTO upload_history(upload_time, report_date, report_type, file_name, records_total, "
        "records_valid, records_rejected, status, duplicate_status, error_count, message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), report_date, rep_type, file_name, total, valid,
         rejected, status, dup_status, errors, message), returning=True)


def mark_history(upload_id, status, message=""):
    """Best effort: record that a save did not go through. Never raises."""
    try:
        db.execute("UPDATE upload_history SET status=?, message=? WHERE id=?", (status, str(message)[:500], upload_id))
    except Exception:
        pass


# ---------------------------------------------------------------- save
def _save_npt(cur, df, mode, upload_id):
    """Every row already carries its own report_date (set by the parser), so this works unchanged for
    a single day's file AND a bulk, multi-day file: 'replace' removes exactly the days present in THIS
    upload, never a neighbouring day's overnight event that belongs to a different upload."""
    cols = NPT_COLS + ["upload_id"]
    rows = _records(df, NPT_COLS, upload_id)
    placeholders = ",".join("?" * len(cols))
    if mode == "replace":
        days = sorted(set(df.report_date.dropna())) if not df.empty else []
        if days:
            cur.execute(f"DELETE FROM npt_events WHERE report_date IN {_in_clause(len(days))}", days)
        if rows:
            set_list = ",".join(f"{c}=excluded.{c}" for c in NPT_UPSERT_COLS
                                if c not in ("machine_id", "start_time", "end_time")) + ",upload_id=excluded.upload_id"
            cur.executemany(
                f"INSERT INTO npt_events ({','.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT (machine_id, start_time, end_time) DO UPDATE SET {set_list}", rows)
        return len(rows)
    if not rows:
        return 0
    written = 0
    for row in rows:
        cur.execute(
            f"INSERT INTO npt_events ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT (machine_id, start_time, end_time) DO NOTHING", row)
        written += cur.rowcount
    return written


def _save_rejection(cur, df, mode, upload_id, day):
    if mode == "skip":
        return 0                                    # the date already has data and the user chose to keep it
    cur.execute("SELECT COUNT(*) FROM rejection WHERE report_date=?", (day.isoformat(),))
    if cur.fetchone()[0] and mode == "new":
        raise AlreadyExists(f"Rejection data for {day:%d-%b-%Y} already exists.")
    if mode == "replace":
        cur.execute("DELETE FROM rejection WHERE report_date=?", (day.isoformat(),))
    rows = _records(df, REJ_COLS, upload_id)
    if rows:
        cols = REJ_COLS + ["upload_id"]
        cur.executemany(f"INSERT INTO rejection ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
    return len(rows)


def save_report(rep, upload_id, mode="new"):
    """mode: 'new' (nothing exists), 'replace', 'skip'. Returns the number of rows written.
    One transaction: rows + machines + warnings + the upload-history count are saved together or not at all.
    Saving the same upload twice never creates duplicates."""
    if mode not in ("new", "replace", "skip"):
        raise ValueError("mode must be new, replace or skip")
    db.init_db()
    df, day = rep.df, rep.report_date
    lock_key = f"{rep.report_type}:{rep.dates[0]}:{rep.dates[-1]}" if rep.dates else f"{rep.report_type}:{day.isoformat()}"

    def _tx(cur):
        _lock(cur, lock_key)
        written = _save_npt(cur, df, mode, upload_id) if rep.report_type == "NPT" \
            else _save_rejection(cur, df, mode, upload_id, day)
        if not df.empty:
            today = dt.date.today().isoformat()
            cur.executemany(
                "INSERT INTO machines (machine_id, size, first_seen) VALUES (?,?,?) ON CONFLICT (machine_id) DO NOTHING",
                [(m, machine_size(m), today) for m in sorted(set(df.machine_id.dropna()))])
        warn = [(upload_id, f.get("warn_date"), f.get("machine_id"), f.get("kind"), f.get("message")) for f in rep.flags]
        warn += [(upload_id, day.isoformat(), None, "Row rejected", f"{loc}: {why}") for loc, why in rep.rejected]
        if warn:
            cur.executemany("INSERT INTO data_warnings (upload_id, warn_date, machine_id, kind, message) "
                            "VALUES (?,?,?,?,?)", warn)
        if upload_id is not None:
            cur.execute("UPDATE upload_history SET records_valid=? WHERE id=?", (written, upload_id))
        return written
    return db.run(_tx, write=True)


# ---------------------------------------------------------------- logical backup / restore
def _json_safe(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def backup_db(tag="backup"):
    """Every table's data as gzip-compressed JSON bytes (a consistent snapshot of all tables)."""
    db.init_db()

    def _dump(cur):
        out = {}
        for t in db.ALL_TABLES:
            order = " ORDER BY id" if t in db.ID_TABLES else (" ORDER BY key" if t == "settings" else " ORDER BY machine_id")
            cur.execute(f"SELECT * FROM {t}{order}")
            cols = [d[0] for d in cur.description]
            out[t] = [{c: _json_safe(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
        return out
    tables = db.run(_dump)
    doc = {"format": BACKUP_FORMAT, "version": 2, "created": dt.datetime.now().isoformat(timespec="seconds"), "tables": tables}
    raw = json.dumps(doc, default=str).encode("utf-8")
    return gzip.compress(raw), f"kpi_backup_{tag}_{dt.datetime.now():%Y%m%d_%H%M%S}.json.gz"


def _read_old_sqlite(file_bytes):
    """One-time import of a .db backup made by an older SQLite version of this app, where the file
    is a whole database rather than the gzip-JSON format backup_db() makes now."""
    import sqlite3
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(file_bytes)
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out = {t: [dict(r) for r in con.execute(f"SELECT * FROM {t}")] for t in db.ALL_TABLES if t in have}
        con.close()
        return out
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _read_backup(file_bytes):
    if file_bytes[:16] == b"SQLite format 3\x00":
        tables = _read_old_sqlite(file_bytes)
    else:
        try:
            raw = gzip.decompress(file_bytes)
        except OSError:
            raw = file_bytes                        # an uncompressed .json is fine too
        try:
            doc = json.loads(raw)
        except ValueError:
            raise ValueError("This is not a backup file made by this app.")
        tables = doc.get("tables") if isinstance(doc, dict) else None
        if not isinstance(tables, dict):
            raise ValueError("This is not a backup file made by this app.")
    if not any(t in tables for t in db.ALL_TABLES):
        raise ValueError("The file contains none of this app's tables.")
    clean = {}
    for t in db.ALL_TABLES:
        rows = tables.get(t) or []
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError(f"The backup data for '{t}' is damaged.")
        clean[t] = [{k: _json_safe(v) for k, v in r.items()} for r in rows]
    return clean


def restore_db(file_bytes):
    """Replaces ALL data with the content of a backup made by backup_db() (or an old .db backup).
    All-or-nothing: if anything is wrong the current data stays exactly as it is.
    Returns the number of rows restored per table."""
    tables = _read_backup(file_bytes)               # checked BEFORE anything is deleted
    db.init_db()

    def _tx(cur):
        _lock(cur, "restore")
        valid = {}
        for t in db.ALL_TABLES:
            cur.execute(f"PRAGMA table_info({t})")
            valid[t] = {r[1] for r in cur.fetchall()}     # r[1] is the column name
        for t in reversed(db.ALL_TABLES):
            cur.execute(f"DELETE FROM {t}")
        counts = {}
        for t in db.ALL_TABLES:
            rows = tables[t]
            counts[t] = len(rows)
            if not rows:
                continue
            cols = [c for c in rows[0] if c in valid.get(t, ())]     # unknown / old columns are ignored
            if not cols:
                continue
            cur.executemany(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [tuple(r.get(c) for c in cols) for r in rows])
            # plain INTEGER PRIMARY KEY (no AUTOINCREMENT) always computes the next id from MAX(rowid)
            # itself, so there is nothing to reset here even after restoring arbitrary high ids.
        db.ensure_defaults(cur)
        db.backfill_npt_report_date(cur)
        return counts
    return db.run(_tx, write=True)


# ---------------------------------------------------------------- machine mapping (Machine No. / Production Line)
def get_machine_map():
    """Every known machine, with its Machine No. and Production Line if mapped (NULL if not yet mapped)."""
    db.init_db()
    return db.query_df("SELECT machine_id, size, first_seen, machine_no, production_line FROM machines "
                       "ORDER BY machine_id", cache=False)


def known_production_lines():
    """The lines seen so far (Settings' saved list, plus any already used in the mapping) - for dropdowns only.
    A value is never rejected for not being in this list; new lines are accepted and added to it automatically,
    the same way NPT/Rejection causes are never hard-coded."""
    s = db.get_settings()
    saved = [x.strip() for x in (s.get("production_lines") or "").split(",") if x.strip()]
    used = db.query_df("SELECT DISTINCT production_line FROM machines WHERE production_line IS NOT NULL", cache=False)
    seen = list(used.production_line) if not used.empty else []
    out, low = [], set()
    for v in saved + seen:
        if v.lower() not in low:
            low.add(v.lower())
            out.append(v)
    return out


def _remember_new_lines(lines):
    """Add any line name not already known to the saved Settings list (never removes anything)."""
    known = {v.lower() for v in known_production_lines()}
    new = [v for v in dict.fromkeys(lines) if v and v.lower() not in known]
    if new:
        s = db.get_settings()
        saved = [x.strip() for x in (s.get("production_lines") or "").split(",") if x.strip()]
        db.set_setting("production_lines", ",".join(saved + new))


def set_machine_map(rows):
    """rows: list of dicts {machine_id, machine_no, production_line} (either may be None to leave unset).
    Upserts into the machines table; a machine_id not seen before is created. size/first_seen of an
    EXISTING machine are never touched by this (only machine_no/production_line are). Returns the count written."""
    rows = [r for r in rows if r.get("machine_id")]
    if not rows:
        return 0
    _remember_new_lines([r["production_line"] for r in rows if r.get("production_line")])
    today = dt.date.today().isoformat()
    recs = [(r["machine_id"], machine_size(r["machine_id"]), today, r.get("machine_no"), r.get("production_line"))
            for r in rows]

    def _tx(cur):
        _lock(cur, "machine_map")
        cur.executemany(
            "INSERT INTO machines (machine_id, size, first_seen, machine_no, production_line) VALUES (?,?,?,?,?) "
            "ON CONFLICT (machine_id) DO UPDATE SET machine_no=excluded.machine_no, "
            "production_line=excluded.production_line", recs)
        return len(recs)
    return db.run(_tx, write=True)


def import_machine_map(rep):
    """Saves every valid row from a parsed Machine Mapping file (processing.machine_map_processor.parse_machine_map)."""
    if rep.df.empty:
        return 0
    return set_machine_map(rep.df.to_dict("records"))


def clear_machine_map(machine_ids):
    """Blanks Machine No. / Production Line for the given machines (they stay in the machines table)."""
    machine_ids = list(machine_ids or [])
    if not machine_ids:
        return 0
    return db.execute(f"UPDATE machines SET machine_no=NULL, production_line=NULL WHERE machine_id IN "
                      f"{_in_clause(len(machine_ids))}", machine_ids)


# ---------------------------------------------------------------- delete / reset
def delete_day(kind, day):
    """Remove one report type for one date (NPT: events first attributed to that report date -
    never a neighbouring day's overnight event). Upload history is kept."""
    db.init_db()
    table = "npt_events" if kind == "NPT" else "rejection"

    def _tx(cur):
        _lock(cur, f"{kind}:{day.isoformat()}")
        cur.execute(f"DELETE FROM {table} WHERE report_date=?", (day.isoformat(),))
        return cur.rowcount
    n = db.run(_tx, write=True)
    add_history(kind, "(deleted by user)", day.isoformat(), n, 0, 0, "Deleted", "-", 0, f"{n} record(s) removed")
    return n


def reset_all():
    """Deletes ALL NPT / Rejection data. A backup is made first (and must succeed first) and
    returned as bytes so the caller can offer it as a download before the data is gone."""
    backup_bytes, backup_name = backup_db("before_reset")

    def _tx(cur):
        _lock(cur, "restore")
        for t in ("npt_events", "rejection", "data_warnings", "machines"):
            cur.execute(f"DELETE FROM {t}")
    db.run(_tx, write=True)
    add_history("-", "(database reset by user)", None, 0, 0, 0, "Reset", "-", 0, f"backup: {backup_name}")
    return backup_bytes, backup_name
