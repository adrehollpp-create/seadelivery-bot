"""Вспомогательные функции для работы с Postgres/SQLite DSN."""

from __future__ import annotations

import re

_STATUS_RE = re.compile(r"^(INSERT|UPDATE|DELETE)(?:\s+\d+)?\s+(\d+)$")


def _affected_rows(status: str) -> int:
    """Разобрать тег ``ExecuteCompleted``, который asyncpg возвращает из execute()."""
    match = _STATUS_RE.match(status.strip())
    if not match:
        return 0
    return int(match.group(2))


def _normalize_postgres_dsn(dsn: str) -> str:
    """Убрать query-параметры, которые asyncpg не понимает.

    Neon и другие провайдеры часто добавляют ``sslmode=require`` и
    ``channel_binding=require`` в строку подключения. asyncpg ожидает, что
    настройки SSL передаются отдельно, поэтому такие параметры отбрасываются.
    """
    if "?" not in dsn:
        return dsn
    base, _, query = dsn.partition("?")
    drop = {"sslmode", "channel_binding", "ssl"}
    kept: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0].lower()
        if key in drop:
            continue
        kept.append(pair)
    return base if not kept else f"{base}?{'&'.join(kept)}"


def _postgres_ssl_required(dsn: str) -> bool:
    """Определить, нужен ли SSL для данного DSN.

    По умолчанию True (большинство managed-провайдеров требуют SSL). Учитывает
    явные ``sslmode=disable`` или ``ssl=false`` в query-параметрах.
    """
    if "?" not in dsn:
        return True
    query = dsn.split("?", 1)[1]
    for pair in query.split("&"):
        key, _, value = pair.partition("=")
        key = key.strip().lower()
        value = value.strip().lower()
        if key == "sslmode" and value == "disable":
            return False
        if key == "ssl" and value in {"false", "off", "0", "disable"}:
            return False
    return True
