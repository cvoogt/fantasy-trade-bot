from src.trade_scorer import score_trade

VALUE_MAP = {
    "0001": {"name": "Star QB", "position": "QB", "dynasty_value": 8000,
             "salary": 40, "value_per_dollar": 200, "vor": 5000},
    "0002": {"name": "Elite WR", "position": "WR", "dynasty_value": 7500,
             "salary": 35, "value_per_dollar": 214, "vor": 4000},
    "0003": {"name": "Mid RB", "position": "RB", "dynasty_value": 4000,
             "salary": 20, "value_per_dollar": 200, "vor": 1500},
    "0004": {"name": "Bench WR", "position": "WR", "dynasty_value": 1200,
             "salary": 5, "value_per_dollar": 240, "vor": 0},
}


def test_lean_trade():
    # Side 1 gives Star QB (8000), gets Elite WR (7500): ~6% gap -> LEAN.
    # Side 1 gave the bigger package, so Side 2 comes out ahead.
    r = score_trade(["0001"], ["0002"], VALUE_MAP)
    assert r.value_delta == 500
    assert r.verdict == "LEAN"
    assert r.favored == 2


def test_truly_fair():
    # Same player both sides hypothetically -> 0% delta
    r = score_trade(["0002"], ["0002"], VALUE_MAP)
    assert r.verdict == "FAIR"
    assert r.favored == 0


def test_fleece():
    # Side 1 gives Star QB + Elite WR (15500), gets Mid RB (4000): huge gap.
    # Side 1 wildly overpaid, so Side 2 is the one fleecing.
    r = score_trade(["0001", "0002"], ["0003"], VALUE_MAP)
    assert r.verdict == "FLEECE-OVERPAY"
    assert r.favored == 2


def test_unmatched_ids():
    r = score_trade(["0001", "9999"], ["0002"], VALUE_MAP)
    assert "9999" in r.side1.unmatched
    assert r.side1.total_value == 8000


def test_positional_fit_flag():
    # owner 'A' is thin at WR; shipping Elite WR should flag
    thin = {"A": {"WR"}, "B": set()}
    r = score_trade(
        ["0002"], ["0003"], VALUE_MAP,
        side1_owner="A", side2_owner="B",
        thin_lookup=lambda fid: thin[fid],
    )
    assert any("WR" in f and "A" in f for f in r.positional_flags)


def test_salary_delta():
    r = score_trade(["0001"], ["0004"], VALUE_MAP)
    assert r.salary_delta == 35  # 40 - 5
