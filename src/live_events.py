"""Live stat-event notifier for my starters.

Polls Sleeper weekly stats during game windows, diffs against SQLite snapshots
(live_stat_snapshots), and emits three kinds of events:

  - count events: each new TD, INT thrown, takeaway, 2-pt conversion, return TD
  - milestones: crossing a yardage / tackle / completion threshold (once each)
  - big plays: 30+ yard receptions and runs

Snapshots survive restarts. A stat is only diffed once it has a snapshot row,
so the first poll of a week (or of a newly tracked stat) is a silent baseline.

Stat-key notes: Sleeper's bare 'int' is interceptions THROWN with no player
context — the QB stat is 'pass_int', defensive takeaways are 'idp_int'.
'idp_tkl' is solo+assisted combined; tackle milestones use 'idp_tkl_solo'.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from src.db import get_conn
from src.config import MFL_FRANCHISE_ID
from src import mfl_api
from src.sleeper_xwalk import get_sleeper_map
from src.sleeper_api import get_stats

# stat key -> (label, emoji); alert once per increment
TRACKED_STATS = {
    "pass_td": ("passing TD", "🏈"),
    "rush_td": ("rushing TD", "🏈"),
    "rec_td": ("receiving TD", "🏈"),
    "kr_td": ("kick return TD", "⚡"),
    "pr_td": ("punt return TD", "⚡"),
    "st_td": ("special teams TD", "⚡"),
    "idp_def_td": ("defensive TD", "🛡️"),
    "idp_int": ("interception", "🛡️"),
    "idp_fum_rec": ("fumble recovery", "🛡️"),
    "pass_int": ("threw an interception", "😬"),
    "pass_2pt": ("two-point conversion (pass)", "✌️"),
    "rush_2pt": ("two-point conversion (run)", "✌️"),
    "rec_2pt": ("two-point conversion (catch)", "✌️"),
}

# Takeaway -> return-yardage stat reported alongside it
_RETURN_YARDS = {"idp_int": "idp_int_ret_yd", "idp_fum_rec": "idp_fum_ret_yd"}

# stat key -> (thresholds, label, emoji); alert when a threshold is crossed
MILESTONES = {
    "idp_tkl_solo": ((7, 10), "tackles", "🧱"),
    "idp_tkl_ast": ((7, 10), "assists", "🧱"),
    "rush_yd": ((100, 150, 200, 250), "rushing yards", "🏃"),
    "rec_yd": ((100, 150, 200, 250), "receiving yards", "🙌"),
    "pass_yd": ((300, 400), "passing yards", "🎯"),
    "pass_cmp": ((25,), "completions", "🎯"),
}

# Raw stats needed to detect 30+ yard plays (see _big_plays)
_BIG_PLAY_STATS = ("rec_30_39", "rec_40p", "rec_lng", "rush_40p", "rush_lng")

SNAPSHOT_STATS = tuple(
    dict.fromkeys(
        list(TRACKED_STATS) + list(_RETURN_YARDS.values())
        + list(MILESTONES) + list(_BIG_PLAY_STATS)
    )
)

# Game windows in US/Eastern: (weekday, start_hour, end_hour_exclusive)
# Mon=0 ... Sun=6. Late spillover windows cover SNF/MNF endings.
_WINDOWS = [
    (3, 19, 24),  # Thu night
    (5, 13, 24),  # Sat (late-season slates; harmless off-season, no stat changes)
    (6, 9, 24),   # Sun (early London games through SNF)
    (0, 0, 1),    # SNF spillover into Mon morning
    (0, 19, 24),  # Mon night
    (1, 0, 1),    # MNF spillover into Tue morning
]


@dataclass
class StatEvent:
    sleeper_id: str
    player_name: str
    stat: str
    label: str
    emoji: str
    delta: int
    total: int
    text: str = ""


def in_game_window(now: datetime | None = None) -> bool:
    try:
        from zoneinfo import ZoneInfo
        now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    except Exception:  # tz database unavailable — poll anyway, diffs are cheap
        return True
    return any(
        now.weekday() == wd and start <= now.hour < end
        for wd, start, end in _WINDOWS
    )


def _count_events(sid, name, now, before, has) -> list[StatEvent]:
    events = []
    for stat, (label, emoji) in TRACKED_STATS.items():
        if not has(stat):
            continue
        cur, old = now(stat), before(stat)
        if cur <= old:
            continue
        delta, total = int(cur - old), int(cur)
        text = f"{emoji} **{name}** {label}!"
        if total > 1:
            text += f" (#{total} on the day)"
        ret_stat = _RETURN_YARDS.get(stat)
        if ret_stat and has(ret_stat):
            yds = int(now(ret_stat) - before(ret_stat))
            text += f" Returned {yds} yards." if yds > 0 else " No return."
        events.append(StatEvent(sid, name, stat, label, emoji, delta, total, text))

    # st_td duplicates a kick/punt return TD in the same poll — keep the specific one
    fired = {e.stat for e in events}
    if "st_td" in fired and fired & {"kr_td", "pr_td"}:
        events = [e for e in events if e.stat != "st_td"]
    return events


def _milestone_events(sid, name, now, before, has) -> list[StatEvent]:
    events = []
    for stat, (thresholds, label, emoji) in MILESTONES.items():
        if not has(stat):
            continue
        cur, old = now(stat), before(stat)
        crossed = [t for t in thresholds if old < t <= cur]
        if not crossed:
            continue
        top = max(crossed)  # 95 -> 155 in one poll reports 150, not 100 and 150
        text = f"{emoji} **{name}** hit {top} {label}! ({int(cur)} so far)"
        events.append(StatEvent(sid, name, f"{stat}_{top}", f"{top} {label}",
                                emoji, 1, int(cur), text))
    return events


def _big_plays(sid, name, now, before, has) -> list[StatEvent]:
    """30+ yard plays. Receptions come in distance buckets, so every one is
    counted. Runs only have a 40+ bucket; a 30-39 yard run is caught when it
    becomes the player's new longest run of the game."""
    if not all(has(s) for s in _BIG_PLAY_STATS):
        return []
    events = []

    rec_count = int((now("rec_30_39") + now("rec_40p"))
                    - (before("rec_30_39") + before("rec_40p")))
    if rec_count > 0:
        lng_cur, lng_old = now("rec_lng"), before("rec_lng")
        if rec_count == 1 and lng_cur > lng_old:
            what = f"{int(lng_cur)}-yard reception"
        else:
            what = f"{rec_count} catch(es) of 30+ yards" if rec_count > 1 else "30+ yard reception"
        events.append(StatEvent(sid, name, "big_rec", what, "💥", rec_count,
                                int(now("rec_30_39") + now("rec_40p")),
                                f"💥 **{name}** big play: {what}!"))

    run_count = int(now("rush_40p") - before("rush_40p"))
    lng_cur, lng_old = now("rush_lng"), before("rush_lng")
    if lng_cur > lng_old and 30 <= lng_cur < 40:
        run_count += 1
    if run_count > 0:
        if run_count == 1 and lng_cur > lng_old and lng_cur >= 30:
            what = f"{int(lng_cur)}-yard run"
        else:
            what = f"{run_count} runs of 30+ yards" if run_count > 1 else "40+ yard run"
        events.append(StatEvent(sid, name, "big_run", what, "💥", run_count,
                                run_count, f"💥 **{name}** big play: {what}!"))
    return events


def detect_events(
    prev: dict[tuple[str, str], float],
    stats: dict[str, dict],
    watched: dict[str, str],
) -> list[StatEvent]:
    """Diff stats vs the previous snapshot for watched players.

    prev: {(sleeper_id, stat): value} — last snapshot. Stats without a
          snapshot row are a silent baseline (no alert).
    stats: Sleeper weekly stats {sleeper_id: {stat: value}}.
    watched: {sleeper_id: display_name}.
    """
    events = []
    for sid, name in watched.items():
        pstats = stats.get(sid) or {}

        def now(stat, _p=pstats):
            return float(_p.get(stat, 0) or 0)

        def before(stat, _sid=sid):
            return prev.get((_sid, stat), 0.0)

        def has(stat, _sid=sid):
            return (_sid, stat) in prev

        events += _count_events(sid, name, now, before, has)
        events += _milestone_events(sid, name, now, before, has)
        events += _big_plays(sid, name, now, before, has)
    return events


def load_snapshots(season: int, week: int) -> dict[tuple[str, str], float]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT sleeper_id, stat, count FROM live_stat_snapshots WHERE season=? AND week=?",
        (season, week),
    ).fetchall()
    conn.close()
    return {(r["sleeper_id"], r["stat"]): r["count"] for r in rows}


def save_snapshots(season: int, week: int, stats: dict[str, dict], watched_ids: set[str]):
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    for sid in watched_ids:
        pstats = stats.get(sid) or {}
        for stat in SNAPSHOT_STATS:
            conn.execute(
                """INSERT OR REPLACE INTO live_stat_snapshots
                   (season, week, sleeper_id, stat, count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (season, week, sid, stat, float(pstats.get(stat, 0) or 0), now),
            )
    conn.commit()
    conn.close()


def my_starters(franchise_id: str = MFL_FRANCHISE_ID) -> dict[str, str]:
    """{sleeper_id: display_name} for my current-week starters.

    Uses MFL liveScoring (starters as submitted); falls back to the whole
    roster if lineups aren't set yet (e.g. early in the week)."""
    smap = get_sleeper_map()
    names = {p["id"]: p.get("name", p["id"]) for p in mfl_api.get_players()}

    starter_ids: list[str] = []
    try:
        ls = mfl_api.get_live_scoring()
        franchises = ls.get("franchise", [])
        if isinstance(franchises, dict):
            franchises = [franchises]
        for fr in franchises:
            if fr.get("id") != franchise_id:
                continue
            players = fr.get("players", {}).get("player", [])
            if isinstance(players, dict):
                players = [players]
            starter_ids = [p.get("id", "") for p in players
                           if p.get("status") == "starter"]
    except Exception:
        pass

    if not starter_ids:  # fallback: whole roster
        for fr in mfl_api.get_rosters():
            if fr.get("id") == franchise_id:
                players = fr.get("player", [])
                if isinstance(players, dict):
                    players = [players]
                starter_ids = [p.get("id", "") for p in players]
                break

    return {
        smap[pid]: names.get(pid, pid)
        for pid in starter_ids
        if pid in smap
    }


def poll_events(season: int, week: int, watched: dict[str, str] | None = None,
                stats: dict[str, dict] | None = None) -> list[StatEvent]:
    """One polling cycle: fetch stats, diff vs snapshot, persist, return events.

    `watched` and `stats` are injectable for testing/backfill."""
    if watched is None:
        watched = my_starters()
    if not watched:
        return []
    if stats is None:
        stats = get_stats(season, week)

    prev = load_snapshots(season, week)
    events = detect_events(prev, stats, watched)
    save_snapshots(season, week, stats, set(watched))
    return events
