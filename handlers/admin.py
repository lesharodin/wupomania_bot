from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from datetime import datetime
from handlers.waitlist import try_assign_from_waitlist
from database.db import get_connection
from config import ADMINS, ADMIN_CHAT_ID

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


# =========================
# CREATE RACE
# =========================
@router.message(F.text.startswith("/create_race"))
async def create_race(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer(
            "❌ Формат:\n"
            "/create_race YYYY-MM-DD SLOTS"
        )
        return

    _, date_str, slots_str = parts

    try:
        race_date = datetime.fromisoformat(date_str)
        slots_total = int(slots_str)
    except Exception:
        await message.answer("❌ Неверный формат даты или количества слотов")
        return

    with get_connection() as conn:
        cursor = conn.cursor()

        # 1️⃣ создаём гонку
        cursor.execute("""
            INSERT INTO races (
                title,
                date,
                slots_total,
                status,
                created_at
            )
            VALUES (?, ?, ?, 'draft', ?)
        """, (
            f"Race {race_date.strftime('%d.%m.%Y')}",
            race_date.isoformat(),
            slots_total,
            datetime.now().isoformat()
        ))

        race_id = cursor.lastrowid

        # 2️⃣ создаём слоты
        for _ in range(slots_total):
            cursor.execute("""
                INSERT INTO race_slots (
                    race_id,
                    status
                )
                VALUES (?, 'free')
            """, (race_id,))

        conn.commit()

    await message.answer(
        f"✅ Гонка создана\n"
        f"📅 {race_date.strftime('%d.%m.%Y')}\n"
        f"🎟 Слотов: {slots_total}\n"
        f"🆔 Race ID: {race_id}"
    )

    await message.bot.send_message(
        ADMIN_CHAT_ID,
        f"🏁 <b>Создана гонка</b>\n"
        f"📅 {race_date.strftime('%d.%m.%Y')}\n"
        f"🎟 Слотов: {slots_total}\n"
        f"🆔 ID: {race_id}",
        parse_mode="HTML"
    )


# =========================
# OPEN SALES
# =========================
@router.message(F.text == "/open_sales")
async def open_sales(message: Message):
    if not is_admin(message.from_user.id):
        return

    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id
            FROM races
            WHERE status = 'draft'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        race = cursor.fetchone()

        if not race:
            await message.answer("❌ Нет гонки для открытия продаж")
            return

        race_id = race[0]

        cursor.execute("""
            UPDATE races
            SET status = 'sales_open'
            WHERE id = ?
        """, (race_id,))

        conn.commit()

        cursor.execute("""
            SELECT telegram_id
            FROM users
            WHERE status = 'registered'
        """)
        users = cursor.fetchall()

        for (telegram_id,) in users:
            try:
                await message.bot.send_message(
                    telegram_id,
                    (
                        "🚀 <b>Продажи билетов на гонку открыты!</b>\n\n"
                        "🎟 Количество мест ограничено.\n"
                        "⏱ Успей записаться.\n\n"
                        "👇 Нажми кнопку ниже:"
                    ),
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
            except:
                pass  # пользователь мог заблокировать бота
    await message.answer("🚀 Продажи открыты")

    await message.bot.send_message(
        ADMIN_CHAT_ID,
        "🚀 <b>Продажи билетов открыты</b>",
        parse_mode="HTML"
    )


STATUS_LABELS = {
    "registered": "📝 Зарегистрирован",
    "reserved": "⏳ Резерв (ждёт оплату)",
    "paid": "💳 Оплатил",
    "form_confirmed": "✅ Оплатил + заполнил форму",
    "waitlist": "📥 Лист ожидания",
}


@router.message(F.text.startswith("/users"))
async def list_users(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    filter_arg = parts[1] if len(parts) > 1 else None

    with get_connection() as conn:
        cursor = conn.cursor()

        # ---------- СТАТИСТИКА ----------
        cursor.execute("""
            SELECT status, COUNT(*)
            FROM users
            GROUP BY status
        """)
        stats = dict(cursor.fetchall())

        cursor.execute("SELECT COUNT(*) FROM users")
        total = cursor.fetchone()[0]

        # ---------- ВЫБОРКА С ФИЛЬТРОМ ----------
        if filter_arg == "not_form":
            cursor.execute("""
                SELECT telegram_id, fio, video_system, drone_size, status
                FROM users
                WHERE status = 'paid'
                   OR (status = 'form_confirmed' AND COALESCE(form_confirmed, 0) = 0)
                ORDER BY created_at
            """)
        elif filter_arg:
            cursor.execute("""
                SELECT telegram_id, fio, video_system, drone_size, status
                FROM users
                WHERE status = ?
                ORDER BY created_at
            """, (filter_arg,))
        else:
            cursor.execute("""
                SELECT telegram_id, fio, video_system, drone_size, status
                FROM users
                ORDER BY created_at
            """)

        rows = cursor.fetchall()

    # ---------- СООБЩЕНИЕ СО СЧЁТЧИКАМИ ----------
    header = (
        "📊 <b>Статистика участников</b>\n\n"
        f"Всего: <b>{total}</b>\n"
        f"📝 Зарегистрированы: {stats.get('registered', 0)}\n"
        f"⏳ Резерв: {stats.get('reserved', 0)}\n"
        f"💳 Оплатили: {stats.get('paid', 0)}\n"
        f"✅ Оплатили + форма: {stats.get('form_confirmed', 0)}\n"
        f"📥 Лист ожидания: {stats.get('waitlist', 0)}\n\n"
    )

    if filter_arg:
        header += f"🔎 <b>Фильтр:</b> {filter_arg}\n\n"

    messages = []
    current = header

    # ---------- СПИСОК ----------
    for tg_id, fio, video, drone, status in rows:
        profile_link = f"<a href='tg://user?id={tg_id}'>Открыть профиль</a>"
        status_label = STATUS_LABELS.get(status, status)

        block = (
            f"👤 <b>{fio}</b>\n"
            f"🔗 {profile_link}\n"
            f" TGID {tg_id}\n"
            f"📌 Статус: <b>{status_label}</b>\n"
            "────────────\n"
        )

        if len(current) + len(block) > 3800:
            messages.append(current)
            current = ""

        current += block

    if current:
        messages.append(current)

    for msg in messages:
        await message.answer(msg, parse_mode="HTML")

# =========================
# CONFIRM CANCEL (ADMIN)
# =========================
@router.callback_query(F.data.startswith("cancel_confirm_admin:"))
async def cancel_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    slot_id = int(callback.data.split(":")[1])

    with get_connection() as conn:
        cursor = conn.cursor()

        # берём user_id и race_id
        cursor.execute("""
            SELECT user_id, race_id
            FROM race_slots
            WHERE id = ?
        """, (slot_id,))
        row = cursor.fetchone()

        if not row:
            await callback.answer("Слот не найден", show_alert=True)
            return

        user_id, race_id = row

        # освобождаем слот
        cursor.execute("""
            UPDATE race_slots
            SET status = 'free',
                user_id = NULL,
                reserved_until = NULL,
                chat_id = NULL,
                message_id = NULL
            WHERE id = ?
        """, (slot_id,))

        # обновляем пользователя
        cursor.execute("""
            UPDATE users
            SET status = 'cancelled',
                refund_pending = 1
            WHERE telegram_id = ?
        """, (user_id,))

        conn.commit()

    # уведомляем пользователя
    await callback.bot.send_message(
        user_id,
        "❌ <b>Ваша запись на гонку отменена</b>\n\n"
        "💰 Возврат средств будет выполнен вручную администратором.",
        parse_mode="HTML"
    )

    # обновляем сообщение админа
    await callback.message.edit_text(
        f"✅ <b>Отмена подтверждена</b>\n🆔 Slot ID: {slot_id}",
        parse_mode="HTML"
    )

    await callback.answer("Отмена подтверждена")

    # 🔥 ВАЖНО: пускаем waitlist
    await try_assign_from_waitlist(callback.bot, race_id)



# =========================
# CANCEL ABORT (ADMIN)
# =========================
@router.callback_query(F.data.startswith("cancel_abort_admin:"))
async def cancel_abort_admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    slot_id = int(callback.data.split(":")[1])

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id FROM race_slots WHERE id = ?
        """, (slot_id,))
        row = cursor.fetchone()

    if row:
        await callback.bot.send_message(
            row[0],
            "❌ <b>Отмена участия отклонена администратором</b>",
            parse_mode="HTML"
        )

    await callback.message.edit_text(
        f"🚫 <b>Отмена отклонена</b>\n🆔 Slot ID: {slot_id}",
        parse_mode="HTML"
    )

    await callback.answer("Отмена отклонена")
