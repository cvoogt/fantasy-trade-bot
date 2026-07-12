"""Lineup advisor: optimal starting lineup from Sleeper weekly projections.

League 68447 is an IDP league — lineup rules come as position groups with
min-max ranges (e.g. WR 2-4, DT+DE 3-4) plus side totals (8 offensive
starters, 11 defensive). The solver is greedy: fill every group minimum
with the best projected players, then spend remaining flex slots on the
best players left wherever a group still has room.
"""
from dataclasses import dataclass, field

from src import mfl_api
from src.sleeper_xwalk import get_sleeper_map
from src.sleeper_api import get_projections, get_nfl_state
from src.config import MFL_FRANCHISE_ID

IOP_POSITIONS = {"QB", "RB", "WR", "TE", "PK"}


@dataclass
class LineupRules:
    # groups: list of (positions tuple, min, max)
    groups: list[tuple[tuple[str, ...], int, int]]
    iop_total: int
    idp_total: int

    @property
    def total(self) -> int:
        return self.iop_total + self.idp_total


def parse_lineup_rules(league: dict | None = None) -> LineupRules:
    if league is None:
        league = mfl_api.get_league()
    starters = league.get("starters", {})
    groups = []
    for pos in starters.get("position", []):
        names = tuple(pos["name"].split("+"))
        limit = pos["limit"]
        if "-" in limit:
            lo, hi = (int(x) for x in limit.split("-"))
        else:
            lo = hi = int(limit)
        groups.append((names, lo, hi))
    return LineupRules(
        groups=groups,
        iop_total=int(starters.get("iop_starters", 0) or 0),
        idp_total=int(starters.get("idp_starters", 0) or 0),
    )


def solve_lineup(players: list[dict], rules: LineupRules) -> list[dict]:
    """Pick the optimal legal lineup.

    `players`: [{'mfl_id', 'name', 'position', 'proj'}]. Returns the chosen
    players, each annotated with the group it fills.
    """
    def group_of(position: str):
        for g in rules.groups:
            if position in g[0]:
                return g
        return None

    def side(position: str) -> str:
        return "iop" if position in IOP_POSITIONS else "idp"

    ranked = sorted(
        (p for p in players if group_of(p["position"]) is not None),
        key=lambda p: p["proj"], reverse=True,
    )

    chosen: list[dict] = []
    group_counts: dict[tuple, int] = {g[0]: 0 for g in rules.groups}
    side_counts = {"iop": 0, "idp": 0}
    side_limits = {"iop": rules.iop_total, "idp": rules.idp_total}
    taken: set[str] = set()

    # Pass 1: satisfy every group minimum with the best available.
    for names, lo, _hi in rules.groups:
        need = lo
        for p in ranked:
            if need == 0:
                break
            if p["mfl_id"] in taken or p["position"] not in names:
                continue
            chosen.append({**p, "slot": "+".join(names)})
            taken.add(p["mfl_id"])
            group_counts[names] += 1
            side_counts[side(p["position"])] += 1
            need -= 1

    # Pass 2: spend remaining flex slots on best projections wherever legal.
    for p in ranked:
        if p["mfl_id"] in taken:
            continue
        g = group_of(p["position"])
        names, _lo, hi = g
        s = side(p["position"])
        if group_counts[names] >= hi or side_counts[s] >= side_limits[s]:
            continue
        chosen.append({**p, "slot": "+".join(names)})
        taken.add(p["mfl_id"])
        group_counts[names] += 1
        side_counts[s] += 1

    return chosen


def _current_starters(franchise_id: str, week=None) -> set[str]:
    """MFL ids currently submitted as starters, empty if lineups not set."""
    try:
        wr = mfl_api.get_weekly_results(week)
    except Exception:
        return set()
    franchises = wr.get("franchise", [])
    if isinstance(franchises, dict):
        franchises = [franchises]
    for fr in franchises:
        if fr.get("id") != franchise_id:
            continue
        players = fr.get("player", [])
        if isinstance(players, dict):
            players = [players]
        return {p.get("id", "") for p in players
                if p.get("status") == "starter" or p.get("shouldStart") == "1"}
    return set()


def lineup_advice(
    franchise_id: str = MFL_FRANCHISE_ID,
    season: int | None = None,
    week: int | None = None,
) -> dict:
    """Optimal lineup + diff vs currently submitted starters.

    Returns {'optimal': [...], 'current': set, 'start': [...], 'sit': [...],
             'season': int, 'week': int, 'no_projection': [...]}."""
    if season is None or week is None:
        state = get_nfl_state()
        season = season or int(state.get("season"))
        week = week or max(int(state.get("week") or 1), 1)

    smap = get_sleeper_map()
    projections = get_projections(season, week)
    mfl_names = {p["id"]: p for p in mfl_api.get_players()}

    # my roster
    my_ids: list[str] = []
    for fr in mfl_api.get_rosters():
        if fr.get("id") == franchise_id:
            players = fr.get("player", [])
            if isinstance(players, dict):
                players = [players]
            my_ids = [p.get("id", "") for p in players]
            break

    players, no_projection = [], []
    for pid in my_ids:
        meta = mfl_names.get(pid, {})
        sid = smap.get(pid)
        proj = (projections.get(sid) or {}).get("pts_ppr") if sid else None
        entry = {
            "mfl_id": pid,
            "name": meta.get("name", pid),
            "position": meta.get("position", ""),
            "proj": float(proj) if proj else 0.0,
        }
        players.append(entry)
        if proj is None:
            no_projection.append(entry)

    rules = parse_lineup_rules()
    optimal = solve_lineup(players, rules)
    optimal_ids = {p["mfl_id"] for p in optimal}
    current = _current_starters(franchise_id, week)

    start = [p for p in optimal if current and p["mfl_id"] not in current]
    sit = [p for p in players if current and p["mfl_id"] in current
           and p["mfl_id"] not in optimal_ids]

    return {
        "optimal": optimal, "current": current, "start": start, "sit": sit,
        "season": season, "week": week, "no_projection": no_projection,
    }
