from src.lineup import solve_lineup, parse_lineup_rules, LineupRules


RULES = LineupRules(
    groups=[
        (("QB",), 1, 1),
        (("RB",), 1, 2),
        (("WR",), 2, 3),
        (("LB",), 1, 2),
        (("CB", "S"), 1, 2),
    ],
    iop_total=5,
    idp_total=3,
)


def _p(pid, pos, proj):
    return {"mfl_id": pid, "name": f"P{pid}", "position": pos, "proj": proj}


ROSTER = [
    _p("qb1", "QB", 20), _p("qb2", "QB", 25),
    _p("rb1", "RB", 15), _p("rb2", "RB", 12), _p("rb3", "RB", 3),
    _p("wr1", "WR", 14), _p("wr2", "WR", 13), _p("wr3", "WR", 11), _p("wr4", "WR", 2),
    _p("lb1", "LB", 10), _p("lb2", "LB", 9), _p("lb3", "LB", 1),
    _p("cb1", "CB", 8), _p("s1", "S", 7), _p("cb2", "CB", 6),
]


def test_group_minimums_met():
    lineup = solve_lineup(ROSTER, RULES)
    by_slot = {}
    for p in lineup:
        by_slot.setdefault(p["slot"], []).append(p)
    assert len(by_slot["QB"]) == 1
    assert len(by_slot["RB"]) >= 1
    assert len(by_slot["WR"]) >= 2
    assert len(by_slot["LB"]) >= 1
    assert len(by_slot["CB+S"]) >= 1


def test_best_qb_starts():
    lineup = solve_lineup(ROSTER, RULES)
    qbs = [p for p in lineup if p["position"] == "QB"]
    assert [q["mfl_id"] for q in qbs] == ["qb2"]  # 25 > 20


def test_side_totals_enforced():
    lineup = solve_lineup(ROSTER, RULES)
    iop = [p for p in lineup if p["position"] in ("QB", "RB", "WR", "TE", "PK")]
    idp = [p for p in lineup if p["position"] in ("DT", "DE", "LB", "CB", "S")]
    assert len(iop) == 5
    assert len(idp) == 3


def test_flex_goes_to_best_projection():
    # After minimums (QB1 RB1 WR2 LB1 CBS1 = 6 slots), flex: 1 iop + 1 idp.
    # Best remaining iop is rb2 (12) over wr3 (11); best remaining idp is lb2 (9).
    lineup = solve_lineup(ROSTER, RULES)
    ids = {p["mfl_id"] for p in lineup}
    assert "rb2" in ids
    assert "lb2" in ids
    assert "rb3" not in ids and "wr4" not in ids and "lb3" not in ids


def test_group_max_respected():
    # WR max is 3 — even if WRs dominate projections, no 4th WR.
    heavy_wr = ROSTER + [_p("wr5", "WR", 30), _p("wr6", "WR", 29)]
    lineup = solve_lineup(heavy_wr, RULES)
    wrs = [p for p in lineup if p["position"] == "WR"]
    assert len(wrs) <= 3


def test_parse_lineup_rules():
    league = {"starters": {
        "iop_starters": "8", "idp_starters": "11", "count": "19",
        "position": [
            {"name": "QB", "limit": "1"},
            {"name": "RB", "limit": "1-2"},
            {"name": "DT+DE", "limit": "3-4"},
        ],
    }}
    rules = parse_lineup_rules(league)
    assert rules.iop_total == 8 and rules.idp_total == 11 and rules.total == 19
    assert (("QB",), 1, 1) in rules.groups
    assert (("RB",), 1, 2) in rules.groups
    assert (("DT", "DE"), 3, 4) in rules.groups
