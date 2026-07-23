# Регламент
RULES_URL = "https://docs.google.com/document/d/1bYUzP41EjBW1N8yuA4X_fpXMh9U6VpDALmYsnkFRZZY"

# Тайминги
RESERVE_TIMEOUT_SECONDS = 600


from dotenv import load_dotenv
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"ENV variable {name} is not set")
    return value


def build_proxy_url() -> Optional[str]:
    explicit_url = os.getenv("TELEGRAM_PROXY_URL")
    if explicit_url:
        return explicit_url

    host = os.getenv("TELEGRAM_PROXY_HOST")
    if not host:
        return None

    port = os.getenv("TELEGRAM_PROXY_PORT")
    if not port:
        raise RuntimeError("ENV variable TELEGRAM_PROXY_PORT is not set")

    proxy_type = os.getenv("TELEGRAM_PROXY_TYPE", "socks5")
    username = os.getenv("TELEGRAM_PROXY_USERNAME")
    password = os.getenv("TELEGRAM_PROXY_PASSWORD")
    auth = ""
    if username and password:
        auth = f"{quote(username)}:{quote(password)}@"

    return f"{proxy_type}://{auth}{host}:{port}"

BOT_TOKEN = require_env("BOT_TOKEN")
RACE_CHANNEL_ID = int(require_env("RACE_CHANNEL_ID"))
ADMIN_CHAT_ID = int(require_env("ADMIN_CHAT_ID"))

ADMINS = [
    int(x) for x in require_env("ADMIN_IDS").split(",")
]

ENV = os.getenv("ENV", "DEV")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
TELEGRAM_PROXY_URL = build_proxy_url()
PARTICIPATION_PRICE_RUB = int(os.getenv("PARTICIPATION_PRICE_RUB", "2000"))
PAYMENT_DB_PATH = os.getenv(
    "PAYMENT_DB_PATH",
    "/home/lesharodin/whoopclub_bot/database/bot.db",
)
