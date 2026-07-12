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


def test_multi_td_burst_reports_delta():
    _clear()
    poll_events(SEASON, WEEK, watched=WATCHED, stats=STATS_T0)
    burst = {"s3": {"pass_td": 4.0}}  # 2 -> 4 between polls
    events = poll_events(SEASON, WEEK, watched=WATCHED, stats=burst)
    assert len(events) == 1
    assert events[0].delta == 2 and events[0].total == 4
