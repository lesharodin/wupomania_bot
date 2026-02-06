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
    user_id = callback.from_user.id
    reserve_until = datetime.now() + timedelta(seconds=RESERVE_TIMEOUT_SECONDS)

    with get_connection() as conn:
        cursor = conn.cursor()

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

        cursor.execute("""
            SELECT status FROM users WHERE telegram_id = ?
        """, (user_id,))
        if cursor.fetchone()[0] != "registered":
            await callback.answer("Недоступно", show_alert=True)
            return

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
                "Ты добавлен в лист ожидания.",
                parse_mode="HTML"
            )
            await callback.bot.send_message(
                ADMIN_CHAT_ID,
                f"📥 В waitlist: <code>{user_id}</code>",
                parse_mode="HTML"
            )
            return

        slot_id = slot[0]

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

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="💳 Перейти к оплате",
                callback_data=f"pay_race:{slot_id}"
            )]
        ]
    )

    msg = await callback.message.answer(
        "🎟 <b>Билет зарезервирован</b>\n\n"
        "⏱ У тебя есть <b>10 минут</b> на оплату.",
        reply_markup=kb,
        parse_mode="HTML"
    )

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
# REAL PAYMENT (YooKassa)
# =========================
@router.callback_query(F.data.startswith("pay_race:"))
async def pay_race(callback: CallbackQuery):
    slot_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    user = callback.from_user
    username = f"@{user.username}" if user.username else f"id{user.id}"

    payment_url = create_payment(
        user_id=user_id,
        amount=1,
        target_type="race_slot",
        target_id=slot_id,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        description=f"{username} Участие в гонке «Вупомания | tgid {user_id} | slot {slot_id}»"
    )

    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="💳 Оплатить через СБП",
                    url=payment_url
                )]
            ]
        )
    )

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
        "Заполни форму для прохода на территорию:",
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

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET status='form_confirmed', form_confirmed=1
            WHERE telegram_id=?
        """, (user_id,))
        conn.commit()

    await callback.message.answer(
        "🎉 <b>Ты успешно записан на гонку!</b>\n\n"
        "Следи за новостями в канале @whoopmania",
        parse_mode="HTML"
    )

    await callback.bot.send_message(
        ADMIN_CHAT_ID,
        f"📄 Форма подтверждена\n🆔 Slot {slot_id}",
        parse_mode="HTML"
    )

    await callback.answer()
