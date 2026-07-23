import asyncio
from datetime import datetime

from database.db import get_connection
from config import ADMIN_CHAT_ID
from handlers.waitlist import try_assign_from_waitlist


CHECK_INTERVAL = 30  # секунд

async def expire_reserved_slots(bot):
    print("[background] expire_reserved_slots started")

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        now = datetime.now().isoformat()

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    rs.id,
                    rs.user_id,
                    rs.chat_id,
                    rs.message_id,
                    rs.reserved_until,
                    u.fio,
                    rs.race_id
                FROM race_slots rs
                JOIN users u ON u.telegram_id = rs.user_id
                WHERE rs.status = 'reserved'
                  AND rs.reserved_until IS NOT NULL
                  AND rs.reserved_until < ?
            """, (now,))
            expired = cursor.fetchall()

        if not expired:
            continue

        admin_lines = []

        for slot_id, user_id, chat_id, message_id, reserved_until, fio, race_id in expired:
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
                    UPDATE race_entries
                    SET status = 'expired',
                        updated_at = ?
                    WHERE race_id = ?
                      AND telegram_id = ?
                      AND slot_id = ?
                """, (datetime.now().isoformat(), race_id, user_id, slot_id))
                conn.commit()

            # 2️⃣ удаляем сообщение оплаты
            if chat_id and message_id:
                try:
                    await bot.delete_message(chat_id, message_id)
                except:
                    pass

            # 3️⃣ уведомляем пользователя
            try:
                await bot.send_message(
                    user_id,
                    "⏱ <b>Время оплаты истекло</b>\n\n"
                    "Ваша бронь на билет была снята.\n"
                    "Если хотите — вы можете попробовать записаться снова.",
                    parse_mode="HTML"
                )
            except:
                pass

            try:
                await try_assign_from_waitlist(bot, race_id)
            except Exception as exc:
                print(
                    "[background] waitlist assignment failed "
                    f"race_id={race_id}: {exc}"
                )

            # 4️⃣ собираем лог админу
            admin_lines.append(
                f"👤 <b>{fio}</b>\n"
                f"🆔 TGID: <code>{user_id}</code>\n"
                f"🎟 Slot ID: <code>{slot_id}</code>\n"
                f"⏰ До: <code>{reserved_until}</code>\n"
                "────────────"
            )

        # 5️⃣ одно сообщение админу
        try:
            await bot.send_message(
                ADMIN_CHAT_ID,
                (
                    "⏱ <b>Сняты просроченные резервы</b>\n\n"
                    + "\n".join(admin_lines)
                ),
                parse_mode="HTML"
            )
        except:
            pass
