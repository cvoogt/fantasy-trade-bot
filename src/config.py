import os
from dotenv import load_dotenv

load_dotenv()

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
