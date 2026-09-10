"""Tests for MFL request routing.

MFL serves some exports only from api.myfantasyleague.com and signals a wrong
host with HTTP 200 plus an error in the body — which parses as an empty result
unless you actually read the payload.
"""
from unittest.mock import patch

import pytest

from src import mfl_api


# Verbatim shape of the failure seen on the deploy host.
WRONG_HOST_ERROR = {
    "version": "1.0",
    "encoding": "utf-8",
    "error": {"$t": "Invalid request. This API request must go to "
                    "api.myfantasyleague.com"},
}
SCHEDULE_OK = {"nflSchedule": {"week": "1", "matchup": [
    {"kickoff": "1757030100", "team": [{"id": "KC", "isHome": "1"},
                                       {"id": "LAC", "isHome": "0"}]}]}}


@pytest.fixture(autouse=True)
def _reset():
    mfl_api._memo.clear()
    mfl_api._global_endpoints.clear()
    mfl_api._global_endpoints.add("nflSchedule")
    mfl_api._detected_year = 2026
    yield
    mfl_api._memo.clear()


def test_error_text_unwraps_mfl_shape():
    assert "api.myfantasyleague.com" in mfl_api.error_text(WRONG_HOST_ERROR)
    assert mfl_api.error_text(SCHEDULE_OK) == ""
    assert mfl_api.error_text({}) == ""
    assert mfl_api.error_text({"error": "plain string"}) == "plain string"


def test_nflschedule_goes_to_the_api_host_without_l():
    """The L parameter is what MFL actually rejects: with it, MFL treats a
    league-independent export as league-scoped and refuses it on either host."""
    with patch.object(mfl_api, "_request", return_value=SCHEDULE_OK) as req:
        mfl_api._get("nflSchedule", {"W": "1"})
    endpoint, params, host = req.call_args[0]
    assert host == mfl_api.API_HOST
    assert "L" not in params
    assert params["W"] == "1"


def test_league_endpoints_still_send_l():
    with patch.object(mfl_api, "_request", return_value={"rosters": {}}) as req:
        mfl_api._get("rosters")
    assert req.call_args[0][1]["L"] == mfl_api.MFL_LEAGUE_ID


def test_league_endpoints_still_use_the_league_host():
    with patch.object(mfl_api, "_request", return_value={"rosters": {}}) as req:
        mfl_api._get("rosters")
    assert req.call_args[0][2].endswith(".myfantasyleague.com")
    assert req.call_args[0][2] != mfl_api.API_HOST


def test_wrong_host_error_triggers_retry_and_is_remembered():
    """An export MFL reclassifies later should fix itself after one retry."""
    mfl_api._global_endpoints.discard("nflSchedule")
    calls = []

    def fake(endpoint, params, host):
        calls.append((host, dict(params)))
        # Mirrors MFL: anything carrying L is refused, wherever it is sent.
        return WRONG_HOST_ERROR if "L" in params else SCHEDULE_OK

    with patch.object(mfl_api, "_request", side_effect=fake):
        data = mfl_api._get("nflSchedule", {"W": "1"})

    assert len(calls) == 2
    assert "L" in calls[0][1]                      # first try carried L
    assert calls[1][0] == mfl_api.API_HOST
    assert "L" not in calls[1][1]                  # retry dropped it
    assert data == SCHEDULE_OK
    assert "nflSchedule" in mfl_api._global_endpoints  # learned


def test_errors_are_not_memoized():
    """A cached error would persist for the whole memo window."""
    with patch.object(mfl_api, "_request", return_value=WRONG_HOST_ERROR):
        mfl_api._get("someEndpoint")
    assert not mfl_api._memo          # nothing cached

    with patch.object(mfl_api, "_request", return_value={"ok": 1}) as req:
        assert mfl_api._get("someEndpoint") == {"ok": 1}
    assert req.called                 # retried rather than serving the error


def test_successful_responses_are_memoized():
    with patch.object(mfl_api, "_request", return_value={"rosters": {}}) as req:
        mfl_api._get("rosters")
        mfl_api._get("rosters")
    assert req.call_count == 1


def test_schedule_parses_after_routing_fix():
    with patch.object(mfl_api, "_request", return_value=SCHEDULE_OK):
        games = mfl_api.get_nfl_schedule(1)
    assert len(games) == 1
    assert {t["id"] for t in games[0]["team"]} == {"KC", "LAC"}


def test_schedule_returns_empty_on_error_rather_than_raising():
    with patch.object(mfl_api, "_request", return_value=WRONG_HOST_ERROR):
        assert mfl_api.get_nfl_schedule(1) == []
