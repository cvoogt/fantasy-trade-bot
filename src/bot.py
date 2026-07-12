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
        live_event_poll.start()
        draft_watch.start()
        injury_watch.start()

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
    from src.mfl_api import franchise_name
    names = await asyncio.to_thread(lambda: {
        r["franchise1"]: franchise_name(r["franchise1"]) for r in results
    } | {r["franchise2"]: franchise_name(r["franchise2"]) for r in results})
    lines = []
    for r in results:
        res = r["result"]
        tag = " **← LOPSIDED**" if r["lopsided"] else ""
        lines.append(
            f"**{names[r['franchise1']]}** ↔ **{names[r['franchise2']]}** — {res.verdict} "
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


@bot.tree.command(name="lineup", description="Optimal starting lineup from weekly projections")
@app_commands.describe(week="NFL week (defaults to current)")
async def lineup_cmd(interaction: discord.Interaction, week: int | None = None):
    await interaction.response.defer(thinking=True)
    from src.lineup import lineup_advice

    adv = await asyncio.to_thread(lineup_advice, MFL_FRANCHISE_ID, None, week)

    embed = discord.Embed(
        title=f"Optimal Lineup — {adv['season']} week {adv['week']}",
        color=EMBED_COLOR,
    )
    by_slot: dict[str, list] = {}
    for p in adv["optimal"]:
        by_slot.setdefault(p["slot"], []).append(p)
    for slot, ps in by_slot.items():
        embed.add_field(
            name=slot,
            value="\n".join(f"{p['name']} ({p['proj']:.1f})" for p in ps),
            inline=True,
        )
    if adv["start"] or adv["sit"]:
        changes = [f"START {p['name']} ({p['proj']:.1f})" for p in adv["start"]]
        changes += [f"SIT {p['name']} ({p['proj']:.1f})" for p in adv["sit"]]
        embed.add_field(name="Changes vs your current lineup",
                        value="\n".join(changes), inline=False)
    elif adv["current"]:
        embed.add_field(name="Changes vs your current lineup",
                        value="None — already optimal.", inline=False)
    else:
        embed.set_footer(text="No submitted lineup found to compare (offseason or lineup not set).")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="trades", description="League trades from the last X days, with verdicts")
@app_commands.describe(days="How many days back to look (default 7)")
async def trades_cmd(interaction: discord.Interaction, days: int = 7):
    await interaction.response.defer(thinking=True)
    from src.scanner import recent_trades
    from src.mfl_api import franchise_name

    rows = await asyncio.to_thread(recent_trades, days)
    if not rows:
        await interaction.followup.send(f"No trades in the last {days} day(s).")
        return

    value_map = await asyncio.to_thread(_cache.get)
    pick_resolver = await asyncio.to_thread(lambda: make_pick_resolver(get_pick_value_map()))
    names = await asyncio.to_thread(lambda: {r["franchise1"]: franchise_name(r["franchise1"])
                                             for r in rows}
                                    | {r["franchise2"]: franchise_name(r["franchise2"])
                                       for r in rows})

    # fallback names for players FantasyCalc doesn't value (not in value_map)
    unresolved = {
        tok.strip()
        for r in rows for side in (r["side1_gave"], r["side2_gave"])
        for tok in (side or "").split(",")
        if tok.strip() and tok.strip().isdigit() and tok.strip() not in value_map
    }
    mfl_names: dict[str, str] = {}
    if unresolved:
        from src.mfl_api import get_players
        mfl_names = await asyncio.to_thread(
            lambda: {p["id"]: p.get("name", p["id"]) for p in get_players()
                     if p.get("id") in unresolved})

    def _pretty_pick(tok: str) -> str | None:
        import re
        m = re.match(r"FP_\d+_(\d{4})_(\d+)$", tok)
        if m:
            ords = {1: "1st", 2: "2nd", 3: "3rd"}
            rnd = int(m.group(2))
            return f"{m.group(1)} {ords.get(rnd, f'{rnd}th')}"
        m = re.match(r"DP_(\d+)_(\d+)$", tok)
        if m:
            return f"Pick {int(m.group(1)) + 1}.{int(m.group(2)) + 1:02d}"
        return None

    def asset_lines(gave: str) -> str:
        out = []
        for tok in (gave or "").split(","):
            tok = tok.strip()
            if not tok:
                continue
            info = value_map.get(tok) or pick_resolver(tok)
            out.append(info["name"] if info
                       else mfl_names.get(tok) or _pretty_pick(tok) or tok)
        return "\n".join(out) or "*(nothing)*"

    embed = discord.Embed(
        title=f"Trades — last {days} day(s)",
        color=EMBED_COLOR,
    )
    # 3 fields per trade (2 side-by-side columns + verdict row); 25-field embed cap
    shown = rows[:8]
    for r in shown:
        ts = datetime.datetime.fromtimestamp(r["timestamp"]).strftime("%b %d")
        f1, f2 = names[r["franchise1"]], names[r["franchise2"]]
        if r["verdict"] == "FAIR":
            verdict = "Fair"
        elif r["verdict"] == "LEAN":
            winner = f1 if r["favored"] == 1 else f2
            verdict = f"Lean — favors {winner} (gap {r['value_delta_pct']*100:.0f}%)"
        else:
            winner = f1 if r["favored"] == 1 else f2
            loser = f2 if r["favored"] == 1 else f1
            verdict = f"Fleece — {winner} fleeced {loser} (gap {r['value_delta_pct']*100:.0f}%)"
        # each column shows what that team RECEIVED (= the other side's assets)
        embed.add_field(name=f"📅 {ts} — {f1} received:",
                        value=asset_lines(r["side2_gave"]), inline=True)
        embed.add_field(name=f"{f2} received:",
                        value=asset_lines(r["side1_gave"]), inline=True)
        embed.add_field(name="​", value=f"Verdict: **{verdict}**", inline=False)
    if len(rows) > len(shown):
        embed.set_footer(text=f"Showing {len(shown)} of {len(rows)} trades.")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="tradefinder", description="Find mutually beneficial trades to propose")
async def tradefinder_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    from src.trade_finder import find_trades
    from src.mfl_api import franchise_name

    value_map = await asyncio.to_thread(_cache.get)
    proposals = await asyncio.to_thread(find_trades, MFL_FRANCHISE_ID, value_map)
    if not proposals:
        await interaction.followup.send(
            "No mutually beneficial trades found right now — your depth and the "
            "league's needs don't line up inside the fair band."
        )
        return

    embed = discord.Embed(
        title="Trade ideas worth pitching",
        description="1-for-1s where both sides fill a below-median position, "
                    "value inside the fair/lean band.",
        color=EMBED_COLOR,
    )
    for i, p in enumerate(proposals, 1):
        partner = await asyncio.to_thread(franchise_name, p["other_franchise"])
        net_txt = f"+{p['net']:,.0f}" if p["net"] >= 0 else f"{p['net']:,.0f}"
        embed.add_field(
            name=f"{i}. Pitch {partner}",
            value=(
                f"Send **{p['give']['name']}** ({p['give']['position']}, "
                f"{p['give']['dynasty_value']:,.0f})\n"
                f"For **{p['get']['name']}** ({p['get']['position']}, "
                f"{p['get']['dynasty_value']:,.0f})\n"
                f"Your net: **{net_txt}** (gap {p['gap_pct']*100:.0f}%) · "
                f"fills your {p['fills_my']}, their {p['fills_their']} · "
                f"salary Δ {p['salary_delta']:+,.0f}"
            ),
            inline=False,
        )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="draft", description="Best available in the rookie draft + my remaining picks")
async def draft_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    from src.draft_assistant import draft_state, best_available

    state = await asyncio.to_thread(draft_state)
    best = await asyncio.to_thread(best_available, 10, state["drafted_ids"])

    embed = discord.Embed(title="Draft Board", color=EMBED_COLOR)
    if state["on_clock"]:
        from src.mfl_api import franchise_name
        who = "**YOU ARE ON THE CLOCK**" if state["my_turn"] \
            else f"**{await asyncio.to_thread(franchise_name, state['on_clock'].franchise)}** on the clock"
        embed.description = f"Pick {state['on_clock'].label} — {who}"
    else:
        embed.description = "No draft in progress."
    embed.add_field(
        name="Best available (dynasty value)",
        value="\n".join(
            f"{i}. **{p['fc_name']}** {p['position']} {p['team']} — {p['dynasty_value']:,.0f}"
            + ("" if p["in_mfl"] else " *(not in MFL yet)*")
            for i, p in enumerate(best, 1)
        ) or "—",
        inline=False,
    )
    if state["my_remaining"]:
        embed.add_field(
            name="My remaining picks",
            value=", ".join(p.label for p in state["my_remaining"]),
            inline=False,
        )
    await interaction.followup.send(embed=embed)


# ---------- background tasks ----------

@tasks.loop(seconds=60)
async def live_event_poll():
    from src.live_events import in_game_window, my_starters, poll_events
    from src.sleeper_api import get_nfl_state

    if not in_game_window():
        return
    try:
        state = await asyncio.to_thread(get_nfl_state)
        if state.get("season_type") not in ("regular", "post"):
            return
        season, week = int(state["season"]), max(int(state.get("week") or 1), 1)

        watched = await asyncio.to_thread(my_starters)
        events = await asyncio.to_thread(poll_events, season, week, watched)
        if not events:
            return
        ch = await bot.alert_channel()
        if ch is None:
            log.warning("Live events detected but no alert channel configured.")
            return
        for ev in events:
            nth = f" — #{ev.total} on the day" if ev.total > 1 else ""
            await ch.send(f"{ev.emoji} **{ev.player_name}** {ev.label}!{nth}")
    except Exception:
        log.exception("live_event_poll failed")


@live_event_poll.before_loop
async def _wait_ready_live():
    await bot.wait_until_ready()


@tasks.loop(seconds=60)
async def draft_watch():
    from src.draft_assistant import draft_state, best_available, already_pinged, mark_pinged

    try:
        state = await asyncio.to_thread(draft_state)
        if not (state["active"] and state["my_turn"]):
            return
        pick = state["on_clock"]
        if await asyncio.to_thread(already_pinged, pick):
            return
        ch = await bot.alert_channel()
        if ch is None:
            log.warning("On the clock but no alert channel configured.")
            return
        best = await asyncio.to_thread(best_available, 5, state["drafted_ids"])
        lines = [
            f"{i}. **{p['fc_name']}** {p['position']} {p['team']} — {p['dynasty_value']:,.0f}"
            + ("" if p["in_mfl"] else " *(not in MFL yet)*")
            for i, p in enumerate(best, 1)
        ]
        embed = discord.Embed(
            title=f"⏰ You're on the clock — pick {pick.label}",
            description="\n".join(lines),
            color=0xE67E22,
        )
        await ch.send("@here", embed=embed)
        await asyncio.to_thread(mark_pinged, pick)
    except Exception:
        log.exception("draft_watch failed")


@draft_watch.before_loop
async def _wait_ready_draft():
    await bot.wait_until_ready()


@tasks.loop(hours=1)
async def injury_watch():
    from src.injuries import check_injuries, bench_replacements

    try:
        changes = await asyncio.to_thread(check_injuries)
        if not changes:
            return
        ch = await bot.alert_channel()
        if ch is None:
            log.warning("Injury changes found but no alert channel configured.")
            return
        for c in changes:
            old = c.old or "Healthy"
            new = c.new or "Healthy"
            if c.is_downgrade:
                emoji, color = "🚑", 0xCC3333
            else:
                emoji, color = "💪", 0x2E8B57
            embed = discord.Embed(
                title=f"{emoji} {c.name} ({c.position}): {old} → {new}",
                color=color,
            )
            if c.is_downgrade:
                subs = await asyncio.to_thread(
                    bench_replacements, c.position, MFL_FRANCHISE_ID, {c.mfl_id})
                if subs:
                    embed.add_field(
                        name="Next man up",
                        value="\n".join(
                            f"{s['name']} (val {s['dynasty_value']:,.0f})" for s in subs),
                        inline=False,
                    )
            await ch.send(embed=embed)
    except Exception:
        log.exception("injury_watch failed")


@injury_watch.before_loop
async def _wait_ready_injury():
    await bot.wait_until_ready()


@tasks.loop(hours=1)
async def hourly_scan():
    try:
        value_map = await asyncio.to_thread(_cache.get)

        # waiver-drop watch: valuable players newly in the FA pool
        from src.waivers import check_new_fas
        new_fas = await asyncio.to_thread(check_new_fas, value_map)
        if new_fas:
            ch = await bot.alert_channel()
            if ch is not None:
                embed = discord.Embed(
                    title="🎣 Valuable player(s) hit the waiver wire",
                    description="\n".join(
                        f"**{p['name']}** ({p['position']}) — value {p['dynasty_value']:,.0f}"
                        for p in new_fas[:10]),
                    color=0xE6B422,
                )
                await ch.send(embed=embed)

        results = await asyncio.to_thread(scan_trades, value_map)
        lopsided = [r for r in results if r["lopsided"]]
        if not lopsided:
            return
        ch = await bot.alert_channel()
        if ch is None:
            log.warning("Lopsided trades found but no alert channel configured.")
            return
        from src.mfl_api import franchise_name
        for r in lopsided:
            res = r["result"]
            winner = r["franchise1"] if res.favored == 1 else r["franchise2"]
            loser = r["franchise2"] if res.favored == 1 else r["franchise1"]
            w, l = await asyncio.to_thread(
                lambda: (franchise_name(winner), franchise_name(loser)))
            embed = discord.Embed(
                title="Lopsided trade detected",
                description=(
                    f"**{w}** fleeced **{l}** "
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
