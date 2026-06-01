"""Морская доставка — Telegram-бот ролевого ивента «торговая экспедиция между островами».

Игроки помогают торговцам с острова «Парламумти» доставлять товары между
островами, проходя игровое поле с клетками-испытаниями (три мини-игры). Всё
взаимодействие — через инлайн-кнопки, реакция только на сообщения с хештегом
``#морскаядоставка``.

Бот собирается из роутеров:

* :func:`make_sea_router` — игровой роутер для участников.
* :func:`make_sea_admin_router` — административный роутер (раунды, статистика,
  экспорт, финальный рейтинг).
* :func:`make_basic_router` — базовые команды (/start, /help, /chatid).

Хранилище (:class:`SeaStore`) поддерживает два бэкенда: SQLite (локально/тесты)
и PostgreSQL (продакшен).
"""

from __future__ import annotations

from .admin import make_sea_admin_router
from .basic import make_basic_router
from .config import Config
from .handlers import make_sea_router
from .store import SeaStore, create_sea_store

__all__ = [
    "Config",
    "SeaStore",
    "create_sea_store",
    "make_basic_router",
    "make_sea_admin_router",
    "make_sea_router",
]
