"""Dynasty values for IDP players.

FantasyCalc only values offense, so defensive players get synthesized values:
last completed season's stats scored under THIS league's rules (tackles,
4-pt sacks, INTs, etc.), age-adjusted, normalized so the top IDP lands at
IDP_TOP_VALUE (default 4000 ≈ solid WR2/elite TE territory — where elite
LBs actually trade in deep IDP leagues). Tune via env if your league prices
defense differently.
"""
import os
import time

from src.sleeper_api import get_nfl_state
from src.scoring import fetch_rules, project_points

IDP_POSITIONS = {"DT", "DE", "LB", "CB", "S"}
IDP_TOP_VALUE = float(os.getenv("IDP_TOP_VALUE", "4000"))

# Age curve: defenders peak mid-20s, fall off fast around 30.
def _age_factor(age: int | None) -> float:
    if age is None:
        return 1.0
    if age <= 23: return 1.15
    if age <= 25: return 1.10
    if age <= 27: return 1.00
    if age <= 29: return 0.85
    if age <= 31: return 0.65
    return 0.45


_cache: dict | None = None
_cache_at: float = 0.0
_TTL = 86400


def compute_idp_values() -> dict[str, dict]:
    """{mfl_id: {'name', 'position', 'dynasty_value', 'season_pts', 'age'}}
    for all crosswalked IDP players with stats last season."""
    global _cache, _cache_at
    if _cache is not None and time.monotonic() - _cache_at < _TTL:
        return _cache

    import requests
    state = get_nfl_state()
    season = int(state.get("previous_season") or int(state["season"]) - 1)
    stats = requests.get(
        f"https://api.sleeper.app/v1/stats/nfl/regular/{season}", timeout=60
    ).json()

    # age isn't in our cache table; pull from the live dump, factor 1.0 if absent
    ages: dict[str, int] = {}
    try:
        dump = requests.get("https://api.sleeper.app/v1/players/nfl", timeout=60).json()
        ages = {sid: p.get("age") for sid, p in dump.items() if p.get("age")}
    except Exception:
        pass

    rules = fetch_rules()

    # Iterate the Sleeper crosswalk, which carries MFL's specific position —
    # Sleeper's own labels lump many defenders into 'DB'/'DL' group codes.
    from src.db import get_conn
    conn = get_conn()
    xwalk = conn.execute(
        "SELECT mfl_id, sleeper_id, sleeper_name, position FROM sleeper_crosswalk"
    ).fetchall()
    conn.close()

    raw: dict[str, dict] = {}
    for r in xwalk:
        if r["position"] not in IDP_POSITIONS:
            continue
        st = stats.get(r["sleeper_id"])
        if not st:
            continue
        pts = project_points(st, rules)
        if pts <= 0:
            continue
        raw[r["mfl_id"]] = {
            "name": r["sleeper_name"],
            "position": r["position"],
            "season_pts": pts,
            "age": ages.get(r["sleeper_id"]),
        }

    if raw:
        top = max(v["season_pts"] * _age_factor(v["age"]) for v in raw.values())
        for v in raw.values():
            adj = v["season_pts"] * _age_factor(v["age"])
            v["dynasty_value"] = round(adj / top * IDP_TOP_VALUE)

    _cache, _cache_at = raw, time.monotonic()
    return raw
