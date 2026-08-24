"""Age weighting for dynasty values.

A dynasty asset is worth its REMAINING career, so a 23-year-old and a
30-year-old putting up the same numbers are not worth the same. This applies a
position-aware age curve to dynasty values — running backs fall off a cliff
around 27 while quarterbacks hold value into their 30s.

FantasyCalc's market values already price age in to some degree, so applying a
full-strength curve on top double-counts it. DYNASTY_AGE_WEIGHT (default 0.5)
blends the curve toward neutral: 0.0 disables age weighting entirely, 1.0
applies the full curve, 0.5 splits the difference.
"""
import os

# Multiplier breakpoints: {age: factor}, linearly interpolated between them and
# flat outside the ends. >1 favours youth, <1 discounts age.
_CURVES: dict[str, dict[int, float]] = {
    "QB": {21: 1.25, 24: 1.20, 27: 1.10, 30: 1.00, 33: 0.85, 36: 0.65, 40: 0.45},
    "RB": {21: 1.35, 23: 1.30, 25: 1.10, 27: 0.85, 29: 0.60, 31: 0.40, 34: 0.25},
    "WR": {21: 1.30, 24: 1.20, 27: 1.05, 29: 0.90, 31: 0.70, 33: 0.50, 36: 0.35},
    "TE": {21: 1.25, 24: 1.15, 27: 1.05, 30: 0.90, 32: 0.70, 34: 0.50, 37: 0.35},
    # Defenders: peak mid-20s, fade around 30.
    "IDP": {21: 1.20, 24: 1.12, 27: 1.00, 29: 0.85, 31: 0.65, 33: 0.45, 36: 0.30},
    # Kickers barely age in fantasy terms.
    "PK": {21: 1.0, 40: 1.0},
}

_IDP_POSITIONS = {"DT", "DE", "LB", "CB", "S"}


def _curve_for(position: str | None) -> dict[int, float]:
    pos = (position or "").upper()
    if pos in _IDP_POSITIONS:
        return _CURVES["IDP"]
    return _CURVES.get(pos, _CURVES["WR"])  # sensible generic default


def age_weight() -> float:
    """Strength of the age curve, from env. Clamped to [0, 1]."""
    try:
        w = float(os.getenv("DYNASTY_AGE_WEIGHT", "0.5"))
    except ValueError:
        return 0.5
    return max(0.0, min(1.0, w))


def age_multiplier(position: str | None, age: int | float | None,
                   weight: float | None = None) -> float:
    """Dynasty value multiplier for a player's position and age.

    Returns 1.0 when age is unknown or weighting is disabled, so a missing age
    never silently penalises a player."""
    if age is None:
        return 1.0
    if weight is None:
        weight = age_weight()
    if weight <= 0:
        return 1.0

    curve = _curve_for(position)
    ages = sorted(curve)
    a = float(age)

    if a <= ages[0]:
        factor = curve[ages[0]]
    elif a >= ages[-1]:
        factor = curve[ages[-1]]
    else:
        factor = curve[ages[0]]
        for lo, hi in zip(ages, ages[1:]):
            if lo <= a <= hi:
                span = hi - lo
                t = (a - lo) / span if span else 0.0
                factor = curve[lo] + t * (curve[hi] - curve[lo])
                break

    # Blend toward neutral so market values aren't double-counted.
    return 1.0 + (factor - 1.0) * weight


def get_ages() -> dict[str, int]:
    """{mfl_id: age} from the Sleeper players cache via the crosswalk.

    Forces a cache refresh if ages are missing entirely — the column is newer
    than the table, so an existing install has NULLs until the next refresh."""
    from src.db import get_conn
    from src.sleeper_api import refresh_players_cache
    from src.sleeper_xwalk import get_sleeper_map

    def _ages_by_sleeper_id() -> dict[str, int]:
        conn = get_conn()
        rows = conn.execute(
            "SELECT sleeper_id, age FROM sleeper_players WHERE age IS NOT NULL"
        ).fetchall()
        conn.close()
        return {r["sleeper_id"]: int(r["age"]) for r in rows}

    ages = _ages_by_sleeper_id()
    if not ages:
        try:
            refresh_players_cache(force=True)
            ages = _ages_by_sleeper_id()
        except Exception:
            return {}

    smap = get_sleeper_map()  # mfl_id -> sleeper_id
    return {mfl_id: ages[sid] for mfl_id, sid in smap.items() if sid in ages}
