from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from config import RACE_CHANNEL_ID, RULES_URL
from database.db import get_connection
from handlers.registration import Registration

router = Router()


@router.message(F.text == "/start")
async def start(message: Message, state: FSMContext):
    user_id = message.from_user.id

    # 1️⃣ проверка подписки
    try:
        member = await message.bot.get_chat_member(RACE_CHANNEL_ID, user_id)
        if member.status in ("left", "kicked"):
            raise Exception()
    except:
        await message.answer(
            "❌ Для участия в гонке нужно быть подписанным на канал @whoopmania.\n\n"
            "После подписки нажми /start ещё раз."
        )
        return

    # 2️⃣ проверяем профиль пользователя
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT telegram_id FROM users WHERE telegram_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()

    # 3️⃣ если профиль уже есть — смотрим текущую гонку
    if row:
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

            entry = None
            if race:
                cursor.execute("""
                    SELECT status
                    FROM race_entries
                    WHERE race_id = ? AND telegram_id = ?
                """, (race[0], user_id))
                entry = cursor.fetchone()

        if not race:
            await message.answer("⏳ Ты зарегистрирован. Продажи ещё не начались.")
            return

        if entry:
            status = entry[0]
            if status == "reserved":
                await message.answer("⏳ У тебя есть активный резерв на эту гонку.")
                return
            if status == "paid":
                await message.answer("💳 Оплата получена. Заполни форму.")
                return
            if status == "form_confirmed":
                await message.answer("🏁 Ты полностью записан на эту гонку.")
                return
            if status == "waitlist":
                await message.answer("📥 Ты в листе ожидания этой гонки.")
                return

        await message.answer(
            "🚀 <b>Продажи билетов уже открыты!</b>\n\n"
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
        return

    # 4️⃣ НОВЫЙ пользователь → дисклеймер
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Согласен, продолжить",
                callback_data="reg_accept"
            )],
            [InlineKeyboardButton(
                text="❌ Не согласен",
                callback_data="reg_decline"
            )],
        ]
    )

    await message.answer(
        "👋 <b>Добро пожаловать на гонку «Вупомания»!</b>\n\n"
        "⚠️ <b>Важная информация</b>\n\n"
        "Для участия в гонке необходимо:\n"
        "• согласие на обработку персональных данных\n"
        "• ознакомление с регламентом гонки\n\n"
        f"📘 <b>Регламент:</b>\n{RULES_URL}\n\n"
        "Нажимая «Согласен, продолжить», вы подтверждаете оба пункта.",
        reply_markup=kb,
        parse_mode="HTML",
    )

    # ✅ ВАЖНО: ставим FSM через context
    await state.set_state(Registration.accept_disclaimer)
