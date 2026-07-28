"""Tests for the /gametime slot mapping. NFL schedule + roster are patched so
nothing hits the network."""
from datetime import datetime
from unittest.mock import patch

from src import gametime


def _ts(month, day, hour, minute=0):
    """Unix seconds for a US-Eastern wall-clock time in 2025 (NFL season)."""
    from zoneinfo import ZoneInfo
    dt = datetime(2025, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    return int(dt.timestamp())


# 2025 calendar anchors: Thu 9/4, Sun 9/7, Mon 9/8.
THU_NIGHT = _ts(9, 4, 20, 15)   # Thursday 8:15 PM ET
SUN_EARLY = _ts(9, 7, 13, 0)    # Sunday 1:00 PM ET
SUN_LATE = _ts(9, 7, 16, 25)    # Sunday 4:25 PM ET
SUN_NIGHT = _ts(9, 7, 20, 20)   # Sunday 8:20 PM ET
MON_NIGHT = _ts(9, 8, 20, 15)   # Monday 8:15 PM ET
SUN_AM = _ts(9, 7, 9, 30)       # Sunday 9:30 AM ET (London)


def test_slot_for_windows():
    assert gametime.slot_for(THU_NIGHT) == "Thu Night"
    assert gametime.slot_for(SUN_AM) == "Sun AM"
    assert gametime.slot_for(SUN_EARLY) == "Sun Early"
    assert gametime.slot_for(SUN_LATE) == "Sun Late"
    assert gametime.slot_for(SUN_NIGHT) == "Sun Night"
    assert gametime.slot_for(MON_NIGHT) == "Mon Night"
    assert gametime.slot_for(0) == "Other"


def _schedule():
    return [
        {"kickoff": str(THU_NIGHT), "team": [
            {"id": "PHI", "isHome": "1"}, {"id": "DAL", "isHome": "0"}]},
        {"kickoff": str(SUN_EARLY), "team": [
            {"id": "ATL", "isHome": "0"}, {"id": "NO", "isHome": "1"}]},
        {"kickoff": str(SUN_LATE), "team": [
            {"id": "KC", "isHome": "1"}, {"id": "LAC", "isHome": "0"}]},
        {"kickoff": str(MON_NIGHT), "team": [
            {"id": "BUF", "isHome": "0"}, {"id": "NYJ", "isHome": "1"}]},
    ]


def test_team_slots_parses_kickoff_and_opponent():
    with patch.object(gametime.mfl_api, "get_nfl_schedule", return_value=_schedule()):
        ts = gametime.team_slots(1)
    assert ts["ATL"]["slot"] == "Sun Early"
    assert ts["ATL"]["opp"] == "NO"
    assert ts["ATL"]["home"] is False
    assert ts["NO"]["home"] is True
    assert ts["KC"]["slot"] == "Sun Late"


def _players():
    return [
        {"id": "1", "name": "Hurts, Jalen", "position": "QB", "team": "PHI"},
        {"id": "2", "name": "Robinson, Bijan", "position": "RB", "team": "ATL"},
        {"id": "3", "name": "Kelce, Travis", "position": "TE", "team": "KC"},
        {"id": "4", "name": "Allen, Josh", "position": "QB", "team": "BUF"},
        {"id": "5", "name": "Bye, Guy", "position": "WR", "team": "CLE"},  # no game
    ]


def _rosters():
    return [{"id": "0002", "player": [{"id": str(i)} for i in range(1, 6)]}]


def _advice(ids):
    return {"current": set(), "optimal": [{"mfl_id": i} for i in ids]}


def test_starters_by_slot_groups_and_orders():
    with patch.object(gametime.mfl_api, "get_nfl_schedule", return_value=_schedule()), \
         patch.object(gametime.mfl_api, "get_players", return_value=_players()), \
         patch("src.lineup.lineup_advice", return_value=_advice(["1", "2", "3", "4", "5"])):
        data = gametime.starters_by_slot("0002", 1)

    slot_names = [s["slot"] for s in data["slots"]]
    assert slot_names == ["Thu Night", "Sun Early", "Sun Late", "Mon Night"]
    # CLE isn't on the schedule -> bye bucket
    assert [p["name"] for p in data["bye"]] == ["Bye, Guy"]
    early = next(s for s in data["slots"] if s["slot"] == "Sun Early")
    assert early["players"][0]["name"] == "Robinson, Bijan"
    assert early["players"][0]["opp"] == "NO"


def test_starters_by_slot_uses_only_the_lineup():
    # Only ids 1 and 5 are in the lineup -> the rest of the roster is ignored.
    with patch.object(gametime.mfl_api, "get_nfl_schedule", return_value=_schedule()), \
         patch.object(gametime.mfl_api, "get_players", return_value=_players()), \
         patch("src.lineup.lineup_advice", return_value=_advice(["1", "5"])):
        data = gametime.starters_by_slot("0002", 1)

    everyone = [p["name"] for s in data["slots"] for p in s["players"]] + \
               [p["name"] for p in data["bye"]]
    assert sorted(everyone) == ["Bye, Guy", "Hurts, Jalen"]


def test_player_game_time_ok_bye_and_no_team():
    with patch.object(gametime.mfl_api, "get_nfl_schedule", return_value=_schedule()):
        ok = gametime.player_game_time("Bijan Robinson", "ATL", 1)
        bye = gametime.player_game_time("Nobody", "CLE", 1)
    no_team = gametime.player_game_time("Ghost", "", 1)

    assert ok["status"] == "ok" and ok["slot"] == "Sun Early" and ok["opp"] == "NO"
    assert bye["status"] == "bye"
    assert no_team["status"] == "no_team"


def test_fmt_kickoff_is_central():
    # 1:00 PM ET kickoff displays as 12:00 PM CT.
    assert gametime.fmt_kickoff(SUN_EARLY) == "Sun 12:00 PM CT"
    assert gametime.fmt_kickoff(0) == "TBD"


def test_slots_stay_eastern_anchored():
    # Display is CT, but a 1pm ET game must still classify as the early Sunday
    # window (not "Sun AM" off a noon-CT read).
    assert gametime.slot_for(SUN_EARLY) == "Sun Early"
