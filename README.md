# Fantasy Trade Bot

Dynasty league trade & waiver assistant for MFL salary-cap leagues.  
Scores trades, surfaces waiver gems, and pushes a weekly Discord report.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/LXC
pip install -r requirements.txt
cp .env.example .env
# Edit .env — add DISCORD_WEBHOOK_URL at minimum
```

## First run

```bash
# Initialize DB, cache FantasyCalc values, build MFL<->FC crosswalk
python -m src.cli init

# Dump player_values.csv for eyeball verification
python -m src.cli values
```

## CLI commands

| Command | Description |
|---|---|
| `init` | Initialize DB + build crosswalk (run once, re-run to refresh) |
| `values` | Dump `player_values.csv` — dynasty value, salary, VPD, VOR |
| `scan` | Score all new league trades (idempotent, deduped by SHA1 txn ID) |
| `score --side1 IDs --side2 IDs` | Score a specific trade by MFL player IDs |
| `waivers` | Print top waiver gems + suggested drops |
| `report` | Build report, print it, push to Discord, update Homarr tile |
| `tile` | Write `homarr_status.json` (or `--serve` to start Flask on port 5055) |

```bash
# Score a trade (comma-separated MFL IDs; picks use FP_/DP_ tokens from MFL)
python -m src.cli score --side1 13163,FP_0003_2026_3 --side2 12626

# Include positional-fit check (flags if you ship from a thin position)
python -m src.cli score --side1 13163 --side2 12626 --owner1 0002 --owner2 0005
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `MFL_LEAGUE_ID` | `68447` | MFL league ID |
| `MFL_HOST` | `www46` | MFL host subdomain |
| `MFL_FRANCHISE_ID` | `0002` | Your franchise ID |
| `DISCORD_WEBHOOK_URL` | _(empty)_ | Discord webhook — report silently skips push if unset |
| `LOPSIDED_THRESHOLD` | `0.15` | Value gap % that triggers a FLEECE flag |
| `HOMARR_PORT` | `5055` | Port for the Flask tile server |

## Cron (inside the LXC)

```cron
# Sunday 10 PM — post weekly report
0 22 * * 0 cd /opt/fantasy-trade-bot && .venv/bin/python -m src.cli report >> /var/log/fbot.log 2>&1

# Tuesday 8 PM — waiver-eve report
0 20 * * 2 cd /opt/fantasy-trade-bot && .venv/bin/python -m src.cli report >> /var/log/fbot.log 2>&1

# Daily midnight — refresh FantasyCalc values + crosswalk
0 0 * * * cd /opt/fantasy-trade-bot && .venv/bin/python -m src.cli init >> /var/log/fbot.log 2>&1
```

## Homarr tile

Option A — JSON file widget: point at `homarr_status.json` (written by every `report` run, or manually via `python -m src.cli tile`).

Option B — iFrame: run the Flask server as a service, point Homarr at `http://<lxc-ip>:5055/status`.

```bash
# Start Flask (keep alive with systemd or screen)
python -m src.homarr_tile
# or
python -m src.cli tile --serve
```

Status JSON shape:
```json
{
  "roster_rank": 5,
  "gems_available": 10,
  "flagged_trades": 16,
  "last_run": "2026-07-07T03:05:57+00:00"
}
```

## Data sources

- **MFL API** — rosters, salaries, free agents, transactions.  
  Base URL: `https://{host}.myfantasyleague.com/2025/export`
- **FantasyCalc API** — dynasty player values (1-QB, non-superflex).  
  Endpoint: `https://api.fantasycalc.com/values/current?isDynasty=true&numQbs=1`  
  Free, no auth required. Cached daily.

## Crosswalk notes

- Fuzzy-matches MFL player IDs to FantasyCalc names by name + position + team.
- Team abbreviations are normalized (MFL `GBP`/`SFO`/`LVR`/etc → FC `GB`/`SF`/`LV`).
- Two-tier threshold: looser name match when team confirms, near-exact otherwise.
- Run `python -c "from src.crosswalk import unmatched_valuable; [print(r) for r in unmatched_valuable()]"` to see high-value gaps for manual fix.
- Manual overrides: set `manual_override=1` in the `crosswalk` table row.

## Draft picks

Pick tokens from MFL transactions are valued automatically:
- `FP_<franchise>_<year>_<round>` → generic round value (`2027 1st`, `2026 3rd`, …)
- `DP_<round0>_<pick0>` → slotted rookie pick (`2026 Pick 1.01`, etc.), 0-indexed
