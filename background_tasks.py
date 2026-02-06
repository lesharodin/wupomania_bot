import asyncio
from datetime import datetime

from database.db import get_connection
from config import ADMIN_CHAT_ID


CHECK_INTERVAL = 30  # секунд

async def expire_reserved_slots(bot):
    print("[background] expire_reserved_slots started")

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        now = datetime.now().isoformat()

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, user_id, chat_id, message_id
                FROM race_slots
                WHERE status = 'reserved'
                  AND reserved_until IS NOT NULL
                  AND reserved_until < ?
            """, (now,))
            expired = cursor.fetchall()

        if not expired:
            continue

        for slot_id, user_id, chat_id, message_id in expired:
            # 1️⃣ освобождаем слот
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE race_slots
                    SET status = 'free',
                        user_id = NULL,
                        reserved_until = NULL,
                        chat_id = NULL,
                        message_id = NULL
                    WHERE id = ?
                """, (slot_id,))

                cursor.execute("""
                    UPDATE users
                    SET status = 'registered'
                    WHERE telegram_id = ?
                """, (user_id,))
                conn.commit()

            # 2️⃣ удаляем сообщение с оплатой
            if chat_id and message_id:
                try:
                    await bot.delete_message(chat_id, message_id)
                except:
                    pass  # сообщение могло быть уже удалено

            # 3️⃣ уведомляем пользователя
            try:
                await bot.send_message(
                    user_id,
                    "⏱ <b>Время оплаты истекло</b>\n\n"
                    "Твоя бронь на билет была снята.\n"
                    "Если хочешь — можешь попробовать записаться снова.",
                    parse_mode="HTML"
                )
            except:
                pass

        # 4️⃣ уведомление админу
        try:
            await bot.send_message(
                ADMIN_CHAT_ID,
                (
                    "⏱ <b>Сняты просроченные резервы</b>\n"
                    f"Количество: {len(expired)}"
                ),
                parse_mode="HTML"
            )
        except:
            pass
