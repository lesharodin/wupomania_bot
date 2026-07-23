# services/waitlist.py

import asyncio
from datetime import datetime, timedelta
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database.db import get_connection
from config import (
    ADMIN_CHAT_ID,
    PARTICIPATION_PRICE_RUB,
    RESERVE_TIMEOUT_SECONDS,
)
from payments.service import create_payment



async def try_assign_from_waitlist(bot, race_id: int):
    """
    Если есть свободный слот — отдаёт его первому пользователю из waitlist
    """

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute("""
            SELECT status
            FROM races
            WHERE id = ?
        """, (race_id,))
        race = cursor.fetchone()
        if not race or race[0] != "sales_open":
            conn.rollback()
            return None

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

        # 2️⃣ первый пользователь в waitlist этой гонки
        cursor.execute("""
            SELECT re.telegram_id, u.fio
            FROM race_entries re
            JOIN users u ON u.telegram_id = re.telegram_id
            WHERE re.race_id = ?
              AND re.status = 'waitlist'
            ORDER BY re.created_at
            LIMIT 1
        """, (race_id,))
        row = cursor.fetchone()

        if not row:
            return None

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
              AND status = 'free'
        """, (user_id, reserve_until, slot_id))
        if cursor.rowcount != 1:
            conn.rollback()
            return None

        cursor.execute("""
            UPDATE race_entries
            SET status = 'reserved',
                slot_id = ?,
                updated_at = ?
            WHERE race_id = ?
              AND telegram_id = ?
              AND status = 'waitlist'
        """, (slot_id, datetime.now().isoformat(), race_id, user_id))
        if cursor.rowcount != 1:
            conn.rollback()
            return None

        conn.commit()

    # ===== дальше БЕЗ БД =====
    try:
        payment_url = await asyncio.to_thread(
            create_payment,
            user_id=user_id,
            amount=PARTICIPATION_PRICE_RUB,
            target_type="race_slot",
            target_id=slot_id,
            chat_id=None,
            message_id=None,
            description=(
                "Вупомания | "
                f"{fio} | "
                f"tgid {user_id} | "
                f"slot {slot_id}"
            )
        )
    except Exception:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE race_slots
                SET status = 'free',
                    user_id = NULL,
                    reserved_until = NULL
                WHERE id = ?
                  AND status = 'reserved'
                  AND user_id = ?
            """, (slot_id, user_id))
            cursor.execute("""
                UPDATE race_entries
                SET status = 'waitlist',
                    slot_id = NULL,
                    updated_at = ?
                WHERE race_id = ?
                  AND telegram_id = ?
                  AND slot_id = ?
                  AND status = 'reserved'
            """, (
                datetime.now().isoformat(),
                race_id,
                user_id,
                slot_id,
            ))
            conn.commit()

        await bot.send_message(
            ADMIN_CHAT_ID,
            (
                "⚠️ <b>Не удалось создать платеж для waitlist</b>\n"
                f"👤 {fio}\n"
                f"🆔 User ID: <code>{user_id}</code>\n"
                f"🎟 Slot ID: <code>{slot_id}</code>"
            ),
            parse_mode="HTML"
        )
        return None

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="💳 Оплатить через СБП",
                url=payment_url
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

    return user_id, fio, slot_id
