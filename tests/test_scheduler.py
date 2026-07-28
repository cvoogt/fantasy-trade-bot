"""Tests for the weekly command scheduler. The DB is pointed at a temp file so
nothing touches the real trade_bot.db."""
from datetime import datetime, timezone

import pytest

from src import scheduler


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import src.db as db
    dbfile = str(tmp_path / "sched_test.db")
    monkeypatch.setattr(db, "DB_PATH", dbfile)
    db.init_db()
    return dbfile


# ---- parsing -------------------------------------------------------------

def test_parse_day():
    assert scheduler.parse_day("Sunday") == 6
    assert scheduler.parse_day("mon") == 0
    assert scheduler.parse_day(" Thursday ") == 3
    with pytest.raises(ValueError):
        scheduler.parse_day("someday")


def test_parse_time_variants():
    assert scheduler.parse_time("11AM") == (11, 0)
    assert scheduler.parse_time("11 am") == (11, 0)
    assert scheduler.parse_time("12AM") == (0, 0)      # midnight
    assert scheduler.parse_time("12PM") == (12, 0)     # noon
    assert scheduler.parse_time("7:30PM") == (19, 30)
    assert scheduler.parse_time("13:00") == (13, 0)    # 24h


def test_parse_time_bad():
    for bad in ("", "25:00", "13PM", "nonsense"):
        with pytest.raises(ValueError):
            scheduler.parse_time(bad)


def test_fmt_time():
    assert scheduler.fmt_time(13, 0) == "1:00 PM"
    assert scheduler.fmt_time(0, 5) == "12:05 AM"
    assert scheduler.fmt_time(11, 0) == "11:00 AM"


# ---- storage + due logic -------------------------------------------------

def test_add_list_remove(tmp_db):
    jid = scheduler.add_job("gametime", 6, 11, 0, channel_id=42, created_by=7)
    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["command"] == "gametime" and jobs[0]["channel_id"] == 42
    assert scheduler.remove_job(jid) is True
    assert scheduler.list_jobs() == []
    assert scheduler.remove_job(jid) is False


def _sunday_11am_ct_utc():
    # A UTC instant that is Sunday 11:00 AM in America/Chicago (CDT = UTC-5).
    from zoneinfo import ZoneInfo
    ct = datetime(2025, 9, 7, 11, 0, tzinfo=ZoneInfo("America/Chicago"))
    return ct.astimezone(timezone.utc)


def test_due_jobs_matches_ct_minute(tmp_db):
    scheduler.add_job("gametime", 6, 11, 0, channel_id=42, created_by=7)  # Sun 11:00
    scheduler.add_job("waivers", 6, 12, 0, channel_id=42, created_by=7)   # Sun 12:00
    now = _sunday_11am_ct_utc()

    due = scheduler.due_jobs(now)
    assert [j["command"] for j in due] == ["gametime"]


def test_due_jobs_not_repeated_after_mark(tmp_db):
    jid = scheduler.add_job("gametime", 6, 11, 0, channel_id=42, created_by=7)
    now = _sunday_11am_ct_utc()

    assert len(scheduler.due_jobs(now)) == 1
    scheduler.mark_run(jid, now)
    assert scheduler.due_jobs(now) == []  # same minute won't re-fire


def test_describe(tmp_db):
    scheduler.add_job("gametime", 6, 11, 0, channel_id=42, created_by=7)
    job = scheduler.list_jobs()[0]
    assert scheduler.describe(job) == "gametime — every Sunday at 11:00 AM CT"
