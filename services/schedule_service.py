"""Messaging and admin notification service."""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import FSInputFile

if TYPE_CHECKING:
    from aiogram import Bot
    from config import Config
    from database import Database

logger = logging.getLogger(__name__)

BROADCAST_DELAY_SEC = 0.05
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


class ScheduleService:
    """Service for broadcasts and admin alerts."""

    def __init__(self, bot: "Bot", config: "Config", db: "Database"):
        self.bot = bot
        self.config = config
        self.db = db

    async def broadcast_message(
        self,
        text: str,
        document_path: str | None = None,
        document_caption: str | None = None,
        pin_document: bool = False,
    ) -> int:
        chats = await self.db.get_chats()
        count = 0

        for chat_id, thread_id in chats:
            try:
                await self._send_to_chat(
                    chat_id,
                    thread_id,
                    text,
                    document_path,
                    document_caption=document_caption,
                    pin_document=pin_document,
                )
                count += 1
            except TelegramRetryAfter as exc:
                logger.warning("Rate limit hit, waiting %s sec", exc.retry_after)
                await asyncio.sleep(exc.retry_after)
                try:
                    await self._send_to_chat(
                        chat_id,
                        thread_id,
                        text,
                        document_path,
                        document_caption=document_caption,
                        pin_document=pin_document,
                    )
                    count += 1
                except Exception:
                    logger.exception("Repeated send error for chat %s", chat_id)
            except TelegramForbiddenError:
                logger.info("Bot removed from chat %s, deleting from registry", chat_id)
                await self.db.remove_chat(chat_id)
            except TelegramBadRequest as exc:
                if "chat not found" in str(exc).lower():
                    await self.db.remove_chat(chat_id)
                else:
                    logger.warning("Telegram bad request for chat %s: %s", chat_id, exc)
            except Exception as exc:
                logger.warning("Broadcast error for chat %s: %s", chat_id, exc)

            await asyncio.sleep(BROADCAST_DELAY_SEC)

        logger.info("Broadcast complete: %d messages", count)
        return count

    async def _send_to_chat(
        self,
        chat_id: int,
        thread_id: int | None,
        text: str,
        document_path: str | None,
        *,
        document_caption: str | None = None,
        pin_document: bool = False,
    ) -> None:
        document_message = None
        if document_path and os.path.exists(document_path):
            document_message = await self.bot.send_document(
                chat_id,
                FSInputFile(document_path),
                caption=document_caption or "📎 Актуальный PDF расписания",
                message_thread_id=thread_id,
            )
            if pin_document and chat_id < 0:
                await self._pin_schedule_message(chat_id, document_message.message_id, thread_id)

        await self.bot.send_message(
            chat_id,
            text,
            message_thread_id=thread_id,
        )

    @staticmethod
    def _pin_metadata_key(chat_id: int, thread_id: int | None) -> str:
        return f"pinned_schedule_message:{chat_id}:{thread_id or 0}"

    async def _pin_schedule_message(
        self,
        chat_id: int,
        message_id: int,
        thread_id: int | None,
    ) -> None:
        key = self._pin_metadata_key(chat_id, thread_id)
        previous = await self.db.get_metadata(key)

        if previous and previous.isdigit() and int(previous) != message_id:
            try:
                await self.bot.unpin_chat_message(chat_id, message_id=int(previous))
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                logger.warning("Cannot unpin old schedule message in chat %s: %s", chat_id, exc)

        try:
            await self.bot.pin_chat_message(
                chat_id,
                message_id=message_id,
                disable_notification=True,
            )
            await self.db.set_metadata(key, str(message_id))
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Cannot pin schedule message in chat %s: %s", chat_id, exc)

    @staticmethod
    async def _can_notify(
        db: "Database",
        throttle_key: str,
        interval_minutes: int,
    ) -> bool:
        last_ts = await db.get_metadata(throttle_key)
        if not last_ts:
            return True

        try:
            last_dt = datetime.fromisoformat(last_ts)
        except ValueError:
            return True

        return datetime.now(MOSCOW_TZ) - last_dt >= timedelta(minutes=interval_minutes)

    async def notify_admins(
        self,
        text: str,
        throttle_key: str,
        interval_minutes: int = 60,
    ) -> None:
        if not self.config.admin_ids:
            return
        if not await self._can_notify(self.db, throttle_key, interval_minutes):
            return

        sent = 0
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
                sent += 1
            except Exception as exc:
                logger.warning("Admin notification error for %s: %s", admin_id, exc)

        if sent:
            await self.db.set_metadata(
                throttle_key,
                datetime.now(MOSCOW_TZ).isoformat(),
            )
