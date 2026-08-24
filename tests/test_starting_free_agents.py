"""Tests for finding NFL starters sitting in the league's free-agent pool."""
from unittest.mock import patch

import pytest

from src import freeagents


FA_META = {
    "1": {"name": "Starter Sam", "position": "WR", "team": "KC", "draft_year": "2023"},
    "2": {"name": "Backup Bob", "position": "WR", "team": "SF", "draft_year": "2022"},
    "3": {"name": "Starter Sue", "position": "RB", "team": "NYJ", "draft_year": "2024"},
    "4": {"name": "Nofl Ned", "position": "TE", "team": "FA", "draft_year": "2021"},
    "5": {"name": "Unmapped Uma", "position": "QB", "team": "DAL", "draft_year": "2020"},
    "6": {"name": "Retired Rex", "position": "QB", "team": "LV", "draft_year": "2012"},
    # Kickers aren't in FantasyCalc, so they never reach the value map.
    "7": {"name": "Kicky Ken", "position": "PK", "team": "CIN", "draft_year": "2019"},
}
# combined_depth() is keyed by MFL id and carries per-source ranks.
DEPTH = {
    "1": {"order": 1, "slot": "WR", "status": "Active", "team": "KC", "injury": "",
          "sources": 2, "sleeper_order": 1, "espn_order": 1},
    "2": {"order": 2, "slot": "WR", "status": "Active", "team": "SF", "injury": "",
          "sources": 2, "sleeper_order": 2, "espn_order": 2},
    "3": {"order": 1, "slot": "RB", "status": "Active", "team": "NYJ",
          "injury": "Questionable", "sources": 1, "sleeper_order": 1,
          "espn_order": None},
    "4": {"order": 1, "slot": "TE", "status": "Active", "team": "", "injury": "",
          "sources": 1, "sleeper_order": 1, "espn_order": None},
    "6": {"order": 1, "slot": "QB", "status": "Inactive", "team": "LV", "injury": "",
          "sources": 1, "sleeper_order": 1, "espn_order": None},
    "7": {"order": 1, "slot": "PK", "status": "Active", "team": "CIN", "injury": "",
          "sources": 2, "sleeper_order": 1, "espn_order": 1},
}

# MFL prices every player, including the kicker the value map has no entry for.
SALARIES = {"1": 12.0, "2": 5.0, "3": 8.0, "7": 4.0}
VALUE_MAP = {
    "1": {"salary": 12, "dynasty_value": 3200},
    "2": {"salary": 5, "dynasty_value": 900},
    "3": {"salary": 8, "dynasty_value": 2100},
}
SEASON_PROJ = {"1": {"points": 180.0}, "3": {"points": 140.0}}


@pytest.fixture
def patched():
    with patch.object(freeagents, "_fa_meta", return_value=FA_META), \
         patch("src.depth_chart.combined_depth", return_value=DEPTH), \
         patch.object(freeagents.mfl_api, "salary_map", return_value=SALARIES), \
         patch.object(freeagents, "get_projected_points",
                      side_effect=lambda s, w: SEASON_PROJ if w is None else {}):
        yield


def test_only_returns_depth_chart_starters(patched):
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP)
    names = [r["name"] for r in rows]
    assert "Starter Sam" in names and "Starter Sue" in names
    assert "Backup Bob" not in names        # depth order 2


def test_excludes_players_with_no_nfl_team(patched):
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP)
    assert "Nofl Ned" not in [r["name"] for r in rows]


def test_excludes_inactive_players(patched):
    """A stale depth-chart row shouldn't surface a retired player."""
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP)
    assert "Retired Rex" not in [r["name"] for r in rows]


def test_excludes_players_missing_from_the_crosswalk(patched):
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP)
    assert "Unmapped Uma" not in [r["name"] for r in rows]


def test_require_both_sources(patched):
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP,
                                           require_both_sources=True)
    names = [r["name"] for r in rows]
    # Sue is Sleeper-only, so she drops out; Sam and the kicker are on both.
    assert names == ["Starter Sam", "Kicky Ken"]


def test_kicker_salary_comes_from_mfl_not_the_value_map(patched):
    """Kickers aren't in FantasyCalc so the value map has no entry for them —
    salary must come from MFL or they all read as $0."""
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP)
    ken = next(r for r in rows if r["name"] == "Kicky Ken")
    assert ken["mfl_id"] not in VALUE_MAP     # genuinely absent from the map
    assert ken["salary"] == 4.0               # still priced correctly


def test_carries_per_source_ranks(patched):
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP)
    sam = next(r for r in rows if r["name"] == "Starter Sam")
    assert sam["sources"] == 2
    assert sam["sleeper_order"] == 1 and sam["espn_order"] == 1


def test_max_depth_includes_backups(patched):
    rows = freeagents.starting_free_agents(season=2025, max_depth=2,
                                           value_map=VALUE_MAP)
    assert "Backup Bob" in [r["name"] for r in rows]


def test_position_filter(patched):
    rows = freeagents.starting_free_agents(position="RB", season=2025,
                                           value_map=VALUE_MAP)
    assert [r["name"] for r in rows] == ["Starter Sue"]


def test_sorted_by_projection_with_unprojected_last(patched):
    rows = freeagents.starting_free_agents(season=2025, max_depth=2,
                                           value_map=VALUE_MAP)
    assert [r["name"] for r in rows] == [
        "Starter Sam",   # 180 pts
        "Starter Sue",   # 140 pts
        # Both unprojected and both on two charts, so dynasty value breaks the
        # tie — the kicker has none (FantasyCalc doesn't value kickers).
        "Backup Bob",
        "Kicky Ken",
    ]


def test_carries_depth_injury_and_salary(patched):
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP)
    sue = next(r for r in rows if r["name"] == "Starter Sue")
    assert sue["depth_slot"] == "RB" and sue["depth_order"] == 1
    assert sue["injury"] == "Questionable"
    assert sue["salary"] == 8
