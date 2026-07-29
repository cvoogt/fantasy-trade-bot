"""Tests for the roster injury listing (/injury)."""
from unittest.mock import patch

import pytest

from src import injuries


ROSTER = [{"id": "0002", "player": [{"id": m} for m in ("1", "2", "3", "4", "5")]}]
SMAP = {"1": "s1", "2": "s2", "3": "s3", "4": "s4"}  # "5" missing from Sleeper

SLEEPER_ROWS = [
    {"sleeper_id": "s1", "injury_status": "Questionable", "name": "Quincy Q", "position": "WR"},
    {"sleeper_id": "s2", "injury_status": "IR", "name": "Ira R", "position": "RB"},
    {"sleeper_id": "s3", "injury_status": "", "name": "Hale Thy", "position": "QB"},
    {"sleeper_id": "s4", "injury_status": "Out", "name": "Otto Ut", "position": "TE"},
]
MFL_PLAYERS = [{"id": "5", "name": "Miss Ing", "position": "LB"}]


@pytest.fixture
def patched(tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "inj.db"))
    db.init_db()
    conn = db.get_conn()
    for r in SLEEPER_ROWS:
        conn.execute(
            "INSERT INTO sleeper_players (sleeper_id, injury_status, name, position) "
            "VALUES (?, ?, ?, ?)",
            (r["sleeper_id"], r["injury_status"], r["name"], r["position"]))
    conn.commit()
    conn.close()

    with patch.object(injuries.mfl_api, "get_rosters", return_value=ROSTER), \
         patch.object(injuries.mfl_api, "get_players", return_value=MFL_PLAYERS), \
         patch.object(injuries, "get_sleeper_map", return_value=SMAP), \
         patch.object(injuries, "refresh_players_cache", return_value=0):
        yield


def test_sorted_worst_status_first(patched):
    out = injuries.roster_injuries("0002")
    assert [p["status"] for p in out] == [
        "IR", "Out", "Questionable", "Healthy", "Healthy"]


def test_blank_status_becomes_healthy(patched):
    out = injuries.roster_injuries("0002")
    healthy = {p["name"] for p in out if p["status"] == "Healthy"}
    assert "Hale Thy" in healthy


def test_player_missing_from_sleeper_falls_back_to_mfl(patched):
    out = injuries.roster_injuries("0002")
    missing = next(p for p in out if p["mfl_id"] == "5")
    assert missing["name"] == "Miss Ing"       # name came from MFL
    assert missing["position"] == "LB"
    assert missing["status"] == "Healthy"


def test_covers_whole_roster(patched):
    assert len(injuries.roster_injuries("0002")) == 5


def test_hide_healthy(patched):
    out = injuries.roster_injuries("0002", include_healthy=False)
    assert [p["status"] for p in out] == ["IR", "Out", "Questionable"]
