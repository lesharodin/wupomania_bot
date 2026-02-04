from db import get_connection
from datetime import datetime

with get_connection() as conn:
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        fio TEXT,
        video_system TEXT,
        drone_size TEXT,
        pd_accepted INTEGER,
        rules_accepted INTEGER,
        status TEXT,
        created_at TEXT,
        form_confirmed INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS races (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        date TEXT,
        slots_total INTEGER,
        sales_start_at TEXT,
        status TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS race_slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        race_id INTEGER,
        status TEXT,
        user_id INTEGER,
        reserved_until TEXT
    );

    CREATE TABLE IF NOT EXISTS waitlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        race_id INTEGER,
        user_id INTEGER,
        created_at TEXT,
        active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        currency TEXT,
        status TEXT,
        target_type TEXT,
        target_id INTEGER,
        chat_id INTEGER,
        message_id INTEGER,
        ui_status TEXT,
        yookassa_payment_id TEXT,
        created_at TEXT,
        paid_at TEXT
    );
    """)

    conn.commit()

print("DB initialized")
