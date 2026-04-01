import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("GROUP_NAME", "ИСП-3-22")
os.environ.setdefault("ADMIN_IDS", "111,222")

from bot import format_schedule_message, get_next_study_date, is_admin_message
from database import Database
from parser.schedule_parser import ScheduleParser
from scraper.schedule_scraper import _resolve_download_target
from services.schedule_service import ScheduleService


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_document(self, chat_id, document, caption=None, message_thread_id=None):
        self.calls.append(
            ("document", chat_id, caption, message_thread_id, type(document).__name__)
        )
        return SimpleNamespace(message_id=777)

    async def send_message(self, chat_id, text, message_thread_id=None):
        self.calls.append(("message", chat_id, text, message_thread_id))

    async def pin_chat_message(self, chat_id, message_id, disable_notification=None):
        self.calls.append(("pin", chat_id, message_id, disable_notification))

    async def unpin_chat_message(self, chat_id, message_id=None):
        self.calls.append(("unpin", chat_id, message_id))


class FakeMetadataDb:
    def __init__(self):
        self.storage = {}

    async def get_metadata(self, key):
        return self.storage.get(key)

    async def set_metadata(self, key, value):
        self.storage[key] = value


class ProjectHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_connect_is_idempotent(self):
        db = Database(":memory:")
        await db.connect()
        first_connection = db.conn
        await db.connect()
        self.assertIs(first_connection, db.conn)
        await db.close()

    async def test_database_picks_period_by_target_date(self):
        db = Database(":memory:")
        await db.connect()

        schedule = [
            {"day": "ПЯТНИЦА", "lessons": [{"num": 1, "time": "08:00-09:20", "subject": "Математика", "room": "2.10"}]},
            {"day": "ПОНЕДЕЛЬНИК", "lessons": [{"num": 1, "time": "08:00-09:20", "subject": "Информатика", "room": "3.05"}]},
        ]
        await db.save_schedule("ИСП-3-22", "10.03.2026-16.03.2026", schedule)
        await db.save_schedule("ИСП-3-22", "17.03.2026-23.03.2026", schedule)

        period_friday, friday_lessons = await db.get_schedule_for_day(
            "ИСП-3-22",
            "ПЯТНИЦА",
            target_date=date(2026, 3, 13),
        )
        period_monday, monday_lessons = await db.get_schedule_for_day(
            "ИСП-3-22",
            "ПОНЕДЕЛЬНИК",
            target_date=date(2026, 3, 17),
        )

        self.assertEqual(period_friday, "10.03.2026-16.03.2026")
        self.assertEqual(period_monday, "17.03.2026-23.03.2026")
        self.assertEqual(friday_lessons[0][3], "Математика")
        self.assertEqual(monday_lessons[0][3], "Информатика")
        await db.close()

    def test_resolve_download_target_blocks_path_traversal(self):
        download_dir = tempfile.TemporaryDirectory()
        self.addCleanup(download_dir.cleanup)

        target = _resolve_download_target(Path(download_dir.name), "../../evil.pdf")
        self.assertEqual(target.parent, Path(download_dir.name).resolve())
        self.assertEqual(target.name, "evil.pdf")

    def test_resolve_download_target_rejects_non_pdf(self):
        download_dir = tempfile.TemporaryDirectory()
        self.addCleanup(download_dir.cleanup)

        with self.assertRaises(ValueError):
            _resolve_download_target(Path(download_dir.name), "notes.txt")

    def test_format_schedule_message_escapes_html(self):
        text = format_schedule_message(
            "ПОНЕДЕЛЬНИК",
            "17.03.2026-23.03.2026",
            [(1, "08:00", "09:20", "Программирование <script>", "2<10>")],
        )
        self.assertIn("Программирование &lt;script&gt;", text)
        self.assertIn("2&lt;10&gt;", text)
        self.assertIn("➖➖➖➖➖➖➖➖➖➖", text)

    def test_next_study_date_skips_sunday(self):
        self.assertEqual(get_next_study_date(date(2026, 3, 14)), date(2026, 3, 16))

    def test_manual_update_admin_check(self):
        admin_message = SimpleNamespace(from_user=SimpleNamespace(id=111))
        user_message = SimpleNamespace(from_user=SimpleNamespace(id=333))
        self.assertTrue(is_admin_message(admin_message))
        self.assertFalse(is_admin_message(user_message))

    def test_schedule_parser_shifts_day_columns_for_moved_layout(self):
        parser = ScheduleParser("dummy.pdf")
        region_words = [
            {"x0": 65.4, "text": "ИСП-3-22"},
            {"x0": 103.2, "text": "4"},
            {"x0": 125.7, "text": "12.40-14.00"},
            {"x0": 151.5, "text": "ПП.07"},
        ]

        day_ranges = parser._resolve_day_x_ranges(region_words)

        self.assertAlmostEqual(day_ranges[0][0], 151.5)
        self.assertAlmostEqual(day_ranges[1][0], 211.5, places=1)

    async def test_schedule_service_sends_document_and_message_separately(self):
        fake_bot = FakeBot()
        fake_db = FakeMetadataDb()
        fake_config = SimpleNamespace(admin_ids=[])
        service = ScheduleService(fake_bot, fake_config, fake_db)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
            temp_file.write(b"%PDF-1.4\n")
            temp_path = temp_file.name

        try:
            await service._send_to_chat(1, 10, "Weekly text", temp_path)
        finally:
            os.unlink(temp_path)

        self.assertEqual(fake_bot.calls[0][0], "document")
        self.assertEqual(fake_bot.calls[0][2], "📎 Актуальный PDF расписания")
        self.assertEqual(fake_bot.calls[1][0], "message")
        self.assertEqual(fake_bot.calls[1][2], "Weekly text")

    async def test_schedule_service_pins_latest_document_in_group(self):
        fake_bot = FakeBot()
        fake_db = FakeMetadataDb()
        fake_config = SimpleNamespace(admin_ids=[])
        service = ScheduleService(fake_bot, fake_config, fake_db)
        fake_db.storage[service._pin_metadata_key(-100123, None)] = "555"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
            temp_file.write(b"%PDF-1.4\n")
            temp_path = temp_file.name

        try:
            await service._send_to_chat(
                -100123,
                None,
                "Weekly text",
                temp_path,
                document_caption="📎 PDF расписания за период: 30.03.2026-04.04.2026",
                pin_document=True,
            )
        finally:
            os.unlink(temp_path)

        self.assertIn(("unpin", -100123, 555), fake_bot.calls)
        self.assertIn(("pin", -100123, 777, True), fake_bot.calls)
        self.assertEqual(
            fake_db.storage[service._pin_metadata_key(-100123, None)],
            "777",
        )

    async def test_database_returns_week_schedule(self):
        db = Database(":memory:")
        await db.connect()
        schedule = [
            {"day": "ПОНЕДЕЛЬНИК", "lessons": [{"num": 1, "time": "08:00-09:20", "subject": "Информатика", "room": "3.05"}]},
            {"day": "ВТОРНИК", "lessons": [{"num": 2, "time": "09:30-10:50", "subject": "Математика", "room": "2.10"}]},
        ]

        await db.save_schedule("ИСП-3-22", "17.03.2026-23.03.2026", schedule)
        week = await db.get_schedule_for_week("ИСП-3-22", "17.03.2026-23.03.2026")

        self.assertEqual(week[0]["day"], "ПОНЕДЕЛЬНИК")
        self.assertEqual(week[0]["lessons"][0]["subject"], "Информатика")
        self.assertEqual(week[1]["lessons"][0]["time"], "09:30-10:50")
        await db.close()


if __name__ == "__main__":
    unittest.main()
