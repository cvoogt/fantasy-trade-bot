"""Multi-source projections converted to league scoring.

Sources (all free, no keys):
  - Sleeper: weekly + season projections, every position INCLUDING full IDP
    stat lines (tackles, sacks, INTs). The backbone.
  - ESPN (unofficial fantasy API): season + weekly projections for offense.
    Second opinion, blended 50/50 with Sleeper where both project a player.

Each source's raw stat line is scored under THIS league's MFL rules
(src/scoring.py), then blended at the points level. Results are cached in
SQLite (proj_points) with a source count; the bot refreshes every 6 hours,
so projections track injury news and depth-chart moves through the season.
"""
import json
import time
from datetime import datetime, timezone

import requests

from src import mfl_api
from src.db import get_conn
from src.scoring import fetch_rules, project_points
from src.sleeper_api import get_projections as sleeper_week_projections
from src.sleeper_xwalk import get_sleeper_map

# ESPN statId -> Sleeper-style stat key (core offense; IDP/K stay Sleeper-only)
_ESPN_STATS = {
    "3": "pass_yd", "4": "pass_td", "20": "pass_int",
    "24": "rush_yd", "25": "rush_td",
    "42": "rec_yd", "43": "rec_td", "53": "rec",
    "72": "fum_lost",
}


def _sleeper_season(season: int) -> dict[str, dict]:
    resp = requests.get(
        f"https://api.sleeper.app/v1/projections/nfl/regular/{season}", timeout=60)
    resp.raise_for_status()
    return resp.json()


def _espn_rows(season: int) -> list[dict]:
    url = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
           f"seasons/{season}/segments/0/leaguedefaults/3")
    headers = {"X-Fantasy-Filter": json.dumps({"players": {
        "limit": 1200,
        "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
    }})}
    resp = requests.get(url, params={"view": "kona_player_info"},
                        headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json().get("players", [])


def _espn_projection(row: dict, season: int, week: int | None) -> dict | None:
    """Extract the projected stat line (normalized keys) from an ESPN row."""
    for st in row.get("player", {}).get("stats", []):
        if st.get("statSourceId") != 1 or st.get("seasonId") != season:
            continue
        target_period = week if week is not None else 0
        if st.get("scoringPeriodId") != target_period:
            continue
        stats = st.get("stats") or {}
        out = {}
        for espn_id, key in _ESPN_STATS.items():
            v = stats.get(espn_id)
            if v:
                out[key] = float(v)
        return out or None
    return None


def _espn_by_mfl(season: int, week: int | None) -> dict[str, float]:
    """{mfl_id: espn league-scored points} for offense."""
    espn_to_mfl = {}
    for p in mfl_api.get_players():
        if p.get("espn_id"):
            espn_to_mfl[str(p["espn_id"])] = p["id"]

    rules = fetch_rules()
    out = {}
    try:
        rows = _espn_rows(season)
    except Exception:
        return out
    for row in rows:
        espn_id = str(row.get("id", ""))
        mfl_id = espn_to_mfl.get(espn_id)
        if not mfl_id:
            continue
        proj = _espn_projection(row, season, week)
        if proj:
            out[mfl_id] = project_points(proj, rules)
    return out


def refresh_projections(season: int, week: int | None = None) -> int:
    """Fetch all sources, blend, store. scope = 'season' or 'week-N'.
    Returns number of players stored."""
    scope = f"week-{week}" if week is not None else "season"
    rules = fetch_rules()
    smap = get_sleeper_map()  # mfl_id -> sleeper_id

    if week is not None:
        sleeper = sleeper_week_projections(season, week)
    else:
        sleeper = _sleeper_season(season)

    sleeper_pts: dict[str, float] = {}
    for mfl_id, sid in smap.items():
        row = sleeper.get(sid)
        if not row:
            continue
        pts = project_points(row, rules)
        if pts > 0:
            sleeper_pts[mfl_id] = pts

    espn_pts = _espn_by_mfl(season, week)

    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM proj_points WHERE season=? AND scope=?", (season, scope))
    n = 0
    for mfl_id in set(sleeper_pts) | set(espn_pts):
        vals = [v for v in (sleeper_pts.get(mfl_id), espn_pts.get(mfl_id))
                if v is not None]
        blended = sum(vals) / len(vals)
        conn.execute(
            """INSERT INTO proj_points
               (season, scope, mfl_id, points, sources, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (season, scope, mfl_id, round(blended, 2), len(vals), now),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def get_projected_points(season: int, week: int | None = None,
                         max_age_hours: float = 12.0) -> dict[str, dict]:
    """{mfl_id: {'points', 'sources', 'updated_at'}} from cache; refreshes
    automatically if the scope is empty or stale."""
    scope = f"week-{week}" if week is not None else "season"
    conn = get_conn()
    rows = conn.execute(
        "SELECT mfl_id, points, sources, updated_at FROM proj_points "
        "WHERE season=? AND scope=?", (season, scope)).fetchall()
    conn.close()

    stale = True
    if rows:
        newest = max(r["updated_at"] for r in rows)
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(newest)).total_seconds() / 3600
        stale = age > max_age_hours
    if stale:
        try:
            refresh_projections(season, week)
            conn = get_conn()
            rows = conn.execute(
                "SELECT mfl_id, points, sources, updated_at FROM proj_points "
                "WHERE season=? AND scope=?", (season, scope)).fetchall()
            conn.close()
        except Exception:
            pass  # serve stale data over no data
    return {r["mfl_id"]: {"points": r["points"], "sources": r["sources"],
                          "updated_at": r["updated_at"]} for r in rows}
