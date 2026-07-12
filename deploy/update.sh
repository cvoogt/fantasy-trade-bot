#!/usr/bin/env bash
# One-command update for the LXC: pull latest, sync deps, restart the bot.
set -euo pipefail
cd /opt/fantasy-trade-bot

echo "== pulling latest =="
git pull --ff-only

echo "== syncing dependencies =="
.venv/bin/pip install -r requirements.txt --quiet

echo "== restarting bot =="
systemctl restart fantasy-bot
systemctl --no-pager --lines=5 status fantasy-bot
