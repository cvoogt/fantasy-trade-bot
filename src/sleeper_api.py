"""Sleeper API client. Free, no auth.

The players dump is ~12k players / several MB, so it's cached in SQLite and
refreshed at most once a day. Projections and stats are fetched per
(season, week) on demand — callers decide their own polling cadence.
"""
import requests
from datetime import datetime, timezone, timedelta

from src.db import get_conn

BASE = "https://api.sleeper.app/v1"
_PLAYERS_TTL = timedelta(hours=24)

# Fields worth keeping from the players dump (join keys + display).
_KEEP = ("full_name", "position", "team", "espn_id", "rotowire_id",
         "sportradar_id", "stats_id", "status")


def _fetch_players_dump() -> dict:
    resp = requests.get(f"{BASE}/players/nfl", timeout=60)
    resp.raise_for_status()
    return resp.json()


def refresh_players_cache(force: bool = False) -> int:
    """Refresh the sleeper_players table if stale. Returns row count."""
    conn = get_conn()
    row = conn.execute("SELECT MAX(fetched_at) AS at FROM sleeper_players").fetchone()
    if not force and row["at"]:
        fetched = datetime.fromisoformat(row["at"])
        if datetime.now(timezone.utc) - fetched < _PLAYERS_TTL:
            n = conn.execute("SELECT COUNT(*) AS n FROM sleeper_players").fetchone()["n"]
            conn.close()
            return n

    players = _fetch_players_dump()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM sleeper_players")
    for sid, p in players.items():
        conn.execute(
            """INSERT INTO sleeper_players
               (sleeper_id, name, position, team, espn_id, rotowire_id,
                sportradar_id, stats_id, status, injury_status, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sid,
                p.get("full_name") or "",
                p.get("position") or "",
                p.get("team") or "",
                str(p["espn_id"]) if p.get("espn_id") else None,
                str(p["rotowire_id"]) if p.get("rotowire_id") else None,
                p.get("sportradar_id"),
                str(p["stats_id"]) if p.get("stats_id") else None,
                p.get("status") or "",
                p.get("injury_status") or "",
                now,
            ),
        )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) AS n FROM sleeper_players").fetchone()["n"]
    conn.close()
    return n


def get_cached_players() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM sleeper_players").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_projections(season: int, week: int) -> dict[str, dict]:
    """{sleeper_id: {pts_ppr, pts_std, ...}} weekly projections."""
    resp = requests.get(f"{BASE}/projections/nfl/regular/{season}/{week}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_stats(season: int, week: int) -> dict[str, dict]:
    """{sleeper_id: {rush_td, rec_td, pass_td, ...}} weekly stats.
    Updated near-real-time during live games."""
    resp = requests.get(f"{BASE}/stats/nfl/regular/{season}/{week}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_nfl_state() -> dict:
    """Current NFL season/week per Sleeper (season, week, season_type...)."""
    resp = requests.get(f"{BASE}/state/nfl", timeout=15)
    resp.raise_for_status()
    return resp.json()
