"""Salary-cap analysis: team salary breakdown and recommended cuts.

Works for any franchise (defaults to mine elsewhere). Values come from the
augmented value map (offense + IDP), so cut advice sees the whole roster.
"""
from src import mfl_api
from src.roster import group_of
from src.value_engine import get_value_map


def cap_amount() -> float:
    try:
        return float(mfl_api.get_league().get("salaryCapAmount") or 0)
    except Exception:
        return 0.0


def _salaries() -> dict[str, float]:
    out = {}
    for p in mfl_api.get_salaries():
        try:
            out[p.get("id", "")] = float(p.get("salary") or 0)
        except ValueError:
            pass
    return out


def team_salary_summary(fid: str, value_map: dict | None = None) -> dict:
    """{'players', 'total_salary', 'cap', 'cap_space', 'by_group',
        'top_contracts', 'best_value', 'worst_value', 'league_rank',
        'league_totals'}"""
    if value_map is None:
        value_map = get_value_map()
    salaries = _salaries()
    mfl_names = {p["id"]: p for p in mfl_api.get_players()}

    rosters: dict[str, list[str]] = {}
    for fr in mfl_api.get_rosters():
        rlist = fr.get("player", [])
        if isinstance(rlist, dict):
            rlist = [rlist]
        rosters[fr.get("id", "")] = [p.get("id", "") for p in rlist]

    def build(fid_: str) -> tuple[list[dict], float]:
        players = []
        for pid in rosters.get(fid_, []):
            meta = mfl_names.get(pid, {})
            info = value_map.get(pid, {})
            sal = salaries.get(pid, 0.0)
            players.append({
                "mfl_id": pid,
                "name": meta.get("name", pid),
                "position": meta.get("position", "?"),
                "salary": sal,
                "dynasty_value": float(info.get("dynasty_value", 0.0)),
                "vor": float(info.get("vor", 0.0)),
            })
        return players, sum(p["salary"] for p in players)

    players, total = build(fid)
    league_totals = {ofid: build(ofid)[1] for ofid in rosters}
    rank = sorted(league_totals, key=lambda f: league_totals[f], reverse=True).index(fid) + 1

    by_group: dict[str, float] = {}
    for p in players:
        g = group_of(p["position"])
        by_group[g] = by_group.get(g, 0.0) + p["salary"]

    paid = [p for p in players if p["salary"] > 0]
    valued = [p for p in paid if p["dynasty_value"] > 0]
    cap = cap_amount()
    return {
        "players": players,
        "total_salary": total,
        "cap": cap,
        "cap_space": cap - total if cap else None,
        "by_group": by_group,
        "top_contracts": sorted(paid, key=lambda p: -p["salary"])[:5],
        "best_value": sorted(valued, key=lambda p: -(p["dynasty_value"] / p["salary"]))[:3],
        "worst_value": sorted(valued, key=lambda p: p["dynasty_value"] / p["salary"])[:3],
        "league_rank": rank,
        "league_totals": league_totals,
    }


# Lineup group minimums — never recommend cutting a group below what the
# starting lineup requires (a $0-value kicker is still your only kicker).
_GROUP_MIN_STARTERS = {"QB": 1, "RB": 1, "WR": 2, "TE": 1, "PK": 1,
                       "DT+DE": 3, "LB": 3, "CB+S": 3}


def select_cuts(players: list[dict], top_n: int = 8) -> list[dict]:
    """Pure cut-selection over a roster list ({name, position, salary,
    dynasty_value, vor}). Excluded: startable players (positive VOR),
    minimum-type deals (bottom salary quartile), and any cut that would
    leave a lineup group without enough bodies to start plus one cushion."""
    paid = [p for p in players if p["salary"] > 0]
    if not paid:
        return []
    floor = sorted(p["salary"] for p in paid)[len(paid) // 4]

    group_counts: dict[str, int] = {}
    for p in players:
        g = group_of(p["position"])
        group_counts[g] = group_counts.get(g, 0) + 1

    cands = [p for p in paid if p["salary"] > floor and p["vor"] <= 0]
    for p in cands:
        p["salary_per_value"] = p["salary"] / max(p["dynasty_value"], 1.0)
    cands.sort(key=lambda p: -p["salary_per_value"])

    out, cuts_per_group = [], {}
    for p in cands:
        g = group_of(p["position"])
        remaining = group_counts.get(g, 0) - cuts_per_group.get(g, 0)
        if remaining - 1 <= _GROUP_MIN_STARTERS.get(g, 0):
            continue
        cuts_per_group[g] = cuts_per_group.get(g, 0) + 1
        out.append(p)
        if len(out) >= top_n:
            break
    return out


def cut_candidates(fid: str, value_map: dict | None = None, top_n: int = 8) -> list[dict]:
    """Cut suggestions for a franchise (see select_cuts for the rules)."""
    if value_map is None:
        value_map = get_value_map()
    summary = team_salary_summary(fid, value_map)
    return select_cuts(summary["players"], top_n)
