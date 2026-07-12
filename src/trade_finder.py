"""Trade finder: propose mutually beneficial trades with other franchises.

A proposal sends one of my players from a position I'm deep at (above league
median) that the other team is thin at, for one of their players at a position
they're deep at and I'm thin at. Both sides address a need; value stays inside
the fair/lean band so it's actually sendable.

My most valuable player at each position is never offered (depth comes from
the guys behind the stud, not the stud).
"""
from src.config import MFL_FRANCHISE_ID, LOPSIDED_THRESHOLD
from src import mfl_api
from src.roster import franchise_positional_value, league_median_by_position
from src.value_engine import get_value_map

MAX_PROPOSALS = 5
# I shouldn't lose more than this fraction of package value on a "beneficial" deal.
MY_MIN_NET_PCT = -0.05


def _rosters_by_franchise() -> dict[str, list[str]]:
    out = {}
    for fr in mfl_api.get_rosters():
        players = fr.get("player", [])
        if isinstance(players, dict):
            players = [players]
        out[fr.get("id", "")] = [p.get("id", "") for p in players]
    return out


def find_trades(franchise_id: str = MFL_FRANCHISE_ID,
                value_map: dict | None = None) -> list[dict]:
    """Top mutually-beneficial 1-for-1 proposals, best first."""
    if value_map is None:
        value_map = get_value_map()

    rosters = _rosters_by_franchise()
    fv = franchise_positional_value(value_map)
    medians = league_median_by_position(fv)

    def surplus(fid: str) -> set[str]:
        return {pos for pos, med in medians.items()
                if fv.get(fid, {}).get(pos, 0.0) > med}

    def thin(fid: str) -> set[str]:
        return {pos for pos, med in medians.items()
                if fv.get(fid, {}).get(pos, 0.0) < med}

    def tradeable(fid: str, positions: set[str]) -> list[dict]:
        """Players at the given positions, minus the best one per position."""
        by_pos: dict[str, list[dict]] = {}
        for pid in rosters.get(fid, []):
            info = value_map.get(pid)
            if info and info["position"] in positions and info["dynasty_value"] > 0:
                by_pos.setdefault(info["position"], []).append(
                    {"mfl_id": pid, **info})
        out = []
        for pos, players in by_pos.items():
            players.sort(key=lambda p: p["dynasty_value"], reverse=True)
            out.extend(players[1:])  # keep the stud
        return out

    my_surplus, my_thin = surplus(franchise_id), thin(franchise_id)
    proposals = []

    for other_id in rosters:
        if other_id == franchise_id:
            continue
        their_surplus, their_thin = surplus(other_id), thin(other_id)

        give_pool = tradeable(franchise_id, my_surplus & their_thin)
        get_pool = tradeable(other_id, their_surplus & my_thin)

        for give in give_pool:
            for get in get_pool:
                bigger = max(give["dynasty_value"], get["dynasty_value"])
                if bigger <= 0:
                    continue
                net = get["dynasty_value"] - give["dynasty_value"]
                gap_pct = abs(net) / bigger
                if gap_pct > LOPSIDED_THRESHOLD:
                    continue  # they'd never take it / I shouldn't send it
                if net / bigger < MY_MIN_NET_PCT:
                    continue  # too much value bleed on my side
                proposals.append({
                    "other_franchise": other_id,
                    "give": give, "get": get,
                    "net": net, "gap_pct": gap_pct,
                    "fills_my": get["position"], "fills_their": give["position"],
                    "salary_delta": get["salary"] - give["salary"],
                })

    # best: biggest net gain for me, tighter gap as tiebreak
    proposals.sort(key=lambda p: (-p["net"], p["gap_pct"]))

    # variety: at most 2 proposals per franchise, no repeated give/get player
    seen_fr: dict[str, int] = {}
    seen_players: set[str] = set()
    out = []
    for p in proposals:
        fid = p["other_franchise"]
        key_players = {p["give"]["mfl_id"], p["get"]["mfl_id"]}
        if seen_fr.get(fid, 0) >= 2 or key_players & seen_players:
            continue
        seen_fr[fid] = seen_fr.get(fid, 0) + 1
        seen_players |= key_players
        out.append(p)
        if len(out) >= MAX_PROPOSALS:
            break
    return out
