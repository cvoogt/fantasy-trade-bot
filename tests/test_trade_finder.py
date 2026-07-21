from unittest.mock import patch

from src.trade_finder import find_trades_for_player

# franchise 0001 = me; 0002 = owner of the target; 0003 = filler for medians.
VALUE_MAP = {
    # my roster
    "me_wr1": {"name": "My WR1", "position": "WR", "dynasty_value": 5000,
               "salary": 30, "value_per_dollar": 166, "vor": 3000},
    "me_rb1": {"name": "My RB1", "position": "RB", "dynasty_value": 3000,
               "salary": 20, "value_per_dollar": 150, "vor": 1000},
    "me_rb2": {"name": "My RB2", "position": "RB", "dynasty_value": 2000,
               "salary": 10, "value_per_dollar": 200, "vor": 500},
    "me_te1": {"name": "My TE1", "position": "TE", "dynasty_value": 2900,
               "salary": 8, "value_per_dollar": 362, "vor": 900},
    # the target, owned by 0002
    "target": {"name": "Target Stud", "position": "QB", "dynasty_value": 4900,
               "salary": 25, "value_per_dollar": 196, "vor": 3000},
    # filler on 0002/0003 so medians exist
    "opp_wr": {"name": "Opp WR", "position": "WR", "dynasty_value": 1000,
               "salary": 5, "value_per_dollar": 200, "vor": 0},
    "flr_qb": {"name": "Flr QB", "position": "QB", "dynasty_value": 800,
               "salary": 5, "value_per_dollar": 160, "vor": 0},
}

ROSTERS = [
    {"id": "0001", "player": [{"id": "me_wr1"}, {"id": "me_rb1"},
                              {"id": "me_rb2"}, {"id": "me_te1"}]},
    {"id": "0002", "player": [{"id": "target"}, {"id": "opp_wr"}]},
    {"id": "0003", "player": [{"id": "flr_qb"}]},
]


def _patched():
    return patch("src.trade_finder.mfl_api.get_rosters", return_value=ROSTERS)


def test_returns_packages_for_target():
    with _patched():
        res = find_trades_for_player("target", "0001", VALUE_MAP)
    assert res["status"] == "ok"
    assert res["owner"] == "0002"
    assert res["offers"], "expected at least one fair package"
    # Every offered package must land inside the fair band (±15% default).
    for o in res["offers"]:
        assert o["gap_pct"] <= 0.15 + 1e-9
    # The single closest package (My WR1 @ 5000 vs 4900) should be offered.
    singles = [o for o in res["offers"] if len(o["give"]) == 1]
    assert any(o["give"][0]["mfl_id"] == "me_wr1" for o in singles)


def test_offers_are_distinct():
    with _patched():
        res = find_trades_for_player("target", "0001", VALUE_MAP)
    sets = [frozenset(p["mfl_id"] for p in o["give"]) for o in res["offers"]]
    assert len(sets) == len(set(sets))


def test_owning_target_is_flagged():
    with _patched():
        res = find_trades_for_player("me_wr1", "0001", VALUE_MAP)
    assert res["status"] == "mine"
    assert res["offers"] == []


def test_free_agent_target():
    fa_map = {**VALUE_MAP, "fa_guy": {"name": "FA Guy", "position": "WR",
              "dynasty_value": 3000, "salary": 0, "value_per_dollar": 0, "vor": 0}}
    with _patched():
        res = find_trades_for_player("fa_guy", "0001", fa_map)
    assert res["status"] == "free_agent"


def test_net_is_target_minus_package():
    with _patched():
        res = find_trades_for_player("target", "0001", VALUE_MAP)
    for o in res["offers"]:
        assert abs(o["net"] - (4900 - o["give_value"])) < 1e-6
