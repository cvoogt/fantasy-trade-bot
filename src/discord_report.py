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


REPORT_COLOR = 0x2E8B57  # sea green, matching the bot's embeds

# Bar drawn next to each position to show roster value vs the league median.
_BAR_WIDTH = 12


def _delta_bar(delta: float, scale: float) -> str:
    """A centered bar: filled blocks right of centre when above median, left
    when below. `scale` is the largest absolute delta, setting full deflection."""
    if scale <= 0:
        return "·" * _BAR_WIDTH
    half = _BAR_WIDTH // 2
    n = min(half, round(half * abs(delta) / scale))
    if delta >= 0:
        return "·" * half + "█" * n + "·" * (half - n)
    return "·" * (half - n) + "█" * n + "·" * half


def _roster_health_section(value_map: dict) -> tuple[str, str]:
    """(field name, field value) for the roster-health block."""
    fv = franchise_positional_value(value_map)
    medians = league_median_by_position(fv)
    mine = fv.get(MFL_FRANCHISE_ID, {})

    totals = {fid: sum(v.values()) for fid, v in fv.items()}
    rank = (sorted(totals, key=lambda f: totals[f], reverse=True)
            .index(MFL_FRANCHISE_ID) + 1) if MFL_FRANCHISE_ID in totals else None

    deltas = {pos: mine.get(pos, 0.0) - medians[pos] for pos in medians}
    scale = max((abs(d) for d in deltas.values()), default=0.0)

    rows = []
    for pos in sorted(medians):
        d = deltas[pos]
        sign = "+" if d >= 0 else "−"
        rows.append(f"{pos:<5} {mine.get(pos, 0.0):>7,.0f} {_delta_bar(d, scale)} "
                    f"{sign}{abs(d):>6,.0f}")

    name = "📊 Roster Health"
    if rank:
        name += f" — #{rank} of {len(totals)}"
    body = "```\n" + "\n".join(rows) + "\n```" if rows else "_No data._"
    return name, body


def _waiver_section(value_map: dict) -> tuple[str, str]:
    report = waiver_gems(value_map=value_map)
    thin = report["thin_positions"]

    lines = []
    for i, pair in enumerate(report["pairs"], 1):
        gem, drop = pair["gem"], pair["drop"]
        tag = " 🎯" if gem["position"] in thin else ""
        lines.append(
            f"**{i}.** 🟢 **{gem['name']}** · {gem['position']} · "
            f"`{gem['dynasty_value']:,.0f}`{tag}"
        )
        if drop:
            lines.append(
                f"　　🔻 drop {drop['name']} · {drop['position']} · "
                f"`{drop['dynasty_value']:,.0f}`"
            )

    name = "💎 Top Waiver Gems"
    if thin:
        name += f" — thin at {', '.join(sorted(thin))}"
    return name, ("\n".join(lines) if lines else "_None worth adding._")


def _trades_section() -> tuple[str, str]:
    lopsided = recent_lopsided(limit=5)
    if not lopsided:
        return "⚖️ Lopsided Trades", "_None flagged this week._"

    from src.mfl_api import franchise_name
    lines = []
    for row in lopsided:
        ts = datetime.fromtimestamp(row["timestamp"], tz=timezone.utc).strftime("%b %d")
        winner = row["franchise1"] if row["favored"] == 1 else row["franchise2"]
        loser = row["franchise2"] if row["favored"] == 1 else row["franchise1"]
        lines.append(
            f"`{ts}` **{franchise_name(winner)}** fleeced {franchise_name(loser)} "
            f"— gap **{row['value_delta_pct']*100:.0f}%**"
        )
    return "⚖️ Lopsided Trades", "\n".join(lines)


def report_sections(value_map: dict | None = None) -> list[tuple[str, str]]:
    """[(heading, body)] for the weekly report, ready to render as embed
    fields (bot) or plain text (webhook/CLI)."""
    if value_map is None:
        value_map = get_value_map()
    return [
        _roster_health_section(value_map),
        _waiver_section(value_map),
        _trades_section(),
    ]


def build_report(value_map: dict | None = None) -> str:
    """Plain-text rendering (webhook + CLI paths)."""
    now = datetime.now(timezone.utc).strftime("%b %d, %Y · %H:%M UTC")
    parts = [f"## 🏈 Fantasy Trade Bot Report\n_{now}_"]
    for heading, body in report_sections(value_map):
        parts.append(f"\n**{heading}**\n{body}")
    return "\n".join(parts)


def build_report_embed(value_map: dict | None = None):
    """discord.Embed rendering (bot path). Imported lazily so this module
    still works without discord.py installed (CLI/webhook use)."""
    import discord

    embed = discord.Embed(
        title="🏈 Fantasy Trade Bot Report",
        color=REPORT_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    for heading, body in report_sections(value_map):
        # Discord caps a field value at 1024 chars.
        embed.add_field(name=heading, value=body[:1024], inline=False)
    embed.set_footer(text="Dynasty values · FantasyCalc + league-scored IDP")
    return embed


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
