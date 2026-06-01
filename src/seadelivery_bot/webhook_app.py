"""FastAPI-приложение для продакшен-деплоя через webhook (Render/Fly.io и т.п.).

Экспонирует один эндпоинт ``/webhook``, куда Telegram шлёт апдейты. На старте
регистрирует webhook, на остановке — удаляет.

Обязательные переменные окружения:
  - ``BOT_TOKEN`` — токен бота от @BotFather.
  - ``WEBHOOK_URL`` (или автоопределение из ``RENDER_EXTERNAL_URL`` / ``FLY_APP_NAME``).

Необязательные:
  - ``WEBHOOK_SECRET`` — секрет для проверки колбэков Telegram.
  - ``DATABASE_URL`` — Postgres URI; иначе SQLite-файл из ``DB_PATH``.
  - ``OWNER_ID`` — Telegram user ID супер-владельца.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request

from .admin import make_sea_admin_router
from .basic import make_basic_router
from .config import Config
from .handlers import make_sea_router
from .middlewares import ChatWhitelistMiddleware
from .store import create_sea_store

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook"


def _resolve_webhook_url() -> str:
    """Определить публичный HTTPS-URL для регистрации в Telegram."""
    for var in ("WEBHOOK_URL", "RENDER_EXTERNAL_URL"):
        url = os.environ.get(var, "").strip()
        if url:
            return url.rstrip("/")

    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if render_host:
        return f"https://{render_host}"

    fly_app = os.environ.get("FLY_APP_NAME", "").strip()
    if fly_app:
        return f"https://{fly_app}.fly.dev"

    raise RuntimeError(
        "Не удаётся определить webhook URL. Задайте WEBHOOK_URL, либо запускайте "
        "на Render (RENDER_EXTERNAL_URL) или Fly.io (FLY_APP_NAME)."
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = Config.from_env()
    store = create_sea_store(config.database_url)
    await store.connect()

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

    webhook_url = _resolve_webhook_url()
    webhook_secret = os.environ.get("WEBHOOK_SECRET", "").strip() or secrets.token_urlsafe(32)

    me = await bot.get_me()
    logger.info("Бот @%s (id=%s) запускается в webhook-режиме", me.username, me.id)

    await bot.set_webhook(
        url=f"{webhook_url}{WEBHOOK_PATH}",
        secret_token=webhook_secret,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=True,
    )
    logger.info("Webhook зарегистрирован: %s%s", webhook_url, WEBHOOK_PATH)

    app.state.bot = bot
    app.state.dp = dp
    app.state.store = store
    app.state.webhook_secret = webhook_secret
    app.state.bot_username = me.username

    try:
        yield
    finally:
        try:
            await bot.delete_webhook()
        except Exception:
            logger.exception("Не удалось удалить webhook при остановке")
        await bot.session.close()
        await store.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def healthz() -> dict[str, str]:
    bot_username = getattr(app.state, "bot_username", None)
    return {"status": "ok", "bot": f"@{bot_username}" if bot_username else "unknown"}


@app.get("/healthz")
async def healthz_alias() -> dict[str, str]:
    return await healthz()


@app.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    expected = getattr(app.state, "webhook_secret", None)
    if expected and x_telegram_bot_api_secret_token != expected:
        raise HTTPException(status_code=403, detail="invalid secret token")

    bot: Bot = app.state.bot
    dp: Dispatcher = app.state.dp
    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}
