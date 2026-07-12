"""Interactive Discord bot — slash commands + background tasks.

Commands: /waivers /roster /scan /player /trade
Background: hourly trade scan (auto-posts lopsided trades), weekly reports
(Sun 10pm + Tue 8pm server time).

Run: python -m src.bot   (requires DISCORD_BOT_TOKEN in .env)
"""
import asyncio
import datetime
import logging
import time

import discord
from discord import app_commands
from discord.ext import tasks

from src.config import (
    DISCORD_BOT_TOKEN,
    DISCORD_GUILD_ID,
    DISCORD_ALERT_CHANNEL_ID,
    MFL_FRANCHISE_ID,
)
from src.db import init_db
from src.crosswalk import resolve_player
from src.value_engine import get_value_map, make_pick_resolver, get_pick_value_map
from src.trade_scorer import score_trade, format_result
from src.waivers import waiver_gems
from src.scanner import scan_trades

log = logging.getLogger("fantasybot")

EMBED_COLOR = 0x2E8B57  # sea green
_VALUE_MAP_TTL = 900  # 15 min — value map build hits MFL + pandas, don't rebuild per command


class ValueMapCache:
    def __init__(self):
        self._map: dict | None = None
        self._at: float = 0.0

    def get(self) -> dict:
        if self._map is None or time.monotonic() - self._at > _VALUE_MAP_TTL:
            self._map = get_value_map()
            self._at = time.monotonic()
        return self._map


_cache = ValueMapCache()


def _resolve_side(raw: str, value_map: dict, pick_resolver) -> tuple[list[str], list[str], list[str]]:
    """Turn a comma-separated string of names / MFL ids / pick tokens into MFL-id-ish
    tokens for score_trade. Returns (tokens, resolved_labels, not_found)."""
    tokens, labels, missing = [], [], []
    for part in [p.strip() for p in raw.split(",") if p.strip()]:
        if part in value_map or pick_resolver(part) is not None:
            tokens.append(part)
            info = value_map.get(part) or pick_resolver(part)
            labels.append(info["name"])
            continue
        cands = resolve_player(part, limit=1, min_score=75)
        if cands:
            tokens.append(cands[0]["mfl_id"])
            labels.append(f"{cands[0]['fc_name']} ({part})" if cands[0]["resolve_score"] < 90
                          else cands[0]["fc_name"])
        else:
            missing.append(part)
    return tokens, labels, missing


class FantasyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        init_db()
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        hourly_scan.start()
        weekly_report.start()

    async def alert_channel(self) -> discord.abc.Messageable | None:
        if not DISCORD_ALERT_CHANNEL_ID:
            return None
        ch = self.get_channel(DISCORD_ALERT_CHANNEL_ID)
        if ch is None:
            try:
                ch = await self.fetch_channel(DISCORD_ALERT_CHANNEL_ID)
            except discord.HTTPException:
                return None
        return ch


bot = FantasyBot()


# ---------- slash commands ----------

@bot.tree.command(name="waivers", description="Top waiver gems + suggested drops")
async def waivers_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    value_map = await asyncio.to_thread(_cache.get)
    report = await asyncio.to_thread(waiver_gems, 5, MFL_FRANCHISE_ID, value_map)

    embed = discord.Embed(title="Top Waiver Gems", color=EMBED_COLOR)
    thin = report["thin_positions"]
    if thin:
        embed.description = f"Thin positions: {', '.join(sorted(thin))}"
    for i, pair in enumerate(report["pairs"], 1):
        gem, drop = pair["gem"], pair["drop"]
        tag = " • fills thin spot" if gem["position"] in thin else ""
        val = (f"ADD **{gem['name']}** ({gem['position']}, val {gem['dynasty_value']:.0f}){tag}")
        if drop:
            val += f"\nDROP {drop['name']} ({drop['position']}, val {drop['dynasty_value']:.0f})"
        embed.add_field(name=f"#{i}", value=val, inline=False)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="roster", description="My roster health vs league median")
async def roster_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    value_map = await asyncio.to_thread(_cache.get)

    from src.roster import franchise_positional_value, league_median_by_position

    fv = await asyncio.to_thread(franchise_positional_value, value_map)
    medians = league_median_by_position(fv)
    mine = fv.get(MFL_FRANCHISE_ID, {})
    totals = {fid: sum(v.values()) for fid, v in fv.items()}
    rank = sorted(totals, key=lambda f: totals[f], reverse=True).index(MFL_FRANCHISE_ID) + 1

    embed = discord.Embed(
        title="Roster Health",
        description=f"League rank by total dynasty value: **#{rank}** of {len(totals)}",
        color=EMBED_COLOR,
    )
    for pos in sorted(medians):
        mv, med = mine.get(pos, 0.0), medians[pos]
        delta = mv - med
        sign = "+" if delta >= 0 else "−"
        embed.add_field(
            name=pos,
            value=f"{mv:,.0f}\n({sign}{abs(delta):,.0f} vs median)",
            inline=True,
        )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="scan", description="Scan league for new trades and score them")
async def scan_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    value_map = await asyncio.to_thread(_cache.get)
    results = await asyncio.to_thread(scan_trades, value_map)
    if not results:
        await interaction.followup.send("No new trades since last scan.")
        return
    lines = []
    for r in results:
        res = r["result"]
        tag = " **← LOPSIDED**" if r["lopsided"] else ""
        lines.append(
            f"`{r['franchise1']} ↔ {r['franchise2']}` {res.verdict} "
            f"(gap {res.value_delta_pct*100:.0f}%){tag}"
        )
    embed = discord.Embed(
        title=f"{len(results)} new trade(s)",
        description="\n".join(lines)[:4000],
        color=EMBED_COLOR,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="player", description="Look up a player's dynasty value, salary, VPD, VOR")
@app_commands.describe(name="Player name (fuzzy match ok)")
async def player_cmd(interaction: discord.Interaction, name: str):
    await interaction.response.defer(thinking=True)
    value_map = await asyncio.to_thread(_cache.get)
    cands = await asyncio.to_thread(resolve_player, name, 3)
    if not cands:
        await interaction.followup.send(f"No player matching **{name}**.")
        return

    embed = discord.Embed(title="Player Lookup", color=EMBED_COLOR)
    for c in cands:
        info = value_map.get(c["mfl_id"])
        if not info:
            continue
        embed.add_field(
            name=f"{c['fc_name']} ({info['position']}, {c['team']})",
            value=(
                f"Dynasty value: **{info['dynasty_value']:,.0f}**\n"
                f"Salary: ${info['salary']:,.0f}\n"
                f"Value/$: {info['value_per_dollar']:.4f}\n"
                f"VOR: {info['vor']:,.0f}"
            ),
            inline=False,
        )
    if not embed.fields:
        await interaction.followup.send(f"Matched **{cands[0]['fc_name']}** but no value data.")
        return
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="trade", description="Score a trade — names, MFL ids, or picks ('2026 1st')")
@app_commands.describe(
    give="What you send, comma-separated (e.g. 'Bijan Robinson, 2026 2nd')",
    get="What you receive, comma-separated",
)
async def trade_cmd(interaction: discord.Interaction, give: str, get: str):
    await interaction.response.defer(thinking=True)
    value_map = await asyncio.to_thread(_cache.get)
    pick_resolver = await asyncio.to_thread(lambda: make_pick_resolver(get_pick_value_map()))

    s1_tokens, s1_labels, s1_missing = _resolve_side(give, value_map, pick_resolver)
    s2_tokens, s2_labels, s2_missing = _resolve_side(get, value_map, pick_resolver)
    if s1_missing or s2_missing:
        await interaction.followup.send(
            "Couldn't resolve: " + ", ".join(f"**{m}**" for m in s1_missing + s2_missing)
        )
        return

    from src.roster import thin_positions
    thin_lookup = lambda fid: thin_positions(fid, value_map)
    result = await asyncio.to_thread(
        score_trade, s1_tokens, s2_tokens, value_map,
        MFL_FRANCHISE_ID, None, thin_lookup, pick_resolver,
    )

    if result.verdict == "FAIR":
        verdict = "FAIR trade"
    elif result.favored == 1:
        verdict = f"**You win this one** ({result.verdict}, gap {result.value_delta_pct*100:.0f}%)"
    else:
        verdict = f"**You're overpaying** ({result.verdict}, gap {result.value_delta_pct*100:.0f}%)"

    embed = discord.Embed(title="Trade Score", description=verdict, color=EMBED_COLOR)
    embed.add_field(
        name=f"You give (value {result.side1.total_value:,.0f})",
        value="\n".join(s1_labels) or "—", inline=True,
    )
    embed.add_field(
        name=f"You get (value {result.side2.total_value:,.0f})",
        value="\n".join(s2_labels) or "—", inline=True,
    )
    embed.add_field(
        name="Net",
        value=(
            f"Value: {result.side2.total_value - result.side1.total_value:+,.0f}\n"
            f"Salary: {-result.salary_delta:+,.0f}"
        ),
        inline=False,
    )
    for flag in result.positional_flags:
        embed.add_field(name="⚠ Positional fit", value=flag, inline=False)
    await interaction.followup.send(embed=embed)


# ---------- background tasks ----------

@tasks.loop(hours=1)
async def hourly_scan():
    try:
        value_map = await asyncio.to_thread(_cache.get)
        results = await asyncio.to_thread(scan_trades, value_map)
        lopsided = [r for r in results if r["lopsided"]]
        if not lopsided:
            return
        ch = await bot.alert_channel()
        if ch is None:
            log.warning("Lopsided trades found but no alert channel configured.")
            return
        for r in lopsided:
            res = r["result"]
            winner = r["franchise1"] if res.favored == 1 else r["franchise2"]
            loser = r["franchise2"] if res.favored == 1 else r["franchise1"]
            embed = discord.Embed(
                title="Lopsided trade detected",
                description=(
                    f"**{winner}** fleeced **{loser}** "
                    f"(gap {res.value_delta_pct*100:.0f}%)"
                ),
                color=0xCC3333,
            )
            await ch.send(embed=embed)
    except Exception:
        log.exception("hourly_scan failed")


@hourly_scan.before_loop
async def _wait_ready_scan():
    await bot.wait_until_ready()


_REPORT_TIMES = [datetime.time(hour=22, minute=0), datetime.time(hour=20, minute=0)]


@tasks.loop(time=_REPORT_TIMES)
async def weekly_report():
    now = datetime.datetime.now()
    # Sunday 22:00 and Tuesday 20:00 only
    if not ((now.weekday() == 6 and now.hour == 22) or (now.weekday() == 1 and now.hour == 20)):
        return
    try:
        from src.discord_report import build_report, _chunks
        from src.homarr_tile import write_status

        value_map = await asyncio.to_thread(_cache.get)
        await asyncio.to_thread(scan_trades, value_map)
        report = await asyncio.to_thread(build_report, value_map)
        await asyncio.to_thread(write_status)

        ch = await bot.alert_channel()
        if ch is None:
            log.warning("Weekly report built but no alert channel configured.")
            return
        for chunk in _chunks(report):
            await ch.send(chunk)
    except Exception:
        log.exception("weekly_report failed")


@weekly_report.before_loop
async def _wait_ready_report():
    await bot.wait_until_ready()


def main():
    logging.basicConfig(level=logging.INFO)
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN not set in .env — create a bot at "
                         "https://discord.com/developers/applications and paste its token.")
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
