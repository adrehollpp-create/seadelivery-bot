"""Проверки прав администратора чата."""

from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ChatMemberStatus


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Вернуть True, если пользователь — владелец или администратор чата."""
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)
