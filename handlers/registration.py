from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from datetime import datetime

from database.db import get_connection
from config import ADMIN_CHAT_ID, RULES_URL

router = Router()


# =========================
# FSM
# =========================
class Registration(StatesGroup):
    accept_disclaimer = State()
    enter_fio = State()


# =========================
# START REGISTRATION
# =========================
@router.callback_query(F.data == "start_reg")
async def start_registration(callback: CallbackQuery, state: FSMContext):
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

    await callback.message.answer(
        "⚠️ <b>Важная информация</b>\n\n"
        "Для участия в гонке необходимо:\n"
        "• согласие на обработку персональных данных\n"
        "• ознакомление с регламентом гонки\n\n"
        f"📘 Регламент:\n{RULES_URL}\n\n"
        "Нажимая «Согласен, продолжить», вы подтверждаете оба пункта.",
        reply_markup=kb,
        parse_mode="HTML",
    )

    await state.set_state(Registration.accept_disclaimer)
    await callback.answer()


# =========================
# ACCEPT / DECLINE
# =========================
@router.callback_query(F.data == "reg_accept", Registration.accept_disclaimer)
async def reg_accept(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        pd_accepted=True,
        rules_accepted=True,
    )

    await callback.message.answer("✍️ Введите ФИО полностью одним сообщением:")
    await state.set_state(Registration.enter_fio)
    await callback.answer()


@router.callback_query(F.data == "reg_decline", Registration.accept_disclaimer)
async def reg_decline(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    await callback.message.answer(
        "❌ Без согласия на условия\n"
        "регистрация и участие в гонке невозможны.\n"
        "если вы передумали, нажмите /start"
    )
    await callback.answer()


# =========================
# FIO
# =========================
@router.message(Registration.enter_fio)
async def enter_fio(message: Message, state: FSMContext):
    fio = message.text.strip()
    data = await state.get_data()
    await state.clear()

    # --- save to DB ---
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (
                telegram_id,
                fio,
                pd_accepted,
                rules_accepted,
                status,
                created_at
            )
            VALUES (?, ?, 1, 1, 'registered', ?)
            """,
            (
                message.from_user.id,
                fio,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()

    # --- check if sales already open ---
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

    # --- user message ---
    if race:
        await message.answer(
            "✅ <b>Регистрация завершена!</b>\n\n"
            "🚀 Продажи билетов уже открыты!\n"
            "Вы можешете записаться на гонку прямо сейчас 👇\n"
            "<b>Стоимость участия 2000р</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🎟 Записаться на гонку",
                        callback_data="buy_ticket"
                    )]
                ]
            ),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "✅ <b>Регистрация завершена!</b>\n\n"
            "⏳ Продажи билетов откроются позже, мы тебя уведомим.",
            parse_mode="HTML",
        )

    # --- admin log ---
    user = message.from_user
    user_display = f"@{user.username}" if user.username else user.full_name

    await message.bot.send_message(
        ADMIN_CHAT_ID,
        (
            "🆕 <b>Новая регистрация</b>\n"
            f"👤 {fio}\n"
            f"🔗 Telegram: {user_display}\n"
            f"🆔 ID: <code>{user.id}</code>"
        ),
        parse_mode="HTML",
    )
