"""Tests for weekly-report rendering. All data sources are patched."""
from unittest.mock import patch

import pytest

import src.discord_report as dr


FV = {
    "0002": {"QB": 8200, "RB": 11400, "WR": 15900, "TE": 4100},
    "0001": {"QB": 9100, "RB": 9800, "WR": 13200, "TE": 5200},
    "0003": {"QB": 7400, "RB": 12600, "WR": 14100, "TE": 3800},
}
GEMS = {
    "thin_positions": {"TE"},
    "pairs": [
        {"gem": {"name": "Ray Davis", "position": "RB", "dynasty_value": 1420},
         "drop": {"name": "Tyler Boyd", "position": "WR", "dynasty_value": 310}},
        {"gem": {"name": "Cade Otton", "position": "TE", "dynasty_value": 1180},
         "drop": None},
    ],
}
LOPSIDED = [{"timestamp": 1757030100, "favored": 1, "franchise1": "0004",
             "franchise2": "0007", "value_delta_pct": 0.31, "verdict": "FLEECE"}]


@pytest.fixture
def patched():
    with patch.object(dr, "franchise_positional_value", return_value=FV), \
         patch.object(dr, "waiver_gems", return_value=GEMS), \
         patch.object(dr, "recent_lopsided", return_value=LOPSIDED), \
         patch("src.mfl_api.franchise_name",
               side_effect=lambda f: {"0004": "Team Chaos", "0007": "Gridiron"}.get(f, f)):
        yield


# ---- the delta bar ----

def test_delta_bar_centred_and_directional():
    above = dr._delta_bar(100, 100)
    below = dr._delta_bar(-100, 100)
    half = dr._BAR_WIDTH // 2
    assert len(above) == len(below) == dr._BAR_WIDTH
    # above median fills to the right of centre, below fills to the left
    assert above.startswith("·" * half) and above.endswith("█" * half)
    assert below.startswith("█" * half) and below.endswith("·" * half)


def test_delta_bar_zero_scale_is_neutral():
    assert set(dr._delta_bar(0, 0)) == {"·"}


# ---- sections ----

def test_report_sections_shape(patched):
    sections = dr.report_sections({})
    assert len(sections) == 3
    assert all(isinstance(name, str) and isinstance(body, str)
               for name, body in sections)


def test_roster_section_includes_rank_and_all_positions(patched):
    name, body = dr._roster_health_section({})
    assert "Roster Health" in name and "of 3" in name
    for pos in ("QB", "RB", "WR", "TE"):
        assert pos in body


def test_waiver_section_flags_thin_position(patched):
    name, body = dr._waiver_section({})
    assert "TE" in name              # thin position called out in the heading
    assert "Ray Davis" in body and "Cade Otton" in body
    assert "Tyler Boyd" in body      # the suggested drop
    assert "🎯" in body               # the thin-spot marker on Otton (TE)


def test_trades_section_empty_state():
    with patch.object(dr, "recent_lopsided", return_value=[]):
        name, body = dr._trades_section()
    assert "None flagged" in body


def test_trades_section_names_winner_first(patched):
    _, body = dr._trades_section()
    assert body.index("Team Chaos") < body.index("Gridiron")
    assert "31%" in body


# ---- full renders ----

def test_build_report_text(patched):
    text = dr.build_report({})
    assert "Fantasy Trade Bot Report" in text
    assert "Roster Health" in text and "Waiver Gems" in text


def test_build_report_embed_fields(patched):
    pytest.importorskip("discord")
    embed = dr.build_report_embed({})
    assert embed.title == "🏈 Fantasy Trade Bot Report"
    assert len(embed.fields) == 3
    # Discord rejects field values over 1024 chars
    assert all(len(f.value) <= 1024 for f in embed.fields)
