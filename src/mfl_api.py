import datetime
import requests
from src.config import MFL_HOST, MFL_LEAGUE_ID, MFL_YEAR

_detected_year: int | None = None


def league_year() -> int:
    """Current MFL league year. MFL rolls leagues over each spring, so the
    current calendar year may not exist yet early in the year — probe it once
    and fall back to the previous year."""
    global _detected_year
    if MFL_YEAR:
        return int(MFL_YEAR)
    if _detected_year is not None:
        return _detected_year
    this_year = datetime.date.today().year
    for cand in (this_year, this_year - 1):
        try:
            resp = requests.get(
                f"https://{MFL_HOST}.myfantasyleague.com/{cand}/export",
                params={"TYPE": "league", "L": MFL_LEAGUE_ID, "JSON": "1"},
                timeout=15,
            )
            if resp.ok and "league" in resp.json():
                _detected_year = cand
                return cand
        except Exception:
            continue
    # Probes failed (rate limit / outage): guess but do NOT cache, so a 429
    # at startup can't pin the wrong league year for the process lifetime.
    return this_year - 1


# Short-TTL response memo: bursts of bot commands reuse identical GETs
# (rosters, players, salaries) within seconds — don't hammer MFL for them.
_memo: dict[tuple, tuple[float, dict]] = {}
_MEMO_TTL = 30.0


def _get(endpoint: str, params: dict | None = None) -> dict:
    import time
    params = params or {}
    params.update({"L": MFL_LEAGUE_ID, "JSON": "1"})
    key = (endpoint, tuple(sorted(params.items())))
    hit = _memo.get(key)
    if hit and time.monotonic() - hit[0] < _MEMO_TTL:
        return hit[1]
    base = f"https://{MFL_HOST}.myfantasyleague.com/{league_year()}/export"
    resp = requests.get(f"{base}?TYPE={endpoint}", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    _memo[key] = (time.monotonic(), data)
    return data


def get_players() -> list[dict]:
    data = _get("players", {"DETAILS": "1"})
    return data.get("players", {}).get("player", [])


def get_rosters() -> list[dict]:
    data = _get("rosters")
    return data.get("rosters", {}).get("franchise", [])


def get_salaries() -> list[dict]:
    data = _get("salaries")
    return data.get("salaries", {}).get("leagueUnit", {}).get("player", [])


def get_free_agents() -> list[dict]:
    data = _get("freeAgents")
    return data.get("freeAgents", {}).get("leagueUnit", {}).get("player", [])


def get_league() -> dict:
    """League config: starting-lineup rules, roster size, franchises."""
    return _get("league").get("league", {})


_franchise_names: dict[str, str] = {}
_franchise_names_at: float = 0.0


def franchise_names() -> dict[str, str]:
    """{franchise_id: team name}, cached for an hour so renames still show up."""
    global _franchise_names, _franchise_names_at
    import time
    if not _franchise_names or time.monotonic() - _franchise_names_at > 3600:
        franchises = get_league().get("franchises", {}).get("franchise", [])
        if isinstance(franchises, dict):
            franchises = [franchises]
        _franchise_names = {f.get("id", ""): f.get("name", f.get("id", "?"))
                            for f in franchises}
        _franchise_names_at = time.monotonic()
    return _franchise_names


def franchise_name(fid: str) -> str:
    return franchise_names().get(fid, fid)


def get_weekly_results(week: str | int | None = None) -> dict:
    """Weekly results incl. each franchise's submitted starters."""
    params = {"W": str(week)} if week is not None else {}
    return _get("weeklyResults", params).get("weeklyResults", {})


def get_live_scoring() -> dict:
    """Live scoring for the current week (starters, scores, players left)."""
    return _get("liveScoring").get("liveScoring", {})


def get_draft_results() -> dict:
    """Rookie/startup draft picks (live-updating during an active draft)."""
    return _get("draftResults").get("draftResults", {})


def get_transactions(trans_type: str = "TRADE") -> list[dict]:
    data = _get("transactions", {"TRANS_TYPE": trans_type})
    txns = data.get("transactions", {}).get("transaction", [])
    if isinstance(txns, dict):
        txns = [txns]
    return txns
