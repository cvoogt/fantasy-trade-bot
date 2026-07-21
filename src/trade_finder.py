"""Trade finder: propose mutually beneficial trades with other franchises.

A proposal sends one of my players from a position I'm deep at (above league
median) that the other team is thin at, for one of their players at a position
they're deep at and I'm thin at. Both sides address a need; value stays inside
the fair/lean band so it's actually sendable.

My most valuable player at each position is never offered (depth comes from
the guys behind the stud, not the stud).
"""
from itertools import combinations

from src.config import MFL_FRANCHISE_ID, LOPSIDED_THRESHOLD
from src import mfl_api
from src.roster import franchise_positional_value, league_median_by_position, group_of
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

    def tradeable(fid: str, groups: set[str]) -> list[dict]:
        """Players in the given lineup groups, minus the best one per group."""
        by_pos: dict[str, list[dict]] = {}
        for pid in rosters.get(fid, []):
            info = value_map.get(pid)
            if info and group_of(info["position"]) in groups and info["dynasty_value"] > 0:
                by_pos.setdefault(group_of(info["position"]), []).append(
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


def find_trades_for_player(
    target_mfl_id: str,
    franchise_id: str = MFL_FRANCHISE_ID,
    value_map: dict | None = None,
    max_offers: int = MAX_PROPOSALS,
) -> dict:
    """Build several distinct packages I could send to acquire one target player.

    Returns:
        {
          "target": {"mfl_id", "name", "position", "dynasty_value", "salary", ...},
          "owner": franchise_id that owns the target (None if free agent),
          "status": "ok" | "free_agent" | "mine" | "no_value",
          "offers": [{"give": [players], "give_value", "net", "gap_pct",
                      "salary_delta", "fills_their": set[str]}, ...],
        }
    """
    if value_map is None:
        value_map = get_value_map()

    target = value_map.get(target_mfl_id)
    rosters = _rosters_by_franchise()

    owner = next((fid for fid, pids in rosters.items()
                  if target_mfl_id in pids), None)

    result = {"target": {"mfl_id": target_mfl_id, **(target or {})},
              "owner": owner, "offers": []}

    if not target or target.get("dynasty_value", 0) <= 0:
        result["status"] = "no_value"
        return result
    if owner == franchise_id:
        result["status"] = "mine"
        return result
    if owner is None:
        result["status"] = "free_agent"
        return result
    result["status"] = "ok"

    target_val = target["dynasty_value"]

    # What the owner is thin at — packages that fill a need are easier to sell.
    fv = franchise_positional_value(value_map)
    medians = league_median_by_position(fv)
    their_thin = {pos for pos, med in medians.items()
                  if fv.get(owner, {}).get(pos, 0.0) < med}

    # My assets with real value (exclude the target itself, just in case).
    my_assets = [
        {"mfl_id": pid, **value_map[pid]}
        for pid in rosters.get(franchise_id, [])
        if pid in value_map and value_map[pid]["dynasty_value"] > 0
        and pid != target_mfl_id
    ]
    my_assets.sort(key=lambda p: p["dynasty_value"], reverse=True)

    # Candidate packages: singles and 2-player combos landing inside the fair
    # band around the target's value (a small overpay is fine — I want the guy).
    low = target_val * (1 - LOPSIDED_THRESHOLD)
    high = target_val * (1 + LOPSIDED_THRESHOLD)

    packages: list[list[dict]] = []
    packages.extend([a] for a in my_assets)
    packages.extend(list(combo) for combo in combinations(my_assets, 2))

    offers = []
    for pkg in packages:
        give_value = sum(p["dynasty_value"] for p in pkg)
        if not (low <= give_value <= high):
            continue
        bigger = max(give_value, target_val)
        gap_pct = abs(give_value - target_val) / bigger
        fills_their = {p["position"] for p in pkg
                       if group_of(p["position"]) in their_thin}
        offers.append({
            "give": pkg,
            "give_value": give_value,
            "net": target_val - give_value,   # + = I come out ahead on value
            "gap_pct": gap_pct,
            "salary_delta": target["salary"] - sum(p["salary"] for p in pkg),
            "fills_their": fills_their,
        })

    # Rank: fairest first, prefer packages that fill their need and are cleaner
    # (fewer players). Then pick distinct offers, capping how often any one of my
    # players is reused so the five options actually differ.
    offers.sort(key=lambda o: (o["gap_pct"], -bool(o["fills_their"]), len(o["give"])))

    used_counts: dict[str, int] = {}
    seen_sets: set[frozenset] = set()
    chosen = []
    for o in offers:
        ids = frozenset(p["mfl_id"] for p in o["give"])
        if ids in seen_sets:
            continue
        if any(used_counts.get(i, 0) >= 2 for i in ids):
            continue
        seen_sets.add(ids)
        for i in ids:
            used_counts[i] = used_counts.get(i, 0) + 1
        chosen.append(o)
        if len(chosen) >= max_offers:
            break

    result["offers"] = chosen
    return result
