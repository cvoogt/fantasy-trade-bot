"""Injury-status alerts for my roster.

check_injuries() re-fetches the Sleeper players dump, diffs injury_status
against the previous snapshot for every player on my roster, updates the
cache, and returns the changes. First run (no previous statuses) is a
silent baseline.
"""
from dataclasses import dataclass

from src.db import get_conn
from src.config import MFL_FRANCHISE_ID
from src import mfl_api
from src.sleeper_api import refresh_players_cache
from src.sleeper_xwalk import get_sleeper_map

# Worse statuses later; used to tell downgrades from recoveries.
_SEVERITY = {"": 0, "Questionable": 1, "Doubtful": 2, "Out": 3, "Sus": 3,
             "PUP": 4, "IR": 4, "NA": 3, "COV": 3, "DNR": 4}


@dataclass
class InjuryChange:
    mfl_id: str
    sleeper_id: str
    name: str
    position: str
    old: str
    new: str

    @property
    def is_downgrade(self) -> bool:
        return _SEVERITY.get(self.new, 2) > _SEVERITY.get(self.old, 0)


def _my_roster_ids(franchise_id: str) -> list[str]:
    for fr in mfl_api.get_rosters():
        if fr.get("id") == franchise_id:
            players = fr.get("player", [])
            if isinstance(players, dict):
                players = [players]
            return [p.get("id", "") for p in players]
    return []


def check_injuries(franchise_id: str = MFL_FRANCHISE_ID) -> list[InjuryChange]:
    """Diff my roster's injury statuses. Refreshes the players cache (forced)."""
    smap = get_sleeper_map()
    my_sids = {smap[pid]: pid for pid in _my_roster_ids(franchise_id) if pid in smap}
    if not my_sids:
        return []

    conn = get_conn()
    prev = {
        r["sleeper_id"]: (r["injury_status"] or "", r["name"], r["position"])
        for r in conn.execute(
            "SELECT sleeper_id, injury_status, name, position FROM sleeper_players"
        )
        if r["sleeper_id"] in my_sids
    }
    conn.close()

    refresh_players_cache(force=True)

    conn = get_conn()
    now = {
        r["sleeper_id"]: (r["injury_status"] or "", r["name"], r["position"])
        for r in conn.execute(
            "SELECT sleeper_id, injury_status, name, position FROM sleeper_players"
        )
        if r["sleeper_id"] in my_sids
    }
    conn.close()

    if not prev:  # baseline: cache had no rows yet
        return []

    changes = []
    for sid, (new_status, name, pos) in now.items():
        old_status = prev.get(sid, ("", "", ""))[0]
        if sid in prev and new_status != old_status:
            changes.append(InjuryChange(
                mfl_id=my_sids[sid], sleeper_id=sid, name=name,
                position=pos, old=old_status, new=new_status,
            ))
    return changes


def bench_replacements(position: str, franchise_id: str = MFL_FRANCHISE_ID,
                       exclude: set[str] | None = None, top_n: int = 2) -> list[dict]:
    """My best other players at a position, by dynasty value (works offseason)."""
    from src.value_engine import get_value_map
    exclude = exclude or set()
    value_map = get_value_map()
    mine = [
        {"mfl_id": pid, **value_map[pid]}
        for pid in _my_roster_ids(franchise_id)
        if pid in value_map and pid not in exclude
        and value_map[pid]["position"] == position
    ]
    mine.sort(key=lambda p: p["dynasty_value"], reverse=True)
    return mine[:top_n]
