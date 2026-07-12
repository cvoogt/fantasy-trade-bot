"""MFL <-> Sleeper player crosswalk.

Unlike the FantasyCalc crosswalk (names only, fuzzy), both MFL and Sleeper
publish shared external IDs, so this is an exact join wherever possible:

    priority: sportradar (MFL 'sportsdata_id') > espn > rotowire > stats

Fuzzy name+position fallback only for players missing all shared IDs.
"""
from rapidfuzz import fuzz
from src.db import get_conn
from src import mfl_api
from src.sleeper_api import refresh_players_cache, get_cached_players
from src.crosswalk import _normalize_name

# (join_key label, MFL field, sleeper_players column)
_JOINS = [
    ("sportradar", "sportsdata_id", "sportradar_id"),
    ("espn", "espn_id", "espn_id"),
    ("rotowire", "rotowire_id", "rotowire_id"),
    ("stats", "stats_id", "stats_id"),
]

_FUZZY_MIN = 90  # near-exact only — ID joins should catch everyone who matters


def build_sleeper_crosswalk() -> dict:
    """Build/refresh the crosswalk. Returns {'matched': n, 'by_key': {...}, 'fuzzy': n}."""
    refresh_players_cache()
    sleeper = get_cached_players()
    mfl_players = mfl_api.get_players()

    # Index sleeper players by each join key
    by_key: dict[str, dict[str, dict]] = {label: {} for label, _, _ in _JOINS}
    for sp in sleeper:
        for label, _, scol in _JOINS:
            v = sp.get(scol)
            if v:
                by_key[label][str(v)] = sp

    # Fuzzy fallback index: position -> [(norm_name, player)]
    by_pos: dict[str, list[tuple[str, dict]]] = {}
    for sp in sleeper:
        if sp["name"]:
            by_pos.setdefault(sp["position"], []).append((_normalize_name(sp["name"]), sp))

    conn = get_conn()
    overrides = {
        r["mfl_id"]
        for r in conn.execute("SELECT mfl_id FROM sleeper_crosswalk WHERE manual_override = 1")
    }
    conn.execute("DELETE FROM sleeper_crosswalk WHERE manual_override = 0")

    counts = {label: 0 for label, _, _ in _JOINS}
    fuzzy_count = 0

    for mp in mfl_players:
        mfl_id = mp.get("id", "")
        if not mfl_id or mfl_id in overrides:
            continue

        hit, join_key = None, None
        for label, mfield, _ in _JOINS:
            v = mp.get(mfield)
            if v and str(v) in by_key[label]:
                hit, join_key = by_key[label][str(v)], label
                break

        if hit is None:
            # fuzzy fallback: same position, near-exact normalized name
            mnorm = _normalize_name(mp.get("name", ""))
            best_score = 0
            for norm, sp in by_pos.get(mp.get("position", ""), []):
                score = fuzz.token_sort_ratio(mnorm, norm)
                if score > best_score:
                    best_score, hit = score, sp
            if best_score < _FUZZY_MIN:
                hit = None
            else:
                join_key = "fuzzy"

        if hit is None:
            continue

        conn.execute(
            """INSERT OR REPLACE INTO sleeper_crosswalk
               (mfl_id, sleeper_id, mfl_name, sleeper_name, position, join_key)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mfl_id, hit["sleeper_id"], mp.get("name", ""), hit["name"],
             mp.get("position", ""), join_key),
        )
        if join_key == "fuzzy":
            fuzzy_count += 1
        else:
            counts[join_key] += 1

    conn.commit()
    conn.close()
    return {"matched": sum(counts.values()) + fuzzy_count, "by_key": counts, "fuzzy": fuzzy_count}


def get_sleeper_map() -> dict[str, str]:
    """{mfl_id: sleeper_id} for all crosswalked players."""
    conn = get_conn()
    rows = conn.execute("SELECT mfl_id, sleeper_id FROM sleeper_crosswalk").fetchall()
    conn.close()
    return {r["mfl_id"]: r["sleeper_id"] for r in rows}


def rostered_coverage() -> dict:
    """Check every player on a league roster maps to Sleeper.
    Returns {'total': n, 'mapped': n, 'missing': [{'mfl_id', 'name'}...]}."""
    smap = get_sleeper_map()
    mfl_names = {p.get("id"): p.get("name", "?") for p in mfl_api.get_players()}

    total, mapped, missing = 0, 0, []
    for fr in mfl_api.get_rosters():
        players = fr.get("player", [])
        if isinstance(players, dict):
            players = [players]
        for p in players:
            pid = p.get("id", "")
            total += 1
            if pid in smap:
                mapped += 1
            else:
                missing.append({"mfl_id": pid, "name": mfl_names.get(pid, "?")})
    return {"total": total, "mapped": mapped, "missing": missing}
