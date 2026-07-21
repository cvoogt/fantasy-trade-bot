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
    DISCORD_OWNER_ID,
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

    def clear(self):
        self._map = None
        self._at = 0.0


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
        projections_refresh.start()

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
        from src.roster import group_of
        tag = " • fills thin spot" if group_of(gem["position"]) in thin else ""
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

    from src.projections import get_projected_points
    from src.sleeper_api import get_nfl_state
    try:
        state = await asyncio.to_thread(get_nfl_state)
        season = int(state["season"])
        season_proj = await asyncio.to_thread(get_projected_points, season, None)
        wk = int(state.get("week") or 0)
        week_proj = (await asyncio.to_thread(get_projected_points, season, wk)
                     if state.get("season_type") == "regular" and wk >= 1 else {})
    except Exception:
        season_proj, week_proj = {}, {}

    embed = discord.Embed(title="Player Lookup", color=EMBED_COLOR)
    for c in cands:
        info = value_map.get(c["mfl_id"])
        if not info:
            continue
        proj_lines = ""
        sp = season_proj.get(c["mfl_id"])
        if sp:
            proj_lines += f"\nProj (season): **{sp['points']:.0f} pts**"
        wp = week_proj.get(c["mfl_id"])
        if wp:
            proj_lines += f"\nProj (this week): **{wp['points']:.1f} pts**"
        embed.add_field(
            name=f"{c['fc_name']} ({info['position']}, {c['team']})",
            value=(
                f"Dynasty value: **{info['dynasty_value']:,.0f}**\n"
                f"Salary: ${info['salary']:,.0f}\n"
                f"Value/$: {info['value_per_dollar']:.4f}\n"
                f"VOR: {info['vor']:,.0f}"
                f"{proj_lines}"
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


@bot.tree.command(name="matchup", description="My head-to-head matchup: opponent, score, players left")
@app_commands.describe(week="NFL week (defaults to current)")
async def matchup_cmd(interaction: discord.Interaction, week: int | None = None):
    await interaction.response.defer(thinking=True)
    from src.matchup import matchup_status
    from src.mfl_api import franchise_name

    st = await asyncio.to_thread(matchup_status, week, MFL_FRANCHISE_ID)
    if st["opponent_id"] is None:
        await interaction.followup.send(
            f"No matchup found for week {st['week']} (bye, playoffs cutoff, or offseason)."
        )
        return
    me_name, opp_name = await asyncio.to_thread(
        lambda: (franchise_name(MFL_FRANCHISE_ID), franchise_name(st["opponent_id"])))

    embed = discord.Embed(
        title=f"Week {st['week']}: {me_name} vs {opp_name}",
        color=EMBED_COLOR,
    )
    for label, s in ((me_name, st["me"]), (opp_name, st["them"])):
        score = s["score"] if s["score"] not in (None, "") else "—"
        extra = ""
        if s["yet_to_play"] is not None:
            extra = f"\nYet to play: {s['yet_to_play']}"
            if s["playing"] not in (None, ""):
                extra += f" · playing now: {s['playing']}"
        embed.add_field(name=label, value=f"**{score}**{extra}", inline=True)
    if not st["live"]:
        embed.set_footer(text="No live games right now — scores are final/last known.")
    await interaction.followup.send(embed=embed)


def _build_tradefinder_embed(value_map: dict) -> discord.Embed | None:
    """Blocking: run the finder and render the embed (None = no proposals)."""
    from src.trade_finder import find_trades
    from src.mfl_api import franchise_name

    proposals = find_trades(MFL_FRANCHISE_ID, value_map)
    if not proposals:
        return None
    embed = discord.Embed(
        title="Trade ideas worth pitching",
        description="1-for-1s where both sides fill a below-median position, "
                    "value inside the fair/lean band.",
        color=EMBED_COLOR,
    )
    for i, p in enumerate(proposals, 1):
        partner = franchise_name(p["other_franchise"])
        net_txt = f"+{p['net']:,.0f}" if p["net"] >= 0 else f"{p['net']:,.0f}"
        embed.add_field(
            name=f"{i}. Pitch {partner}",
            value=(
                f"Send **{p['give']['name']}** ({p['give']['position']}, "
                f"val {p['give']['dynasty_value']:,.0f}, ${p['give']['salary']:,.0f})\n"
                f"For **{p['get']['name']}** ({p['get']['position']}, "
                f"val {p['get']['dynasty_value']:,.0f}, ${p['get']['salary']:,.0f})\n"
                f"Your net: **{net_txt}** (gap {p['gap_pct']*100:.0f}%) · "
                f"fills your {p['fills_my']}, their {p['fills_their']} · "
                f"cap change {p['salary_delta']:+,.0f}"
            ),
            inline=False,
        )
    return embed


class TradeFinderView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=900)

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        _cache.clear()  # fresh MFL rosters/salaries + FantasyCalc cache + IDP values
        value_map = await asyncio.to_thread(_cache.get)
        embed = await asyncio.to_thread(_build_tradefinder_embed, value_map)
        if embed is None:
            await interaction.edit_original_response(
                content="No mutually beneficial trades found right now.",
                embed=None, view=self)
            return
        embed.set_footer(text="Refreshed just now")
        await interaction.edit_original_response(embed=embed, view=self)


@bot.tree.command(name="tradefinder", description="Find mutually beneficial trades to propose")
async def tradefinder_cmd(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    value_map = await asyncio.to_thread(_cache.get)
    embed = await asyncio.to_thread(_build_tradefinder_embed, value_map)
    if embed is None:
        await interaction.followup.send(
            "No mutually beneficial trades found right now — your depth and the "
            "league's needs don't line up inside the fair band.",
            view=TradeFinderView(),
        )
        return
    await interaction.followup.send(embed=embed, view=TradeFinderView())


async def _team_autocomplete(interaction: discord.Interaction, current: str):
    from src.mfl_api import franchise_names
    names = await asyncio.to_thread(franchise_names)
    return [
        app_commands.Choice(name=n, value=fid)
        for fid, n in names.items()
        if current.lower() in n.lower()
    ][:10]


@bot.tree.command(name="salary", description="Salary-cap analysis (defaults to my team)")
@app_commands.describe(team="Another team to analyze (defaults to yours)")
@app_commands.autocomplete(team=_team_autocomplete)
async def salary_cmd(interaction: discord.Interaction, team: str | None = None):
    await interaction.response.defer(thinking=True)
    from src.salary_tools import team_salary_summary
    from src.mfl_api import franchise_name

    fid = team or MFL_FRANCHISE_ID
    value_map = await asyncio.to_thread(_cache.get)
    s = await asyncio.to_thread(team_salary_summary, fid, value_map)
    name = await asyncio.to_thread(franchise_name, fid)

    embed = discord.Embed(title=f"💰 Salary Analysis — {name}", color=EMBED_COLOR)
    cap_line = f"Payroll: **${s['total_salary']:,.0f}**"
    if s["cap"]:
        cap_line += f" / ${s['cap']:,.0f} cap → space **${s['cap_space']:,.0f}**"
    cap_line += f"\nLeague payroll rank: #{s['league_rank']} of {len(s['league_totals'])}"
    embed.description = cap_line

    embed.add_field(
        name="By position group",
        value="\n".join(f"{g}: ${v:,.0f}"
                        for g, v in sorted(s["by_group"].items(), key=lambda t: -t[1])),
        inline=True,
    )
    embed.add_field(
        name="Top contracts",
        value="\n".join(f"{p['name']} ${p['salary']:,.0f}" for p in s["top_contracts"]),
        inline=True,
    )
    embed.add_field(
        name="Best bang-for-buck",
        value="\n".join(
            f"{p['name']} (val {p['dynasty_value']:,.0f} @ ${p['salary']:,.0f})"
            for p in s["best_value"]) or "—",
        inline=False,
    )
    embed.add_field(
        name="Worst contracts",
        value="\n".join(
            f"{p['name']} (val {p['dynasty_value']:,.0f} @ ${p['salary']:,.0f})"
            for p in s["worst_value"]) or "—",
        inline=False,
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="cuts", description="Recommended cuts: cap relief for low-value contracts")
@app_commands.describe(team="Another team to analyze (defaults to yours)")
@app_commands.autocomplete(team=_team_autocomplete)
async def cuts_cmd(interaction: discord.Interaction, team: str | None = None):
    await interaction.response.defer(thinking=True)
    from src.salary_tools import cut_candidates
    from src.mfl_api import franchise_name

    fid = team or MFL_FRANCHISE_ID
    value_map = await asyncio.to_thread(_cache.get)
    cands = await asyncio.to_thread(cut_candidates, fid, value_map)
    name = await asyncio.to_thread(franchise_name, fid)

    if not cands:
        await interaction.followup.send(
            f"No obvious cuts for **{name}** — nobody expensive is sitting below "
            "replacement level."
        )
        return

    embed = discord.Embed(
        title=f"✂️ Recommended Cuts — {name}",
        description="Above-minimum salaries on players below replacement level "
                    "at their position, worst cap-efficiency first.",
        color=0xCC6633,
    )
    total_relief = 0.0
    for i, p in enumerate(cands, 1):
        total_relief += p["salary"]
        embed.add_field(
            name=f"{i}. {p['name']} ({p['position']})",
            value=(f"Cap relief: **${p['salary']:,.0f}** · "
                   f"value lost: {p['dynasty_value']:,.0f} · VOR {p['vor']:,.0f}"),
            inline=False,
        )
    embed.set_footer(text=f"Cutting all {len(cands)} frees ${total_relief:,.0f}")
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


@bot.tree.command(name="projections", description="Top projected players under league scoring")
@app_commands.describe(
    scope="Season-long or a specific week",
    position="Filter to a position (QB/RB/WR/TE/PK/DT/DE/LB/CB/S)",
    week="Week number (only with scope=week; defaults to current)",
)
@app_commands.choices(scope=[
    app_commands.Choice(name="season", value="season"),
    app_commands.Choice(name="week", value="week"),
])
async def projections_cmd(interaction: discord.Interaction, scope: str = "season",
                          position: str | None = None, week: int | None = None):
    await interaction.response.defer(thinking=True)
    from src.projections import get_projected_points
    from src.sleeper_api import get_nfl_state
    from src.mfl_api import get_players, get_rosters

    state = await asyncio.to_thread(get_nfl_state)
    season = int(state["season"])
    wk = None
    if scope == "week":
        wk = week or max(int(state.get("week") or 1), 1)

    proj = await asyncio.to_thread(get_projected_points, season, wk)
    if not proj:
        await interaction.followup.send("No projections available for that scope yet.")
        return

    def _meta():
        players = {p["id"]: p for p in get_players()}
        mine = set()
        for fr in get_rosters():
            if fr.get("id") == MFL_FRANCHISE_ID:
                pl = fr.get("player", [])
                if isinstance(pl, dict):
                    pl = [pl]
                mine = {p.get("id") for p in pl}
        return players, mine

    players, mine = await asyncio.to_thread(_meta)

    rows = []
    for mfl_id, p in proj.items():
        meta = players.get(mfl_id)
        if not meta:
            continue
        pos = meta.get("position", "?")
        if position and pos.upper() != position.upper():
            continue
        rows.append((p["points"], meta.get("name", mfl_id), pos,
                     mfl_id in mine, p["sources"]))
    rows.sort(reverse=True)

    label = f"week {wk}" if wk else "season"
    title = f"Projections — {label}" + (f" — {position.upper()}" if position else "")
    lines = []
    for i, (pts, name, pos, is_mine, sources) in enumerate(rows[:15], 1):
        star = " ⭐" if is_mine else ""
        src = "²" if sources > 1 else ""
        lines.append(f"`{i:>2}.` **{name}** ({pos}) — {pts:.1f}{src}{star}")
    embed = discord.Embed(title=title, description="\n".join(lines) or "—",
                          color=EMBED_COLOR)
    embed.set_footer(text="League scoring · ² = multi-source blend · ⭐ = your roster")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="freeagent", description="Top free agents: projected points + salary")
@app_commands.describe(
    position="Filter to a position (QB/RB/WR/TE/PK/DT/DE/LB/CB/S)",
    rookies="Y = rookies only, n = exclude rookies, omit = both",
)
@app_commands.choices(rookies=[
    app_commands.Choice(name="Y", value="y"),
    app_commands.Choice(name="n", value="n"),
])
async def freeagent_cmd(interaction: discord.Interaction, position: str | None = None,
                        rookies: str | None = None):
    await interaction.response.defer(thinking=True)
    from src.freeagents import top_free_agents
    from src.sleeper_api import get_nfl_state

    value_map = await asyncio.to_thread(_cache.get)
    state = await asyncio.to_thread(get_nfl_state)
    season = int(state["season"])
    wk = int(state.get("week") or 0)
    week = wk if state.get("season_type") == "regular" and wk >= 1 else None

    rookies_filter = {"y": True, "n": False}.get(rookies)

    rows = await asyncio.to_thread(
        top_free_agents, position, rookies_filter, season, week, 15, value_map,
    )
    if not rows:
        await interaction.followup.send("No free agents matched.")
        return

    label = (position.upper() if position else "All positions")
    if rookies_filter is True:
        label += " — rookies only"
    elif rookies_filter is False:
        label += " — no rookies"

    lines = []
    for i, r in enumerate(rows, 1):
        wk_str = f" | Week {week}: {r['week_pts']:.1f} pts" if r["week_pts"] is not None else ""
        lines.append(
            f"`{i:>2}.` **{r['name']}** ({r['position']}, {r['team']}) — "
            f"Season: {r['season_pts']:.1f} pts{wk_str} | Salary: ${r['salary']:,.0f}"
        )
    embed = discord.Embed(title=f"Free Agents — {label}",
                          description="\n".join(lines), color=EMBED_COLOR)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="update", description="Pull the latest bot code and restart")
async def update_cmd(interaction: discord.Interaction):
    if DISCORD_OWNER_ID and interaction.user.id != DISCORD_OWNER_ID:
        await interaction.response.send_message(
            "Only the bot owner can run /update.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)

    import os
    import subprocess
    import sys

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def run(*cmd) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=repo, timeout=300)

    old = (await asyncio.to_thread(run, "git", "rev-parse", "HEAD")).stdout.strip()
    pull = await asyncio.to_thread(run, "git", "pull", "--ff-only")
    if pull.returncode != 0:
        await interaction.followup.send(
            f"❌ `git pull` failed:\n```{(pull.stderr or pull.stdout)[-1500:]}```")
        return
    new = (await asyncio.to_thread(run, "git", "rev-parse", "HEAD")).stdout.strip()

    if old == new:
        await interaction.followup.send("Already up to date — no restart needed.")
        return

    changes = (await asyncio.to_thread(
        run, "git", "log", "--oneline", f"{old}..{new}")).stdout.strip()
    pip = await asyncio.to_thread(
        run, sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt")
    if pip.returncode != 0:
        await interaction.followup.send(
            f"⚠️ Code pulled but `pip install` failed — NOT restarting:\n"
            f"```{(pip.stderr or pip.stdout)[-1500:]}```")
        return

    await interaction.followup.send(
        f"✅ Updated ({len(changes.splitlines())} commit(s)) — restarting now:\n"
        f"```{changes[-1500:]}```"
    )
    log.info("Restarting via /update:\n%s", changes)
    # Replace this process with a fresh one; systemd sees the same service,
    # a foreground run just re-execs in place. `-m src.bot` needs the repo cwd.
    os.chdir(repo)
    os.execv(sys.executable, [sys.executable, "-m", "src.bot"])


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


@tasks.loop(hours=6)
async def projections_refresh():
    from src.projections import refresh_projections
    from src.sleeper_api import get_nfl_state

    try:
        state = await asyncio.to_thread(get_nfl_state)
        season = int(state["season"])
        n = await asyncio.to_thread(refresh_projections, season, None)
        log.info("Season projections refreshed: %d players", n)
        if state.get("season_type") == "regular" and int(state.get("week") or 0) >= 1:
            wk = int(state["week"])
            n = await asyncio.to_thread(refresh_projections, season, wk)
            log.info("Week %d projections refreshed: %d players", wk, n)
    except Exception:
        log.exception("projections_refresh failed")


@projections_refresh.before_loop
async def _wait_ready_proj():
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
