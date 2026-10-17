"""VERSION 2 (NOT ACTIVE). Table for the Injection Due report. Run PRODUCTION_SQL to activate it."""
PRODUCTION_SQL = """
CREATE TABLE IF NOT EXISTS production (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prod_date TEXT NOT NULL, machine_id TEXT NOT NULL, machine_raw TEXT, mc_sl TEXT, position TEXT,
    order_type TEXT, product_code TEXT, product_name TEXT, color TEXT,
    cavity REAL, cycle_time_sec REAL, unit_weight_kg REAL,
    a_good REAL, a_bad REAL, b_good REAL, b_bad REAL, source_row INTEGER, upload_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_prod_date ON production(prod_date, machine_id);
"""
