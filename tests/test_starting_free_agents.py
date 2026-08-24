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
}
SMAP = {"1": "s1", "2": "s2", "3": "s3", "4": "s4"}  # "5" has no Sleeper id

DEPTH = {
    "s1": {"order": 1, "slot": "WR", "status": "Active", "team": "KC", "injury": ""},
    "s2": {"order": 2, "slot": "WR", "status": "Active", "team": "SF", "injury": ""},
    "s3": {"order": 1, "slot": "RB", "status": "Active", "team": "NYJ",
           "injury": "Questionable"},
    "s4": {"order": 1, "slot": "TE", "status": "Active", "team": "", "injury": ""},
}
VALUE_MAP = {
    "1": {"salary": 12, "dynasty_value": 3200},
    "2": {"salary": 5, "dynasty_value": 900},
    "3": {"salary": 8, "dynasty_value": 2100},
}
SEASON_PROJ = {"1": {"points": 180.0}, "3": {"points": 140.0}}


@pytest.fixture
def patched():
    with patch.object(freeagents, "_fa_meta", return_value=FA_META), \
         patch.object(freeagents, "_depth_chart", return_value=DEPTH), \
         patch("src.sleeper_xwalk.get_sleeper_map", return_value=SMAP), \
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


def test_excludes_players_missing_from_the_crosswalk(patched):
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP)
    assert "Unmapped Uma" not in [r["name"] for r in rows]


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
        "Backup Bob",    # unprojected
    ]


def test_carries_depth_injury_and_salary(patched):
    rows = freeagents.starting_free_agents(season=2025, value_map=VALUE_MAP)
    sue = next(r for r in rows if r["name"] == "Starter Sue")
    assert sue["depth_slot"] == "RB" and sue["depth_order"] == 1
    assert sue["injury"] == "Questionable"
    assert sue["salary"] == 8
