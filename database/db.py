import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.path.join(
    os.path.dirname(__file__),
    "race.db"
)

@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()
def get_club_connection():
    return sqlite3.connect(
        "/home/lesharodin/whoopclub_bot/database/bot.db"
    )