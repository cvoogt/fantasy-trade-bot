from src.db import init_db, get_conn
from src.live_events import detect_events, poll_events, load_snapshots, TRACKED_STATS

# fake season/week so tests never collide with real snapshot data
SEASON, WEEK = 1999, 1

WATCHED = {"s1": "Puka Nacua", "s2": "Frankie Luvu", "s3": "Josh Allen"}

STATS_T0 = {
    "s1": {"rec_td": 1.0, "rec": 5.0},
    "s2": {"idp_int": 1.0, "idp_fum_rec": 0.0},
    "s3": {"pass_td": 2.0, "int": 1.0},  # bare 'int' = INT thrown, must NOT alert
}

STATS_T1 = {
    "s1": {"rec_td": 2.0, "rec": 8.0},                  # +1 receiving TD
    "s2": {"idp_int": 1.0, "idp_fum_rec": 1.0},          # +1 fumble recovery
    "s3": {"pass_td": 2.0, "int": 2.0},                  # +1 INT thrown -> no alert
}


def _clear():
    init_db()
    conn = get_conn()
    conn.execute("DELETE FROM live_stat_snapshots WHERE season=?", (SEASON,))
    conn.commit()
    conn.close()


def test_detect_events_diffs_increments():
    prev = {("s1", "rec_td"): 1.0, ("s2", "idp_fum_rec"): 0.0}
    events = detect_events(prev, STATS_T1, WATCHED)
    found = {(e.sleeper_id, e.stat) for e in events}
    assert ("s1", "rec_td") in found
    assert ("s2", "idp_fum_rec") in found


def test_int_thrown_never_alerts():
    assert "int" not in TRACKED_STATS
    events = detect_events({}, STATS_T1, WATCHED)
    assert all(e.stat != "int" for e in events)


def test_first_poll_is_silent_baseline():
    _clear()
    events = poll_events(SEASON, WEEK, watched=WATCHED, stats=STATS_T0)
    assert events == []  # baseline: record history, don't replay it
    snaps = load_snapshots(SEASON, WEEK)
    assert snaps[("s1", "rec_td")] == 1.0


def test_second_poll_emits_exactly_new_events_then_goes_quiet():
    _clear()
    poll_events(SEASON, WEEK, watched=WATCHED, stats=STATS_T0)

    events = poll_events(SEASON, WEEK, watched=WATCHED, stats=STATS_T1)
    got = {(e.sleeper_id, e.stat, e.delta) for e in events}
    assert got == {("s1", "rec_td", 1), ("s2", "idp_fum_rec", 1)}

    # idempotency: same stats again -> no repeat alerts
    events2 = poll_events(SEASON, WEEK, watched=WATCHED, stats=STATS_T1)
    assert events2 == []


def _snap(sid, **stats):
    """A full previous snapshot for one player (every tracked stat present)."""
    from src.live_events import SNAPSHOT_STATS
    return {(sid, s): float(stats.get(s, 0)) for s in SNAPSHOT_STATS}


def _stats_of(events):
    return {e.stat for e in events}


def test_milestone_fires_once_at_crossing():
    prev = _snap("s1", rush_yd=95)
    events = detect_events(prev, {"s1": {"rush_yd": 104}}, {"s1": "RB"})
    assert _stats_of(events) == {"rush_yd_100"}
    # already past 100, no new threshold -> quiet
    prev = _snap("s1", rush_yd=104)
    assert detect_events(prev, {"s1": {"rush_yd": 130}}, {"s1": "RB"}) == []


def test_milestone_jump_reports_highest_only():
    prev = _snap("s1", rec_yd=90)
    events = detect_events(prev, {"s1": {"rec_yd": 162}}, {"s1": "WR"})
    assert _stats_of(events) == {"rec_yd_150"}


def test_tackle_and_assist_milestones_use_solo_and_ast():
    prev = _snap("s2", idp_tkl_solo=6, idp_tkl_ast=9)
    events = detect_events(
        prev, {"s2": {"idp_tkl_solo": 7, "idp_tkl_ast": 10, "idp_tkl": 17}},
        {"s2": "LB"})
    assert _stats_of(events) == {"idp_tkl_solo_7", "idp_tkl_ast_10"}


def test_passing_milestones_and_completions():
    prev = _snap("s3", pass_yd=280, pass_cmp=24)
    events = detect_events(prev, {"s3": {"pass_yd": 305, "pass_cmp": 25}},
                           {"s3": "QB"})
    assert _stats_of(events) == {"pass_yd_300", "pass_cmp_25"}


def test_interception_thrown_alerts_via_pass_int():
    prev = _snap("s3")
    events = detect_events(prev, {"s3": {"pass_int": 1, "int": 1}}, {"s3": "QB"})
    assert _stats_of(events) == {"pass_int"}


def test_takeaway_reports_return_yards():
    prev = _snap("s2")
    events = detect_events(
        prev, {"s2": {"idp_int": 1, "idp_int_ret_yd": 34}}, {"s2": "S"})
    assert len(events) == 1
    assert "Returned 34 yards" in events[0].text


def test_two_point_conversions():
    prev = _snap("s1")
    events = detect_events(prev, {"s1": {"rec_2pt": 1}}, {"s1": "WR"})
    assert _stats_of(events) == {"rec_2pt"}


def test_return_td_suppresses_generic_st_td():
    prev = _snap("s1")
    events = detect_events(prev, {"s1": {"kr_td": 1, "st_td": 1}}, {"s1": "WR"})
    assert _stats_of(events) == {"kr_td"}


def test_big_reception_with_length():
    prev = _snap("s1", rec_lng=12)
    events = detect_events(
        prev, {"s1": {"rec_40p": 1, "rec_lng": 47}}, {"s1": "WR"})
    assert len(events) == 1 and "47-yard reception" in events[0].text


def test_big_run_30_to_39_via_longest():
    prev = _snap("s1", rush_lng=8)
    events = detect_events(prev, {"s1": {"rush_lng": 33}}, {"s1": "RB"})
    assert len(events) == 1 and "33-yard run" in events[0].text


def test_short_plays_do_not_alert():
    prev = _snap("s1", rush_lng=8, rec_lng=12)
    events = detect_events(
        prev, {"s1": {"rush_lng": 22, "rec_lng": 29}}, {"s1": "RB"})
    assert events == []


def test_new_stat_without_snapshot_is_silent_baseline():
    # Snapshot from an older bot version lacks rush_yd -> first sighting is quiet
    prev = {("s1", "rush_td"): 0.0}
    assert detect_events(prev, {"s1": {"rush_yd": 160}}, {"s1": "RB"}) == []


def test_multi_td_burst_reports_delta():
    _clear()
    poll_events(SEASON, WEEK, watched=WATCHED, stats=STATS_T0)
    burst = {"s3": {"pass_td": 4.0}}  # 2 -> 4 between polls
    events = poll_events(SEASON, WEEK, watched=WATCHED, stats=burst)
    assert len(events) == 1
    assert events[0].delta == 2 and events[0].total == 4
