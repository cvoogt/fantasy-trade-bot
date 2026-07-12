"""Whole-league trade auto-scan.

Polls MFL transactions (type=TRADE), scores each new trade once (deduped via
SQLite), and flags lopsided ones. The verdict snapshot is stored at scan time
so it stays reproducible even as dynasty values drift.
"""
import hashlib
from datetime import datetime, timezone

from src.config import LOPSIDED_THRESHOLD
from src.db import get_conn, init_db
from src import mfl_api
from src.value_engine import get_value_map, make_pick_resolver, get_pick_value_map
from src.roster import all_thin_positions
from src.trade_scorer import score_trade, TradeResult


def _split_ids(gave_up: str) -> list[str]:
    return [tok.strip() for tok in (gave_up or "").split(",") if tok.strip()]


def _txn_id(txn: dict) -> str:
    """Stable synthetic ID — MFL trades carry no explicit id."""
    raw = "|".join([
        str(txn.get("timestamp", "")),
        txn.get("franchise", ""),
        txn.get("franchise2", ""),
        txn.get("franchise1_gave_up", ""),
        txn.get("franchise2_gave_up", ""),
    ])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def parse_trade(txn: dict) -> dict:
    return {
        "txn_id": _txn_id(txn),
        "timestamp": int(txn.get("timestamp", 0) or 0),
        "franchise1": txn.get("franchise", ""),
        "franchise2": txn.get("franchise2", ""),
        "side1_ids": _split_ids(txn.get("franchise1_gave_up", "")),
        "side2_ids": _split_ids(txn.get("franchise2_gave_up", "")),
    }


def scan_trades(value_map: dict | None = None) -> list[dict]:
    """Score every not-yet-seen TRADE. Returns list of dicts for newly scanned trades."""
    init_db()
    if value_map is None:
        value_map = get_value_map()
    pick_resolver = make_pick_resolver(get_pick_value_map())
    thin_map = all_thin_positions(value_map)
    thin_lookup = lambda fid: thin_map.get(fid, set())

    txns = mfl_api.get_transactions("TRADE")
    conn = get_conn()
    seen = {r["txn_id"] for r in conn.execute("SELECT txn_id FROM scanned_trades")}
    now = datetime.now(timezone.utc).isoformat()

    new_results: list[dict] = []
    for txn in txns:
        p = parse_trade(txn)
        if p["txn_id"] in seen:
            continue

        result: TradeResult = score_trade(
            p["side1_ids"], p["side2_ids"], value_map,
            side1_owner=p["franchise1"], side2_owner=p["franchise2"],
            thin_lookup=thin_lookup,
            pick_resolver=pick_resolver,
        )
        lopsided = int(result.value_delta_pct >= LOPSIDED_THRESHOLD)

        conn.execute(
            """INSERT OR REPLACE INTO scanned_trades
               (txn_id, timestamp, franchise1, franchise2, side1_gave, side2_gave,
                value_delta, value_delta_pct, favored, verdict, lopsided, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                p["txn_id"], p["timestamp"], p["franchise1"], p["franchise2"],
                ",".join(p["side1_ids"]), ",".join(p["side2_ids"]),
                result.value_delta, result.value_delta_pct, result.favored,
                result.verdict, lopsided, now,
            ),
        )
        new_results.append({**p, "result": result, "lopsided": bool(lopsided)})

    conn.commit()
    conn.close()
    return new_results


def recent_trades(days: int = 7) -> list[dict]:
    """All scored trades from the last N days, most recent first."""
    import time
    cutoff = int(time.time()) - days * 86400
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM scanned_trades WHERE timestamp >= ? ORDER BY timestamp DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recent_lopsided(limit: int = 10) -> list[dict]:
    """Lopsided trades from the store, most recent first (for the Discord report)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM scanned_trades WHERE lopsided = 1 ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
