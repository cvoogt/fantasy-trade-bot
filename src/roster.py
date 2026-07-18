"""Roster depth helpers: positional value per franchise vs league median.

Depth is measured per lineup GROUP, not raw position — this league starts
DT+DE and CB+S as combined slots, so a roster with zero DTs but five DEs
is deep, not thin, on the defensive line."""
import statistics
from src import mfl_api
from src.value_engine import get_value_map

_GROUP_OF = {"DT": "DT+DE", "DE": "DT+DE", "CB": "CB+S", "S": "CB+S"}


def group_of(position: str) -> str:
    """Lineup group for a position ('DT' -> 'DT+DE', 'RB' -> 'RB')."""
    return _GROUP_OF.get(position, position)


def _player_ids(roster_entry: dict) -> list[str]:
    players = roster_entry.get("player", [])
    if isinstance(players, dict):
        players = [players]
    return [p.get("id", "") for p in players]


def franchise_positional_value(value_map: dict | None = None) -> dict[str, dict[str, float]]:
    """Return {franchise_id: {position: total_dynasty_value}}."""
    if value_map is None:
        value_map = get_value_map()
    rosters = mfl_api.get_rosters()
    out: dict[str, dict[str, float]] = {}
    for fr in rosters:
        fid = fr.get("id", "")
        pos_totals: dict[str, float] = {}
        for pid in _player_ids(fr):
            info = value_map.get(pid)
            if not info:
                continue
            g = group_of(info["position"])
            pos_totals[g] = pos_totals.get(g, 0.0) + info["dynasty_value"]
        out[fid] = pos_totals
    return out


def league_median_by_position(
    franchise_values: dict[str, dict[str, float]] | None = None,
    value_map: dict | None = None,
) -> dict[str, float]:
    """Median positional dynasty value across all franchises."""
    if franchise_values is None:
        franchise_values = franchise_positional_value(value_map)
    positions: dict[str, list[float]] = {}
    for pos_totals in franchise_values.values():
        for pos, val in pos_totals.items():
            positions.setdefault(pos, []).append(val)
    return {pos: statistics.median(vals) for pos, vals in positions.items()}


def all_thin_positions(value_map: dict | None = None) -> dict[str, set[str]]:
    """{franchise_id: positions below league median}. Computed in one pass."""
    fv = franchise_positional_value(value_map)
    medians = league_median_by_position(fv)
    return {
        fid: {pos for pos, med in medians.items() if pos_totals.get(pos, 0.0) < med}
        for fid, pos_totals in fv.items()
    }


def thin_positions(franchise_id: str, value_map: dict | None = None) -> set[str]:
    """Positions where this franchise is below the league median."""
    return all_thin_positions(value_map).get(franchise_id, set())
