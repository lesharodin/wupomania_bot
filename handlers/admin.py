import asyncio

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from datetime import datetime, timedelta
from html import escape
from handlers.waitlist import try_assign_from_waitlist
from handlers.sales import show_pass_form
from database.db import get_club_connection, get_connection
from payments.service import create_payment
from config import ADMINS, ADMIN_CHAT_ID, RESERVE_TIMEOUT_SECONDS

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


ADMIN_HELP = """
🛠 <b>Администрирование гонки</b>

<b>Создание и продажи</b>
<code>/create_race YYYY-MM-DD SLOTS</code>
Создать новую гонку в статусе черновика.
Пример: <code>/create_race 2026-09-12 40</code>

<code>/open_sales</code>
Открыть продажи для последнего созданного черновика, закрыть предыдущую активную гонку и разослать уведомления всем профилям.

<code>/add_slots COUNT</code>
Добавить места в активную гонку. Новые места автоматически предлагаются участникам вейтлиста.
Пример: <code>/add_slots 5</code>

<code>/add_user TELEGRAM_ID</code>
Записать пользователя на активную гонку без оплаты и отправить ему форму пропуска. У пользователя уже должен быть профиль в боте.
Пример: <code>/add_user 123456789</code>

<b>Тестирование оплаты</b>
<code>/test_payment AMOUNT</code>
Создать платеж только для себя на последнем черновике без открытия продаж и рассылки. По умолчанию сумма 1 ₽.
Пример: <code>/test_payment 1</code>

<code>/reset_test_entry</code>
Освободить свой тестовый слот после завершения платежа. Работает только для черновика.

<b>Участники</b>
<code>/users</code> — сводка активной гонки
<code>/users all</code> — все участники гонки
<code>/users profiles</code> — все профили в базе
<code>/users reserved</code> — ожидают оплату
<code>/users paid</code> — оплатили, но не подтвердили форму
<code>/users form_confirmed</code> — полностью записаны
<code>/users waitlist</code> — лист ожидания
<code>/users expired</code> — истекшие резервы
<code>/users cancelled</code> — отмененные записи

Запросы на отмену подтверждаются или отклоняются кнопками в сообщении администратора.
""".strip()


USER_FILTERS = {
    "all": None,
    "reserved": "reserved",
    "paid": "paid",
    "not_form": "paid",
    "form_confirmed": "form_confirmed",
    "waitlist": "waitlist",
    "expired": "expired",
    "cancelled": "cancelled",
}


def build_user_pages(header: str, blocks: list[str]) -> list[str]:
    if not blocks:
        return [f"{header}\nЗаписей не найдено."]

    body_limit = 3300
    bodies = []
    current = ""
    for block in blocks:
        if current and len(current) + len(block) > body_limit:
            bodies.append(current.rstrip())
            current = ""
        current += block
    if current:
        bodies.append(current.rstrip())

    total = len(bodies)
    return [
        f"{header}\nСтраница {index}/{total}\n\n{body}"
        for index, body in enumerate(bodies, start=1)
    ]


@router.message(Command("admin"))
async def admin_help(message: Message):
    if not is_admin(message.from_user.id):
        return

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, date
            FROM races
            WHERE status = 'sales_open'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        active_race = cursor.fetchone()
        cursor.execute("""
            SELECT COUNT(*)
            FROM races
            WHERE status = 'draft'
        """)
        drafts_count = cursor.fetchone()[0]

    state = "\n\n<b>Текущее состояние</b>\n"
    if active_race:
        race_id, title, race_date = active_race
        state += (
            f"Активная гонка: <b>{escape(title or 'Без названия')}</b>\n"
            f"ID: <code>{race_id}</code>, дата: "
            f"<code>{escape(race_date or 'не указана')}</code>\n"
        )
    else:
        state += "Активной гонки нет.\n"
    state += f"Черновиков: <b>{drafts_count}</b>"

    await message.answer(ADMIN_HELP + state, parse_mode="HTML")


# =========================
# CREATE RACE
# =========================
@router.message(Command("create_race"))
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
    if slots_total <= 0:
        await message.answer("❌ Количество слотов должно быть больше нуля")
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
@router.message(Command("open_sales"))
async def open_sales(message: Message):
    if not is_admin(message.from_user.id):
        return
    if len(message.text.split()) != 1:
        await message.answer("❌ Формат:\n/open_sales")
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
            SET status = 'closed'
            WHERE status = 'sales_open'
        """)

        cursor.execute("""
            UPDATE races
            SET status = 'sales_open'
            WHERE id = ?
        """, (race_id,))

        conn.commit()

        cursor.execute("""
            SELECT telegram_id
            FROM users
            WHERE telegram_id IS NOT NULL
        """)
        users = cursor.fetchall()

    delivered = 0
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
            delivered += 1
        except Exception:
            pass  # пользователь мог заблокировать бота
    await message.answer(
        f"🚀 Продажи открыты. Уведомлений доставлено: {delivered}/{len(users)}"
    )

    await message.bot.send_message(
        ADMIN_CHAT_ID,
        "🚀 <b>Продажи билетов открыты</b>",
        parse_mode="HTML"
    )


@router.message(Command("test_payment"))
async def create_test_payment(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) > 2:
        await message.answer("❌ Формат:\n/test_payment AMOUNT")
        return

    try:
        amount = int(parts[1]) if len(parts) == 2 else 1
        if not 1 <= amount <= 1000:
            raise ValueError
    except ValueError:
        await message.answer("❌ AMOUNT должен быть целым числом от 1 до 1000")
        return

    user_id = message.from_user.id
    now = datetime.now().isoformat()
    reserve_until = (
        datetime.now() + timedelta(seconds=RESERVE_TIMEOUT_SECONDS)
    ).isoformat()
    error = None
    slot_id = None

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("""
            SELECT id, title
            FROM races
            WHERE status = 'draft'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        race = cursor.fetchone()
        if not race:
            error = (
                "❌ Нет гонки в статусе draft.\n"
                "Сначала создай ее через /create_race YYYY-MM-DD SLOTS."
            )
        else:
            race_id, race_title = race
            cursor.execute(
                "SELECT fio FROM users WHERE telegram_id = ?",
                (user_id,),
            )
            user = cursor.fetchone()
            if not user:
                error = (
                    "❌ У тебя нет профиля участника.\n"
                    "Сначала пройди регистрацию через /start."
                )
            else:
                cursor.execute("""
                    SELECT status
                    FROM race_entries
                    WHERE race_id = ?
                      AND telegram_id = ?
                """, (race_id, user_id))
                existing_entry = cursor.fetchone()
                if existing_entry:
                    error = (
                        "❌ Для тебя уже существует запись на этот черновик: "
                        f"{existing_entry[0]}.\n"
                        "После теста используй /reset_test_entry."
                    )
                else:
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
                        error = "❌ В черновике нет свободных слотов"
                    else:
                        slot_id = slot[0]
                        cursor.execute("""
                            UPDATE race_slots
                            SET status = 'reserved',
                                user_id = ?,
                                reserved_until = ?
                            WHERE id = ?
                              AND status = 'free'
                        """, (user_id, reserve_until, slot_id))
                        if cursor.rowcount != 1:
                            error = "❌ Не удалось зарезервировать тестовый слот"
                        else:
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
                            """, (race_id, user_id, slot_id, now, now))
                            cursor.execute("""
                                INSERT INTO race_test_entries (
                                    race_id,
                                    telegram_id,
                                    slot_id,
                                    created_at
                                )
                                VALUES (?, ?, ?, ?)
                            """, (race_id, user_id, slot_id, now))

        if error:
            conn.rollback()
        else:
            conn.commit()

    if error:
        await message.answer(error)
        return

    try:
        payment_url = await asyncio.to_thread(
            create_payment,
            user_id=user_id,
            amount=amount,
            target_type="race_slot",
            target_id=slot_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            description=(
                f"ТЕСТ Вупомания | tgid {user_id} | "
                f"slot {slot_id} | {amount} RUB"
            ),
        )
    except Exception:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE race_slots
                SET status = 'free',
                    user_id = NULL,
                    reserved_until = NULL
                WHERE id = ?
                  AND status = 'reserved'
                  AND user_id = ?
            """, (slot_id, user_id))
            cursor.execute("""
                DELETE FROM race_entries
                WHERE race_id = ?
                  AND telegram_id = ?
                  AND slot_id = ?
                  AND status = 'reserved'
            """, (race_id, user_id, slot_id))
            cursor.execute("""
                DELETE FROM race_test_entries
                WHERE race_id = ?
                  AND telegram_id = ?
                  AND slot_id = ?
            """, (race_id, user_id, slot_id))
            conn.commit()
        await message.answer(
            "❌ Не удалось создать тестовый платеж. Слот освобожден."
        )
        return

    payment_message = await message.answer(
        "🧪 <b>Тестовый платеж создан</b>\n\n"
        f"🏁 {escape(race_title or 'Без названия')}\n"
        f"🎟 Slot ID: <code>{slot_id}</code>\n"
        f"💳 Сумма: <b>{amount} ₽</b>\n"
        "⏱ Платеж действует 10 минут.\n\n"
        "Уведомления другим пользователям не отправлялись.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"💳 Оплатить {amount} ₽",
                        url=payment_url,
                    )
                ]
            ]
        ),
        parse_mode="HTML",
    )

    with get_connection() as conn:
        conn.execute("""
            UPDATE race_slots
            SET chat_id = ?, message_id = ?
            WHERE id = ?
              AND status = 'reserved'
              AND user_id = ?
        """, (message.chat.id, payment_message.message_id, slot_id, user_id))
        conn.commit()


@router.message(Command("reset_test_entry"))
async def reset_test_entry(message: Message):
    if not is_admin(message.from_user.id):
        return
    if len(message.text.split()) != 1:
        await message.answer("❌ Формат:\n/reset_test_entry")
        return

    user_id = message.from_user.id
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT re.race_id, re.slot_id, re.status, r.title
            FROM race_entries re
            JOIN races r ON r.id = re.race_id
            JOIN race_test_entries rte
              ON rte.race_id = re.race_id
             AND rte.telegram_id = re.telegram_id
             AND rte.slot_id = re.slot_id
            WHERE re.telegram_id = ?
              AND r.status = 'draft'
            ORDER BY r.created_at DESC
            LIMIT 1
        """, (user_id,))
        entry = cursor.fetchone()

    if not entry:
        await message.answer("ℹ️ Тестовая запись в черновике не найдена")
        return

    race_id, slot_id, entry_status, race_title = entry
    with get_club_connection() as payment_conn:
        payment_cursor = payment_conn.cursor()
        payment_cursor.execute("""
            SELECT id, status
            FROM payments
            WHERE user_id = ?
              AND target_id = ?
              AND target_type = 'race_slot'
            ORDER BY id DESC
            LIMIT 1
        """, (user_id, slot_id))
        payment = payment_cursor.fetchone()

        if not payment:
            await message.answer(
                "❌ Для этой записи не найден тестовый платеж. "
                "Слот автоматически не изменен."
            )
            return

        payment_id, payment_status = payment
        if payment_status not in ("succeeded", "canceled"):
            await message.answer(
                "⏳ Платеж еще не завершен. Оплати его или дождись отмены "
                "в YooKassa, затем повтори /reset_test_entry."
            )
            return

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("""
                SELECT status, user_id
                FROM race_slots
                WHERE id = ?
                  AND race_id = ?
            """, (slot_id, race_id))
            slot = cursor.fetchone()
            slot_is_owned = (
                slot
                and slot[0] in ("reserved", "paid")
                and slot[1] == user_id
            )
            slot_already_free = (
                slot
                and slot[0] == "free"
                and slot[1] is None
                and entry_status == "expired"
            )
            if not slot_is_owned and not slot_already_free:
                conn.rollback()
                await message.answer(
                    "❌ Тестовый слот не найден или уже был освобожден"
                )
                return

            if slot_is_owned:
                cursor.execute("""
                    UPDATE race_slots
                    SET status = 'free',
                        user_id = NULL,
                        reserved_until = NULL,
                        chat_id = NULL,
                        message_id = NULL
                    WHERE id = ?
                      AND race_id = ?
                      AND user_id = ?
                """, (slot_id, race_id, user_id))

            cursor.execute("""
                DELETE FROM race_entries
                WHERE race_id = ?
                  AND telegram_id = ?
                  AND slot_id = ?
            """, (race_id, user_id, slot_id))
            if cursor.rowcount != 1:
                conn.rollback()
                await message.answer("❌ Не удалось удалить тестовую запись")
                return

            cursor.execute("""
                DELETE FROM race_test_entries
                WHERE race_id = ?
                  AND telegram_id = ?
                  AND slot_id = ?
            """, (race_id, user_id, slot_id))
            if cursor.rowcount != 1:
                conn.rollback()
                await message.answer("❌ Не удалось удалить маркер теста")
                return
            conn.commit()

        payment_cursor.execute("""
            UPDATE payments
            SET target_type = 'race_slot_test_reset',
                ui_status = 'reset'
            WHERE id = ?
              AND target_type = 'race_slot'
        """, (payment_id,))
        payment_conn.commit()

    await message.answer(
        "✅ <b>Тестовая запись сброшена</b>\n"
        f"🏁 {escape(race_title or 'Без названия')}\n"
        f"🎟 Slot ID: <code>{slot_id}</code> снова свободен\n"
        f"🧾 Payment ID: <code>{payment_id}</code>\n"
        f"Статус платежа: <code>{payment_status}</code>",
        parse_mode="HTML",
    )


@router.message(Command("add_user"))
async def add_user_without_payment(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("❌ Формат:\n/add_user TELEGRAM_ID")
        return

    try:
        user_id = int(parts[1])
        if user_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ TELEGRAM_ID должен быть положительным числом")
        return

    now = datetime.now().isoformat()
    error = None
    slot_id = None
    fio = None
    already_registered = False

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")

        cursor.execute("""
            SELECT id, title
            FROM races
            WHERE status = 'sales_open'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        race = cursor.fetchone()
        if not race:
            error = "❌ Нет активной гонки с открытыми продажами"
        else:
            race_id, race_title = race
            cursor.execute("""
                SELECT fio
                FROM users
                WHERE telegram_id = ?
            """, (user_id,))
            user = cursor.fetchone()
            if not user:
                error = (
                    "❌ Профиль пользователя не найден.\n"
                    "Пользователь должен сначала нажать /start и заполнить ФИО."
                )
            else:
                fio = user[0]
                cursor.execute("""
                    SELECT slot_id, status
                    FROM race_entries
                    WHERE race_id = ?
                      AND telegram_id = ?
                """, (race_id, user_id))
                entry = cursor.fetchone()

                if entry and entry[1] == "form_confirmed":
                    error = (
                        "ℹ️ Пользователь уже полностью записан на активную гонку"
                    )
                elif entry and entry[1] == "paid":
                    slot_id = entry[0]
                    already_registered = True
                elif entry and entry[1] == "reserved":
                    slot_id = entry[0]
                    cursor.execute("""
                        UPDATE race_slots
                        SET status = 'paid',
                            reserved_until = NULL,
                            chat_id = NULL,
                            message_id = NULL
                        WHERE id = ?
                          AND race_id = ?
                          AND user_id = ?
                          AND status = 'reserved'
                    """, (slot_id, race_id, user_id))
                    if cursor.rowcount != 1:
                        error = (
                            "❌ Резерв пользователя поврежден. "
                            "Проверь запись через /users reserved."
                        )
                else:
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
                        error = (
                            "❌ Свободных мест нет.\n"
                            "Сначала добавь места командой /add_slots COUNT."
                        )
                    else:
                        slot_id = slot[0]
                        cursor.execute("""
                            UPDATE race_slots
                            SET status = 'paid',
                                user_id = ?,
                                reserved_until = NULL,
                                chat_id = NULL,
                                message_id = NULL
                            WHERE id = ?
                              AND status = 'free'
                        """, (user_id, slot_id))
                        if cursor.rowcount != 1:
                            error = "❌ Не удалось назначить свободный слот"

                if not error and not already_registered:
                    cursor.execute("""
                        INSERT INTO race_entries (
                            race_id,
                            telegram_id,
                            slot_id,
                            status,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, 'paid', ?, ?)
                        ON CONFLICT(race_id, telegram_id)
                        DO UPDATE SET
                            slot_id = excluded.slot_id,
                            status = 'paid',
                            updated_at = excluded.updated_at
                    """, (race_id, user_id, slot_id, now, now))

        if error:
            conn.rollback()
        else:
            conn.commit()

    if error:
        await message.answer(error)
        return

    notification_sent = True
    try:
        await show_pass_form(
            message.bot,
            user_id,
            slot_id,
            payment_received=False,
        )
    except Exception:
        notification_sent = False

    result_title = (
        "ℹ️ <b>Форма отправлена повторно</b>\n"
        if already_registered
        else "✅ <b>Пользователь записан без оплаты</b>\n"
    )
    result = (
        result_title
        +
        f"👤 {escape(fio or 'Без ФИО')}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"🎟 Slot ID: <code>{slot_id}</code>\n"
        f"🏁 {escape(race_title or 'Без названия')}\n"
        f"📨 Форма отправлена: <b>{'да' if notification_sent else 'нет'}</b>"
    )
    await message.answer(result, parse_mode="HTML")

    if message.chat.id != ADMIN_CHAT_ID:
        await message.bot.send_message(
            ADMIN_CHAT_ID,
            result,
            parse_mode="HTML",
        )


STATUS_LABELS = {
    "reserved": "⏳ Резерв (ждёт оплату)",
    "paid": "💳 Оплатил",
    "form_confirmed": "✅ Оплатил + заполнил форму",
    "waitlist": "📥 Лист ожидания",
    "expired": "⏱ Резерв истёк",
    "cancelled": "❌ Отменён",
}


@router.message(Command("users"))
async def list_users(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    filter_arg = parts[1].lower() if len(parts) > 1 else None
    if len(parts) > 2:
        await message.answer(
            "❌ Слишком много аргументов.\nИспользуй <code>/admin</code>.",
            parse_mode="HTML",
        )
        return
    if filter_arg not in ({None, "profiles"} | set(USER_FILTERS)):
        await message.answer(
            "❌ Неизвестный фильтр.\nИспользуй <code>/admin</code>.",
            parse_mode="HTML",
        )
        return

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        profiles_total = cursor.fetchone()[0]

        if filter_arg == "profiles":
            cursor.execute("""
                SELECT telegram_id, fio
                FROM users
                WHERE telegram_id IS NOT NULL
                ORDER BY created_at, id
            """)
            profile_rows = cursor.fetchall()
            race = None
        else:
            profile_rows = None

            cursor.execute("""
                SELECT id, title, date
                FROM races
                WHERE status = 'sales_open'
                ORDER BY created_at DESC
                LIMIT 1
            """)
            race = cursor.fetchone()

        if race:
            race_id, race_title, race_date = race

            cursor.execute("""
                SELECT status, COUNT(*)
                FROM race_entries
                WHERE race_id = ?
                GROUP BY status
            """, (race_id,))
            stats = dict(cursor.fetchall())

            cursor.execute("""
                SELECT status, COUNT(*)
                FROM race_slots
                WHERE race_id = ?
                GROUP BY status
            """, (race_id,))
            slot_stats = dict(cursor.fetchall())
            slots_total = sum(slot_stats.values())

            rows = []
            if filter_arg:
                status_filter = USER_FILTERS[filter_arg]
                query = """
                    SELECT
                        u.telegram_id,
                        u.fio,
                        re.status,
                        re.created_at
                    FROM race_entries re
                    JOIN users u ON u.telegram_id = re.telegram_id
                    WHERE re.race_id = ?
                """
                params = [race_id]
                if status_filter:
                    query += " AND re.status = ?"
                    params.append(status_filter)
                query += " ORDER BY re.created_at, re.id"
                cursor.execute(query, params)
                rows = cursor.fetchall()

    if filter_arg == "profiles":
        header = (
            "👥 <b>Все профили</b>\n"
            f"Всего: <b>{len(profile_rows)}</b>"
        )
        blocks = [
            (
                f"{index}. <a href='tg://user?id={tg_id}'>"
                f"<b>{escape(fio or 'Без ФИО')}</b></a>\n"
                f"ID: <code>{tg_id}</code>\n\n"
            )
            for index, (tg_id, fio) in enumerate(profile_rows, start=1)
        ]
        for page in build_user_pages(header, blocks):
            await message.answer(page, parse_mode="HTML")
        return

    if not race:
        await message.answer(
            "📊 <b>Профили</b>\n"
            f"Всего в базе: <b>{profiles_total}</b>\n\n"
            "Активной гонки нет.\n"
            "Список профилей: <code>/users profiles</code>",
            parse_mode="HTML",
        )
        return

    race_title_safe = escape(race_title or "Без названия")
    race_date_safe = escape(race_date or "не указана")
    summary = (
        "📊 <b>Активная гонка</b>\n"
        f"<b>{race_title_safe}</b>\n"
        f"ID: <code>{race_id}</code> · дата: <code>{race_date_safe}</code>\n\n"
        f"Профилей в базе: <b>{profiles_total}</b>\n"
        f"Слотов: <b>{slots_total}</b> · "
        f"свободно: <b>{slot_stats.get('free', 0)}</b>\n"
        f"Резерв: <b>{stats.get('reserved', 0)}</b>\n"
        f"Оплатили, форма не подтверждена: <b>{stats.get('paid', 0)}</b>\n"
        f"Полностью записаны: <b>{stats.get('form_confirmed', 0)}</b>\n"
        f"Вейтлист: <b>{stats.get('waitlist', 0)}</b>\n"
        f"Истекшие резервы: <b>{stats.get('expired', 0)}</b>\n"
        f"Отменены: <b>{stats.get('cancelled', 0)}</b>"
    )

    if not filter_arg:
        await message.answer(
            summary
            + "\n\nСписки и фильтры: <code>/admin</code>",
            parse_mode="HTML",
        )
        return

    filter_label = (
        "все участники"
        if filter_arg == "all"
        else STATUS_LABELS.get(USER_FILTERS[filter_arg], filter_arg)
    )
    header = (
        f"👥 <b>{escape(filter_label)}</b>\n"
        f"Гонка: <b>{race_title_safe}</b> · найдено: <b>{len(rows)}</b>"
    )
    blocks = []
    for index, (tg_id, fio, status, created_at) in enumerate(rows, start=1):
        status_label = escape(STATUS_LABELS.get(status, status))
        blocks.append(
            f"{index}. <a href='tg://user?id={tg_id}'>"
            f"<b>{escape(fio or 'Без ФИО')}</b></a>\n"
            f"ID: <code>{tg_id}</code> · {status_label}\n\n"
        )

    for page in build_user_pages(header, blocks):
        await message.answer(page, parse_mode="HTML")

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

        # берём user_id и race_id только для новой модели участия
        cursor.execute("""
            SELECT rs.user_id, rs.race_id
            FROM race_slots rs
            JOIN race_entries re ON re.slot_id = rs.id
            WHERE rs.id = ?
              AND re.status IN ('paid', 'form_confirmed')
        """, (slot_id,))
        row = cursor.fetchone()

        if not row:
            await callback.answer("Активная запись не найдена", show_alert=True)
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

        # обновляем участие в гонке
        cursor.execute("""
            UPDATE race_entries
            SET status = 'cancelled',
                updated_at = ?
            WHERE race_id = ?
              AND telegram_id = ?
              AND slot_id = ?
        """, (datetime.now().isoformat(), race_id, user_id, slot_id))

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
# =========================
# ADD SLOTS TO ACTIVE RACE
# =========================
@router.message(Command("add_slots"))
async def add_slots(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("❌ Формат:\n/add_slots COUNT")
        return

    try:
        count = int(parts[1])
        if count <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ COUNT должен быть положительным числом")
        return

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
        row = cursor.fetchone()

        if not row:
            await message.answer("❌ Нет активной гонки (sales_open)")
            return

        race_id = row[0]

        # 2️⃣ добавляем слоты
        for _ in range(count):
            cursor.execute("""
                INSERT INTO race_slots (race_id, status)
                VALUES (?, 'free')
            """, (race_id,))

        cursor.execute("""
            UPDATE races
            SET slots_total = COALESCE(slots_total, 0) + ?
            WHERE id = ?
        """, (count, race_id))

        conn.commit()

    await message.answer(
        f"➕ <b>Добавлены слоты</b>\n"
        f"🎟 Количество: <b>{count}</b>\n"
        f"🏁 Race ID: <code>{race_id}</code>",
        parse_mode="HTML"
    )

    await message.bot.send_message(
        ADMIN_CHAT_ID,
        (
            "➕ <b>Админ добавил слоты</b>\n"
            f"🎟 Количество: <b>{count}</b>\n"
            f"🏁 Race ID: <code>{race_id}</code>"
        ),
        parse_mode="HTML"
    )

    # 3️⃣ отдаём слоты waitlist’у + логируем КАЖДОГО
    for _ in range(count):
        result = await try_assign_from_waitlist(message.bot, race_id)

        if not result:
            break  # waitlist закончился

        user_id, fio, slot_id = result

        await message.bot.send_message(
            ADMIN_CHAT_ID,
            (
                "⏭️ <b>Слот отдан из waitlist (добавление)</b>\n"
                f"👤 {fio}\n"
                f"🆔 User ID: <code>{user_id}</code>\n"
                f"🎟 Slot ID: <code>{slot_id}</code>\n"
                f"🏁 Race ID: <code>{race_id}</code>"
            ),
            parse_mode="HTML"
        )
