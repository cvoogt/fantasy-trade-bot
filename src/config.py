import os
from pathlib import Path

from dotenv import load_dotenv

# Load the repo-root .env explicitly rather than searching from the current
# working directory — the running bot must always read the .env next to its
# code (e.g. /opt/fantasy-trade-bot/.env), never a stray copy elsewhere.
# override=True so an edited .env wins over any stale value already present in
# the process environment; without it, a shadowing env var would silently pin
# the old value even after a restart.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=True)

MFL_LEAGUE_ID = os.getenv("MFL_LEAGUE_ID", "68447")
MFL_HOST = os.getenv("MFL_HOST", "www46")
MFL_FRANCHISE_ID = os.getenv("MFL_FRANCHISE_ID", "0002")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
DISCORD_ALERT_CHANNEL_ID = int(os.getenv("DISCORD_ALERT_CHANNEL_ID", "0") or 0)
# If set, /update only works for this Discord user id.
DISCORD_OWNER_ID = int(os.getenv("DISCORD_OWNER_ID", "0") or 0)
LOPSIDED_THRESHOLD = float(os.getenv("LOPSIDED_THRESHOLD", "0.15"))
# Minimum dynasty value for a new free agent to trigger a drop alert.
WAIVER_ALERT_VALUE = float(os.getenv("WAIVER_ALERT_VALUE", "1000"))

# League year: empty = auto-detect (current calendar year, falling back to the
# previous one before MFL's spring rollover). Set explicitly to pin a year.
MFL_YEAR = os.getenv("MFL_YEAR", "")
FANTASYCALC_BASE_URL = "https://api.fantasycalc.com/values/current"

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "trade_bot.db")
