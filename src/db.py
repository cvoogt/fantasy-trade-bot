import sqlite3
from src.config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS crosswalk (
            mfl_id TEXT PRIMARY KEY,
            mfl_name TEXT,
            fc_name TEXT,
            position TEXT,
            team TEXT,
            match_score REAL,
            manual_override INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS fantasycalc_cache (
            fc_name TEXT,
            position TEXT,
            team TEXT,
            dynasty_value REAL,
            overall_rank INTEGER,
            fetched_at TEXT,
            PRIMARY KEY (fc_name, position)
        );

        CREATE TABLE IF NOT EXISTS scanned_trades (
            txn_id TEXT PRIMARY KEY,
            timestamp INTEGER,
            franchise1 TEXT,
            franchise2 TEXT,
            verdict TEXT,
            value_delta REAL,
            scanned_at TEXT
        );
    """)
    conn.commit()
    conn.close()
