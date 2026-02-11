"""Сервис рассылки и уведомлений."""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from aiogram.types import FSInputFile
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramBadRequest,
    TelegramRetryAfter,
)

if TYPE_CHECKING:
    from aiogram import Bot
    from config import Config
    from database import Database

logger = logging.getLogger(__name__)

BROADCAST_DELAY_SEC = 0.05


class ScheduleService:
    """Сервис для рассылки и уведомлений админов."""

    def __init__(self, bot: "Bot", config: "Config", db: "Database"):
        self.bot = bot
        self.config = config
        self.db = db

    async def broadcast_message(
        self, text: str, document_path: str | None = None
    ) -> int:
        """Рассылка сообщения во все зарегистрированные чаты.
        Возвращает количество успешно отправленных сообщений.
        """
        chats = await self.db.get_chats()
        count = 0
        for chat_id, thread_id in chats:
            try:
                await self._send_to_chat(chat_id, thread_id, text, document_path)
                count += 1
            except TelegramRetryAfter as e:
                logger.warning("Rate limit! Жду %s сек", e.retry_after)
                await asyncio.sleep(e.retry_after)
                try:
                    await self._send_to_chat(
                        chat_id, thread_id, text, document_path
                    )
                    count += 1
                except Exception:
                    logger.exception("Повторная ошибка для чата %s", chat_id)
            except TelegramForbiddenError:
                logger.info("Бот заблокирован в чате %s, удаляю", chat_id)
                await self.db.remove_chat(chat_id)
            except TelegramBadRequest as e:
                if "chat not found" in str(e).lower():
                    await self.db.remove_chat(chat_id)
                else:
                    logger.warning(
                        "Ошибка запроса для чата %s: %s", chat_id, e
                    )
            except Exception as e:
                logger.warning("Ошибка рассылки в чат %s: %s", chat_id, e)

            await asyncio.sleep(BROADCAST_DELAY_SEC)

        logger.info("Рассылка завершена: %d сообщений", count)
        return count

    async def _send_to_chat(
        self,
        chat_id: int,
        thread_id: int | None,
        text: str,
        document_path: str | None,
    ) -> None:
        if document_path and os.path.exists(document_path):
            await self.bot.send_document(
                chat_id,
                FSInputFile(document_path),
                caption=text,
                message_thread_id=thread_id,
            )
        else:
            await self.bot.send_message(
                chat_id, text, message_thread_id=thread_id
            )

    @staticmethod
    async def _can_notify(
        db: "Database", throttle_key: str, interval_minutes: int
    ) -> bool:
        last_ts = await db.get_metadata(throttle_key)
        if not last_ts:
            return True
        try:
            last_dt = datetime.fromisoformat(last_ts)
        except ValueError:
            return True
        from zoneinfo import ZoneInfo
        moscow_tz = ZoneInfo("Europe/Moscow")
        return datetime.now(moscow_tz) - last_dt >= timedelta(
            minutes=interval_minutes
        )

    async def notify_admins(
        self,
        text: str,
        throttle_key: str,
        interval_minutes: int = 60,
    ) -> None:
        if not self.config.admin_ids:
            return
        if not await self._can_notify(
            self.db, throttle_key, interval_minutes
        ):
            return
        sent = 0
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
                sent += 1
            except Exception as e:
                logger.warning(
                    "Ошибка уведомления админа %s: %s", admin_id, e
                )
        if sent:
            from zoneinfo import ZoneInfo
            moscow_tz = ZoneInfo("Europe/Moscow")
            await self.db.set_metadata(
                throttle_key, datetime.now(moscow_tz).isoformat()
            )
