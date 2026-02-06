import asyncio
from database.db import get_club_connection, get_connection
from handlers.sales import show_pass_form
from logging_config import logger


async def payments_watcher(bot):
    logger.info("[payments_watcher] started")

    while True:
        await asyncio.sleep(5)

        with get_club_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    id,
                    user_id,
                    target_id
                FROM payments
                WHERE status = 'succeeded'
                  AND ui_status = 'shown'
                  AND target_type = 'race_slot'
                ORDER BY id
                LIMIT 10
            """)
            payments = cursor.fetchall()

        for payment_id, user_id, slot_id in payments:
            try:
                await handle_race_payment(
                    bot=bot,
                    payment_id=payment_id,
                    user_id=user_id,
                    slot_id=slot_id
                )
            except Exception as e:
                logger.exception(
                    f"[payments_watcher] error payment_id={payment_id}: {e}"
                )
            else:
                # 🔒 помечаем как обработанный
                with get_club_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE payments
                        SET ui_status = 'paid'
                        WHERE id = ?
                    """, (payment_id,))
                    conn.commit()


async def handle_race_payment(
    *,
    bot,
    payment_id: int,
    user_id: int,
    slot_id: int
):
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT status
            FROM race_slots
            WHERE id = ?
        """, (slot_id,))
        row = cursor.fetchone()

        if not row:
            raise RuntimeError(f"slot {slot_id} not found")

        if row[0] != "reserved":
            logger.warning(
                f"[payments_watcher] skip payment {payment_id}, "
                f"slot {slot_id} status={row[0]}"
            )
            return


        # подтверждаем слот
        cursor.execute("""
            UPDATE race_slots
            SET status = 'paid'
            WHERE id = ?
        """, (slot_id,))

        cursor.execute("""
            UPDATE users
            SET status = 'paid'
            WHERE telegram_id = ?
        """, (user_id,))

        conn.commit()

    # 4️⃣ показываем форму
    await show_pass_form(bot, user_id, slot_id)

    # 5️⃣ лог админам
    try:
        chat_member = await bot.get_chat_member(user_id, user_id)
        user_display = (
            f"@{chat_member.user.username}"
            if chat_member.user.username
            else chat_member.user.full_name
        )
    except:
        user_display = f"id {user_id}"

    await bot.send_message(
        ADMIN_CHAT_ID,
        (
            "💳 <b>Оплата гонки подтверждена</b>\n\n"
            f"👤 {user_display}\n"
            f"🆔 Slot ID: <code>{slot_id}</code>\n"
            f"🧾 Payment ID: <code>{payment_id}</code>\n"
            "📄 Пользователю отправлена форма"
        ),
        parse_mode="HTML"
    )

