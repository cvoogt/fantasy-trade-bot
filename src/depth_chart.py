"""NFL depth charts from multiple sources.

Sleeper ships `depth_chart_order` in its players dump — cheap, already cached,
but it's one vendor's editorial call and can go stale (notably in the
off-season). ESPN publishes real depth charts through its core API, keyed by
ESPN athlete id, which MFL already carries as `espn_id` — so the two can be
cross-checked per player.

combined_depth() merges them into {mfl_id: {...}} carrying each source's slot
and rank plus a `sources` count, so callers can prefer players both sources
agree are starting. Every network path fails closed: if ESPN is unreachable or
changes shape, the Sleeper-only view is still returned.
"""
import re
import time
from datetime import datetime, timezone

import requests

from src.db import get_conn

_ESPN_TEAMS_URL = ("https://site.api.espn.com/apis/site/v2/sports/football/"
                   "nfl/teams")
_ESPN_DEPTH_URL = ("https://sports.core.api.espn.com/v2/sports/football/"
                   "leagues/nfl/seasons/{season}/teams/{team_id}/depthcharts")
_ESPN_TTL_HOURS = 24
_ATHLETE_ID = re.compile(r"/athletes/(\d+)")


# ---------------------------------------------------------------- Sleeper ---

def sleeper_depth() -> dict[str, dict]:
    """{sleeper_id: {order, slot, status, team, injury}} from the players cache."""
    from src.sleeper_api import refresh_players_cache

    def _rows():
        conn = get_conn()
        out = {
            r["sleeper_id"]: {
                "order": r["depth_chart_order"],
                "slot": r["depth_chart_position"] or "",
                "status": r["status"] or "",
                "team": r["team"] or "",
                "injury": r["injury_status"] or "",
            }
            for r in conn.execute(
                "SELECT sleeper_id, depth_chart_order, depth_chart_position, "
                "status, team, injury_status FROM sleeper_players")
        }
        conn.close()
        return out

    rows = _rows()
    # The depth_chart_* columns are newer than the table; an existing install
    # has NULLs until the cache is refreshed.
    if not any(r["order"] is not None for r in rows.values()):
        try:
            refresh_players_cache(force=True)
            rows = _rows()
        except Exception:
            pass
    return rows


# ------------------------------------------------------------------- ESPN ---

def _espn_team_ids() -> list[str]:
    """ESPN numeric team ids, fetched so we don't hardcode a stale list."""
    resp = requests.get(_ESPN_TEAMS_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    ids = []
    for league in data.get("sports", [{}])[0].get("leagues", []):
        for entry in league.get("teams", []):
            tid = entry.get("team", {}).get("id")
            if tid:
                ids.append(str(tid))
    return ids


def _parse_depthchart(payload: dict) -> list[tuple[str, str, int]]:
    """[(espn_athlete_id, slot, rank)] from one team's depthcharts response.

    ESPN nests formations -> positions -> athletes, with the athlete behind a
    `$ref` URL whose trailing id is the ESPN athlete id. Best rank per player
    wins, since a player appears under several formations."""
    best: dict[str, tuple[str, int]] = {}
    for item in payload.get("items", []):
        positions = item.get("positions") or {}
        if not isinstance(positions, dict):
            continue
        for _key, entry in positions.items():
            pos = (entry.get("position") or {})
            slot = (pos.get("abbreviation") or pos.get("name") or "").upper()
            for ath in entry.get("athletes") or []:
                ref = (ath.get("athlete") or {}).get("$ref") or ""
                m = _ATHLETE_ID.search(ref)
                if not m:
                    continue
                try:
                    rank = int(ath.get("rank"))
                except (TypeError, ValueError):
                    continue
                aid = m.group(1)
                if aid not in best or rank < best[aid][1]:
                    best[aid] = (slot, rank)
    return [(aid, slot, rank) for aid, (slot, rank) in best.items()]


def fetch_espn_depth(season: int) -> dict[str, dict]:
    """{espn_athlete_id: {slot, order}} across the league. Network-heavy
    (one request per team), so callers should use the cached accessor."""
    out: dict[str, dict] = {}
    for team_id in _espn_team_ids():
        try:
            resp = requests.get(
                _ESPN_DEPTH_URL.format(season=season, team_id=team_id), timeout=30)
            resp.raise_for_status()
            rows = _parse_depthchart(resp.json())
        except Exception:
            continue  # one bad team shouldn't sink the whole chart
        for aid, slot, rank in rows:
            prev = out.get(aid)
            if prev is None or rank < prev["order"]:
                out[aid] = {"slot": slot, "order": rank}
    return out


def refresh_espn_depth(season: int) -> int:
    """Fetch ESPN depth charts and cache them. Returns rows stored."""
    depth = fetch_espn_depth(season)
    if not depth:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    conn.execute("DELETE FROM espn_depth")
    for aid, d in depth.items():
        conn.execute(
            "INSERT INTO espn_depth (espn_id, slot, depth_order, fetched_at) "
            "VALUES (?, ?, ?, ?)", (aid, d["slot"], d["order"], now))
    conn.commit()
    conn.close()
    return len(depth)


def get_espn_depth(season: int | None = None,
                   max_age_hours: float = _ESPN_TTL_HOURS) -> dict[str, dict]:
    """Cached {espn_athlete_id: {slot, order}}, refreshed when stale.

    Returns whatever is cached (possibly nothing) if the refresh fails."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT espn_id, slot, depth_order, fetched_at FROM espn_depth").fetchall()
    conn.close()

    stale = True
    if rows:
        newest = max(r["fetched_at"] for r in rows)
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(newest)).total_seconds() / 3600
            stale = age > max_age_hours
        except ValueError:
            stale = True

    if stale:
        if season is None:
            try:
                from src.sleeper_api import get_nfl_state
                season = int(get_nfl_state()["season"])
            except Exception:
                season = datetime.now(timezone.utc).year
        try:
            if refresh_espn_depth(season):
                conn = get_conn()
                rows = conn.execute(
                    "SELECT espn_id, slot, depth_order, fetched_at "
                    "FROM espn_depth").fetchall()
                conn.close()
        except Exception:
            pass  # serve stale/empty rather than failing the command

    return {r["espn_id"]: {"slot": r["slot"] or "", "order": r["depth_order"]}
            for r in rows}


# --------------------------------------------------------------- combined ---

_combined_cache: tuple[float, dict] | None = None
_COMBINED_TTL = 3600


def combined_depth(season: int | None = None,
                   use_espn: bool = True) -> dict[str, dict]:
    """{mfl_id: {slot, order, sources, sleeper_order, espn_order, status,
                 team, injury}} merging both sources.

    `order` is the better (lowest) rank the sources agree on; `sources` counts
    how many placed the player on a chart at all, so callers can require
    corroboration. Falls back to Sleeper alone when ESPN is unavailable."""
    global _combined_cache
    if _combined_cache and time.monotonic() - _combined_cache[0] < _COMBINED_TTL:
        return _combined_cache[1]

    from src import mfl_api
    from src.sleeper_xwalk import get_sleeper_map

    smap = get_sleeper_map()                    # mfl_id -> sleeper_id
    sleeper = sleeper_depth()

    espn_to_mfl: dict[str, str] = {}
    for p in mfl_api.get_players():
        if p.get("espn_id"):
            espn_to_mfl[str(p["espn_id"])] = p["id"]

    espn: dict[str, dict] = {}
    if use_espn:
        try:
            raw = get_espn_depth(season)
        except Exception:
            raw = {}
        for aid, d in raw.items():
            mfl_id = espn_to_mfl.get(aid)
            if mfl_id:
                espn[mfl_id] = d

    out: dict[str, dict] = {}
    for mfl_id in set(smap) | set(espn):
        s = sleeper.get(smap.get(mfl_id, ""), {})
        e = espn.get(mfl_id, {})
        s_order = s.get("order")
        e_order = e.get("order")
        orders = [o for o in (s_order, e_order) if o is not None]
        if not orders:
            continue
        out[mfl_id] = {
            "slot": e.get("slot") or s.get("slot") or "",
            "order": min(orders),
            "sources": len(orders),
            "sleeper_order": s_order,
            "espn_order": e_order,
            "status": s.get("status", ""),
            "team": s.get("team", ""),
            "injury": s.get("injury", ""),
        }

    _combined_cache = (time.monotonic(), out)
    return out


def clear_cache():
    """Drop the in-process combined-depth memo (used by tests and /update)."""
    global _combined_cache
    _combined_cache = None
