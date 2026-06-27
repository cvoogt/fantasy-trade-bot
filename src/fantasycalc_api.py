import requests
from datetime import datetime, timezone
from src.config import FANTASYCALC_BASE_URL
from src.db import get_conn


def fetch_dynasty_values() -> list[dict]:
    """Fetch current dynasty values from FantasyCalc. 1-QB, non-superflex."""
    resp = requests.get(
        FANTASYCALC_BASE_URL,
        params={"isDynasty": "true", "numQbs": "1"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_and_cache() -> list[dict]:
    """Fetch values and cache in SQLite. Returns the raw list."""
    values = fetch_dynasty_values()
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()

    conn.execute("DELETE FROM fantasycalc_cache")
    for p in values:
        player = p.get("player", {})
        conn.execute(
            """INSERT OR REPLACE INTO fantasycalc_cache
               (fc_name, position, team, dynasty_value, overall_rank, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                player.get("name", ""),
                player.get("position", ""),
                player.get("maybeTeam", ""),
                p.get("value", 0),
                p.get("overallRank", 0),
                now,
            ),
        )
    conn.commit()
    conn.close()
    return values


def get_cached_values() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM fantasycalc_cache").fetchall()
    conn.close()
    return [dict(r) for r in rows]
