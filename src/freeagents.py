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


# Sleeper marks retired/practice-squad players with a non-Active status; a
# stale depth-chart row would otherwise surface them as waiver targets.
_ACTIVE_STATUSES = {"", "ACTIVE"}


def starting_free_agents(
    position: str | None = None,
    max_depth: int = _STARTER_DEPTH,
    season: int | None = None,
    week: int | None = None,
    value_map: dict | None = None,
    require_both_sources: bool = False,
    use_espn: bool = True,
) -> list[dict]:
    """Free agents who start for their NFL team (depth chart order <= max_depth).

    These are the real waiver-wire finds: someone nobody in the league rosters
    who is nonetheless first on an NFL depth chart. Depth comes from Sleeper
    and ESPN combined (see depth_chart.py); `require_both_sources` keeps only
    players both agree are starting. Sorted by season projection (unprojected
    players last, so preseason still returns results).

    Returns [{mfl_id, name, position, team, depth_slot, depth_order, sources,
              sleeper_order, espn_order, status, injury, season_pts, week_pts,
              salary, dynasty_value}].
    """
    from src.depth_chart import combined_depth

    if value_map is None:
        value_map = get_value_map()

    meta = _fa_meta()
    depth = combined_depth(season, use_espn=use_espn)

    season_proj = get_projected_points(season, None) if season else {}
    week_proj = get_projected_points(season, week) if season and week else {}

    rows = []
    for pid, m in meta.items():
        if position and m["position"].upper() != position.upper():
            continue
        d = depth.get(pid)
        if not d or d["order"] is None or d["order"] > max_depth:
            continue
        if require_both_sources and d["sources"] < 2:
            continue
        # Retired / practice-squad players aren't starting for anyone.
        if d["status"].upper() not in _ACTIVE_STATUSES:
            continue
        # Neither is a player with no NFL team ("FA" = not on a roster).
        nfl_team = d["team"] or m["team"]
        if nfl_team in ("", "FA"):
            continue

        sp = season_proj.get(pid)
        wp = week_proj.get(pid)
        info = value_map.get(pid, {})
        rows.append({
            "mfl_id": pid,
            "name": m["name"],
            "position": m["position"],
            "team": nfl_team,
            "depth_slot": d["slot"],
            "depth_order": d["order"],
            "sources": d["sources"],
            "sleeper_order": d["sleeper_order"],
            "espn_order": d["espn_order"],
            "status": d["status"],
            "injury": d["injury"],
            "season_pts": sp["points"] if sp else None,
            "week_pts": wp["points"] if wp else None,
            "salary": info.get("salary", 0.0),
            "dynasty_value": info.get("dynasty_value", 0.0),
        })

    # Projection leads (that's what makes a player worth adding); source
    # corroboration only breaks ties among otherwise equal candidates.
    rows.sort(key=lambda r: (r["season_pts"] is None,
                             -(r["season_pts"] or 0),
                             -r["sources"],
                             -r["dynasty_value"]))
    return rows
