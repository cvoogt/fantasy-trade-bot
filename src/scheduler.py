"""Weekly command scheduler: post a bot command's output to a channel on a
chosen day + time each week.

Times are interpreted in US Central (the league's timezone). The bot ticks
once a minute and fires any job whose day/hour/minute matches "now" in CT,
guarding against double-fires within the same minute via last_run."""
from datetime import datetime, timedelta, timezone

from src.db import get_conn

# Mon=0 .. Sun=6, matching datetime.weekday().
_DAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]


def parse_day(text: str) -> int:
    """Day-of-week name/abbrev -> 0..6 (Mon..Sun). Raises ValueError."""
    key = (text or "").strip().lower()
    if key not in _DAYS:
        raise ValueError(f"Unknown day of week: {text!r}")
    return _DAYS[key]


def parse_time(text: str) -> tuple[int, int]:
    """'11AM', '7 pm', '7:30PM', '13:00' -> (hour_24, minute). Raises ValueError."""
    s = (text or "").strip().lower().replace(" ", "")
    ampm = None
    if s.endswith("am"):
        ampm, s = "am", s[:-2]
    elif s.endswith("pm"):
        ampm, s = "pm", s[:-2]
    if not s:
        raise ValueError("Missing time")
    if ":" in s:
        hh, mm = s.split(":", 1)
    else:
        hh, mm = s, "0"
    try:
        hour, minute = int(hh), int(mm)
    except ValueError:
        raise ValueError(f"Couldn't read a time from {text!r}")
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError(f"Hour out of range for AM/PM: {text!r}")
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range: {text!r}")
    return hour, minute


def fmt_time(hour: int, minute: int) -> str:
    """(13, 0) -> '1:00 PM'."""
    h12 = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    return f"{h12}:{minute:02d} {ampm}"


def now_ct(now: datetime | None = None) -> datetime:
    """Current time in US Central (accepts an override for tests)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Chicago"))
    except Exception:
        return now.astimezone(timezone.utc) - timedelta(hours=5)


def add_job(command: str, dow: int, hour: int, minute: int,
            channel_id: int, created_by: int) -> int:
    """Insert a scheduled job; returns its id."""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO scheduled_jobs
           (command, dow, hour, minute, channel_id, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (command, dow, hour, minute, channel_id, created_by,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    job_id = cur.lastrowid
    conn.close()
    return job_id


def list_jobs() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM scheduled_jobs ORDER BY dow, hour, minute").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_job(job_id: int) -> bool:
    """Delete a job by id; returns True if a row was removed."""
    conn = get_conn()
    cur = conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
    conn.commit()
    removed = cur.rowcount > 0
    conn.close()
    return removed


def due_jobs(now: datetime | None = None) -> list[dict]:
    """Jobs whose day/hour/minute match the current CT minute and that haven't
    already fired this minute."""
    ct = now_ct(now)
    stamp = ct.strftime("%Y-%m-%d %H:%M")
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM scheduled_jobs
           WHERE dow = ? AND hour = ? AND minute = ?
             AND (last_run IS NULL OR last_run != ?)""",
        (ct.weekday(), ct.hour, ct.minute, stamp),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_run(job_id: int, now: datetime | None = None):
    """Stamp a job as fired for the current CT minute."""
    stamp = now_ct(now).strftime("%Y-%m-%d %H:%M")
    conn = get_conn()
    conn.execute("UPDATE scheduled_jobs SET last_run = ? WHERE id = ?",
                 (stamp, job_id))
    conn.commit()
    conn.close()


def describe(job: dict) -> str:
    """Human summary: 'gametime — every Sunday at 11:00 AM'."""
    return (f"{job['command']} — every {DAY_NAMES[job['dow']]} at "
            f"{fmt_time(job['hour'], job['minute'])} CT")
