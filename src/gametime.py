"""When do my starters play? Maps each starter to their NFL game slot for the
week (from the MFL nflSchedule export) and answers single-player "when does X
play this week" lookups.

Slots are derived from each game's kickoff time in US Eastern: Thursday night,
the Sunday windows (international morning / 1pm early / 4pm late / night),
Friday/Saturday specials, and Monday night."""
from datetime import datetime, timedelta, timezone

from src.config import MFL_FRANCHISE_ID
from src import mfl_api

# Weekly game slots in chronological order — used to sort output.
SLOT_ORDER = ["Thu Night", "Fri", "Sat", "Sun AM", "Sun Early",
              "Sun Late", "Sun Night", "Mon Night", "Other"]


# NFL game windows are defined in US Eastern (1pm/4pm/SNF), so slotting is done
# in ET; kickoffs are *displayed* in US Central per league preference.
_SLOT_TZ = ("America/New_York", 4)
_DISPLAY_TZ = ("America/Chicago", 5)


def _in_zone(ts: int, zone: tuple[str, int]) -> datetime:
    """Kickoff unix seconds -> datetime in `zone` (name, DST-season UTC offset)."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo(zone[0]))
    except Exception:  # tz database unavailable — fall back to a fixed offset
        return dt - timedelta(hours=zone[1])


def slot_for(ts: int) -> str:
    """Label the game slot a kickoff timestamp falls in (windows anchored in ET)."""
    if not ts:
        return "Other"
    et = _in_zone(ts, _SLOT_TZ)
    wd = et.weekday()  # Mon=0 .. Sun=6
    hm = et.hour + et.minute / 60.0
    if wd == 3:
        return "Thu Night"
    if wd == 4:
        return "Fri"
    if wd == 5:
        return "Sat"
    if wd == 0:
        return "Mon Night"
    if wd == 6:            # Sunday
        if hm < 12:        # international morning games (~9:30 ET)
            return "Sun AM"
        if hm < 15:        # 1:00 ET wave
            return "Sun Early"
        if hm < 18.5:      # 4:05 / 4:25 ET wave
            return "Sun Late"
        return "Sun Night"
    return "Other"


def fmt_kickoff(ts: int) -> str:
    """Kickoff timestamp -> 'Sun 12:00 PM CT' (or 'TBD' when unknown)."""
    if not ts:
        return "TBD"
    ct = _in_zone(ts, _DISPLAY_TZ)
    hour12 = ct.hour % 12 or 12
    ampm = "AM" if ct.hour < 12 else "PM"
    return f"{ct:%a} {hour12}:{ct.minute:02d} {ampm} CT"


def team_slots(week: int | None = None) -> dict[str, dict]:
    """{TEAM: {slot, kickoff, opp, home}} for the week from MFL nflSchedule."""
    out: dict[str, dict] = {}
    for g in mfl_api.get_nfl_schedule(week):
        try:
            ts = int(g.get("kickoff") or 0)
        except (TypeError, ValueError):
            ts = 0
        teams = g.get("team", [])
        if isinstance(teams, dict):
            teams = [teams]
        ids = [t.get("id", "") for t in teams]
        slot = slot_for(ts)
        for t in teams:
            tid = t.get("id", "")
            if not tid:
                continue
            opp = next((x for x in ids if x != tid), "")
            out[tid] = {"slot": slot, "kickoff": ts, "opp": opp,
                        "home": t.get("isHome") == "1"}
    return out


def _starter_ids(franchise_id: str, week: int | None) -> list[str]:
    """MFL ids of my starting lineup — submitted starters if the lineup is set,
    otherwise the optimal lineup the solver would recommend. Only the true
    starters (~19 in this league), never the whole roster."""
    try:
        from src.lineup import lineup_advice
        adv = lineup_advice(franchise_id, None, week)
        ids = list(adv.get("current") or [])
        if not ids:
            ids = [p["mfl_id"] for p in adv.get("optimal", [])]
        if ids:
            return ids
    except Exception:
        pass
    # Last resort (projections/rules unavailable): whole roster.
    for fr in mfl_api.get_rosters():
        if fr.get("id") == franchise_id:
            players = fr.get("player", [])
            if isinstance(players, dict):
                players = [players]
            return [p.get("id", "") for p in players]
    return []


def _starter_meta(franchise_id: str = MFL_FRANCHISE_ID,
                  week: int | None = None) -> list[dict]:
    """[{mfl_id, name, position, team}] for my starting lineup this week."""
    names = {p.get("id"): p for p in mfl_api.get_players()}
    out = []
    for pid in _starter_ids(franchise_id, week):
        m = names.get(pid, {})
        out.append({
            "mfl_id": pid,
            "name": m.get("name", pid),
            "position": m.get("position", "?"),
            "team": m.get("team", ""),
        })
    return out


def starters_by_slot(franchise_id: str = MFL_FRANCHISE_ID,
                     week: int | None = None) -> dict:
    """Group my starters by NFL game slot for the week.

    Returns {'week', 'slots': [{'slot','kickoff','players':[...]}], 'bye': [...]}
    with slots in chronological order and bye = starters with no game this week.
    Each player carries name/position/team plus opp/home/kickoff."""
    ts_map = team_slots(week)
    starters = _starter_meta(franchise_id, week)

    buckets: dict[str, list] = {}
    slot_kick: dict[str, int] = {}
    bye: list[dict] = []
    for s in starters:
        g = ts_map.get(s["team"])
        if not g:
            bye.append(s)
            continue
        buckets.setdefault(g["slot"], []).append({
            **s, "opp": g["opp"], "home": g["home"], "kickoff": g["kickoff"],
        })
        slot_kick.setdefault(g["slot"], g["kickoff"])

    def _order(slot: str):
        idx = SLOT_ORDER.index(slot) if slot in SLOT_ORDER else 99
        return (idx, slot_kick.get(slot, 0))

    slots = []
    for slot in sorted(buckets, key=_order):
        players = sorted(buckets[slot], key=lambda p: p["position"])
        slots.append({"slot": slot, "kickoff": slot_kick.get(slot, 0),
                      "players": players})
    return {"week": week, "slots": slots, "bye": bye}


def player_game_time(name: str, team: str, week: int | None = None) -> dict:
    """When does one player play this week?

    Returns {'name','team','status', ...}. status: 'ok' (with slot/kickoff/
    opp/home), 'bye' (team not playing), or 'no_team' (no NFL team listed)."""
    if not team:
        return {"name": name, "team": team, "status": "no_team"}
    g = team_slots(week).get(team)
    if not g:
        return {"name": name, "team": team, "status": "bye"}
    return {"name": name, "team": team, "status": "ok", "slot": g["slot"],
            "kickoff": g["kickoff"], "opp": g["opp"], "home": g["home"]}
