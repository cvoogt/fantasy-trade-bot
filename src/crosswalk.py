from rapidfuzz import fuzz
from src.db import get_conn
from src import mfl_api, fantasycalc_api

# When team confirms the match, a lower name score is trustworthy.
# When team is unknown/mismatched, demand a near-exact name to avoid
# scrubs inheriting a star's value (e.g. Keilan Robinson -> Bijan Robinson).
NAME_MIN_WITH_TEAM = 80
NAME_MIN_NO_TEAM = 90
TEAM_BONUS = 15

# MFL team abbreviations that differ from FantasyCalc's.
MFL_TO_FC_TEAM = {
    "GBP": "GB", "JAC": "JAX", "KCC": "KC", "LVR": "LV",
    "NEP": "NE", "NOS": "NO", "SFO": "SF", "TBB": "TB",
}

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _norm_team(mfl_team: str) -> str:
    if not mfl_team or mfl_team == "FA":
        return ""
    return MFL_TO_FC_TEAM.get(mfl_team, mfl_team)


def _normalize_name(name: str) -> str:
    n = name.strip().replace(".", "").replace("'", "").replace("-", " ").lower()
    if "," in n:  # MFL "Last, First" -> "First Last"
        last, _, first = n.partition(",")
        n = f"{first.strip()} {last.strip()}"
    toks = [t for t in n.split() if t not in SUFFIXES]
    return " ".join(toks)


def build_crosswalk():
    """Build MFL playerID -> FantasyCalc name crosswalk.

    Matches on normalized name + position + team. Team is a strong signal:
    a confirmed team lets a looser name match through; without it we require
    a near-exact name. One-to-one is enforced (best score wins a contested
    FantasyCalc entry). manual_override rows are preserved.
    """
    mfl_players = mfl_api.get_players()
    fc_values = fantasycalc_api.get_cached_values()
    if not fc_values:
        fantasycalc_api.fetch_and_cache()
        fc_values = fantasycalc_api.get_cached_values()

    fc_by_pos: dict[str, list[dict]] = {}
    for fc in fc_values:
        fc_by_pos.setdefault(fc["position"], []).append({
            "fc_name": fc["fc_name"],
            "norm": _normalize_name(fc["fc_name"]),
            "team": fc.get("team", "") or "",
        })

    conn = get_conn()
    overrides = {
        r["mfl_id"]
        for r in conn.execute("SELECT mfl_id FROM crosswalk WHERE manual_override = 1")
    }

    # best candidate per MFL player
    best: dict[str, dict] = {}
    for mp in mfl_players:
        mfl_id = mp.get("id", "")
        if not mfl_id or mfl_id in overrides:
            continue
        mfl_name = mp.get("name", "")
        position = mp.get("position", "")
        team = _norm_team(mp.get("team", ""))
        if not mfl_name or position not in fc_by_pos:
            continue
        mnorm = _normalize_name(mfl_name)

        chosen = None
        for fc in fc_by_pos[position]:
            score = fuzz.token_sort_ratio(mnorm, fc["norm"])
            team_match = bool(team) and bool(fc["team"]) and team == fc["team"]
            threshold = NAME_MIN_WITH_TEAM if team_match else NAME_MIN_NO_TEAM
            if score < threshold:
                continue
            eff = score + (TEAM_BONUS if team_match else 0)
            if chosen is None or eff > chosen["eff"]:
                chosen = {"eff": eff, "score": score, "fc_name": fc["fc_name"],
                          "team_match": team_match}
        if chosen:
            best[mfl_id] = {
                "mfl_name": mfl_name, "position": position,
                "team": mp.get("team", ""), **chosen,
            }

    # one-to-one: best score wins a contested FantasyCalc (name, position)
    winners: dict[tuple, tuple] = {}
    for mfl_id, b in best.items():
        key = (b["fc_name"], b["position"])
        if key not in winners or b["score"] > winners[key][1]:
            winners[key] = (mfl_id, b["score"])
    winner_ids = {w[0] for w in winners.values()}

    conn.execute("DELETE FROM crosswalk WHERE manual_override = 0")
    matched = 0
    for mfl_id, b in best.items():
        if mfl_id not in winner_ids:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO crosswalk
               (mfl_id, mfl_name, fc_name, position, team, match_score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mfl_id, b["mfl_name"], b["fc_name"], b["position"], b["team"], b["score"]),
        )
        matched += 1

    conn.commit()
    conn.close()
    print(f"Crosswalk built: {matched} matched ({len(mfl_players)} MFL players scanned)")


def unmatched_valuable(top_n: int = 25) -> list[dict]:
    """High-value FantasyCalc players with no MFL crosswalk entry — manual-fix candidates."""
    conn = get_conn()
    mapped = {r["fc_name"] for r in conn.execute("SELECT fc_name FROM crosswalk")}
    rows = conn.execute(
        "SELECT fc_name, position, team, dynasty_value FROM fantasycalc_cache "
        "ORDER BY dynasty_value DESC"
    ).fetchall()
    conn.close()
    out = [dict(r) for r in rows if r["fc_name"] not in mapped]
    return out[:top_n]


def resolve_player(query: str, limit: int = 3, min_score: int = 60) -> list[dict]:
    """Fuzzy-resolve a typed player name to crosswalk entries, best first.

    Matches against both the MFL name and the FantasyCalc name so
    'bijan', 'Robinson, Bijan', and 'Bijan Robinson' all hit.
    """
    q = _normalize_name(query)
    conn = get_conn()
    rows = conn.execute("SELECT * FROM crosswalk").fetchall()
    conn.close()

    scored = []
    for r in rows:
        score = max(
            fuzz.token_set_ratio(q, _normalize_name(r["mfl_name"])),
            fuzz.token_set_ratio(q, _normalize_name(r["fc_name"])),
        )
        if score >= min_score:
            scored.append((score, dict(r)))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [{**row, "resolve_score": s} for s, row in scored[:limit]]


def get_fc_name(mfl_id: str) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT fc_name FROM crosswalk WHERE mfl_id = ?", (mfl_id,)
    ).fetchone()
    conn.close()
    return row["fc_name"] if row else None
