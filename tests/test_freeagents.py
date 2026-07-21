from unittest.mock import patch

from src.freeagents import top_free_agents

FREE_AGENTS = [{"id": "1"}, {"id": "2"}, {"id": "3"}]

PLAYERS = [
    {"id": "1", "name": "Vet, Wide", "position": "WR", "team": "KC", "draft_year": "2019"},
    {"id": "2", "name": "Rook, Running", "position": "RB", "team": "DAL", "draft_year": "2026"},
    {"id": "3", "name": "Backup, Quarter", "position": "QB", "team": "NYG", "draft_year": "2020"},
]

SEASON_PROJ = {
    "1": {"points": 150.0, "sources": 2, "updated_at": ""},
    "2": {"points": 200.0, "sources": 1, "updated_at": ""},
    "3": {"points": 80.0, "sources": 1, "updated_at": ""},
}

WEEK_PROJ = {"1": {"points": 12.0, "sources": 2, "updated_at": ""}}

VALUE_MAP = {
    "1": {"salary": 5.0},
    "2": {"salary": 1.0},
    "3": {"salary": 0.0},
}


def _get_projected_points(season, week=None, max_age_hours=12.0):
    return WEEK_PROJ if week else SEASON_PROJ


@patch("src.freeagents.get_projected_points", side_effect=_get_projected_points)
@patch("src.freeagents.mfl_api.get_players", return_value=PLAYERS)
@patch("src.freeagents.mfl_api.get_free_agents", return_value=FREE_AGENTS)
def test_sorted_by_season_points(mock_fa, mock_players, mock_proj):
    rows = top_free_agents(season=2026, week=5, value_map=VALUE_MAP)
    assert [r["mfl_id"] for r in rows] == ["2", "1", "3"]
    assert rows[1]["week_pts"] == 12.0
    assert rows[0]["week_pts"] is None


@patch("src.freeagents.get_projected_points", side_effect=_get_projected_points)
@patch("src.freeagents.mfl_api.get_players", return_value=PLAYERS)
@patch("src.freeagents.mfl_api.get_free_agents", return_value=FREE_AGENTS)
def test_position_filter(mock_fa, mock_players, mock_proj):
    rows = top_free_agents(position="RB", season=2026, value_map=VALUE_MAP)
    assert [r["mfl_id"] for r in rows] == ["2"]


@patch("src.freeagents.get_projected_points", side_effect=_get_projected_points)
@patch("src.freeagents.mfl_api.get_players", return_value=PLAYERS)
@patch("src.freeagents.mfl_api.get_free_agents", return_value=FREE_AGENTS)
def test_rookies_only(mock_fa, mock_players, mock_proj):
    rows = top_free_agents(rookies=True, season=2026, value_map=VALUE_MAP)
    assert [r["mfl_id"] for r in rows] == ["2"]


@patch("src.freeagents.get_projected_points", side_effect=_get_projected_points)
@patch("src.freeagents.mfl_api.get_players", return_value=PLAYERS)
@patch("src.freeagents.mfl_api.get_free_agents", return_value=FREE_AGENTS)
def test_exclude_rookies(mock_fa, mock_players, mock_proj):
    rows = top_free_agents(rookies=False, season=2026, value_map=VALUE_MAP)
    assert [r["mfl_id"] for r in rows] == ["1", "3"]
