"""Schedule refresh and update logic."""
import logging
import re
from datetime import datetime
from typing import Any, Callable

from parser import ScheduleParser
from scraper import ScheduleScraper

logger = logging.getLogger(__name__)

THROTTLE_SHORT = 60
THROTTLE_LONG = 180

EXCLUDED_KEYWORDS = frozenset(
    [
        "абинск",
        "темрюк",
        "мостовской",
        "трудобеликовский",
        "фарм",
        "сдоз",
        "тм_",
        "_тм",
        "_тб",
        "очной формы",
    ]
)


def parse_date_from_filename(text: str) -> datetime | None:
    """Extract the week start date from a file title or URL."""
    match = re.search(r"(\d{2}\.\d{2}\.\d{2,4})", text)
    if not match:
        return None

    date_str = match.group(1)
    try:
        if len(date_str.split(".")[-1]) == 2:
            return datetime.strptime(date_str, "%d.%m.%y")
        return datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        return None


class ScheduleUpdater:
    """Orchestrates finding, downloading, parsing, and sending schedule updates."""

    def __init__(
        self,
        config: Any,
        db: Any,
        service: Any,
        format_week_schedule: Callable[[str, list], str] | None = None,
        format_document_caption: Callable[[str], str] | None = None,
    ):
        self.config = config
        self.db = db
        self.service = service
        self.format_week_schedule = format_week_schedule
        self.format_document_caption = format_document_caption

    def filter_links(self, links: list[dict]) -> list[dict]:
        candidates = []
        for link in links:
            text_lower = link["text"].lower()
            url_lower = link["url"].lower()
            if "расписание" not in text_lower:
                continue
            if ".xls" in url_lower or ".xls" in text_lower:
                continue
            if any(keyword in text_lower for keyword in EXCLUDED_KEYWORDS):
                continue
            candidates.append((parse_date_from_filename(link["text"]) or datetime.min, link))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return [link for _, link in candidates]

    async def _fetch_links(self, scraper: ScheduleScraper) -> list[dict] | None:
        try:
            links = await scraper.get_schedule_links()
        except Exception:
            logger.exception("Failed to fetch links")
            await self.service.notify_admins(
                "⚠️ Не удалось получить ссылки.",
                "links_fetch_error",
                THROTTLE_SHORT,
            )
            return None

        if not links:
            await self.service.notify_admins(
                "⚠️ Ссылки не найдены.",
                "links_missing",
                THROTTLE_LONG,
            )
            return None

        return links

    async def _parse_and_save(
        self,
        file_path: str,
        new_hash: str,
        notify_users: bool,
    ) -> tuple[str | None, list | None, bool]:
        try:
            parser = ScheduleParser(file_path)
            data = parser.parse(self.config.group_name)
        except Exception as exc:
            logger.exception("PDF parsing failed")
            await self.service.notify_admins(
                f"⚠️ Ошибка парсинга: {exc}",
                "parse_failed",
                THROTTLE_LONG,
            )
            return None, None, False

        if not data:
            await self.service.notify_admins(
                "⚠️ Парсер не извлёк данные.",
                "parse_failed",
                THROTTLE_LONG,
            )
            return None, None, False

        period = data["metadata"].get("period", "Неизвестно")
        schedule = data["schedule"]

        await self.db.set_metadata("last_file_hash", new_hash)
        await self.db.save_schedule(self.config.group_name, period, schedule)
        await self.db.set_metadata("last_week_period", period)

        if notify_users and self.format_week_schedule:
            last_sent = await self.db.get_metadata("last_weekly_sent_period")
            if last_sent != period:
                message_text = self.format_week_schedule(period, schedule)
                await self.service.broadcast_message(
                    message_text,
                    document_path=file_path,
                    document_caption=self.format_document_caption(period)
                    if self.format_document_caption
                    else None,
                    pin_document=True,
                )
                await self.db.set_metadata("last_weekly_sent_period", period)

        return period, schedule, True

    async def check_and_update(
        self,
        *,
        notify_users: bool = True,
        reason: str = "scheduled",
        force: bool = False,
    ) -> tuple[str | None, list | None, bool]:
        logger.info("Checking schedule (reason: %s)", reason)
        scraper = ScheduleScraper()

        links = await self._fetch_links(scraper)
        if not links:
            return None, None, False

        candidates = self.filter_links(links)
        if not candidates:
            await self.service.notify_admins(
                "⚠️ После фильтрации не осталось подходящих файлов.",
                "links_filtered_empty",
                THROTTLE_LONG,
            )
            return None, None, False

        target = candidates[0]
        logger.info("Selected schedule file: %s", target["text"])

        file_path, _, new_hash = await scraper.download_file(target["url"], target["filename"])
        if not file_path or not new_hash:
            await self.service.notify_admins(
                "❌ Ошибка скачивания.",
                "download_failed",
                THROTTLE_SHORT,
            )
            return None, None, False

        last_hash = await self.db.get_metadata("last_file_hash")
        if not force and new_hash == last_hash:
            logger.info("Schedule file hash unchanged")
            return None, None, False

        if force:
            logger.info("Force reparsing schedule file: %s", file_path)
        else:
            logger.info("New schedule detected: %s", file_path)
        return await self._parse_and_save(file_path, new_hash, notify_users)
