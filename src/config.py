import os
from dotenv import load_dotenv

load_dotenv()

MFL_LEAGUE_ID = os.getenv("MFL_LEAGUE_ID", "68447")
MFL_HOST = os.getenv("MFL_HOST", "www46")
MFL_FRANCHISE_ID = os.getenv("MFL_FRANCHISE_ID", "0002")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
LOPSIDED_THRESHOLD = float(os.getenv("LOPSIDED_THRESHOLD", "0.15"))

MFL_BASE_URL = f"https://{MFL_HOST}.myfantasyleague.com/2025/export"
FANTASYCALC_BASE_URL = "https://api.fantasycalc.com/values/current"

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "trade_bot.db")
