# Fantasy Trade Bot

Dynasty league trade & waiver assistant for MFL salary-cap leagues.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Linux
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your league details
```

## Usage

```bash
# Phase 1: Initialize crosswalk + cache dynasty values
python -m src.cli init

# Dump player values CSV for verification
python -m src.cli values
```

## Environment Variables

| Variable | Description |
|---|---|
| `MFL_LEAGUE_ID` | MFL league ID (default: 68447) |
| `MFL_HOST` | MFL host subdomain (default: www46) |
| `MFL_FRANCHISE_ID` | Your franchise ID (default: 0002) |
| `DISCORD_WEBHOOK_URL` | Discord webhook for reports |
| `LOPSIDED_THRESHOLD` | Trade lopsidedness threshold (default: 0.15) |

## Data Sources

- **MFL API**: Rosters, salaries, free agents, transactions. Base URL: `https://{host}.myfantasyleague.com/2025/export`
- **FantasyCalc API**: Dynasty player values. Endpoint: `https://api.fantasycalc.com/values/current?isDynasty=true&numQbs=1`. Free, no auth.

## Cron (Phase 5+)

```cron
# Sunday night report
0 22 * * 0 cd /path/to/fantasy-trade-bot && .venv/bin/python -m src.cli report
# Tuesday waiver eve
0 20 * * 2 cd /path/to/fantasy-trade-bot && .venv/bin/python -m src.cli report
```
