import asyncio
from datetime import datetime
from database.db import get_club_connection, get_connection
from handlers.sales import show_pass_form
from logging_config import logger
from config import ADMIN_CHAT_ID


PAYMENT_PROCESSED = "processed"
PAYMENT_REVIEW_REQUIRED = "review_required"


async def payments_watcher(bot):
    logger.info("[payments_watcher] started")

    while True:
        await asyncio.sleep(5)

        try:
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
        except Exception:
            logger.exception("[payments_watcher] cannot read payments")
            continue

        for payment_id, user_id, slot_id in payments:
            try:
                result = await handle_race_payment(
                    bot=bot,
                    payment_id=payment_id,
                    user_id=user_id,
                    slot_id=slot_id,
                )
            except Exception as e:
                logger.exception(
                    f"[payments_watcher] error payment_id={payment_id}: {e}"
                )
                continue

            ui_status = (
                "paid"
                if result == PAYMENT_PROCESSED
                else PAYMENT_REVIEW_REQUIRED
            )
            try:
                with get_club_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE payments
                        SET ui_status = ?
                        WHERE id = ?
                          AND ui_status = 'shown'
                    """, (ui_status, payment_id))
                    conn.commit()
            except Exception:
                logger.exception(
                    f"[payments_watcher] cannot update payment_id={payment_id}"
                )


async def handle_race_payment(
    *,
    bot,
    payment_id: int,
    user_id: int,
    slot_id: int,
):
    review_reason = None
    already_processed = False
    is_test_payment = False

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute("""
            SELECT
                rs.status,
                rs.race_id,
                rs.user_id,
                re.status,
                CASE WHEN rte.slot_id IS NULL THEN 0 ELSE 1 END
            FROM race_slots rs
            LEFT JOIN race_entries re
              ON re.race_id = rs.race_id
             AND re.telegram_id = ?
             AND re.slot_id = rs.id
            LEFT JOIN race_test_entries rte
              ON rte.race_id = rs.race_id
             AND rte.telegram_id = ?
             AND rte.slot_id = rs.id
            WHERE rs.id = ?
        """, (user_id, user_id, slot_id))
        row = cursor.fetchone()

        if not row:
            review_reason = "слот не найден"
        else:
            (
                slot_status,
                race_id,
                slot_user_id,
                entry_status,
                is_test_payment,
            ) = row
            is_test_payment = bool(is_test_payment)
            already_processed = (
                slot_status == "paid"
                and slot_user_id == user_id
                and entry_status in ("paid", "form_confirmed")
            )

            if already_processed:
                conn.commit()
            elif (
                slot_status != "reserved"
                or slot_user_id != user_id
                or entry_status != "reserved"
            ):
                review_reason = (
                    f"slot_status={slot_status}, "
                    f"slot_user_id={slot_user_id}, "
                    f"entry_status={entry_status}"
                )
            else:
                cursor.execute("""
                    UPDATE race_slots
                    SET status = 'paid',
                        reserved_until = NULL
                    WHERE id = ?
                      AND status = 'reserved'
                      AND user_id = ?
                """, (slot_id, user_id))
                slot_updated = cursor.rowcount

                cursor.execute("""
                    UPDATE race_entries
                    SET status = 'paid',
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
                entry_updated = cursor.rowcount

                if slot_updated != 1 or entry_updated != 1:
                    conn.rollback()
                    review_reason = (
                        "не удалось атомарно подтвердить слот и участие"
                    )
                else:
                    conn.commit()

        if review_reason:
            conn.rollback()

    if review_reason:
        logger.error(
            f"[payments_watcher] payment {payment_id} requires review: "
            f"{review_reason}"
        )
        await bot.send_message(
            ADMIN_CHAT_ID,
            (
                "⚠️ <b>Оплата требует ручной проверки</b>\n\n"
                f"🧾 Payment ID: <code>{payment_id}</code>\n"
                f"👤 User ID: <code>{user_id}</code>\n"
                f"🎟 Slot ID: <code>{slot_id}</code>\n"
                f"Причина: <code>{review_reason}</code>\n\n"
                "Слот автоматически не подтвержден."
            ),
            parse_mode="HTML"
        )
        return PAYMENT_REVIEW_REQUIRED

    if already_processed:
        logger.info(
            f"[payments_watcher] payment {payment_id} was already processed"
        )
        return PAYMENT_PROCESSED

    # 4️⃣ показываем форму
    await show_pass_form(bot, user_id, slot_id)

    try:
        chat_member = await bot.get_chat_member(user_id, user_id)
        user_display = (
            f"@{chat_member.user.username}"
            if chat_member.user.username
            else chat_member.user.full_name
        )
    except Exception:
        user_display = f"id {user_id}"

    payment_title = (
        "🧪💳 <b>Тестовая оплата подтверждена</b>"
        if is_test_payment
        else "💳 <b>Оплата гонки подтверждена</b>"
    )
    await bot.send_message(
        ADMIN_CHAT_ID,
        (
            f"{payment_title}\n\n"
            f"👤 {user_display}\n"
            f"🆔 User ID: <code>{user_id}</code>\n"
            f"🎟 Slot ID: <code>{slot_id}</code>\n"
            f"🧾 Payment ID: <code>{payment_id}</code>\n"
            "📄 Пользователю отправлена форма"
        ),
        parse_mode="HTML"
    )

    return PAYMENT_PROCESSED
