from src.salary_tools import select_cuts


def _p(name, pos, salary, value, vor):
    return {"mfl_id": name, "name": name, "position": pos,
            "salary": salary, "dynasty_value": value, "vor": vor}


ROSTER = [
    # the only kicker — $0 value but must never be recommended as a cut
    _p("Kicker", "PK", 1_500_000, 0, 0),
    # obvious cut: expensive, below replacement
    _p("BustRB", "RB", 2_000_000, 300, -1500),
    # cheap dead weight: below the salary floor, should be skipped
    _p("MinDeal", "WR", 100_000, 50, -900),
    # startable players: positive VOR, never cut
    _p("StudRB", "RB", 3_000_000, 8000, 5000),
    _p("StudWR", "WR", 3_000_000, 7000, 4000),
    # filler to establish a salary quartile and group counts
    _p("RB3", "RB", 500_000, 900, -100),
    _p("WR2", "WR", 800_000, 2000, 1000),
    _p("WR3", "WR", 700_000, 1500, 500),
    _p("QB1", "QB", 4_000_000, 6000, 3000),
    _p("TE1", "TE", 900_000, 3000, 1500),
]


def test_only_kicker_is_protected():
    cuts = select_cuts(ROSTER)
    assert all(p["name"] != "Kicker" for p in cuts)


def test_bust_is_cut_first():
    cuts = select_cuts(ROSTER)
    assert cuts and cuts[0]["name"] == "BustRB"


def test_startable_players_never_cut():
    names = {p["name"] for p in select_cuts(ROSTER)}
    assert not names & {"StudRB", "StudWR", "QB1"}


def test_min_deals_skipped():
    names = {p["name"] for p in select_cuts(ROSTER)}
    assert "MinDeal" not in names


def test_group_never_cut_below_startable():
    # Two kickers: one is cuttable, but never both
    roster = ROSTER + [_p("Kicker2", "PK", 1_400_000, 0, 0)]
    cuts = select_cuts(roster, top_n=20)
    pk_cuts = [p for p in cuts if p["position"] == "PK"]
    assert len(pk_cuts) == 0  # 2 PKs - 1 cut = 1 left = min, cushion rule blocks it
