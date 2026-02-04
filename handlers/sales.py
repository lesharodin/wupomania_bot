from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta

from database.db import get_connection
from config import ADMIN_CHAT_ID, RESERVE_TIMEOUT_SECONDS

router = Router()

PASS_FORM_URL = "https://example.com/pass-form"  # <-- сюда реальную Google Form


# =========================
# ЗАПИСЬ НА ГОНКУ (РЕЗЕРВ)
# =========================
@router.callback_query(F.data == "buy_ticket")
async def buy_ticket(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = datetime.now()
    reserve_until = now + timedelta(seconds=RESERVE_TIMEOUT_SECONDS)

    with get_connection() as conn:
        cursor = conn.cursor()

        # 1️⃣ активная гонка
        cursor.execute("""
            SELECT id
            FROM races
            WHERE status = 'sales_open'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        race = cursor.fetchone()
        if not race:
            await callback.answer("Продажи не открыты", show_alert=True)
            return
        race_id = race[0]

        # 2️⃣ проверка пользователя
        cursor.execute("""
            SELECT status
            FROM users
            WHERE telegram_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if not row or row[0] != "registered":
            await callback.answer("Недоступно", show_alert=True)
            return

        # 3️⃣ свободный слот
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
            await callback.answer("Все места заняты", show_alert=True)
            return

        slot_id = slot[0]

        # 4️⃣ резерв
        cursor.execute("""
            UPDATE race_slots
            SET status = 'reserved',
                user_id = ?,
                reserved_until = ?
            WHERE id = ?
        """, (user_id, reserve_until.isoformat(), slot_id))

        cursor.execute("""
            UPDATE users
            SET status = 'reserved'
            WHERE telegram_id = ?
        """, (user_id,))

        conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💳 Оплатить (тест)",
            callback_data=f"fake_pay:{slot_id}"
        )]
    ])

    await callback.message.answer(
        "🎟 <b>Место зарезервировано</b>\n\n"
        "⏱ У тебя есть 10 минут.\n"
        "Нажми кнопку ниже, чтобы подтвердить оплату (тестовый режим).",
        reply_markup=kb,
        parse_mode="HTML"
    )

    user = callback.from_user
    user_display = f"@{user.username}" if user.username else user.full_name

    await callback.bot.send_message(
        ADMIN_CHAT_ID,
        (
            "🎟 <b>Резерв слота</b>\n"
            f"👤 {user_display}\n"
            f"🏁 Race ID: {race_id}\n"
            f"🆔 Slot ID: {slot_id}\n"
            f"⏱ До: {reserve_until.strftime('%H:%M:%S')}"
        ),
        parse_mode="HTML"
    )

    await callback.answer()


# =========================
# ФЕЙК-ОПЛАТА (ТЕСТ)
# =========================
@router.callback_query(F.data.startswith("fake_pay:"))
async def fake_payment(callback: CallbackQuery):
    slot_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    with get_connection() as conn:
        cursor = conn.cursor()

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

    await callback.message.answer(
        "✅ <b>Оплата прошла успешно</b>\n\n"
        "Теперь необходимо заполнить форму для пропуска на территорию РЭУ.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="📄 Заполнить форму",
                    url=PASS_FORM_URL
                )],
                [InlineKeyboardButton(
                    text="✅ Я заполнил",
                    callback_data=f"form_done:{slot_id}"
                )]
            ]
        )
    )

    user = callback.from_user
    user_display = f"@{user.username}" if user.username else user.full_name

    await callback.bot.send_message(
        ADMIN_CHAT_ID,
        (
            "💳 <b>Оплата подтверждена (тест)</b>\n"
            f"👤 {user_display}\n"
            f"🆔 Slot ID: {slot_id}"
        ),
        parse_mode="HTML"
    )

    await callback.answer()


# =========================
# ПОДТВЕРЖДЕНИЕ ФОРМЫ
# =========================
@router.callback_query(F.data.startswith("form_done:"))
async def form_done(callback: CallbackQuery):
    user_id = callback.from_user.id
    slot_id = callback.data.split(":")[1]

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET
                status = 'form_confirmed',
                form_confirmed = 1
            WHERE telegram_id = ?
        """, (user_id,))
        conn.commit()

    user = callback.from_user
    user_display = f"@{user.username}" if user.username else user.full_name

    await callback.message.answer(
        "🙏 Спасибо! Подтверждение получено.\n"
        "Мы ждём тебя на гонке 🏁"
    )

    await callback.bot.send_message(
        ADMIN_CHAT_ID,
        (
            "📄 <b>Форма подтверждена</b>\n"
            f"👤 {user_display}\n"
            f"🆔 Slot ID: {slot_id}"
        ),
        parse_mode="HTML"
    )

    await callback.answer()
