"""Build and push the weekly Discord report via webhook.

The report has three sections:
  1. My roster health (dynasty value by position vs league median).
  2. Top waiver gems + suggested drops.
  3. Lopsided league trades since last report.

Posts as chunked messages if content exceeds Discord's 2000-char limit.
"""
import requests
from datetime import datetime, timezone

from src.config import DISCORD_WEBHOOK_URL, MFL_FRANCHISE_ID
from src.value_engine import get_value_map
from src.roster import franchise_positional_value, league_median_by_position
from src.waivers import waiver_gems
from src.scanner import scan_trades, recent_lopsided


_LINE = "-" * 40


def _roster_health_section(value_map: dict) -> str:
    fv = franchise_positional_value(value_map)
    medians = league_median_by_position(fv)
    mine = fv.get(MFL_FRANCHISE_ID, {})

    lines = ["**My Roster Health**"]
    for pos in sorted(medians):
        my_val = mine.get(pos, 0.0)
        med = medians[pos]
        delta = my_val - med
        arrow = "^" if delta >= 0 else "v"
        lines.append(
            f"  {pos:<4} {my_val:>6.0f}  vs median {med:>6.0f}  [{arrow}{abs(delta):.0f}]"
        )
    return "\n".join(lines)


def _waiver_section(value_map: dict) -> str:
    report = waiver_gems(value_map=value_map)
    thin = report["thin_positions"]
    lines = ["**Top Waiver Gems**"]
    if thin:
        lines.append(f"  Thin positions: {', '.join(sorted(thin))}")

    for i, pair in enumerate(report["pairs"], 1):
        gem = pair["gem"]
        drop = pair["drop"]
        tag = " [thin]" if gem["position"] in thin else ""
        lines.append(
            f"  {i}. ADD  {gem['name']} ({gem['position']}, val={gem['dynasty_value']:.0f}){tag}"
        )
        if drop:
            lines.append(
                f"     DROP {drop['name']} ({drop['position']}, val={drop['dynasty_value']:.0f})"
            )

    return "\n".join(lines)


def _trades_section() -> str:
    lopsided = recent_lopsided(limit=5)
    lines = ["**Lopsided Trades This Week**"]
    if not lopsided:
        lines.append("  None flagged.")
        return "\n".join(lines)

    for row in lopsided:
        ts = datetime.fromtimestamp(row["timestamp"], tz=timezone.utc).strftime("%b %d")
        winner = row["franchise1"] if row["favored"] == 1 else row["franchise2"]
        loser = row["franchise2"] if row["favored"] == 1 else row["franchise1"]
        lines.append(
            f"  {ts}  {winner} FLEECED {loser}  "
            f"(gap {row['value_delta_pct']*100:.0f}%, {row['verdict']})"
        )
    return "\n".join(lines)


def build_report(value_map: dict | None = None) -> str:
    if value_map is None:
        value_map = get_value_map()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"**Fantasy Trade Bot Report** - {now}",
        _LINE,
        _roster_health_section(value_map),
        _LINE,
        _waiver_section(value_map),
        _LINE,
        _trades_section(),
    ]
    return "\n".join(parts)


def _chunks(text: str, limit: int = 1990) -> list[str]:
    """Split at newline boundaries to stay under Discord's 2000-char message limit."""
    if len(text) <= limit:
        return [text]
    out, buf = [], []
    for line in text.splitlines(keepends=True):
        if sum(len(l) for l in buf) + len(line) > limit:
            out.append("".join(buf))
            buf = []
        buf.append(line)
    if buf:
        out.append("".join(buf))
    return out


def push_report(report: str | None = None) -> bool:
    """Post the report to Discord. Returns True on success."""
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL not set - skipping push.")
        return False

    if report is None:
        report = build_report()

    ok = True
    for chunk in _chunks(report):
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": chunk},
            timeout=15,
        )
        if not resp.ok:
            print(f"Discord push failed: {resp.status_code} {resp.text}")
            ok = False
    return ok


def run_weekly():
    """Entry point called by cron. Scans new trades, pushes report, updates Homarr tile."""
    value_map = get_value_map()
    new = scan_trades(value_map=value_map)
    if new:
        print(f"Scanned {len(new)} new trade(s).")
    report = build_report(value_map=value_map)
    print(report)
    print()
    push_report(report)

    from src.homarr_tile import write_status
    write_status()
    print("homarr_status.json updated.")
