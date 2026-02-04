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
    accept_pd = State()
    accept_rules = State()
    enter_fio = State()
    select_video = State()
    select_drone = State()


# =========================
# START REGISTRATION
# =========================
@router.callback_query(F.data == "start_reg")
async def start_registration(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, согласен", callback_data="reg_pd_yes")],
        [InlineKeyboardButton(text="❌ Не согласен", callback_data="reg_pd_no")],
    ])

    await callback.message.answer(
        "📄 <b>Согласие на обработку персональных данных</b>\n\n"
        "Для участия в гонке необходимо дать согласие "
        "на обработку персональных данных.\n\n"
        "Нажимая «Да, согласен», вы подтверждаете своё согласие.",
        reply_markup=kb,
        parse_mode="HTML",
    )

    await state.set_state(Registration.accept_pd)
    await callback.answer()


# =========================
# PD ACCEPT / DECLINE
# =========================
@router.callback_query(F.data == "reg_pd_yes", Registration.accept_pd)
async def reg_pd_yes(callback: CallbackQuery, state: FSMContext):
    await state.update_data(pd_accepted=True)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📘 Ознакомился с регламентом",
            callback_data="reg_rules_yes",
        )]
    ])

    await callback.message.answer(
        "📘 <b>Регламент гонки</b>\n\n"
        f"Ознакомьтесь с регламентом:\n{RULES_URL}\n\n"
        "После прочтения нажмите кнопку ниже.",
        reply_markup=kb,
        parse_mode="HTML",
    )

    await state.set_state(Registration.accept_rules)
    await callback.answer()


@router.callback_query(F.data == "reg_pd_no", Registration.accept_pd)
async def reg_pd_no(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    await callback.message.answer(
        "❌ Без согласия на обработку персональных данных\n"
        "регистрация и участие в гонке невозможны."
    )
    await callback.answer()


# =========================
# RULES ACCEPT
# =========================
@router.callback_query(F.data == "reg_rules_yes", Registration.accept_rules)
async def reg_rules_yes(callback: CallbackQuery, state: FSMContext):
    await state.update_data(rules_accepted=True)

    await callback.message.answer("✍️ Введите ФИО полностью:")
    await state.set_state(Registration.enter_fio)
    await callback.answer()


# =========================
# FIO
# =========================
@router.message(Registration.enter_fio)
async def enter_fio(message: Message, state: FSMContext):
    await state.update_data(fio=message.text.strip())

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Analog", callback_data="reg_video_analog"),
            InlineKeyboardButton(text="HDZero", callback_data="reg_video_hdzero"),
        ]
    ])

    await message.answer("🎥 Выберите видеосистему:", reply_markup=kb)
    await state.set_state(Registration.select_video)


# =========================
# VIDEO SYSTEM
# =========================
@router.callback_query(F.data.startswith("reg_video_"), Registration.select_video)
async def select_video(callback: CallbackQuery, state: FSMContext):
    video_system = callback.data.replace("reg_video_", "")
    await state.update_data(video_system=video_system)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="65 мм", callback_data="reg_drone_65"),
            InlineKeyboardButton(text="75 мм", callback_data="reg_drone_75"),
        ]
    ])

    await callback.message.answer("🚁 Выберите размер дрона:", reply_markup=kb)
    await state.set_state(Registration.select_drone)
    await callback.answer()


# =========================
# DRONE SIZE
# =========================
@router.callback_query(F.data.startswith("reg_drone_"), Registration.select_drone)
async def select_drone(callback: CallbackQuery, state: FSMContext):
    drone_size = callback.data.replace("reg_drone_", "")
    await state.update_data(drone_size=drone_size)

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
                video_system,
                drone_size,
                pd_accepted,
                rules_accepted,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, 1, 1, 'registered', ?)
            """,
            (
                callback.from_user.id,
                data["fio"],
                data["video_system"],
                drone_size,
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
        await callback.message.answer(
            "✅ <b>Регистрация завершена!</b>\n\n"
            "🚀 Продажи билетов уже открыты!\n"
            "Ты можешь записаться на гонку прямо сейчас 👇",
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
        await callback.message.answer(
            "✅ <b>Регистрация завершена!</b>\n\n"
            "⏳ Продажи билетов откроются позже, мы тебя уведомим.",
            parse_mode="HTML",
        )


    # --- admin log ---
    user = callback.from_user
    user_display = f"@{user.username}" if user.username else user.full_name

    await callback.bot.send_message(
        ADMIN_CHAT_ID,
        (
            "🆕 <b>Новая регистрация</b>\n"
            f"👤 {data['fio']}\n"
            f"🎥 Видео: {data['video_system']}\n"
            f"🚁 Дрон: {drone_size}\n"
            f"🔗 Telegram: {user_display}\n"
            f"🆔 ID: <code>{user.id}</code>"
        ),
        parse_mode="HTML",
    )

    await callback.answer()
