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


# Some exports are served only from MFL's central API host, not the league's
# own www<N> host. MFL signals this with HTTP 200 and an error in the body
# ("This API request must go to api.myfantasyleague.com"), so it looks like an
# empty result unless you read the payload. Seeded with the ones we know and
# extended at runtime whenever MFL tells us.
API_HOST = "api.myfantasyleague.com"
_api_host_endpoints: set[str] = {"nflSchedule"}


def error_text(data: dict) -> str:
    """MFL's in-body error message, '' when the response is fine."""
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        err = err.get("$t", "")
    return str(err or "")


# MFL asks API clients to identify themselves; the default python-requests
# agent gets throttled and, on some endpoints, refused outright.
USER_AGENT = "fantasy-trade-bot/1.0 (+https://github.com/cvoogt/fantasy-trade-bot)"
_HEADERS = {"User-Agent": USER_AGENT}


def _request(endpoint: str, params: dict, host: str) -> dict:
    base = f"https://{host}/{league_year()}/export"
    resp = requests.get(f"{base}?TYPE={endpoint}", params=params,
                        headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _get(endpoint: str, params: dict | None = None) -> dict:
    import time
    params = params or {}
    params.update({"L": MFL_LEAGUE_ID, "JSON": "1"})
    key = (endpoint, tuple(sorted(params.items())))
    hit = _memo.get(key)
    if hit and time.monotonic() - hit[0] < _MEMO_TTL:
        return hit[1]

    league_host = f"{MFL_HOST}.myfantasyleague.com"
    host = API_HOST if endpoint in _api_host_endpoints else league_host
    data = _request(endpoint, params, host)

    # Wrong host: MFL says so in the body. Learn it and retry once, so any
    # other endpoint MFL restricts later fixes itself.
    if host != API_HOST and API_HOST in error_text(data):
        _api_host_endpoints.add(endpoint)
        data = _request(endpoint, params, API_HOST)

    if error_text(data):
        return data  # don't memoize an error — let the next call try again
    _memo[key] = (time.monotonic(), data)
    return data


def probe_schedule_hosts(week: int | str = 1) -> list[dict]:
    """Try every plausible nflSchedule URL and report which MFL accepts.

    MFL's rules about which host serves which export aren't documented in a way
    we can rely on, so when the schedule comes back empty this finds a working
    combination empirically instead of guessing. Prints a table and returns the
    variants tried."""
    year = league_year()
    league_host = f"{MFL_HOST}.myfantasyleague.com"
    variants = [
        ("league host, with L", league_host, {"W": str(week), "L": MFL_LEAGUE_ID}, True),
        ("league host, no L", league_host, {"W": str(week)}, True),
        ("api host, with L", API_HOST, {"W": str(week), "L": MFL_LEAGUE_ID}, True),
        ("api host, no L", API_HOST, {"W": str(week)}, True),
        ("api host, no L/no W", API_HOST, {}, True),
        # Same as the two most likely, but without our User-Agent header, to
        # show whether the UA is what MFL is reacting to.
        ("api host, default UA", API_HOST, {"W": str(week), "L": MFL_LEAGUE_ID}, False),
        ("league host, default UA", league_host, {"W": str(week), "L": MFL_LEAGUE_ID}, False),
    ]

    results = []
    for label, host, extra, use_ua in variants:
        params = {"JSON": "1", **extra}
        url = f"https://{host}/{year}/export?TYPE=nflSchedule"
        entry = {"label": label, "host": host, "params": params, "url": url}
        try:
            resp = requests.get(url, params=params,
                                headers=_HEADERS if use_ua else None, timeout=30)
            entry["status"] = resp.status_code
            try:
                data = resp.json()
            except ValueError:
                entry["error"] = f"non-JSON: {resp.text[:120]}"
                data = {}
            err = error_text(data)
            if err:
                entry["error"] = err
            else:
                sched = data.get("nflSchedule", {})
                if isinstance(sched, list):
                    sched = sched[0] if sched else {}
                games = sched.get("matchup", []) if isinstance(sched, dict) else []
                if isinstance(games, dict):
                    games = [games]
                entry["games"] = len(games)
                if games:
                    entry["sample_teams"] = [t.get("id") for t in games[0].get("team", [])]
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
        results.append(entry)

        status = entry.get("status", "-")
        if entry.get("games"):
            print(f"  ✅ {label:<22} HTTP {status}  {entry['games']} games  "
                  f"teams={entry.get('sample_teams')}")
        else:
            print(f"  ❌ {label:<22} HTTP {status}  "
                  f"{entry.get('error', '0 games, no error')[:90]}")
        print(f"       {entry['url']}&" +
              "&".join(f"{k}={v}" for k, v in params.items() if k != "JSON"))

    working = [r for r in results if r.get("games")]
    if working:
        print(f"\n  -> Use: {working[0]['label']}")
    else:
        print("\n  -> No variant worked. Paste this output and the raw error.")
    return results


def get_players() -> list[dict]:
    data = _get("players", {"DETAILS": "1"})
    return data.get("players", {}).get("player", [])


def get_rosters() -> list[dict]:
    data = _get("rosters")
    return data.get("rosters", {}).get("franchise", [])


def get_salaries() -> list[dict]:
    data = _get("salaries")
    return data.get("salaries", {}).get("leagueUnit", {}).get("player", [])


def salary_map() -> dict[str, float]:
    """{mfl_id: salary} for every player MFL prices.

    Prefer this over reading salaries off the value map: the value map only
    covers players FantasyCalc values plus synthesized IDP, so kickers (and
    anyone else FantasyCalc skips) are absent from it and would read as $0."""
    out: dict[str, float] = {}
    for p in get_salaries():
        pid = p.get("id", "")
        if not pid:
            continue
        try:
            out[pid] = float(p.get("salary") or 0)
        except (TypeError, ValueError):
            pass
    return out


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


def get_nfl_schedule(week: str | int | None = None) -> list[dict]:
    """NFL game schedule for a week: each game's kickoff (unix seconds) and the
    two teams (with `id` abbreviation and `isHome`). Omit `week` for the
    current week."""
    params = {"W": str(week)} if week is not None else {}
    sched = _get("nflSchedule", params).get("nflSchedule", {})
    # Asking for a single week returns one object; omitting W (or some MFL
    # hosts) returns a list of weeks. Flatten either into a list of games.
    if isinstance(sched, list):
        weeks = sched
    else:
        weeks = [sched]

    games = []
    for wk in weeks:
        if not isinstance(wk, dict):
            continue
        matchups = wk.get("matchup", [])
        if isinstance(matchups, dict):
            matchups = [matchups]
        games.extend(m for m in matchups if isinstance(m, dict))
    return games


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
