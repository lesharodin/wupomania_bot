from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import RACE_CHANNEL_ID
from database.db import get_connection

router = Router()


@router.message(F.text == "/start")
async def start(message: Message):
    user_id = message.from_user.id

    # 1️⃣ проверка подписки на канал
    try:
        member = await message.bot.get_chat_member(RACE_CHANNEL_ID, user_id)
        if member.status in ("left", "kicked"):
            raise Exception()
    except:
        await message.answer(
            "❌ Для участия в гонке нужно   быть подписанным на канал @whoopmania.\n\n"
            "После подписки нажми /start ещё раз."
        )
        return

    # 2️⃣ проверка пользователя
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM users WHERE telegram_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()

    # пользователь не зарегистрирован
    if not row:
        await message.answer(
            "👋 Добро пожаловать на гонку «Вупомания»!\n\n"
            "Перед покупкой билета нужно пройти регистрацию.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📝 Регистрация",
                        callback_data="start_reg"
                    )]
                ]
            )
        )
        return

    status = row[0]

    # 3️⃣ роутинг по статусу
    if status == "registered":
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id
                FROM races
                WHERE status = 'sales_open'
                ORDER BY created_at DESC
                LIMIT 1
            """)
            race = cursor.fetchone()

        if race:
            await message.answer(
                "🚀 <b>Продажи билетов на гонку уже открыты!</b>\n\n"
                "🎟 Количество мест ограничено.\n"
                "👇 Нажми кнопку ниже, чтобы записаться:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(
                            text="🎟 Записаться на гонку",
                            callback_data="buy_ticket"
                        )]
                    ]
                ),
                parse_mode="HTML"
            )
        else:
            await message.answer("⏳ Продажи билетов ещё не начались.")

    elif status == "reserved":
        await message.answer(
            "⏳ У тебя есть активный резерв.\n"
            "Заверши оплату в течение 10 минут."
        )

    elif status == "paid":
        await message.answer("✅ Оплата получена. Участие подтверждено.")

    elif status == "form_confirmed":
        await message.answer(
            "🏁 Ты полностью зарегистрирован на гонку.\n"
            "Мы ждём тебя!"
        )

    elif status == "waitlist":
        await message.answer("📥 Ты находишься в листе ожидания.")

    else:
        await message.answer("ℹ️ Статус обновляется.")
