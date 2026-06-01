"""Базовые команды: /start, /help, /chatid."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from . import content

_HELP = (
    "🌊 <b>Морская доставка</b> — ролевой ивент с торговой экспедицией между островами.\n\n"
    f"Бот реагирует только на сообщения с хештегом <code>{content.HASHTAG}</code>.\n\n"
    "<b>Игроку:</b>\n"
    "• /sea — открыть меню ивента / текущую сессию\n"
    f"• любое сообщение с {content.HASHTAG} — то же самое\n\n"
    "<b>Администратору чата:</b>\n"
    "• /sea_panel — панель управления ивентом\n"
    "• /sea_open — открыть набор в раунд (на 1 час)\n"
    "• /sea_round — запустить раунд\n"
    "• /sea_endround — завершить раунд\n"
    "• /sea_players — записавшиеся\n"
    "• /sea_stats — рейтинг игроков\n"
    "• /sea_roundstats N — статистика раунда N\n"
    "• /sea_final — финальный рейтинг\n"
    "• /sea_newevent — сбросить ивент\n\n"
    "/chatid — показать ID этого чата."
)


def make_basic_router() -> Router:
    router = Router(name="basic")

    @router.message(Command("start", "help"))
    async def cmd_start(message: Message) -> None:
        await message.answer(_HELP, parse_mode="HTML")

    @router.message(Command("chatid"))
    async def cmd_chatid(message: Message) -> None:
        await message.answer(f"ID этого чата: <code>{message.chat.id}</code>", parse_mode="HTML")

    return router
