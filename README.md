# Fantasy Trade Bot

Your fantasy right-hand man for MFL salary-cap dynasty leagues — an interactive
Discord bot that scores trades, finds waiver gems, optimizes your lineup,
assists your rookie draft in real time, and pings you the moment one of your
starters scores a TD, picks off a pass, or recovers a fumble.

## Discord commands

| Command | What it does |
|---|---|
| `/trade give: get:` | Score a trade. Accepts player names, MFL ids, or picks (`2026 1st`, `2026 pick 1.01`). Verdict from your perspective. |
| `/waivers` | Top 5 waiver gems by value + suggested drop for each. |
| `/lineup [week]` | Optimal starting lineup from weekly projections (IDP-aware), plus start/sit changes vs your submitted lineup. |
| `/player name:` | Dynasty value, salary, value-per-dollar, VOR for any player (fuzzy name ok). |
| `/roster` | Roster health: value by position vs league median + league rank. |
| `/scan` | Scan the league for new trades and score them. |
| `/draft` | Rookie draft board: who's on the clock, best available, your remaining picks. |

## Automatic (no command needed)

- **Live scoring alerts** — during game windows the bot polls every 60s and posts
  when one of your starters logs a passing/rushing/receiving/ST TD, an
  interception (`idp_int`), a fumble recovery (`idp_fum_rec`), or a defensive TD.
  Idempotent across restarts (SQLite snapshots); first poll of a week is a
  silent baseline.
- **On-the-clock draft ping** — `@here` with the top-5 best available when it's
  your pick in the rookie draft. Never re-pings the same pick.
- **Lopsided trade watch** — hourly league scan; trades with a value gap ≥ 15%
  get posted with the fleece verdict.
- **Weekly reports** — Sunday 10pm and Tuesday 8pm (server time): roster health,
  waiver gems, lopsided trades. Also refreshes the Homarr tile.

## Setup

### 1. Discord application (one-time)

1. Create an application at <https://discord.com/developers/applications> → Bot → copy the **token**.
2. Invite it: OAuth2 → URL Generator → scopes `bot` + `applications.commands`
   → permissions `Send Messages`, `Embed Links` → open the URL, pick your server.
3. Right-click your server icon → *Copy Server ID* (`DISCORD_GUILD_ID`);
   right-click the alert channel → *Copy Channel ID* (`DISCORD_ALERT_CHANNEL_ID`).
   (Enable Developer Mode in Discord settings if you don't see those.)

### 2. Install

```bash
git clone https://github.com/cvoogt/fantasy-trade-bot.git /opt/fantasy-trade-bot
cd /opt/fantasy-trade-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, DISCORD_ALERT_CHANNEL_ID
.venv/bin/python -m src.cli init          # build FantasyCalc crosswalk + value cache
.venv/bin/python -c "from src.sleeper_xwalk import build_sleeper_crosswalk as b; print(b())"
```

### 3. Run

```bash
.venv/bin/python -m src.bot               # foreground
# or as a service:
cp deploy/fantasy-bot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now fantasy-bot
```

### Optional: keep the daily value refresh cron

```cron
0 0 * * * cd /opt/fantasy-trade-bot && .venv/bin/python -m src.cli init >> /var/log/fbot.log 2>&1
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `MFL_LEAGUE_ID` | `68447` | MFL league ID |
| `MFL_HOST` | `www46` | MFL host subdomain |
| `MFL_FRANCHISE_ID` | `0002` | Your franchise ID |
| `DISCORD_BOT_TOKEN` | — | Bot token (required for the bot) |
| `DISCORD_GUILD_ID` | — | Server ID (instant slash-command sync) |
| `DISCORD_ALERT_CHANNEL_ID` | — | Channel for alerts, pings, weekly reports |
| `DISCORD_WEBHOOK_URL` | — | Legacy webhook path (`python -m src.cli report`) |
| `LOPSIDED_THRESHOLD` | `0.15` | Value gap that triggers a FLEECE flag |
| `HOMARR_PORT` | `5055` | Port for the Flask status tile |

## CLI (still available without the bot)

`python -m src.cli {init | values | scan | score | waivers | report | tile}` —
see `--help`. The `report` command is the old cron/webhook path; the bot
supersedes it but both work.

## Data sources (all free, no keys)

- **MFL API** — rosters, salaries, transactions, live scoring, draft results.
  `https://{host}.myfantasyleague.com/2025/export`
- **FantasyCalc** — dynasty values (1-QB): `api.fantasycalc.com/values/current?isDynasty=true&numQbs=1`. Cached daily.
- **Sleeper** — weekly projections + near-real-time stats: `api.sleeper.app/v1`.
  Players dump cached daily in SQLite.

## Player ID crosswalks

- **MFL ↔ FantasyCalc**: fuzzy name+position+team (normalized team abbreviations,
  two-tier threshold). Check gaps: `python -c "from src.crosswalk import unmatched_valuable; [print(r) for r in unmatched_valuable()]"`
- **MFL ↔ Sleeper**: exact join on shared IDs (sportradar > espn > rotowire > stats)
  — 100% coverage of rostered players in practice. Manual fixes: set
  `manual_override=1` on the row.

## League notes

League 68447 is a 10-team IDP salary-cap dynasty league: 19 starters
(QB 1, RB 1-2, WR 2-4, TE 1-2, PK 1 / DT+DE 3-4, LB 3-4, CB+S 3-5).
The lineup solver reads these rules from the MFL `league` endpoint, so rule
changes are picked up automatically. Draft pick tokens in trades
(`FP_0003_2026_3`, `DP_0_0`) are valued via FantasyCalc's pick values.

Caveat: Sleeper's default PPR projections barely weight IDP stats, so defensive
start/sit ordering is a coarse signal; offensive advice is solid.
