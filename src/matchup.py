"""Weekly head-to-head matchup: opponent, live score, players yet to play.

Opponent comes from the MFL schedule; live numbers from liveScoring when
games are underway (absent off-season), falling back to weeklyResults for
completed weeks.
"""
from src.config import MFL_FRANCHISE_ID
from src import mfl_api
from src.sleeper_api import get_nfl_state


def _aslist(v):
    return [v] if isinstance(v, dict) else (v or [])


def opponent_for_week(week: int, franchise_id: str = MFL_FRANCHISE_ID) -> str | None:
    sched = mfl_api._get("schedule").get("schedule", {})
    for wk in _aslist(sched.get("weeklySchedule")):
        if str(wk.get("week")) != str(week):
            continue
        for m in _aslist(wk.get("matchup")):
            ids = [f.get("id") for f in _aslist(m.get("franchise"))]
            if franchise_id in ids:
                others = [i for i in ids if i != franchise_id]
                return others[0] if others else None
    return None


def _live_entry(franchise_id: str) -> dict | None:
    ls = mfl_api.get_live_scoring()
    for m in _aslist(ls.get("matchup")):
        for f in _aslist(m.get("franchise")):
            if f.get("id") == franchise_id:
                return f
    # some MFL responses list franchises flat
    for f in _aslist(ls.get("franchise")):
        if f.get("id") == franchise_id:
            return f
    return None


def _final_score(week: int, franchise_id: str) -> str | None:
    wr = mfl_api.get_weekly_results(week)
    for m in _aslist(wr.get("matchup")):
        for f in _aslist(m.get("franchise")):
            if f.get("id") == franchise_id and f.get("score") not in (None, ""):
                return f.get("score")
    for f in _aslist(wr.get("franchise")):
        if f.get("id") == franchise_id and f.get("score") not in (None, ""):
            return f.get("score")
    return None


def matchup_status(week: int | None = None,
                   franchise_id: str = MFL_FRANCHISE_ID) -> dict:
    """{'week', 'opponent_id', 'me': {...}, 'them': {...}, 'live': bool}
    Side dicts: {'score', 'yet_to_play', 'playing'} with None where unknown."""
    if week is None:
        state = get_nfl_state()
        week = max(int(state.get("week") or 1), 1)

    opp = opponent_for_week(week, franchise_id)

    def side(fid: str | None) -> dict:
        if fid is None:
            return {"score": None, "yet_to_play": None, "playing": None}
        entry = None
        try:
            entry = _live_entry(fid)
        except Exception:
            pass
        if entry and entry.get("score") not in (None, ""):
            return {
                "score": entry.get("score"),
                "yet_to_play": entry.get("playersYetToPlay"),
                "playing": entry.get("playersCurrentlyPlaying"),
            }
        return {"score": _final_score(week, fid), "yet_to_play": None, "playing": None}

    me, them = side(franchise_id), side(opp)
    return {
        "week": week,
        "opponent_id": opp,
        "me": me,
        "them": them,
        "live": me["yet_to_play"] is not None,
    }
