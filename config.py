import os
# Регламент
RULES_URL = "https://docs.google.com/document/d/1bYUzP41EjBW1N8yuA4X_fpXMh9U6VpDALmYsnkFRZZY"

# Тайминги
RESERVE_TIMEOUT_SECONDS = 600


from dotenv import load_dotenv
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"ENV variable {name} is not set")
    return value

BOT_TOKEN = require_env("BOT_TOKEN")
print("ENV CHECK:", os.getenv("RACE_CHANNEL_ID"))

RACE_CHANNEL_ID = int(require_env("RACE_CHANNEL_ID"))
ADMIN_CHAT_ID = int(require_env("ADMIN_CHAT_ID"))

ADMINS = [
    int(x) for x in require_env("ADMIN_IDS").split(",")
]

ENV = os.getenv("ENV", "DEV")
YOOKASSA_SHOP_ID = os.getenv ("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")