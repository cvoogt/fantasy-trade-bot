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

        CREATE TABLE IF NOT EXISTS proj_points (
            season INTEGER,
            scope TEXT,
            mfl_id TEXT,
            points REAL,
            sources INTEGER,
            updated_at TEXT,
            PRIMARY KEY (season, scope, mfl_id)
        );

        CREATE TABLE IF NOT EXISTS fa_pool (
            mfl_id TEXT PRIMARY KEY
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
    """Add columns missing from older schemas (idempotent)."""
    wanted_by_table = {
        "scanned_trades": {
            "side1_gave": "TEXT", "side2_gave": "TEXT", "value_delta_pct": "REAL",
            "favored": "INTEGER", "lopsided": "INTEGER",
        },
        "sleeper_players": {"injury_status": "TEXT"},
    }
    for table, wanted in wanted_by_table.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, coltype in wanted.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
