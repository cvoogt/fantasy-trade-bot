from src.scoring import (_eval_event, project_points, season_points,
                         rules_for_position, explain_points, unmapped_events)

# Brackets mirroring league 68447's real rules
PY = [  # passing yards: dead zone, rate, milestone bonuses at 300/400
    {"event": "PY", "points": "0", "lo": 0, "hi": 19, "threshold": None},
    {"event": "PY", "points": "1/20", "lo": 20, "hi": 299, "threshold": None},
    {"event": "PY", "points": "1/20", "lo": 300, "hi": 399, "threshold": 20},
    {"event": "PY", "points": "1/20", "lo": 400, "hi": 999, "threshold": 30},
]
RY = [  # rushing yards: step table with the 100-yard jump.
    # Covers the full per-game range, as MFL's real tables do — a table that
    # started at 80 would score every low-volume game as zero.
    {"event": "RY", "points": "0", "lo": 0, "hi": 9, "threshold": None},
    {"event": "RY", "points": "1", "lo": 10, "hi": 19, "threshold": None},
    {"event": "RY", "points": "2", "lo": 20, "hi": 29, "threshold": None},
    {"event": "RY", "points": "3", "lo": 30, "hi": 39, "threshold": None},
    {"event": "RY", "points": "4", "lo": 40, "hi": 49, "threshold": None},
    {"event": "RY", "points": "5", "lo": 50, "hi": 59, "threshold": None},
    {"event": "RY", "points": "6", "lo": 60, "hi": 69, "threshold": None},
    {"event": "RY", "points": "7", "lo": 70, "hi": 79, "threshold": None},
    {"event": "RY", "points": "8", "lo": 80, "hi": 89, "threshold": None},
    {"event": "RY", "points": "9", "lo": 90, "hi": 99, "threshold": None},
    {"event": "RY", "points": "15", "lo": 100, "hi": 109, "threshold": None},
]
TK = [  # tackles: 1/tackle with threshold bonuses
    {"event": "TK", "points": "1/1", "lo": 0, "hi": 6, "threshold": 0},
    {"event": "TK", "points": "1/1", "lo": 7, "hi": 11, "threshold": 10},
]
SK = [{"event": "SK", "points": "2/0.5", "lo": 0.5, "hi": 99, "threshold": None}]
IC = [{"event": "IC", "points": "*10", "lo": 1, "hi": 99, "threshold": None}]
CC = [{"event": "CC", "points": "1/2", "lo": 0, "hi": 99, "threshold": None}]


def test_yardage_rate():
    assert _eval_event(PY, 250) == 12.5          # 250/20


def test_yardage_dead_zone():
    assert _eval_event(PY, 15) == 0.0            # under 20 yds = 0


def test_milestone_bonus():
    assert _eval_event(PY, 300) == 20.0          # threshold base
    assert _eval_event(PY, 350) == 22.5          # 20 + 50/20


def test_step_table_jump():
    assert _eval_event(RY, 95) == 9.0
    assert _eval_event(RY, 100) == 15.0          # the 100-yard jump
    assert _eval_event(RY, 500) == 15.0          # clamps to last bracket


def test_tackle_threshold():
    assert _eval_event(TK, 5) == 5.0
    assert _eval_event(TK, 8) == 11.0            # 10 base + 1 over bracket start


def test_sack_rate():
    assert _eval_event(SK, 2) == 8.0             # 2 per 0.5 = 4/sack


def test_per_event():
    assert _eval_event(IC, 2) == 20.0            # *10 per INT


def test_reception_half_ppr():
    assert _eval_event(CC, 6) == 3.0


def test_project_points_sums_events():
    rules = PY + IC + CC
    proj = {"pass_yd": 250, "idp_int": 1, "rec": 4}
    # 12.5 + 10 + 2
    assert project_points(proj, rules) == 24.5


def test_project_points_ignores_unmapped():
    rules = [{"event": "ZZ", "points": "*100", "lo": 0, "hi": 99, "threshold": None}]
    assert project_points({"pass_yd": 300}, rules) == 0.0


# ---- season scoring: per-game brackets must not be fed season totals ----

def test_step_table_clamps_on_season_total():
    # The bug: a step table tops out at its last bracket, so a whole season of
    # rushing scores the same as a single 100-yard game.
    assert _eval_event(RY, 531) == _eval_event(RY, 100) == 15.0


def test_season_points_scales_through_step_table():
    """531 rushing yards should far exceed a single 100-yard game's 15 pts."""
    season = season_points({"rush_yd": 531}, RY, games=17)
    single_game = project_points({"rush_yd": 531}, RY)
    assert single_game == 15.0        # old, clamped behaviour
    assert season > 40                # ~31 yds/gm scored 17 times
    assert season > single_game * 2


def test_season_points_is_scale_invariant_for_rates_and_counts():
    """Pure rates ('1/20') and per-event ('*10') must be unaffected."""
    # 3400 pass yds at 1/20 = 170 either way (no threshold bracket in play)
    flat_py = [{"event": "PY", "points": "1/20", "lo": 0, "hi": 99999,
                "threshold": None}]
    assert season_points({"pass_yd": 3400}, flat_py, games=17) == 170.0
    assert season_points({"idp_int": 17}, IC, games=17) == 170.0


def test_season_points_zero_games():
    assert season_points({"rush_yd": 500}, RY, games=0) == 0.0


# ---- position-scoped rules ----

QB_RY = [{"event": "RY", "points": "1/10", "lo": 0, "hi": 999,
          "threshold": None, "positions": {"QB"}}]
RB_RY = [{"event": "RY", "points": "8", "lo": 80, "hi": 89,
          "threshold": None, "positions": {"RB"}}]


def test_rules_for_position_filters():
    both = QB_RY + RB_RY
    assert rules_for_position(both, "QB") == QB_RY
    assert rules_for_position(both, "RB") == RB_RY


def test_rules_for_position_keeps_unscoped_rules():
    unscoped = [{"event": "PY", "points": "1/20", "lo": 0, "hi": 999,
                 "threshold": None, "positions": set()}]
    assert rules_for_position(unscoped + QB_RY, "RB") == unscoped


def test_rules_for_position_falls_back_when_nothing_matches():
    # A league that doesn't scope by position shouldn't score everyone as 0.
    assert rules_for_position(QB_RY, "WR") == QB_RY


def test_position_scoping_changes_the_score():
    both = QB_RY + RB_RY
    assert project_points({"rush_yd": 85}, both, "QB") == 8.5
    assert project_points({"rush_yd": 85}, both, "RB") == 8.0


# ---- diagnostics ----

# ---- fractional projections vs whole-event brackets ----

def test_fractional_per_event_scores_proportionally():
    """Weekly TD/INT projections are fractional; a '1-99' bracket must still
    pay out below 1.0 or every sub-1.0 TD projection silently scores zero."""
    assert _eval_event(IC, 0.5) == 5.0     # was 0.0
    assert _eval_event(IC, 0.9) == 9.0     # was 0.0
    assert _eval_event(IC, 1.0) == 10.0    # unchanged


def test_fractional_rate_below_first_bracket():
    sk = [{"event": "SK", "points": "2/0.5", "lo": 0.5, "hi": 99, "threshold": None}]
    assert _eval_event(sk, 0.25) == 1.0    # quarter sack still pays


def test_explicit_dead_zone_still_scores_zero():
    """PY's 0-19 bracket says '0' points — that must stay zero, since it's a
    real bracket rather than a below-the-table amount."""
    for amt in (5, 15, 19):
        assert _eval_event(PY, amt) == 0.0
    assert _eval_event(PY, 20) == 1.0


def test_step_table_below_first_step_stays_zero():
    """A step table hasn't been reached yet — no partial credit."""
    steps = [{"event": "X", "points": "8", "lo": 80, "hi": 89, "threshold": None}]
    assert _eval_event(steps, 30) == 0.0


def test_gap_between_brackets_uses_step_below():
    gap = [{"event": "X", "points": "5", "lo": 0, "hi": 9, "threshold": None},
           {"event": "X", "points": "9", "lo": 20, "hi": 29, "threshold": None}]
    assert _eval_event(gap, 15) == 5.0     # was 0.0


def test_unmapped_events_reported():
    rules = [{"event": "PY", "points": "1/20", "lo": 0, "hi": 9, "threshold": None,
              "positions": set()},
             {"event": "XYZ", "points": "*5", "lo": 0, "hi": 9, "threshold": None,
              "positions": set()}]
    assert unmapped_events(rules) == ["XYZ"]   # PY is mapped, XYZ is not


def test_explain_points_breaks_down_contributions():
    rows = explain_points({"pass_yd": 250, "idp_int": 1}, PY + IC)
    by_event = {r["event"]: r["points"] for r in rows}
    assert by_event["PY"] == 12.5
    assert by_event["IC"] == 10.0
    assert rows[0]["event"] == "PY"       # sorted by contribution
    assert all(r["amount"] for r in rows)  # zero stats omitted
