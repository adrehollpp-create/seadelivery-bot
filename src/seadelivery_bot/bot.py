"""Запуск бота «Морская доставка» в режиме long-polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .admin import make_sea_admin_router
from .basic import make_basic_router
from .config import Config
from .handlers import make_sea_router
from .middlewares import ChatWhitelistMiddleware
from .store import create_sea_store

logger = logging.getLogger(__name__)


async def run(config: Config) -> None:
    store = create_sea_store(config.database_url)
    await store.connect()
    try:
        bot = Bot(
            token=config.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp = Dispatcher()
        whitelist = ChatWhitelistMiddleware(config.allowed_chat_ids)
        dp.message.outer_middleware(whitelist)
        dp.callback_query.outer_middleware(whitelist)
        dp.include_router(make_basic_router())
        dp.include_router(make_sea_admin_router(store, config))
        dp.include_router(make_sea_router(store))

        me = await bot.get_me()
        logger.info("Бот @%s (id=%s) запущен в режиме polling", me.username, me.id)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await store.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = Config.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
