# services/waitlist.py

from datetime import datetime, timedelta
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database.db import get_connection
from config import ADMIN_CHAT_ID, RESERVE_TIMEOUT_SECONDS


async def try_assign_from_waitlist(bot, race_id: int):
    """
    Если есть свободный слот — отдаёт его первому пользователю из waitlist
    """

    with get_connection() as conn:
        cursor = conn.cursor()

        # 1️⃣ свободный слот
        cursor.execute("""
            SELECT id
            FROM race_slots
            WHERE race_id = ?
              AND status = 'free'
            ORDER BY id
            LIMIT 1
        """, (race_id,))
        slot = cursor.fetchone()

        if not slot:
            return

        slot_id = slot[0]

        # 2️⃣ первый пользователь в waitlist
        cursor.execute("""
            SELECT telegram_id, fio
            FROM users
            WHERE status = 'waitlist'
            ORDER BY created_at
            LIMIT 1
        """)
        row = cursor.fetchone()

        if not row:
            return

        user_id, fio = row
        reserve_until = (
            datetime.now() + timedelta(seconds=RESERVE_TIMEOUT_SECONDS)
        ).isoformat()

        # 3️⃣ резервируем слот
        cursor.execute("""
            UPDATE race_slots
            SET status = 'reserved',
                user_id = ?,
                reserved_until = ?
            WHERE id = ?
        """, (user_id, reserve_until, slot_id))

        cursor.execute("""
            UPDATE users
            SET status = 'reserved'
            WHERE telegram_id = ?
        """, (user_id,))

        conn.commit()

    # ===== дальше БЕЗ БД =====

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="💳 Перейти к оплате",
                callback_data=f"pay_race:{slot_id}"
            )]
        ]
    )

    await bot.send_message(
        user_id,
        "🎟 <b>Освободилось место!</b>\n\n"
        "Мы зарезервировали слот для тебя на <b>10 минут</b>.\n"
        "Успей оплатить 👇",
        parse_mode="HTML",
        reply_markup=kb
    )

    await bot.send_message(
        ADMIN_CHAT_ID,
        (
            "⏭️ <b>Слот отдан из waitlist</b>\n"
            f"👤 {fio}\n"
            f"🆔 User ID: <code>{user_id}</code>\n"
            f"🎟 Slot ID: <code>{slot_id}</code>"
        ),
        parse_mode="HTML"
    )
