import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.path.join(
    os.path.dirname(__file__),
    "race.db"
)
PAYMENT_DB_PATH = os.getenv(
    "PAYMENT_DB_PATH",
    "/home/lesharodin/whoopclub_bot/database/bot.db",
)

@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        yield conn
    finally:
        conn.close()


def get_club_connection():
    return sqlite3.connect(PAYMENT_DB_PATH, timeout=10)
