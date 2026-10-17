"""Database layer - local SQLite file (falls back to an in-memory database if the file cannot be
opened for any reason, so the app always starts cleanly with no setup, no secrets, and no external
service to configure).

* Storage: one SQLite file at KPI_DB_PATH (default: data/kpi.db next to this project, or
  data/demo.db when KPI_DEMO=1). If that path cannot be created or opened (for example a read-only
  filesystem), the app automatically uses an in-memory database instead so it still launches -
  that database only lasts for the life of this process/container.
* A single, lazily-opened connection is reused for the whole app, protected by one lock, so every
  read and write is naturally serialised - simple and safe for this app's scale.
* Every write runs inside ONE transaction: it is saved completely or not at all.
* Dates/times are stored as ISO text ("2026-09-18", "2026-09-18 14:30:00"), compared as plain text.
"""
import os
import re
import sqlite3
import threading
import time

import numpy as np
import pandas as pd

DEMO = os.environ.get("KPI_DEMO") == "1"          # practice mode: a separate file, never mixed with real data
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PATH = os.path.join(BASE_DIR, "data", "demo.db" if DEMO else "kpi.db")
DB_PATH = os.environ.get("KPI_DB_PATH") or _DEFAULT_PATH
IN_MEMORY = False     # set to True by _get_conn() if the file path could not be used

CACHE_SECONDS = 30      # read cache; cleared at once by any write made through this app
ALL_TABLES = ["settings", "machines", "upload_history", "npt_events", "rejection", "data_warnings"]
ID_TABLES = ["upload_history", "npt_events", "rejection", "data_warnings"]   # tables with an "id" counter

# Editable in the Settings page. Empty string = not set.
DEFAULT_SETTINGS = {
    "data_start_date": "2026-09-01",      # first day of the real database; earlier report dates are refused
    "scheduled_hours": "24",              # 2 shifts x 12 hours
    "shift_a_start": "8",                 # shift A = 08:00-20:00, shift B = 20:00-08:00 (empty = shift split off)
    "machine_count": "",                  # number of machines in the department (for NPT % of scheduled time)
    "long_npt_hours": "24",               # warn for a single NPT event longer than this
    "npt_goal_h_per_day": "",             # optional goal line on charts
    "rejection_goal_pieces_per_day": "",  # optional goal line on charts
    "rejection_qty_to_pieces": "1000",    # Rejection report: Quantity is in thousand pieces
    "rejection_weight_to_kg": "1000",     # Rejection report: Weight is in tons
    "production_lines": "A Line,B Line,C Line,D Line,E Line,Model Line",  # allowed Production Line names
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS machines (
    machine_id      TEXT PRIMARY KEY,
    size            TEXT,
    first_seen      TEXT,
    machine_no      TEXT,              -- e.g. "A1" - the machine's position/number (Machine Mapping)
    production_line TEXT               -- e.g. "A Line" - one of the lines set in Settings (Machine Mapping)
);
CREATE INDEX IF NOT EXISTS ix_machines_line ON machines (production_line);

CREATE TABLE IF NOT EXISTS upload_history (
    id               INTEGER PRIMARY KEY,
    upload_time      TEXT,
    report_date      TEXT,
    report_type      TEXT,
    file_name        TEXT,
    records_total    INTEGER,
    records_valid    INTEGER,
    records_rejected INTEGER,
    status           TEXT,
    duplicate_status TEXT,
    error_count      INTEGER,
    message          TEXT
);

CREATE TABLE IF NOT EXISTS npt_events (
    id           INTEGER PRIMARY KEY,
    machine_id   TEXT NOT NULL,
    machine_raw  TEXT,
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    duration_sec REAL,
    cause_raw    TEXT,
    cause        TEXT,
    added_by     TEXT,
    added_time   TEXT,
    upload_id    INTEGER,
    report_date  TEXT,   -- report day that first brought this event in (used by Replace / Delete day)
    UNIQUE (machine_id, start_time, end_time)
);
CREATE INDEX IF NOT EXISTS ix_npt_start ON npt_events (start_time);
CREATE INDEX IF NOT EXISTS ix_npt_report_date ON npt_events (report_date);

CREATE TABLE IF NOT EXISTS rejection (
    id          INTEGER PRIMARY KEY,
    report_date TEXT NOT NULL,
    machine_id  TEXT NOT NULL,
    machine_raw TEXT,
    item_name   TEXT,
    item_code   TEXT,
    wip_name    TEXT,
    qty_raw     REAL,
    weight_raw  REAL,
    qty_pieces  REAL,
    weight_kg   REAL,
    cause_raw   TEXT,
    cause       TEXT,
    added_by    TEXT,
    added_time  TEXT,
    upload_id   INTEGER
);
CREATE INDEX IF NOT EXISTS ix_rej_date ON rejection (report_date, machine_id);

CREATE TABLE IF NOT EXISTS data_warnings (
    id         INTEGER PRIMARY KEY,
    upload_id  INTEGER,
    warn_date  TEXT,
    machine_id TEXT,
    kind       TEXT,
    message    TEXT
);
"""


class DatabaseUnavailable(Exception):
    """The local database could not be opened or written to."""


# numpy numbers -> plain Python numbers (pandas hands these over; sqlite3 only accepts plain types)
for _t in (np.int8, np.int16, np.int32, np.int64, np.uint8, np.uint16, np.uint32, np.uint64):
    sqlite3.register_adapter(_t, int)
sqlite3.register_adapter(np.float64, float)
sqlite3.register_adapter(np.bool_, int)


# ---------------------------------------------------------------- connection
_conn = None
_conn_lock = threading.RLock()
_conn_path = None       # the path/":memory:" the current _conn was actually opened with


def _open(path):
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")       # lets reads and writes overlap better on a real file
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _get_conn():
    """One connection for the whole app's lifetime (a fresh :memory: connection on every call would
    throw its data away, so this must be a singleton, not opened-and-closed per query)."""
    global _conn, _conn_path, IN_MEMORY, DB_PATH
    if _conn is not None:
        return _conn
    with _conn_lock:
        if _conn is not None:
            return _conn
        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = _open(DB_PATH)
            conn.execute("SELECT 1")                      # make sure the file is actually usable, not just openable
            IN_MEMORY = False
            _conn_path = DB_PATH
        except Exception:
            conn = _open(":memory:")
            IN_MEMORY = True
            _conn_path = ":memory:"
        _conn = conn
        return _conn


def reset_pool():
    """Close and forget the connection (used by tests, and to pick up a new KPI_DB_PATH)."""
    global _conn, _init_done
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = None
        _init_done = False
    bump()


_version = 0
_cache = {}
_cache_lock = threading.Lock()


def bump():
    """Forget every cached read. Called after every write."""
    global _version
    with _cache_lock:
        _version += 1
        _cache.clear()


def run(fn, write=False):
    """Run fn(cursor) in ONE transaction, holding the single shared connection's lock for its
    whole duration. Commits if it finishes, rolls everything back if it fails."""
    conn = _get_conn()
    with _conn_lock:
        try:
            cur = conn.cursor()
            if write:
                cur.execute("BEGIN IMMEDIATE")
            result = fn(cur)
            conn.commit()
            return result
        except sqlite3.Error as e:
            try:
                conn.rollback()
            except Exception:
                pass
            raise DatabaseUnavailable(f"Local database error: {e}" + ("  Nothing was saved." if write else ""))
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            if write:
                bump()


# ---------------------------------------------------------------- simple helpers used everywhere
def execute(sql, params=(), returning=False):
    """One write statement. returning=True: the new row's id (cur.lastrowid) is returned."""
    def _x(cur):
        cur.execute(sql, tuple(params))
        return cur.lastrowid if returning else cur.rowcount
    return run(_x, write=True)


def query_rows(sql, params=()):
    """(column names, list of row tuples). Never cached."""
    def _q(cur):
        cur.execute(sql, tuple(params))
        return [d[0] for d in cur.description], cur.fetchall()
    return run(_q)


def query_df(sql, params=(), cache=True):
    """SELECT -> DataFrame. Identical repeated reads within 30 s are answered from memory,
    but any write made by the app clears that memory first, so screens never show old data."""
    params = tuple(params) if params is not None else ()
    key = (DB_PATH, sql, params)
    try:
        hash(key)
    except TypeError:
        cache = False
    if cache:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and hit[0] == _version and time.time() - hit[1] < CACHE_SECONDS:
                return hit[2].copy()
    ver = _version
    cols, rows = query_rows(sql, params)
    df = pd.DataFrame.from_records(rows, columns=cols, coerce_float=True)
    if cache:
        with _cache_lock:
            if len(_cache) > 300:
                _cache.clear()
            if ver == _version:
                _cache[key] = (ver, time.time(), df)
    return df.copy()


# ---------------------------------------------------------------- start-up
_init_lock = threading.Lock()
_init_done = False


def _create_all(cur):
    cur.executescript(SCHEMA_SQL)
    ensure_defaults(cur)
    backfill_npt_report_date(cur)


def ensure_defaults(cur):
    cur.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                    list(DEFAULT_SETTINGS.items()))


def backfill_npt_report_date(cur):
    cur.execute("UPDATE npt_events SET report_date = substr(start_time, 1, 10) WHERE report_date IS NULL")


def init_db(force=False):
    """Create the tables if they do not exist. Runs once per app start (not on every click)."""
    global _init_done
    if _init_done and not force:
        return
    with _init_lock:
        if _init_done and not force:
            return
        run(_create_all, write=True)
        _init_done = True


def check_connection():
    """(ok, message) - used at start-up so one clear banner is shown instead of many errors."""
    try:
        run(lambda cur: cur.execute("SELECT 1"))
        where = "an in-memory database (nothing persists after this app restarts)" if IN_MEMORY else DB_PATH
        return True, f"Connected to the local database: {where}."
    except DatabaseUnavailable as e:
        return False, str(e)


def get_settings():
    init_db()
    df = query_df("SELECT key, value FROM settings")
    s = dict(DEFAULT_SETTINGS)
    s.update(dict(zip(df["key"], df["value"])))
    return s


def set_setting(key, value):
    execute("INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def date_bounds():
    """Earliest / latest date that has any data."""
    init_db()
    df = query_df("""
        SELECT MIN(d) AS lo, MAX(d) AS hi FROM (
            SELECT substr(start_time,1,10) AS d FROM npt_events
            UNION ALL SELECT report_date FROM rejection)""")
    lo, hi = df.iloc[0]["lo"], df.iloc[0]["hi"]
    if lo is None:
        return None, None
    return pd.to_datetime(lo).date(), pd.to_datetime(hi).date()
