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
            side1_gave TEXT,
            side2_gave TEXT,
            value_delta REAL,
            value_delta_pct REAL,
            favored INTEGER,
            verdict TEXT,
            lopsided INTEGER,
            scanned_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sleeper_players (
            sleeper_id TEXT PRIMARY KEY,
            name TEXT,
            position TEXT,
            team TEXT,
            espn_id TEXT,
            rotowire_id TEXT,
            sportradar_id TEXT,
            stats_id TEXT,
            status TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sleeper_crosswalk (
            mfl_id TEXT PRIMARY KEY,
            sleeper_id TEXT,
            mfl_name TEXT,
            sleeper_name TEXT,
            position TEXT,
            join_key TEXT,
            manual_override INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS draft_pings (
            round INTEGER,
            pick INTEGER,
            PRIMARY KEY (round, pick)
        );

        CREATE TABLE IF NOT EXISTS live_stat_snapshots (
            season INTEGER,
            week INTEGER,
            sleeper_id TEXT,
            stat TEXT,
            count REAL,
            updated_at TEXT,
            PRIMARY KEY (season, week, sleeper_id, stat)
        );
    """)
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn: sqlite3.Connection):
    """Add any columns missing from older scanned_trades schemas."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(scanned_trades)")}
    wanted = {
        "side1_gave": "TEXT", "side2_gave": "TEXT", "value_delta_pct": "REAL",
        "favored": "INTEGER", "lopsided": "INTEGER",
    }
    for col, coltype in wanted.items():
        if col not in have:
            conn.execute(f"ALTER TABLE scanned_trades ADD COLUMN {col} {coltype}")
