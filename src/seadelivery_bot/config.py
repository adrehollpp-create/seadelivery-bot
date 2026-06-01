"""Конфигурация бота, загружаемая из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SQLITE_PATH = "seadelivery.db"


@dataclass(frozen=True)
class Config:
    """Параметры запуска бота."""

    bot_token: str
    database_url: str
    super_owner_id: int | None
    allowed_chat_ids: frozenset[int]

    @classmethod
    def from_env(cls) -> Config:
        token = os.environ.get("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "Требуется переменная окружения BOT_TOKEN. "
                "Получите токен у @BotFather и задайте его через BOT_TOKEN."
            )

        # Предпочтительная переменная — DATABASE_URL (Postgres URI или путь к SQLite).
        # Для SQLite-развёртываний также читается legacy DB_PATH.
        database_url = (
            os.environ.get("DATABASE_URL", "").strip()
            or os.environ.get("DB_PATH", "").strip()
            or DEFAULT_SQLITE_PATH
        )

        super_owner_raw = os.environ.get("OWNER_ID", "").strip()
        super_owner_id: int | None
        if super_owner_raw:
            try:
                super_owner_id = int(super_owner_raw)
            except ValueError as exc:
                raise RuntimeError(
                    f"OWNER_ID должен быть целым Telegram user ID, получено {super_owner_raw!r}"
                ) from exc
        else:
            super_owner_id = None

        # ALLOWED_CHAT_IDS: список chat_id через запятую или пробел.
        # Пусто (или не задано) — бот работает в любом чате.
        # Если задано — апдейты из других чатов молча игнорируются.
        allowed_chat_raw = os.environ.get("ALLOWED_CHAT_IDS", "").strip()
        allowed: set[int] = set()
        if allowed_chat_raw:
            tokens = [t for t in allowed_chat_raw.replace(",", " ").split() if t]
            for tok in tokens:
                try:
                    allowed.add(int(tok))
                except ValueError as exc:
                    raise RuntimeError(
                        "ALLOWED_CHAT_IDS должен быть списком целых chat_id "
                        f"через запятую или пробел, получено {tok!r}"
                    ) from exc

        return cls(
            bot_token=token,
            database_url=database_url,
            super_owner_id=super_owner_id,
            allowed_chat_ids=frozenset(allowed),
        )
