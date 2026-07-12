"""Rookie draft assistant.

Polls MFL draftResults during an active draft. A pick with no 'player' yet
is pending; the first pending pick is on the clock. When that's my franchise,
ping with the best available by dynasty value.

Best-available pool = FantasyCalc-valued players (non-pick) who are neither
on a league roster nor already drafted. Incoming rookies FantasyCalc ranks
before MFL adds them to its player DB are included, flagged 'not in MFL yet'.
"""
from dataclasses import dataclass

from src.db import get_conn
from src.config import MFL_FRANCHISE_ID
from src import mfl_api
from src.fantasycalc_api import get_cached_values


@dataclass
class DraftPick:
    round: int
    pick: int
    franchise: str
    player: str | None  # MFL id, None if not yet made
    timestamp: int | None

    @property
    def label(self) -> str:
        return f"{self.round}.{self.pick:02d}"


def get_draft_picks() -> list[DraftPick]:
    dr = mfl_api.get_draft_results()
    unit = dr.get("draftUnit", {})
    if isinstance(unit, list):
        unit = unit[0] if unit else {}
    raw = unit.get("draftPick", [])
    if isinstance(raw, dict):
        raw = [raw]
    picks = []
    for p in raw:
        player = p.get("player") or None
        ts = p.get("timestamp") or None
        picks.append(DraftPick(
            round=int(p.get("round", 0)),
            pick=int(p.get("pick", 0)),
            franchise=p.get("franchise", ""),
            player=player if player and player != "0000" else None,
            timestamp=int(ts) if ts else None,
        ))
    return picks


def draft_state(picks: list[DraftPick] | None = None) -> dict:
    """{'active': bool, 'on_clock': DraftPick|None, 'my_turn': bool,
        'drafted_ids': set, 'my_remaining': [DraftPick]}."""
    if picks is None:
        picks = get_draft_picks()
    pending = [p for p in picks if p.player is None]
    on_clock = pending[0] if pending else None
    return {
        "active": bool(pending) and any(p.player for p in picks),
        "on_clock": on_clock,
        "my_turn": bool(on_clock and on_clock.franchise == MFL_FRANCHISE_ID),
        "drafted_ids": {p.player for p in picks if p.player},
        "my_remaining": [p for p in pending if p.franchise == MFL_FRANCHISE_ID],
    }


def _rostered_ids() -> set[str]:
    ids = set()
    for fr in mfl_api.get_rosters():
        players = fr.get("player", [])
        if isinstance(players, dict):
            players = [players]
        ids.update(p.get("id", "") for p in players)
    return ids


def best_available(top_n: int = 10, drafted_ids: set[str] | None = None) -> list[dict]:
    """Top undrafted, unrostered players by dynasty value.

    Returns [{'fc_name', 'position', 'team', 'dynasty_value', 'mfl_id'|None,
              'in_mfl': bool}]. mfl_id None => FantasyCalc ranks them but MFL
    hasn't added them to its player DB yet."""
    if drafted_ids is None:
        drafted_ids = draft_state()["drafted_ids"]
    taken = _rostered_ids() | drafted_ids

    conn = get_conn()
    fc_to_mfl = {
        r["fc_name"]: r["mfl_id"]
        for r in conn.execute("SELECT fc_name, mfl_id FROM crosswalk")
    }
    conn.close()

    out = []
    for fc in sorted(get_cached_values(), key=lambda r: r["dynasty_value"], reverse=True):
        if fc["position"] == "PICK":
            continue
        mfl_id = fc_to_mfl.get(fc["fc_name"])
        if mfl_id and mfl_id in taken:
            continue
        out.append({
            "fc_name": fc["fc_name"],
            "position": fc["position"],
            "team": fc.get("team") or "?",
            "dynasty_value": fc["dynasty_value"],
            "mfl_id": mfl_id,
            "in_mfl": mfl_id is not None,
        })
        if len(out) >= top_n:
            break
    return out


# --- on-the-clock ping dedup (survives restarts) ---

def already_pinged(pick: DraftPick) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM draft_pings WHERE round=? AND pick=?",
        (pick.round, pick.pick),
    ).fetchone()
    conn.close()
    return row is not None


def mark_pinged(pick: DraftPick):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO draft_pings (round, pick) VALUES (?, ?)",
        (pick.round, pick.pick),
    )
    conn.commit()
    conn.close()
