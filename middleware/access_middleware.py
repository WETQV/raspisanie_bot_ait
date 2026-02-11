"""Middleware для проверки доступа (зарегистрированные чаты и админы)."""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class AccessMiddleware(BaseMiddleware):
    """Пропускает только зарегистрированные чаты и админов."""

    def __init__(self, db: Any, admin_ids: list[int]):
        self.db = db
        self.admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        message: Message = event

        if message.from_user and message.from_user.id in self.admin_ids:
            return await handler(event, data)

        if await self.db.is_chat_registered(message.chat.id):
            return await handler(event, data)

        if message.text and message.text.strip().startswith("/start"):
            await message.answer("🚫 Регистрация новых групп ограничена.")

        return None
