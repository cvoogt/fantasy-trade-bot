from rapidfuzz import fuzz, process
from src.db import get_conn
from src import mfl_api, fantasycalc_api

MATCH_THRESHOLD = 75


def _normalize_name(name: str) -> str:
    return name.strip().replace(".", "").replace("'", "").replace("-", " ").lower()


def build_crosswalk():
    """Build MFL player ID → FantasyCalc name crosswalk using fuzzy matching."""
    mfl_players = mfl_api.get_players()
    fc_values = fantasycalc_api.get_cached_values()
    if not fc_values:
        fantasycalc_api.fetch_and_cache()
        fc_values = fantasycalc_api.get_cached_values()

    fc_by_pos: dict[str, list[dict]] = {}
    for fc in fc_values:
        fc_by_pos.setdefault(fc["position"], []).append(fc)

    conn = get_conn()
    matched = 0
    unmatched = 0

    for mp in mfl_players:
        mfl_id = mp.get("id", "")
        mfl_name = mp.get("name", "")
        position = mp.get("position", "")
        team = mp.get("team", "")

        if not mfl_name or position not in fc_by_pos:
            continue

        existing = conn.execute(
            "SELECT * FROM crosswalk WHERE mfl_id = ? AND manual_override = 1",
            (mfl_id,),
        ).fetchone()
        if existing:
            continue

        mfl_norm = _normalize_name(mfl_name)
        # MFL uses "Last, First" format
        parts = mfl_norm.split(",")
        if len(parts) == 2:
            mfl_norm = f"{parts[1].strip()} {parts[0].strip()}"

        candidates = fc_by_pos[position]
        fc_names = [_normalize_name(c["fc_name"]) for c in candidates]

        result = process.extractOne(mfl_norm, fc_names, scorer=fuzz.token_sort_ratio)
        if result and result[1] >= MATCH_THRESHOLD:
            best_name, score, idx = result
            fc_player = candidates[idx]
            conn.execute(
                """INSERT OR REPLACE INTO crosswalk
                   (mfl_id, mfl_name, fc_name, position, team, match_score)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (mfl_id, mfl_name, fc_player["fc_name"], position, team, score),
            )
            matched += 1
        else:
            unmatched += 1

    conn.commit()
    conn.close()
    print(f"Crosswalk built: {matched} matched, {unmatched} unmatched")


def get_fc_name(mfl_id: str) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT fc_name FROM crosswalk WHERE mfl_id = ?", (mfl_id,)
    ).fetchone()
    conn.close()
    return row["fc_name"] if row else None
