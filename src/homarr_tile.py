"""Homarr tile: minimal Flask endpoint + JSON status file.

Exposes GET /status -> JSON with roster rank, gem count, flagged trade count,
and last-run timestamp. Also writes homarr_status.json on disk so a simple
file-read widget can consume it without HTTP.

Run standalone: python -m src.homarr_tile
Or import write_status() to call after each cron run.
"""
import json
import os
from datetime import datetime, timezone

from flask import Flask, jsonify

from src.db import get_conn, init_db
from src.config import MFL_FRANCHISE_ID
from src.value_engine import get_value_map
from src.roster import franchise_positional_value
from src.waivers import waiver_gems

_STATUS_PATH = os.path.join(os.path.dirname(__file__), "..", "homarr_status.json")
app = Flask(__name__)


def _roster_rank(value_map: dict) -> int:
    """1-indexed rank of my franchise by total dynasty value."""
    fv = franchise_positional_value(value_map)
    totals = {fid: sum(v.values()) for fid, v in fv.items()}
    ranked = sorted(totals, key=lambda f: totals[f], reverse=True)
    try:
        return ranked.index(MFL_FRANCHISE_ID) + 1
    except ValueError:
        return -1


def _gem_count(value_map: dict) -> int:
    report = waiver_gems(top_n=10, value_map=value_map)
    return len(report["gems"])


def _flagged_trades_count() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) as n FROM scanned_trades WHERE lopsided=1").fetchone()
    conn.close()
    return row["n"]


def build_status() -> dict:
    init_db()
    value_map = get_value_map()
    return {
        "roster_rank": _roster_rank(value_map),
        "gems_available": _gem_count(value_map),
        "flagged_trades": _flagged_trades_count(),
        "last_run": datetime.now(timezone.utc).isoformat(),
    }


def write_status(status: dict | None = None) -> dict:
    if status is None:
        status = build_status()
    with open(_STATUS_PATH, "w") as f:
        json.dump(status, f, indent=2)
    return status


@app.route("/status")
def status_endpoint():
    return jsonify(build_status())


if __name__ == "__main__":
    port = int(os.getenv("HOMARR_PORT", "5055"))
    app.run(host="0.0.0.0", port=port)
