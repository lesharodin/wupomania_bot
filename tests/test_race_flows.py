import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from handlers import payments_watcher
from handlers.waitlist import try_assign_from_waitlist


SCHEMA = """
CREATE TABLE users (
    telegram_id INTEGER PRIMARY KEY,
    fio TEXT
);
CREATE TABLE races (
    id INTEGER PRIMARY KEY,
    status TEXT
);
CREATE TABLE race_slots (
    id INTEGER PRIMARY KEY,
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
CREATE TABLE race_test_entries (
    race_id INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    slot_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (race_id, telegram_id)
);
"""


class RaceFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "race.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)

    def tearDown(self):
        self.temp_dir.cleanup()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def insert_reserved_slot(self, slot_user_id, entry_user_id=None):
        entry_user_id = entry_user_id or slot_user_id
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO races (id, status) VALUES (1, 'sales_open')"
            )
            conn.execute(
                """
                INSERT INTO race_slots (
                    id, race_id, status, user_id, reserved_until
                )
                VALUES (10, 1, 'reserved', ?, '2099-01-01T00:00:00')
                """,
                (slot_user_id,),
            )
            conn.execute(
                """
                INSERT INTO race_entries (
                    race_id, telegram_id, slot_id, status, created_at
                )
                VALUES (1, ?, 10, 'reserved', '2026-01-01T00:00:00')
                """,
                (entry_user_id,),
            )
            conn.commit()

    async def test_payment_confirms_matching_reservation(self):
        self.insert_reserved_slot(slot_user_id=100)
        bot = AsyncMock()

        with (
            patch.object(payments_watcher, "get_connection", self.connection),
            patch.object(
                payments_watcher,
                "show_pass_form",
                new=AsyncMock(),
            ) as show_form,
        ):
            result = await payments_watcher.handle_race_payment(
                bot=bot,
                payment_id=500,
                user_id=100,
                slot_id=10,
            )

        self.assertEqual(result, payments_watcher.PAYMENT_PROCESSED)
        show_form.assert_awaited_once_with(bot, 100, 10)
        with self.connection() as conn:
            slot = conn.execute(
                "SELECT status, user_id, reserved_until FROM race_slots"
            ).fetchone()
            entry = conn.execute(
                "SELECT status FROM race_entries"
            ).fetchone()
        self.assertEqual(slot, ("paid", 100, None))
        self.assertEqual(entry, ("paid",))

    async def test_late_payment_does_not_confirm_reassigned_slot(self):
        self.insert_reserved_slot(slot_user_id=200)
        bot = AsyncMock()

        with (
            patch.object(payments_watcher, "get_connection", self.connection),
            patch.object(
                payments_watcher,
                "show_pass_form",
                new=AsyncMock(),
            ) as show_form,
        ):
            result = await payments_watcher.handle_race_payment(
                bot=bot,
                payment_id=501,
                user_id=100,
                slot_id=10,
            )

        self.assertEqual(result, payments_watcher.PAYMENT_REVIEW_REQUIRED)
        show_form.assert_not_awaited()
        with self.connection() as conn:
            slot = conn.execute(
                "SELECT status, user_id FROM race_slots"
            ).fetchone()
            entry = conn.execute(
                "SELECT status FROM race_entries"
            ).fetchone()
        self.assertEqual(slot, ("reserved", 200))
        self.assertEqual(entry, ("reserved",))

    async def test_payment_processing_is_idempotent(self):
        self.insert_reserved_slot(slot_user_id=100)
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE race_slots
                SET status = 'paid', reserved_until = NULL
                WHERE id = 10
                """
            )
            conn.execute(
                """
                UPDATE race_entries
                SET status = 'paid'
                WHERE slot_id = 10
                """
            )
            conn.commit()

        bot = AsyncMock()
        with (
            patch.object(payments_watcher, "get_connection", self.connection),
            patch.object(
                payments_watcher,
                "show_pass_form",
                new=AsyncMock(),
            ) as show_form,
        ):
            result = await payments_watcher.handle_race_payment(
                bot=bot,
                payment_id=502,
                user_id=100,
                slot_id=10,
            )

        self.assertEqual(result, payments_watcher.PAYMENT_PROCESSED)
        show_form.assert_not_awaited()
        bot.send_message.assert_not_awaited()

    async def test_test_payment_does_not_notify_admin_chat(self):
        self.insert_reserved_slot(slot_user_id=100)
        with self.connection() as conn:
            conn.execute("""
                INSERT INTO race_test_entries (
                    race_id, telegram_id, slot_id, created_at
                )
                VALUES (1, 100, 10, '2026-01-01T00:00:00')
            """)
            conn.commit()
        bot = AsyncMock()

        with (
            patch.object(payments_watcher, "get_connection", self.connection),
            patch.object(
                payments_watcher,
                "show_pass_form",
                new=AsyncMock(),
            ) as show_form,
        ):
            result = await payments_watcher.handle_race_payment(
                bot=bot,
                payment_id=503,
                user_id=100,
                slot_id=10,
            )

        self.assertEqual(result, payments_watcher.PAYMENT_PROCESSED)
        show_form.assert_awaited_once_with(bot, 100, 10)
        bot.send_message.assert_not_awaited()
        bot.get_chat_member.assert_not_awaited()

    async def test_waitlist_assigns_first_user(self):
        with self.connection() as conn:
            conn.executescript("""
                INSERT INTO races (id, status) VALUES (1, 'sales_open');
                INSERT INTO race_slots (id, race_id, status)
                VALUES (10, 1, 'free');
                INSERT INTO users (telegram_id, fio) VALUES (100, 'First User');
                INSERT INTO users (telegram_id, fio) VALUES (200, 'Second User');
                INSERT INTO race_entries (
                    race_id, telegram_id, status, created_at
                ) VALUES (1, 100, 'waitlist', '2026-01-01T00:00:00');
                INSERT INTO race_entries (
                    race_id, telegram_id, status, created_at
                ) VALUES (1, 200, 'waitlist', '2026-01-02T00:00:00');
            """)
            conn.commit()

        bot = AsyncMock()
        with (
            patch(
                "handlers.waitlist.get_connection",
                self.connection,
            ),
            patch(
                "handlers.waitlist.create_payment",
                return_value="https://payment.example/",
            ),
        ):
            result = await try_assign_from_waitlist(bot, 1)

        self.assertEqual(result, (100, "First User", 10))
        with self.connection() as conn:
            slot = conn.execute(
                "SELECT status, user_id FROM race_slots"
            ).fetchone()
            statuses = conn.execute(
                """
                SELECT telegram_id, status
                FROM race_entries
                ORDER BY telegram_id
                """
            ).fetchall()
        self.assertEqual(slot, ("reserved", 100))
        self.assertEqual(
            statuses,
            [(100, "reserved"), (200, "waitlist")],
        )


if __name__ == "__main__":
    unittest.main()
