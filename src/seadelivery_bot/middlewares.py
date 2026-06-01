"""Middleware'ы aiogram.

Здесь живёт :class:`ChatWhitelistMiddleware`, ограничивающий бота заданным
набором чатов. Команда ``/chatid`` всегда проходит, чтобы можно было узнать
chat_id до добавления чата в список.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)


# Команды, которые проходят всегда, даже если чат не в списке.
_BYPASS_COMMANDS: frozenset[str] = frozenset({"/chatid"})


def _is_bypass_command(text: str | None) -> bool:
    if not text:
        return False
    head = text.lstrip().split(maxsplit=1)[0] if text.strip() else ""
    head = head.split("@", 1)[0].lower()
    return head in _BYPASS_COMMANDS


class ChatWhitelistMiddleware(BaseMiddleware):
    """Отбрасывать апдейты из чатов вне списка.

    Пустой ``allowed_chat_ids`` полностью отключает фильтр — удобно, пока
    оператор ещё узнаёт chat_id через ``/chatid``.
    """

    def __init__(self, allowed_chat_ids: frozenset[int]) -> None:
        self.allowed_chat_ids = allowed_chat_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not self.allowed_chat_ids:
            return await handler(event, data)

        chat_id: int | None = None
        text: str | None = None

        if isinstance(event, Message):
            chat_id = event.chat.id
            text = event.text or event.caption
        elif isinstance(event, CallbackQuery) and event.message is not None:
            chat_id = event.message.chat.id

        if chat_id is None:
            return None

        if chat_id in self.allowed_chat_ids:
            return await handler(event, data)

        if isinstance(event, Message) and _is_bypass_command(text):
            return await handler(event, data)

        logger.debug("Апдейт из чата %s вне списка — отброшен", chat_id)
        return None
