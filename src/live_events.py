"""Live stat-event notifier: TDs, interceptions, fumble recoveries for my starters.

Polls Sleeper weekly stats during game windows, diffs tracked stat counts
against SQLite snapshots (live_stat_snapshots), and emits an event per
increment. Snapshots survive restarts, so no double-alerts.

Note on stat keys: Sleeper's bare 'int' is interceptions THROWN by a QB —
defensive takeaways are 'idp_int'. We alert on the defensive ones.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from src.db import get_conn
from src.config import MFL_FRANCHISE_ID
from src import mfl_api
from src.sleeper_xwalk import get_sleeper_map
from src.sleeper_api import get_stats

# stat key -> (label, emoji)
TRACKED_STATS = {
    "pass_td": ("passing TD", "🏈"),
    "rush_td": ("rushing TD", "🏈"),
    "rec_td": ("receiving TD", "🏈"),
    "st_td": ("special teams TD", "⚡"),
    "idp_def_td": ("defensive TD", "🛡️"),
    "idp_int": ("interception", "🛡️"),
    "idp_fum_rec": ("fumble recovery", "🛡️"),
}

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


def detect_events(
    prev: dict[tuple[str, str], float],
    stats: dict[str, dict],
    watched: dict[str, str],
) -> list[StatEvent]:
    """Diff tracked stats vs previous counts for watched players.

    prev: {(sleeper_id, stat): count} — last snapshot.
    stats: Sleeper weekly stats {sleeper_id: {stat: value}}.
    watched: {sleeper_id: display_name}.
    """
    events = []
    for sid, name in watched.items():
        pstats = stats.get(sid) or {}
        for stat, (label, emoji) in TRACKED_STATS.items():
            now_count = float(pstats.get(stat, 0) or 0)
            before = prev.get((sid, stat), 0.0)
            if now_count > before:
                events.append(StatEvent(
                    sleeper_id=sid, player_name=name, stat=stat, label=label,
                    emoji=emoji, delta=int(now_count - before), total=int(now_count),
                ))
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
        for stat in TRACKED_STATS:
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
    first_run = not prev  # baseline run: record counts, don't alert history
    events = [] if first_run else detect_events(prev, stats, watched)
    save_snapshots(season, week, stats, set(watched))
    return events
