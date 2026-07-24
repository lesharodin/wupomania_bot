import asyncio

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
from urllib.parse import urlencode
from payments.service import create_payment
from database.db import get_connection
from handlers.waitlist import try_assign_from_waitlist
from config import (
    ADMIN_CHAT_ID,
    PARTICIPATION_PRICE_RUB,
    RESERVE_TIMEOUT_SECONDS,
)

router = Router()

PASS_FORM_URL = "https://forms.yandex.ru/u/6a22fb1eeb6146117d653108"

ENTRY_MESSAGES = {
    "reserved": "⏳ У тебя уже есть активный резерв на эту гонку.",
    "paid": "💳 Оплата уже получена. Заполни форму.",
    "form_confirmed": "🏁 Ты уже записан на эту гонку.",
    "waitlist": "📥 Ты уже в листе ожидания этой гонки.",
}


# =========================
# HELPERS
# =========================
def can_cancel(race_date_iso: str) -> bool:
    race_date = datetime.fromisoformat(race_date_iso)
    return race_date - datetime.now() >= timedelta(days=3)


def split_fio(fio: str):
    parts = fio.strip().split()
    return (
        parts[0] if len(parts) > 0 else "",
        parts[1] if len(parts) > 1 else "",
        parts[2] if len(parts) > 2 else "",
    )


def build_prefilled_form_url(base_url: str, fio: str) -> str:
    last_name, first_name, middle_name = split_fio(fio)

    params = {
        "answer_short_text_93740": last_name,
        "answer_short_text_93741": first_name,
        "answer_short_text_93742": middle_name,
    }

    return f"{base_url}?{urlencode(params)}"


# =========================
# BUY TICKET (RESERVE)
# =========================
@router.callback_query(F.data == "buy_ticket")
async def buy_ticket(callback: CallbackQuery):
    user = callback.from_user
    user_id = user.id
    reserve_until = datetime.now() + timedelta(seconds=RESERVE_TIMEOUT_SECONDS)

    with get_connection() as conn:
        cursor = conn.cursor()

        # 1️⃣ активная гонка
        cursor.execute("""
            SELECT id FROM races
            WHERE status = 'sales_open'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        race = cursor.fetchone()
        if not race:
            await callback.answer("Продажи не открыты", show_alert=True)
            return
        race_id = race[0]

        # 2️⃣ профиль пользователя
        cursor.execute("""
            SELECT fio FROM users WHERE telegram_id = ?
        """, (user_id,))
        user_row = cursor.fetchone()
        if not user_row:
            await callback.answer("Сначала нажми /start", show_alert=True)
            return
        fio = user_row[0]

        cursor.execute("""
            SELECT status
            FROM race_entries
            WHERE race_id = ? AND telegram_id = ?
        """, (race_id, user_id))
        entry = cursor.fetchone()
        if entry and entry[0] in ENTRY_MESSAGES:
            await callback.answer(ENTRY_MESSAGES[entry[0]], show_alert=True)
            return

        # 3️⃣ свободный слот
        cursor.execute("""
            SELECT id FROM race_slots
            WHERE race_id = ? AND status = 'free'
            ORDER BY id LIMIT 1
        """, (race_id,))
        slot = cursor.fetchone()

        # WAITLIST
        if not slot:
            now = datetime.now().isoformat()
            cursor.execute("""
                INSERT INTO race_entries (
                    race_id,
                    telegram_id,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, 'waitlist', ?, ?)
                ON CONFLICT(race_id, telegram_id)
                DO UPDATE SET
                    status = 'waitlist',
                    slot_id = NULL,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
            """, (race_id, user_id, now, now))
            conn.commit()

            await callback.message.answer(
                "📥 <b>Все места заняты</b>\n\n"
                "Вы добавлены в лист ожидания.",
                parse_mode="HTML"
            )
            await callback.bot.send_message(
                ADMIN_CHAT_ID,
                (
                    "📥 <b>Пользователь добавлен в waitlist</b>\n"
                    f"👤 @{user.username if user.username else user.full_name}\n"
                    f"🆔 User ID: <code>{user_id}</code>\n"
                    f"🏁 Race ID: <code>{race_id}</code>"
                ),
                parse_mode="HTML"
            )

            return

        slot_id = slot[0]
        now = datetime.now().isoformat()

        # 4️⃣ резервируем слот
        cursor.execute("""
            UPDATE race_slots
            SET status='reserved', user_id=?, reserved_until=?
            WHERE id=? AND status='free'
        """, (user_id, reserve_until.isoformat(), slot_id))
        if cursor.rowcount != 1:
            await callback.answer(
                "Место уже занято, попробуй еще раз",
                show_alert=True,
            )
            return

        cursor.execute("""
            INSERT INTO race_entries (
                race_id,
                telegram_id,
                slot_id,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 'reserved', ?, ?)
            ON CONFLICT(race_id, telegram_id)
            DO UPDATE SET
                slot_id = excluded.slot_id,
                status = 'reserved',
                updated_at = excluded.updated_at
        """, (race_id, user_id, slot_id, now, now))
        conn.commit()

    # ===== СОЗДАЁМ ПЛАТЁЖ СРАЗУ =====

    username = f"@{user.username}" if user.username else f"id{user.id}"

    try:
        payment_url = await asyncio.to_thread(
            create_payment,
            user_id=user_id,
            amount=PARTICIPATION_PRICE_RUB,
            target_type="race_slot",
            target_id=slot_id,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            description=(
                "Вупомания | "
                f"{username} | "
                f"tgid {user.id} | "
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
                    reserved_until = NULL,
                    chat_id = NULL,
                    message_id = NULL
                WHERE id = ?
                  AND status = 'reserved'
                  AND user_id = ?
            """, (slot_id, user_id))
            cursor.execute("""
                UPDATE race_entries
                SET status = 'expired',
                    updated_at = ?
                WHERE race_id = ?
                  AND telegram_id = ?
                  AND slot_id = ?
                  AND status = 'reserved'
            """, (datetime.now().isoformat(), race_id, user_id, slot_id))
            conn.commit()

        await callback.message.answer(
            "❌ Не удалось создать платеж. Место освобождено, попробуй снова."
        )
        await callback.answer()
        await try_assign_from_waitlist(callback.bot, race_id)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="💳 Оплатить через СБП",
                url=payment_url
            )]
        ]
    )

    msg = await callback.message.answer(
        "🎟 <b>Билет зарезервирован</b>\n\n"
        "⏱ У тебя есть <b>10 минут</b> на оплату.",
        reply_markup=kb,
        parse_mode="HTML"
    )

    # сохраняем message_id для watcher
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE race_slots
            SET chat_id=?, message_id=?
            WHERE id=?
        """, (callback.message.chat.id, msg.message_id, slot_id))
        conn.commit()

    await callback.answer()


# =========================
# SHOW FORM (CALLED BY WATCHER AFTER PAYMENT)
# =========================
async def show_pass_form(
    bot,
    user_id: int,
    slot_id: int,
    *,
    payment_received: bool = True,
):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT fio FROM users WHERE telegram_id=?
        """, (user_id,))
        fio = cursor.fetchone()[0]

    form_url = build_prefilled_form_url(PASS_FORM_URL, fio)
    title = (
        "✅ <b>Оплата получена</b>"
        if payment_received
        else "✅ <b>Участие подтверждено администратором</b>"
    )

    await bot.send_message(
        user_id,
        f"{title}\n\n"
        "Заполните форму для прохода на территорию:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📄 Заполнить форму", url=form_url)],
                [InlineKeyboardButton(
                    text="✅ Я заполнил",
                    callback_data=f"form_done:{slot_id}"
                )]
            ]
        )
    )


# =========================
# FORM CONFIRM
# =========================
@router.callback_query(F.data.startswith("form_done:"))
async def form_done(callback: CallbackQuery):
    user_id = callback.from_user.id
    slot_id = int(callback.data.split(":")[1])
    user = callback.from_user
    username = f"@{user.username}" if user.username else f"id{user.id}"

    with get_connection() as conn:
        cursor = conn.cursor()

        # получаем дату гонки
        cursor.execute("""
            SELECT r.date, rs.race_id
            FROM race_slots rs
            JOIN races r ON r.id = rs.race_id
            WHERE rs.id = ?
        """, (slot_id,))
        row = cursor.fetchone()
        if not row:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        _, race_id = row

        cursor.execute("""
            UPDATE race_entries
            SET status = 'form_confirmed',
                updated_at = ?
            WHERE race_id = ?
              AND telegram_id = ?
              AND slot_id = ?
              AND status = 'paid'
        """, (datetime.now().isoformat(), race_id, user_id, slot_id))
        if cursor.rowcount == 0:
            await callback.answer("Оплаченная запись не найдена", show_alert=True)
            return
        conn.commit()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="❌ Запросить отмену участия",
                callback_data=f"cancel_request:{slot_id}"
            )]
        ]
    )

    await callback.message.answer(
        "🎉 <b>Вы успешно записались на гонку!</b>\n\n"
        "🎉 <b>Следи за новостями в канале @whoopmania</b>\n\n"
        "❗ Отменить участие можно <b>не позднее чем за 3 суток</b> до гонки.\n"
        "Запрос на отмену подтверждается администратором.",
        parse_mode="HTML",
        reply_markup=kb
    )

    await callback.bot.send_message(
        ADMIN_CHAT_ID,
        f"📄 {username} Записался на гонку\n <b>✅Форма подтверждена</b>\n 🆔 Slot ID: {slot_id}",
        parse_mode="HTML"
    )

    await callback.answer()
@router.callback_query(F.data.startswith("cancel_request:"))
async def cancel_request(callback: CallbackQuery):
    slot_id = int(callback.data.split(":")[1])
    user = callback.from_user

    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT r.date, re.status
            FROM race_slots rs
            JOIN races r ON r.id = rs.race_id
            JOIN race_entries re ON re.slot_id = rs.id
            WHERE rs.id = ? AND rs.user_id = ?
              AND re.telegram_id = ?
              AND re.status IN ('paid', 'form_confirmed')
        """, (slot_id, user.id, user.id))
        row = cursor.fetchone()

    if not row:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    race_date = datetime.fromisoformat(row[0])

    if race_date - datetime.now() < timedelta(days=3):
        await callback.answer(
            "❌ Отмена возможна только не позднее чем за 3 суток до гонки",
            show_alert=True
        )
        return

    # сообщение пользователю
    await callback.message.answer(
        "📨 <b>Запрос на отмену отправлен</b>\n\n"
        "Администратор рассмотрит его в ближайшее время.",
        parse_mode="HTML"
    )

    # сообщение админам
    user_display = (
        f"@{user.username} (<code>{user.id}</code>)"
        if user.username
        else f"<a href='tg://user?id={user.id}'>{user.full_name}</a> (<code>{user.id}</code>)"
    )

    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить отмену",
                    callback_data=f"cancel_confirm_admin:{slot_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"cancel_abort_admin:{slot_id}"
                )
            ]
        ]
    )

    await callback.bot.send_message(
        ADMIN_CHAT_ID,
        (
            "❌ <b>Запрос отмены участия</b>\n\n"
            f"👤 {user_display}\n"
            f"🆔 Slot ID: {slot_id}"
        ),
        reply_markup=admin_kb,
        parse_mode="HTML"
    )

    await callback.answer()
