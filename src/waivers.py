"""Waiver gem scanner.

Ranks free agents by value-per-dollar, cross-references against my roster's
thin positions and droppable bench (lowest VPD guys I own), surfaces top gems
with a suggested drop for each.
"""
from src.config import MFL_FRANCHISE_ID
from src import mfl_api
from src.value_engine import get_value_map


def _my_player_ids(franchise_id: str) -> set[str]:
    rosters = mfl_api.get_rosters()
    for fr in rosters:
        if fr.get("id") == franchise_id:
            players = fr.get("player", [])
            if isinstance(players, dict):
                players = [players]
            return {p.get("id", "") for p in players}
    return set()


def _fa_ids() -> set[str]:
    return {p.get("id", "") for p in mfl_api.get_free_agents()}


def waiver_gems(
    top_n: int = 5,
    franchise_id: str = MFL_FRANCHISE_ID,
    value_map: dict | None = None,
) -> dict:
    """Return top waiver gems + suggested drops.

    Returns:
        {
          "gems": [{"mfl_id", "name", "position", "dynasty_value", "salary",
                    "value_per_dollar", "vor"}, ...],
          "droppables": [same shape, from my roster],
          "thin_positions": set[str],
          "pairs": [{"gem": ..., "drop": ...}],
        }
    """
    if value_map is None:
        value_map = get_value_map()

    fa_ids = _fa_ids()
    my_ids = _my_player_ids(franchise_id)

    # Thin positions = positions where I'm below league median
    from src.roster import thin_positions as _thin
    thin = _thin(franchise_id, value_map)

    # FA players with known dynasty values.
    # Rank: contracted FAs (salary>0) by VPD first, then all by raw dynasty value.
    fa_valued = sorted(
        [
            {"mfl_id": pid, **info}
            for pid, info in value_map.items()
            if pid in fa_ids and info["dynasty_value"] > 0
        ],
        key=lambda r: (r["value_per_dollar"] if r["salary"] > 0 else 0, r["dynasty_value"]),
        reverse=True,
    )

    gems = fa_valued[:top_n]

    # My droppable bench: players I own, sorted by VPD ascending (worst first)
    my_valued = sorted(
        [
            {"mfl_id": pid, **info}
            for pid, info in value_map.items()
            if pid in my_ids
        ],
        key=lambda r: r["value_per_dollar"],
    )
    droppables = my_valued[:top_n]

    # Pair each gem with the worst-VPD player on my roster at same position,
    # falling back to the overall worst if no same-position match.
    pairs = []
    for gem in gems:
        same_pos = [d for d in droppables if d["position"] == gem["position"]]
        drop = same_pos[0] if same_pos else (droppables[0] if droppables else None)
        pairs.append({"gem": gem, "drop": drop})

    return {
        "gems": gems,
        "droppables": droppables,
        "thin_positions": thin,
        "pairs": pairs,
    }


def check_new_fas(value_map: dict | None = None) -> list[dict]:
    """Diff the FA pool vs the last snapshot; return newly available players
    worth alerting on (dynasty value >= WAIVER_ALERT_VALUE). First run is a
    silent baseline. Players leaving the pool just update the snapshot."""
    from src.config import WAIVER_ALERT_VALUE
    from src.db import get_conn

    if value_map is None:
        value_map = get_value_map()

    current = _fa_ids()
    conn = get_conn()
    prev = {r["mfl_id"] for r in conn.execute("SELECT mfl_id FROM fa_pool")}

    first_run = not prev
    new_ids = current - prev

    conn.execute("DELETE FROM fa_pool")
    conn.executemany("INSERT INTO fa_pool (mfl_id) VALUES (?)",
                     [(pid,) for pid in current])
    conn.commit()
    conn.close()

    if first_run:
        return []
    return sorted(
        ({"mfl_id": pid, **value_map[pid]} for pid in new_ids
         if pid in value_map and value_map[pid]["dynasty_value"] >= WAIVER_ALERT_VALUE),
        key=lambda p: p["dynasty_value"], reverse=True,
    )


def format_waiver_report(report: dict) -> str:
    lines = ["=== WAIVER GEMS ==="]
    thin = report["thin_positions"]
    if thin:
        lines.append(f"Your thin positions: {', '.join(sorted(thin))}\n")

    from src.roster import group_of
    for i, pair in enumerate(report["pairs"], 1):
        gem = pair["gem"]
        drop = pair["drop"]
        tag = " [fills thin spot]" if group_of(gem["position"]) in thin else ""
        lines.append(
            f"{i}. ADD  {gem['name']:<22} {gem['position']:<3} "
            f"val={gem['dynasty_value']:.0f}  VPD={gem['value_per_dollar']:.3f}{tag}"
        )
        if drop:
            lines.append(
                f"   DROP {drop['name']:<22} {drop['position']:<3} "
                f"val={drop['dynasty_value']:.0f}  VPD={drop['value_per_dollar']:.3f}"
            )
        lines.append("")

    return "\n".join(lines).rstrip()
