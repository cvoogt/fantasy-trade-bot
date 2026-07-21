"""Free agent lookup: top available players by season projection, with
next-week projection and salary (cost to sign) alongside."""
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
