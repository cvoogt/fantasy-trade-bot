"""League-accurate projected points: MFL scoring rules × Sleeper stat projections.

MFL rules come as event brackets with three point styles:
  - "*3"    -> 3 points per event (count stats: TDs, INTs, forced fumbles)
  - "1/20"  -> rate per unit (yardage), optionally with thresholdPoints as the
               cumulative base at the bracket start (milestone bonuses)
  - "8"     -> step table: total points for landing in this bracket
               (this league's RY/CY tables, with jumps at 100/150/200...)

Kickers are special-cased onto Sleeper's fgm/fgmiss distance buckets.
"""
import time
from fractions import Fraction

from src import mfl_api

# MFL event code -> Sleeper projection stat key.
EVENT_TO_SLEEPER = {
    "PY": "pass_yd", "PS": "pass_td", "IN": "pass_int", "P2": "pass_2pt",
    "RY": "rush_yd", "RS": "rush_td", "R2": "rush_2pt",
    "CC": "rec", "CY": "rec_yd", "RC": "rec_td", "C2": "rec_2pt",
    "FU": "fum_lost",
    "TK": "idp_tkl", "AS": "idp_tkl_ast", "SK": "idp_sack",
    "IC": "idp_int", "ICY": "idp_int_ret_yd",
    "FC": "idp_fum_rec", "FCY": "idp_fum_ret_yd", "FF": "idp_ff",
    "PD": "idp_pass_def", "TKL": "idp_tkl_loss", "SF": "idp_saf",
    "EP": "xpm", "EM": "xpmiss",
    # FG / MG handled specially via distance buckets.
    # Return TDs / return yards (KO, PR, IR, DR, KY, UY) have no reliable
    # projection keys — skipped; they're noise at projection time anyway.
}

_rules_cache: list | None = None
_rules_at: float = 0.0
_RULES_TTL = 86400


def _t(v):
    """Unwrap MFL's {'$t': value} JSON quirk."""
    return v.get("$t") if isinstance(v, dict) else v


def fetch_rules() -> list[dict]:
    """Flatten MFL positionRules into [{event, points, lo, hi, threshold, positions}].

    `positions` is the set of positions the rule applies to (empty = all).
    Keeping it matters: different position groups define the same event with
    different brackets (QB rushing yards vs RB rushing yards), so scoring a
    player against the merged pile picks whichever bracket happens to sort
    first. Pass a position to rules_for_position() to score correctly."""
    global _rules_cache, _rules_at
    if _rules_cache is not None and time.monotonic() - _rules_at < _RULES_TTL:
        return _rules_cache

    raw = mfl_api._get("rules").get("rules", {}).get("positionRules", [])
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for group in raw:
        # MFL gives the group's positions as e.g. 'QB' or 'RB|WR|TE'.
        pos_raw = _t(group.get("positions")) or ""
        positions = {p.strip().upper() for p in pos_raw.split("|") if p.strip()}
        rl = group.get("rule", [])
        if isinstance(rl, dict):
            rl = [rl]
        for r in rl:
            rng = _t(r.get("range")) or "0-999"
            lo, _, hi = rng.partition("-")
            thr = _t(r.get("thresholdPoints"))
            out.append({
                "event": _t(r.get("event")),
                "points": str(_t(r.get("points"))),
                "lo": float(lo), "hi": float(hi or lo),
                "threshold": float(thr) if thr is not None else None,
                "positions": positions,
            })
    _rules_cache, _rules_at = out, time.monotonic()
    return out


def rules_for_position(rules: list[dict], position: str | None) -> list[dict]:
    """Subset of `rules` applying to `position` (rules with no position list
    apply to everyone). Unknown/absent position returns the rules unchanged."""
    if not position:
        return rules
    pos = position.strip().upper()
    scoped = [r for r in rules
              if not r.get("positions") or pos in r["positions"]]
    # If the league doesn't scope rules by position at all, don't filter to
    # nothing — fall back to the full set.
    return scoped or rules


def _eval_event(brackets: list[dict], amount: float) -> float:
    """Points for a projected stat amount under one event's brackets."""
    if amount <= 0:
        return 0.0
    # find the bracket containing the amount (clamp above the last)
    brackets = sorted(brackets, key=lambda b: b["lo"])
    hit = None
    for b in brackets:
        if b["lo"] <= amount <= b["hi"]:
            hit = b
            break
    if hit is None:
        hit = brackets[-1] if amount > brackets[-1]["hi"] else None
    if hit is None:
        return 0.0

    pts = hit["points"]
    if pts.startswith("*"):           # per-event: '*3' x count
        return float(pts[1:]) * amount
    if "/" in pts:                    # rate per unit: '1/20', '2/0.5'
        num, den = pts.split("/")
        rate = float(Fraction(num) / Fraction(den))
        if hit["threshold"] is not None:  # milestone base at bracket start
            return hit["threshold"] + rate * (amount - hit["lo"])
        return rate * amount
    return float(pts)                 # step table: absolute points


def _kicker_points(proj: dict, by_event: dict) -> float:
    """FG/MG via Sleeper distance buckets (bracket points read from rules)."""
    def bracket_pts(event: str, dist: float) -> float:
        for b in by_event.get(event, []):
            if b["lo"] <= dist <= b["hi"]:
                return float(b["points"])
        return 0.0

    fgm_short = max(
        float(proj.get("fgm", 0) or 0)
        - float(proj.get("fgm_40_49", 0) or 0)
        - float(proj.get("fgm_50p", 0) or 0),
        0.0,
    )
    pts = fgm_short * bracket_pts("FG", 30)
    pts += float(proj.get("fgm_40_49", 0) or 0) * bracket_pts("FG", 45)
    pts += float(proj.get("fgm_50p", 0) or 0) * bracket_pts("FG", 52)
    for key, dist in (("fgmiss_30_39", 35), ("fgmiss_40_49", 45), ("fgmiss_50p", 52)):
        pts += float(proj.get(key, 0) or 0) * bracket_pts("MG", dist)
    return pts


def project_points(proj: dict, rules: list[dict] | None = None,
                   position: str | None = None) -> float:
    """League-scored projected points from a SINGLE-GAME stat line.

    MFL's brackets are per-game (the 100-yard rushing step, the 300-yard
    passing bonus), so `proj` must be one game's stats. For a season-total
    stat line use season_points() instead. Pass `position` so position-scoped
    rules are applied correctly."""
    if rules is None:
        rules = fetch_rules()
    rules = rules_for_position(rules, position)
    by_event: dict[str, list[dict]] = {}
    for r in rules:
        by_event.setdefault(r["event"], []).append(r)

    total = 0.0
    for event, brackets in by_event.items():
        skey = EVENT_TO_SLEEPER.get(event)
        if skey is None:
            continue
        amount = float(proj.get(skey, 0) or 0)
        total += _eval_event(brackets, amount)
    total += _kicker_points(proj, by_event)
    return round(total, 2)


# Games in an NFL regular season — the divisor turning a season-total stat
# line into the per-game line MFL's brackets expect.
GAMES_PER_SEASON = 17


def season_points(proj: dict, rules: list[dict] | None = None,
                  position: str | None = None,
                  games: int = GAMES_PER_SEASON) -> float:
    """League-scored points for a SEASON-TOTAL stat line.

    Scoring a season total directly against per-game brackets badly
    understates players: step tables (rushing/receiving yards) clamp at their
    last bracket, so 1,500 rushing yards would score the same as 100. Instead
    score the implied per-game line and multiply back up.

    Per-event ('*6') and pure-rate ('1/20') events are scale-invariant, so
    this only changes the bracketed events — which is exactly the intent."""
    if games <= 0:
        return 0.0
    per_game = {k: (v / games if isinstance(v, (int, float)) else v)
                for k, v in proj.items()}
    return round(project_points(per_game, rules, position) * games, 2)


def explain_points(proj: dict, rules: list[dict] | None = None,
                   position: str | None = None, season: bool = False,
                   games: int = GAMES_PER_SEASON) -> list[dict]:
    """Per-event breakdown of a scored stat line, for debugging scoring.

    Returns [{event, stat, amount, points}] sorted by contribution. `season`
    scores the line the way season_points does (per-game, scaled up)."""
    if rules is None:
        rules = fetch_rules()
    scoped = rules_for_position(rules, position)
    by_event: dict[str, list[dict]] = {}
    for r in scoped:
        by_event.setdefault(r["event"], []).append(r)

    divisor = games if season else 1
    rows = []
    for event, brackets in by_event.items():
        skey = EVENT_TO_SLEEPER.get(event)
        if skey is None:
            continue
        amount = float(proj.get(skey, 0) or 0)
        if not amount:
            continue
        pts = _eval_event(brackets, amount / divisor) * divisor
        rows.append({"event": event, "stat": skey, "amount": amount,
                     "points": round(pts, 2)})
    rows.sort(key=lambda r: abs(r["points"]), reverse=True)
    return rows
