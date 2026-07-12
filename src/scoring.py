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
    """Flatten MFL positionRules into [{event, points, range, threshold}]."""
    global _rules_cache, _rules_at
    if _rules_cache is not None and time.monotonic() - _rules_at < _RULES_TTL:
        return _rules_cache

    raw = mfl_api._get("rules").get("rules", {}).get("positionRules", [])
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for group in raw:
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
            })
    _rules_cache, _rules_at = out, time.monotonic()
    return out


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


def project_points(proj: dict, rules: list[dict] | None = None) -> float:
    """League-scored projected points from a Sleeper projection row."""
    if rules is None:
        rules = fetch_rules()
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
