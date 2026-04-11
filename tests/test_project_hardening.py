import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("GROUP_NAME", "ИСП-3-22")
os.environ.setdefault("ADMIN_IDS", "111,222")

import bot
from bot import (
    format_schedule_message,
    get_next_study_date,
    is_admin_message,
    should_send_weekly_preview,
    should_skip_daily_evening_mailing,
)
from config import Config
from database import Database
from parser.schedule_parser import ScheduleParser
from scraper.link_finder import LinkFinder, is_allowed_schedule_url
from scraper.schedule_scraper import (
    MAX_PDF_SIZE,
    _content_length_too_large,
    _resolve_download_target,
)
from services.schedule_service import ScheduleService
from services.schedule_updater import ScheduleUpdater


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


class ConfigTests(unittest.TestCase):
    def test_reads_optional_telegram_proxy_url(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_PROXY_URL": " socks5://user:pass@proxy.example:1080 "},
        ):
            config = Config.from_env()

        self.assertEqual(config.telegram_proxy_url, "socks5://user:pass@proxy.example:1080")

    def test_blank_telegram_proxy_url_is_ignored(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_PROXY_URL": "   "},
        ):
            config = Config.from_env()

        self.assertIsNone(config.telegram_proxy_url)

    def test_reads_optional_telegram_api_base_url(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_API_BASE_URL": " https://telegram-gateway.example/secret/ "},
        ):
            config = Config.from_env()

        self.assertEqual(
            config.telegram_api_base_url,
            "https://telegram-gateway.example/secret",
        )


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

    def test_link_finder_rejects_external_absolute_urls(self):
        finder = LinkFinder("https://aitanapa.ru/raspisanie/")

        self.assertEqual(
            finder._normalize_url("/download/schedule.pdf"),
            "https://aitanapa.ru/download/schedule.pdf",
        )
        self.assertIsNone(finder._normalize_url("http://127.0.0.1/admin.pdf"))
        self.assertIsNone(finder._normalize_url("https://evil.example/schedule.pdf"))

    def test_schedule_url_allowlist_requires_https_and_expected_host(self):
        self.assertTrue(is_allowed_schedule_url("https://aitanapa.ru/file.pdf"))
        self.assertTrue(is_allowed_schedule_url("https://www.aitanapa.ru/file.pdf"))
        self.assertFalse(is_allowed_schedule_url("http://aitanapa.ru/file.pdf"))
        self.assertFalse(is_allowed_schedule_url("https://aitanapa.ru.evil/file.pdf"))

    def test_content_length_limit_rejects_large_pdf(self):
        response = SimpleNamespace(headers={"Content-Length": str(MAX_PDF_SIZE + 1)})

        self.assertTrue(_content_length_too_large(response))

    def test_content_length_limit_allows_missing_or_invalid_header(self):
        self.assertFalse(_content_length_too_large(SimpleNamespace(headers={})))
        self.assertFalse(
            _content_length_too_large(SimpleNamespace(headers={"Content-Length": "bad"}))
        )

    def test_format_schedule_message_escapes_html(self):
        text = format_schedule_message(
            "ПОНЕДЕЛЬНИК",
            "17.03.2026-23.03.2026",
            [(1, "08:00", "09:20", "Программирование <script>", "2<10>")],
        )
        self.assertIn("Программирование &lt;script&gt;", text)
        self.assertIn("2&lt;10&gt;", text)
        self.assertIn("➖➖➖➖➖➖➖➖➖➖", text)

    async def test_parse_error_notification_escapes_exception_text(self):
        fake_service = SimpleNamespace(notify_admins=AsyncMock())
        updater = ScheduleUpdater(
            SimpleNamespace(group_name="ИСП-3-22"),
            SimpleNamespace(),
            fake_service,
        )

        with patch(
            "services.schedule_updater.ScheduleParser",
            side_effect=ValueError('<a href="tg://user?id=1">bad</a>'),
        ):
            await updater._parse_and_save("bad.pdf", "hash", notify_users=False)

        sent_text = fake_service.notify_admins.await_args.args[0]
        self.assertIn("&lt;a href=", sent_text)
        self.assertNotIn("<a href=", sent_text)

    def test_next_study_date_skips_sunday(self):
        self.assertEqual(get_next_study_date(date(2026, 3, 14)), date(2026, 3, 16))

    def test_should_send_weekly_preview_only_before_monday(self):
        self.assertTrue(should_send_weekly_preview(date(2026, 3, 16)))
        self.assertFalse(should_send_weekly_preview(date(2026, 3, 17)))

    def test_should_skip_daily_evening_mailing_on_saturday_only(self):
        self.assertTrue(should_skip_daily_evening_mailing(date(2026, 3, 14)))
        self.assertFalse(should_skip_daily_evening_mailing(date(2026, 3, 15)))

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

    async def test_daily_evening_mailing_skips_saturday(self):
        fake_now = bot.datetime(2026, 3, 14, 19, 0, tzinfo=bot.MOSCOW_TZ)

        with patch.object(bot, "now_moscow", return_value=fake_now):
            with patch.object(bot, "get_metadata", new=AsyncMock()) as get_meta_mock:
                with patch.object(bot, "broadcast_message", new=AsyncMock()) as broadcast_mock:
                    with patch.object(bot, "set_metadata", new=AsyncMock()) as set_meta_mock:
                        await bot.daily_evening_mailing()

        get_meta_mock.assert_not_called()
        broadcast_mock.assert_not_called()
        set_meta_mock.assert_not_called()

    async def test_daily_evening_mailing_sends_monday_message_on_sunday(self):
        fake_now = bot.datetime(2026, 3, 15, 19, 0, tzinfo=bot.MOSCOW_TZ)

        with patch.object(bot, "now_moscow", return_value=fake_now):
            with patch.object(bot, "get_metadata", new=AsyncMock(return_value=None)):
                with patch.object(
                    bot,
                    "get_schedule_for_target_date",
                    new=AsyncMock(return_value=("ПОНЕДЕЛЬНИК", "16.03.2026-21.03.2026", [(1, "08:00", "09:20", "Информатика", "")])),
                ):
                    with patch.object(bot, "broadcast_message", new=AsyncMock()) as broadcast_mock:
                        with patch.object(bot, "set_metadata", new=AsyncMock()) as set_meta_mock:
                            await bot.daily_evening_mailing()

        broadcast_mock.assert_awaited_once()
        sent_text = broadcast_mock.await_args.args[0]
        self.assertIn("Расписание на завтра", sent_text)
        self.assertIn("ПОНЕДЕЛЬНИК", sent_text)
        set_meta_mock.assert_awaited_once_with("last_daily_sent_date", "2026-03-16")

    async def test_startup_recovery_runs_weekly_preview_on_sunday_after_one_pm(self):
        fake_now = bot.datetime(2026, 3, 15, 14, 0, tzinfo=bot.MOSCOW_TZ)

        with patch.object(bot, "now_moscow", return_value=fake_now):
            with patch.object(bot, "check_and_update_schedule", new=AsyncMock()):
                with patch.object(bot, "weekly_preview_mailing", new=AsyncMock()) as weekly_mock:
                    with patch.object(bot, "set_metadata", new=AsyncMock()) as set_meta_mock:
                        with patch.object(bot, "get_metadata", new=AsyncMock(return_value="2026-03-16")):
                            await bot.startup_recovery()

        weekly_mock.assert_awaited_once_with()
        set_meta_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
