"""Tests for multi-source depth charts (Sleeper + ESPN)."""
from unittest.mock import patch

import pytest

from src import depth_chart as dc


# A trimmed ESPN depthcharts payload: formations -> positions -> athletes,
# each athlete behind a $ref whose trailing id is the ESPN athlete id.
ESPN_PAYLOAD = {
    "items": [
        {
            "name": "3WR Base",
            "positions": {
                "wr": {
                    "position": {"name": "Wide Receiver", "abbreviation": "WR"},
                    "athletes": [
                        {"rank": 1, "athlete": {"$ref": "http://x/athletes/101?lang=en"}},
                        {"rank": 2, "athlete": {"$ref": "http://x/athletes/102?lang=en"}},
                    ],
                },
                "rb": {
                    "position": {"abbreviation": "RB"},
                    "athletes": [
                        {"rank": 1, "athlete": {"$ref": "http://x/athletes/103"}},
                    ],
                },
            },
        },
        {
            # Same player in a second formation with a worse rank — best wins.
            "name": "Goal Line",
            "positions": {
                "wr": {
                    "position": {"abbreviation": "WR"},
                    "athletes": [
                        {"rank": 4, "athlete": {"$ref": "http://x/athletes/101"}},
                    ],
                },
            },
        },
    ]
}


# ---- ESPN payload parsing ----

def test_parses_athlete_ids_slots_and_ranks():
    rows = dict((aid, (slot, rank))
                for aid, slot, rank in dc._parse_depthchart(ESPN_PAYLOAD))
    assert rows["101"] == ("WR", 1)
    assert rows["102"] == ("WR", 2)
    assert rows["103"] == ("RB", 1)


def test_best_rank_wins_across_formations():
    rows = dict((aid, rank) for aid, _slot, rank in dc._parse_depthchart(ESPN_PAYLOAD))
    assert rows["101"] == 1        # not the goal-line rank of 4


def test_parser_tolerates_junk():
    """ESPN changing shape must not raise — the caller falls back to Sleeper."""
    for junk in ({}, {"items": []}, {"items": [{"positions": None}]},
                 {"items": [{"positions": {"x": {"athletes": [{"rank": "n/a"}]}}}]},
                 {"items": [{"positions": {"x": {"athletes": [
                     {"rank": 1, "athlete": {"$ref": "no-id-here"}}]}}}]}):
        assert dc._parse_depthchart(junk) == []


# ---- combining sources ----

SMAP = {"m1": "s1", "m2": "s2", "m3": "s3"}
SLEEPER = {
    "s1": {"order": 1, "slot": "WR", "status": "Active", "team": "KC", "injury": ""},
    "s2": {"order": 3, "slot": "WR", "status": "Active", "team": "SF", "injury": ""},
    "s3": {"order": None, "slot": "", "status": "Active", "team": "NYJ", "injury": ""},
}
MFL_PLAYERS = [
    {"id": "m1", "espn_id": "101"},
    {"id": "m2", "espn_id": "102"},
    {"id": "m3", "espn_id": "103"},
]


@pytest.fixture(autouse=True)
def _clear():
    dc.clear_cache()
    yield
    dc.clear_cache()


def _combined(espn):
    with patch.object(dc, "sleeper_depth", return_value=SLEEPER), \
         patch.object(dc, "get_espn_depth", return_value=espn), \
         patch("src.sleeper_xwalk.get_sleeper_map", return_value=SMAP), \
         patch("src.mfl_api.get_players", return_value=MFL_PLAYERS):
        return dc.combined_depth(2025)


def test_counts_agreeing_sources():
    out = _combined({"101": {"slot": "WR", "order": 1}})
    assert out["m1"]["sources"] == 2      # Sleeper + ESPN
    assert out["m2"]["sources"] == 1      # Sleeper only


def test_takes_the_better_rank_of_the_two():
    out = _combined({"102": {"slot": "WR", "order": 1}})
    # Sleeper says 3, ESPN says 1 -> treated as a starter
    assert out["m2"]["order"] == 1
    assert out["m2"]["sleeper_order"] == 3 and out["m2"]["espn_order"] == 1


def test_espn_only_player_still_appears():
    """Sleeper has no order for m3; ESPN alone should surface them."""
    out = _combined({"103": {"slot": "RB", "order": 1}})
    assert out["m3"]["order"] == 1
    assert out["m3"]["sources"] == 1


def test_players_on_no_chart_are_dropped():
    out = _combined({})
    assert "m3" not in out                # neither source ranked them


def test_falls_back_to_sleeper_when_espn_fails():
    with patch.object(dc, "sleeper_depth", return_value=SLEEPER), \
         patch.object(dc, "get_espn_depth", side_effect=RuntimeError("ESPN down")), \
         patch("src.sleeper_xwalk.get_sleeper_map", return_value=SMAP), \
         patch("src.mfl_api.get_players", return_value=MFL_PLAYERS):
        out = dc.combined_depth(2025)
    assert out["m1"]["order"] == 1        # Sleeper-only view still works
    assert all(v["sources"] == 1 for v in out.values())


def test_use_espn_false_skips_the_network_entirely():
    with patch.object(dc, "sleeper_depth", return_value=SLEEPER), \
         patch.object(dc, "get_espn_depth") as espn, \
         patch("src.sleeper_xwalk.get_sleeper_map", return_value=SMAP), \
         patch("src.mfl_api.get_players", return_value=MFL_PLAYERS):
        dc.combined_depth(2025, use_espn=False)
    espn.assert_not_called()
