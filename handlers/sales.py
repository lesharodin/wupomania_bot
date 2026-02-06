from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
from urllib.parse import urlencode
from payments.service import create_payment
from database.db import get_connection
from config import ADMIN_CHAT_ID, RESERVE_TIMEOUT_SECONDS

router = Router()

PASS_FORM_URL = "https://forms.yandex.ru/u/6984f9c3068ff03215f42371/"


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

        # 2️⃣ статус пользователя
        cursor.execute("""
            SELECT status FROM users WHERE telegram_id = ?
        """, (user_id,))
        if cursor.fetchone()[0] != "registered":
            await callback.answer("Недоступно", show_alert=True)
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
            cursor.execute("""
                UPDATE users SET status = 'waitlist'
                WHERE telegram_id = ?
            """, (user_id,))
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

        # 4️⃣ резервируем слот
        cursor.execute("""
            UPDATE race_slots
            SET status='reserved', user_id=?, reserved_until=?
            WHERE id=?
        """, (user_id, reserve_until.isoformat(), slot_id))

        cursor.execute("""
            UPDATE users SET status='reserved'
            WHERE telegram_id=?
        """, (user_id,))
        conn.commit()

    # ===== СОЗДАЁМ ПЛАТЁЖ СРАЗУ =====

    username = f"@{user.username}" if user.username else f"id{user.id}"

    payment_url = create_payment(
        user_id=user_id,
        amount=1,  # ← потом вынесешь в конфиг
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
async def show_pass_form(bot, user_id: int, slot_id: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT fio FROM users WHERE telegram_id=?
        """, (user_id,))
        fio = cursor.fetchone()[0]

    form_url = build_prefilled_form_url(PASS_FORM_URL, fio)

    await bot.send_message(
        user_id,
        "✅ <b>Оплата получена</b>\n\n"
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
            SELECT r.date
            FROM race_slots rs
            JOIN races r ON r.id = rs.race_id
            WHERE rs.id = ?
        """, (slot_id,))
        race_date = cursor.fetchone()[0]

        cursor.execute("""
            UPDATE users
            SET status='form_confirmed', form_confirmed=1
            WHERE telegram_id=?
        """, (user_id,))
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
            SELECT r.date
            FROM race_slots rs
            JOIN races r ON r.id = rs.race_id
            WHERE rs.id = ? AND rs.user_id = ?
        """, (slot_id, user.id))
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
