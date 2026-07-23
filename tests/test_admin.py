import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import admin


SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE,
    fio TEXT,
    created_at TEXT
);
CREATE TABLE races (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    date TEXT,
    slots_total INTEGER,
    status TEXT,
    created_at TEXT
);
CREATE TABLE race_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id INTEGER,
    status TEXT,
    user_id INTEGER,
    reserved_until TEXT,
    chat_id INTEGER,
    message_id INTEGER
);
CREATE TABLE race_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    slot_id INTEGER,
    status TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(race_id, telegram_id)
);
"""


class AdminUserListTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "race.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.executescript("""
                INSERT INTO users (telegram_id, fio, created_at)
                VALUES (100, 'Иван <Пилот>', '2026-01-01T00:00:00');
                INSERT INTO users (telegram_id, fio, created_at)
                VALUES (200, 'Петр Второй', '2026-01-02T00:00:00');
                INSERT INTO users (telegram_id, fio, created_at)
                VALUES (300, 'Друг Без Оплаты', '2026-01-03T00:00:00');
                INSERT INTO races (
                    id, title, date, slots_total, status, created_at
                ) VALUES (
                    1, 'Race & Test', '2026-09-12T00:00:00',
                    3, 'sales_open', '2026-01-01T00:00:00'
                );
                INSERT INTO race_slots (race_id, status, user_id)
                VALUES
                    (1, 'paid', 100),
                    (1, 'reserved', 200),
                    (1, 'free', NULL);
                INSERT INTO race_entries (
                    race_id, telegram_id, slot_id, status, created_at
                ) VALUES (1, 100, 1, 'paid', '2026-01-01T00:00:00');
                INSERT INTO race_entries (
                    race_id, telegram_id, slot_id, status, created_at
                ) VALUES (1, 200, 2, 'reserved', '2026-01-02T00:00:00');
            """)
            conn.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def message(self, text):
        return SimpleNamespace(
            text=text,
            from_user=SimpleNamespace(id=1),
            answer=AsyncMock(),
            bot=AsyncMock(),
            chat=SimpleNamespace(id=999),
        )

    async def call_users(self, text):
        message = self.message(text)
        with (
            patch.object(admin, "get_connection", self.connection),
            patch.object(admin, "is_admin", return_value=True),
        ):
            await admin.list_users(message)
        return message

    async def test_users_without_filter_returns_summary_only(self):
        message = await self.call_users("/users")

        message.answer.assert_awaited_once()
        response = message.answer.await_args.args[0]
        self.assertIn("Активная гонка", response)
        self.assertIn("свободно: <b>1</b>", response)
        self.assertNotIn("Иван", response)

    async def test_users_all_returns_escaped_compact_list(self):
        message = await self.call_users("/users all")

        message.answer.assert_awaited_once()
        response = message.answer.await_args.args[0]
        self.assertIn("Иван &lt;Пилот&gt;", response)
        self.assertIn("Петр Второй", response)
        self.assertIn("Race &amp; Test", response)
        self.assertNotIn("video_system", response)

    async def test_unknown_filter_is_rejected(self):
        message = await self.call_users("/users unknown")

        message.answer.assert_awaited_once()
        response = message.answer.await_args.args[0]
        self.assertIn("Неизвестный фильтр", response)

    async def test_add_user_assigns_free_slot_without_payment(self):
        message = self.message("/add_user 300")
        with (
            patch.object(admin, "get_connection", self.connection),
            patch.object(admin, "is_admin", return_value=True),
            patch.object(
                admin,
                "show_pass_form",
                new=AsyncMock(),
            ) as show_form,
        ):
            await admin.add_user_without_payment(message)

        show_form.assert_awaited_once_with(
            message.bot,
            300,
            3,
            payment_received=False,
        )
        with self.connection() as conn:
            slot = conn.execute(
                """
                SELECT status, user_id
                FROM race_slots
                WHERE id = 3
                """
            ).fetchone()
            entry = conn.execute(
                """
                SELECT status, slot_id
                FROM race_entries
                WHERE race_id = 1 AND telegram_id = 300
                """
            ).fetchone()
        self.assertEqual(slot, ("paid", 300))
        self.assertEqual(entry, ("paid", 3))

    async def test_add_user_resends_form_without_second_slot(self):
        message = self.message("/add_user 100")
        with (
            patch.object(admin, "get_connection", self.connection),
            patch.object(admin, "is_admin", return_value=True),
            patch.object(
                admin,
                "show_pass_form",
                new=AsyncMock(),
            ) as show_form,
        ):
            await admin.add_user_without_payment(message)

        show_form.assert_awaited_once_with(
            message.bot,
            100,
            1,
            payment_received=False,
        )
        response = message.answer.await_args.args[0]
        self.assertIn("Форма отправлена повторно", response)
        with self.connection() as conn:
            occupied = conn.execute(
                """
                SELECT COUNT(*)
                FROM race_slots
                WHERE user_id = 100
                """
            ).fetchone()[0]
        self.assertEqual(occupied, 1)

    def test_pages_stay_below_telegram_limit(self):
        blocks = [f"{index}. {'x' * 500}\n" for index in range(30)]
        pages = admin.build_user_pages("Заголовок", blocks)

        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) < 4096 for page in pages))


if __name__ == "__main__":
    unittest.main()
