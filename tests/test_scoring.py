from src.scoring import _eval_event, project_points

# Brackets mirroring league 68447's real rules
PY = [  # passing yards: dead zone, rate, milestone bonuses at 300/400
    {"event": "PY", "points": "0", "lo": 0, "hi": 19, "threshold": None},
    {"event": "PY", "points": "1/20", "lo": 20, "hi": 299, "threshold": None},
    {"event": "PY", "points": "1/20", "lo": 300, "hi": 399, "threshold": 20},
    {"event": "PY", "points": "1/20", "lo": 400, "hi": 999, "threshold": 30},
]
RY = [  # rushing yards: step table with the 100-yard jump
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
