"""Free agent lookup: top available players by season projection, with
next-week projection and salary (cost to sign) alongside.

Also finds NFL starters sitting in the league's free-agent pool — players
listed first on their NFL team's depth chart that nobody has rostered."""
from src import mfl_api
from src.projections import get_projected_points
from src.value_engine import get_value_map


def _fa_meta() -> dict[str, dict]:
    """{mfl_id: {name, position, team, draft_year}} for current free agents."""
    fa_ids = {p.get("id", "") for p in mfl_api.get_free_agents()}
    out = {}
    for p in mfl_api.get_players():
        pid = p.get("id", "")
        if pid in fa_ids:
            out[pid] = {
                "name": p.get("name", pid),
                "position": p.get("position", "?"),
                "team": p.get("team", "FA"),
                "draft_year": p.get("draft_year", ""),
            }
    return out


def top_free_agents(
    position: str | None = None,
    rookies: bool | None = None,
    season: int | None = None,
    week: int | None = None,
    top_n: int = 15,
    value_map: dict | None = None,
) -> list[dict]:
    """Top free agents by season projected points.

    rookies: True = rookie-year players only, False = exclude rookies,
             None = no filter.

    Returns list of {mfl_id, name, position, team, season_pts, week_pts, salary}.
    """
    if value_map is None:
        value_map = get_value_map()

    meta = _fa_meta()

    season_proj = get_projected_points(season, None) if season else {}
    week_proj = get_projected_points(season, week) if season and week else {}

    rookie_year = str(season) if season else None

    rows = []
    for pid, m in meta.items():
        if position and m["position"].upper() != position.upper():
            continue
        if rookies is True and m["draft_year"] != rookie_year:
            continue
        if rookies is False and m["draft_year"] == rookie_year:
            continue

        sp = season_proj.get(pid)
        if not sp:
            continue

        wp = week_proj.get(pid)
        info = value_map.get(pid, {})
        rows.append({
            "mfl_id": pid,
            "name": m["name"],
            "position": m["position"],
            "team": m["team"],
            "season_pts": sp["points"],
            "week_pts": wp["points"] if wp else None,
            "salary": info.get("salary", 0.0),
        })

    rows.sort(key=lambda r: r["season_pts"], reverse=True)
    return rows[:top_n]


# Sleeper lists these as depth-chart positions we care about; a player first on
# the chart at one of them is an NFL starter in a fantasy-relevant role.
_STARTER_DEPTH = 1


def _depth_chart() -> dict[str, dict]:
    """{sleeper_id: {depth_chart_order, depth_chart_position, status, team}}."""
    from src.db import get_conn
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
    # depth_chart_* are newer than the table; an existing install has NULLs
    # until the cache is refreshed.
    if not any(r["order"] is not None for r in rows.values()):
        try:
            refresh_players_cache(force=True)
            rows = _rows()
        except Exception:
            pass
    return rows


def starting_free_agents(
    position: str | None = None,
    max_depth: int = _STARTER_DEPTH,
    season: int | None = None,
    week: int | None = None,
    value_map: dict | None = None,
) -> list[dict]:
    """Free agents who start for their NFL team (depth chart order <= max_depth).

    These are the real waiver-wire finds: someone nobody in the league rosters
    who is nonetheless first on an NFL depth chart. Sorted by season projection
    (unprojected players last, so preseason still returns results).

    Returns [{mfl_id, name, position, team, depth_slot, depth_order, status,
              injury, season_pts, week_pts, salary, dynasty_value}].
    """
    from src.sleeper_xwalk import get_sleeper_map

    if value_map is None:
        value_map = get_value_map()

    meta = _fa_meta()
    smap = get_sleeper_map()
    depth = _depth_chart()

    season_proj = get_projected_points(season, None) if season else {}
    week_proj = get_projected_points(season, week) if season and week else {}

    rows = []
    for pid, m in meta.items():
        if position and m["position"].upper() != position.upper():
            continue
        sid = smap.get(pid)
        if not sid:
            continue
        d = depth.get(sid)
        if not d or d["order"] is None or d["order"] > max_depth:
            continue
        # Free agents with no NFL team can't be starting for anyone.
        if not d["team"]:
            continue

        sp = season_proj.get(pid)
        wp = week_proj.get(pid)
        info = value_map.get(pid, {})
        rows.append({
            "mfl_id": pid,
            "name": m["name"],
            "position": m["position"],
            "team": d["team"] or m["team"],
            "depth_slot": d["slot"],
            "depth_order": d["order"],
            "status": d["status"],
            "injury": d["injury"],
            "season_pts": sp["points"] if sp else None,
            "week_pts": wp["points"] if wp else None,
            "salary": info.get("salary", 0.0),
            "dynasty_value": info.get("dynasty_value", 0.0),
        })

    # Projected players first (highest first), then the unprojected.
    rows.sort(key=lambda r: (r["season_pts"] is None,
                             -(r["season_pts"] or 0),
                             -r["dynasty_value"]))
    return rows
