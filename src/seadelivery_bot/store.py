"""Хранилище состояния события «Морская доставка».

Поддерживает те же два бэкенда, что и репутационная часть бота:

* SQLite (локально / тесты) — :class:`SqliteSeaStore`.
* PostgreSQL (продакшен) — :class:`PostgresSeaStore`.

Сессии игроков сериализуются в JSON целиком (колонка ``state``), а лёгкие
агрегаты статистики хранятся в отдельных таблицах для быстрых выборок и
экспорта. Бэкенд выбирается автоматически по строке подключения через
:func:`create_sea_store` (та же логика, что и у ``create_database``).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import aiosqlite
import asyncpg

from .db_utils import _affected_rows, _normalize_postgres_dsn, _postgres_ssl_required
from .models import (
    EventState,
    EventStatus,
    PlayerStats,
    Session,
    StatsDelta,
)

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sea_events (
    chat_id          INTEGER PRIMARY KEY,
    status           TEXT NOT NULL,
    current_round    INTEGER NOT NULL DEFAULT 0,
    recruit_round    INTEGER NOT NULL DEFAULT 0,
    recruit_deadline REAL,
    requests         TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS sea_registrations (
    chat_id       INTEGER NOT NULL,
    round_no      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    username      TEXT,
    full_name     TEXT,
    registered_at REAL NOT NULL,
    PRIMARY KEY (chat_id, round_no, user_id)
);

CREATE TABLE IF NOT EXISTS sea_sessions (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    round_no  INTEGER NOT NULL,
    status    TEXT NOT NULL,
    state     TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS sea_player_stats (
    chat_id           INTEGER NOT NULL,
    user_id           INTEGER NOT NULL,
    username          TEXT,
    full_name         TEXT,
    games_played      INTEGER NOT NULL DEFAULT 0,
    orders_completed  INTEGER NOT NULL DEFAULT 0,
    challenges_won    INTEGER NOT NULL DEFAULT 0,
    challenges_failed INTEGER NOT NULL DEFAULT 0,
    moves_used        INTEGER NOT NULL DEFAULT 0,
    island_travels    INTEGER NOT NULL DEFAULT 0,
    score             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS sea_round_stats (
    chat_id           INTEGER NOT NULL,
    round_no          INTEGER NOT NULL,
    user_id           INTEGER NOT NULL,
    username          TEXT,
    full_name         TEXT,
    games_played      INTEGER NOT NULL DEFAULT 0,
    orders_completed  INTEGER NOT NULL DEFAULT 0,
    challenges_won    INTEGER NOT NULL DEFAULT 0,
    challenges_failed INTEGER NOT NULL DEFAULT 0,
    moves_used        INTEGER NOT NULL DEFAULT 0,
    island_travels    INTEGER NOT NULL DEFAULT 0,
    score             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, round_no, user_id)
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS sea_events (
    chat_id          BIGINT PRIMARY KEY,
    status           TEXT NOT NULL,
    current_round    INTEGER NOT NULL DEFAULT 0,
    recruit_round    INTEGER NOT NULL DEFAULT 0,
    recruit_deadline DOUBLE PRECISION,
    requests         TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS sea_registrations (
    chat_id       BIGINT NOT NULL,
    round_no      INTEGER NOT NULL,
    user_id       BIGINT NOT NULL,
    username      TEXT,
    full_name     TEXT,
    registered_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (chat_id, round_no, user_id)
);

CREATE TABLE IF NOT EXISTS sea_sessions (
    chat_id   BIGINT NOT NULL,
    user_id   BIGINT NOT NULL,
    round_no  INTEGER NOT NULL,
    status    TEXT NOT NULL,
    state     TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS sea_player_stats (
    chat_id           BIGINT NOT NULL,
    user_id           BIGINT NOT NULL,
    username          TEXT,
    full_name         TEXT,
    games_played      BIGINT NOT NULL DEFAULT 0,
    orders_completed  BIGINT NOT NULL DEFAULT 0,
    challenges_won    BIGINT NOT NULL DEFAULT 0,
    challenges_failed BIGINT NOT NULL DEFAULT 0,
    moves_used        BIGINT NOT NULL DEFAULT 0,
    island_travels    BIGINT NOT NULL DEFAULT 0,
    score             BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS sea_round_stats (
    chat_id           BIGINT NOT NULL,
    round_no          INTEGER NOT NULL,
    user_id           BIGINT NOT NULL,
    username          TEXT,
    full_name         TEXT,
    games_played      BIGINT NOT NULL DEFAULT 0,
    orders_completed  BIGINT NOT NULL DEFAULT 0,
    challenges_won    BIGINT NOT NULL DEFAULT 0,
    challenges_failed BIGINT NOT NULL DEFAULT 0,
    moves_used        BIGINT NOT NULL DEFAULT 0,
    island_travels    BIGINT NOT NULL DEFAULT 0,
    score             BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, round_no, user_id)
);
"""

_STAT_COLUMNS = (
    "games_played",
    "orders_completed",
    "challenges_won",
    "challenges_failed",
    "moves_used",
    "island_travels",
    "score",
)


def _delta_values(delta: StatsDelta) -> tuple[int, ...]:
    return (
        delta.games_played,
        delta.orders_completed,
        delta.challenges_won,
        delta.challenges_failed,
        delta.moves_used,
        delta.island_travels,
        delta.score,
    )


class SeaStore(ABC):
    """Абстрактный интерфейс хранилища события."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # ----- событие -----
    @abstractmethod
    async def get_event(self, chat_id: int) -> EventState | None: ...

    @abstractmethod
    async def save_event(self, event: EventState) -> None: ...

    @abstractmethod
    async def reset_event(self, chat_id: int) -> None: ...

    # ----- набор игроков -----
    @abstractmethod
    async def add_registration(
        self,
        chat_id: int,
        round_no: int,
        user_id: int,
        username: str | None,
        full_name: str | None,
        registered_at: float,
    ) -> bool: ...

    @abstractmethod
    async def count_registrations(self, chat_id: int, round_no: int) -> int: ...

    @abstractmethod
    async def is_registered(self, chat_id: int, round_no: int, user_id: int) -> bool: ...

    @abstractmethod
    async def remove_registration(self, chat_id: int, round_no: int, user_id: int) -> bool: ...

    @abstractmethod
    async def clear_registrations(self, chat_id: int, round_no: int) -> None: ...

    @abstractmethod
    async def list_registrations(
        self, chat_id: int, round_no: int
    ) -> list[tuple[int, str | None, str | None]]: ...

    # ----- сессии -----
    @abstractmethod
    async def get_session(self, chat_id: int, user_id: int) -> Session | None: ...

    @abstractmethod
    async def save_session(self, session: Session) -> None: ...

    @abstractmethod
    async def delete_session(self, chat_id: int, user_id: int) -> None: ...

    # ----- статистика -----
    @abstractmethod
    async def bump_stats(
        self,
        chat_id: int,
        round_no: int,
        user_id: int,
        username: str | None,
        full_name: str | None,
        delta: StatsDelta,
    ) -> None: ...

    @abstractmethod
    async def player_stats(self, chat_id: int) -> list[PlayerStats]: ...

    @abstractmethod
    async def round_stats(self, chat_id: int, round_no: int) -> list[PlayerStats]: ...


# ---------- SQLite ----------


class SqliteSeaStore(SeaStore):
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SQLITE_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def _c(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("SeaStore is not connected; call connect() first.")
        return self._conn

    async def get_event(self, chat_id: int) -> EventState | None:
        async with self._c.execute("SELECT * FROM sea_events WHERE chat_id = ?", (chat_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return EventState(
            chat_id=int(row["chat_id"]),
            status=EventStatus(row["status"]),
            current_round=int(row["current_round"]),
            recruit_round=int(row["recruit_round"]),
            recruit_deadline=(
                float(row["recruit_deadline"]) if row["recruit_deadline"] is not None else None
            ),
            requests=EventState.requests_from_dicts(json.loads(row["requests"])),
        )

    async def save_event(self, event: EventState) -> None:
        await self._c.execute(
            """
            INSERT INTO sea_events
                (chat_id, status, current_round, recruit_round, recruit_deadline, requests)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                status = excluded.status,
                current_round = excluded.current_round,
                recruit_round = excluded.recruit_round,
                recruit_deadline = excluded.recruit_deadline,
                requests = excluded.requests
            """,
            (
                event.chat_id,
                event.status.value,
                event.current_round,
                event.recruit_round,
                event.recruit_deadline,
                json.dumps(event.requests_to_dicts(), ensure_ascii=False),
            ),
        )
        await self._c.commit()

    async def reset_event(self, chat_id: int) -> None:
        for table in (
            "sea_events",
            "sea_registrations",
            "sea_sessions",
            "sea_player_stats",
            "sea_round_stats",
        ):
            await self._c.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
        await self._c.commit()

    async def add_registration(
        self,
        chat_id: int,
        round_no: int,
        user_id: int,
        username: str | None,
        full_name: str | None,
        registered_at: float,
    ) -> bool:
        cur = await self._c.execute(
            """
            INSERT INTO sea_registrations
                (chat_id, round_no, user_id, username, full_name, registered_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, round_no, user_id) DO NOTHING
            """,
            (chat_id, round_no, user_id, username, full_name, registered_at),
        )
        await self._c.commit()
        return cur.rowcount > 0

    async def count_registrations(self, chat_id: int, round_no: int) -> int:
        async with self._c.execute(
            "SELECT COUNT(*) AS n FROM sea_registrations WHERE chat_id = ? AND round_no = ?",
            (chat_id, round_no),
        ) as cur:
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def is_registered(self, chat_id: int, round_no: int, user_id: int) -> bool:
        async with self._c.execute(
            """
            SELECT 1 FROM sea_registrations
            WHERE chat_id = ? AND round_no = ? AND user_id = ?
            """,
            (chat_id, round_no, user_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def remove_registration(self, chat_id: int, round_no: int, user_id: int) -> bool:
        cur = await self._c.execute(
            """
            DELETE FROM sea_registrations
            WHERE chat_id = ? AND round_no = ? AND user_id = ?
            """,
            (chat_id, round_no, user_id),
        )
        await self._c.commit()
        return cur.rowcount > 0

    async def clear_registrations(self, chat_id: int, round_no: int) -> None:
        await self._c.execute(
            "DELETE FROM sea_registrations WHERE chat_id = ? AND round_no = ?",
            (chat_id, round_no),
        )
        await self._c.commit()

    async def list_registrations(
        self, chat_id: int, round_no: int
    ) -> list[tuple[int, str | None, str | None]]:
        async with self._c.execute(
            """
            SELECT user_id, username, full_name FROM sea_registrations
            WHERE chat_id = ? AND round_no = ?
            ORDER BY registered_at ASC
            """,
            (chat_id, round_no),
        ) as cur:
            rows = await cur.fetchall()
        return [(int(r["user_id"]), r["username"], r["full_name"]) for r in rows]

    async def get_session(self, chat_id: int, user_id: int) -> Session | None:
        async with self._c.execute(
            "SELECT state FROM sea_sessions WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Session.from_dict(json.loads(row["state"]))

    async def save_session(self, session: Session) -> None:
        await self._c.execute(
            """
            INSERT INTO sea_sessions (chat_id, user_id, round_no, status, state)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                round_no = excluded.round_no,
                status = excluded.status,
                state = excluded.state
            """,
            (
                session.chat_id,
                session.user_id,
                session.round_no,
                session.status.value,
                json.dumps(session.to_dict(), ensure_ascii=False),
            ),
        )
        await self._c.commit()

    async def delete_session(self, chat_id: int, user_id: int) -> None:
        await self._c.execute(
            "DELETE FROM sea_sessions WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        await self._c.commit()

    async def _bump_one(
        self,
        table: str,
        keys: tuple[tuple[str, int], ...],
        username: str | None,
        full_name: str | None,
        delta: StatsDelta,
    ) -> None:
        key_cols = [k for k, _ in keys]
        key_vals = [v for _, v in keys]
        all_cols = [*key_cols, "username", "full_name", *_STAT_COLUMNS]
        placeholders = ", ".join("?" for _ in all_cols)
        update_stats = ", ".join(f"{col} = {table}.{col} + excluded.{col}" for col in _STAT_COLUMNS)
        sql = f"""
            INSERT INTO {table}
                ({", ".join(key_cols)}, username, full_name, {", ".join(_STAT_COLUMNS)})
            VALUES ({placeholders})
            ON CONFLICT({", ".join(key_cols)}) DO UPDATE SET
                username = COALESCE(excluded.username, {table}.username),
                full_name = COALESCE(excluded.full_name, {table}.full_name),
                {update_stats}
        """
        await self._c.execute(sql, (*key_vals, username, full_name, *_delta_values(delta)))

    async def bump_stats(
        self,
        chat_id: int,
        round_no: int,
        user_id: int,
        username: str | None,
        full_name: str | None,
        delta: StatsDelta,
    ) -> None:
        await self._bump_one(
            "sea_player_stats",
            (("chat_id", chat_id), ("user_id", user_id)),
            username,
            full_name,
            delta,
        )
        await self._bump_one(
            "sea_round_stats",
            (("chat_id", chat_id), ("round_no", round_no), ("user_id", user_id)),
            username,
            full_name,
            delta,
        )
        await self._c.commit()

    async def player_stats(self, chat_id: int) -> list[PlayerStats]:
        async with self._c.execute(
            "SELECT * FROM sea_player_stats WHERE chat_id = ? ORDER BY score DESC, user_id ASC",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_stats(r) for r in rows]

    async def round_stats(self, chat_id: int, round_no: int) -> list[PlayerStats]:
        async with self._c.execute(
            """
            SELECT * FROM sea_round_stats
            WHERE chat_id = ? AND round_no = ?
            ORDER BY score DESC, user_id ASC
            """,
            (chat_id, round_no),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_stats(r) for r in rows]


def _row_to_stats(row: aiosqlite.Row | asyncpg.Record) -> PlayerStats:
    return PlayerStats(
        user_id=int(row["user_id"]),
        username=row["username"],
        full_name=row["full_name"],
        games_played=int(row["games_played"]),
        orders_completed=int(row["orders_completed"]),
        challenges_won=int(row["challenges_won"]),
        challenges_failed=int(row["challenges_failed"]),
        moves_used=int(row["moves_used"]),
        island_travels=int(row["island_travels"]),
        score=int(row["score"]),
    )


# ---------- PostgreSQL ----------


class PostgresSeaStore(SeaStore):
    def __init__(self, dsn: str) -> None:
        self._dsn = _normalize_postgres_dsn(dsn)
        self._ssl_required = _postgres_ssl_required(dsn)
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        kwargs: dict[str, Any] = {"min_size": 1, "max_size": 5, "command_timeout": 30.0}
        if self._ssl_required:
            kwargs["ssl"] = "require"
        self._pool = await asyncpg.create_pool(self._dsn, **kwargs)
        async with self._pool.acquire() as conn:
            await conn.execute(POSTGRES_SCHEMA)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def _p(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("SeaStore is not connected; call connect() first.")
        return self._pool

    async def get_event(self, chat_id: int) -> EventState | None:
        row = await self._p.fetchrow("SELECT * FROM sea_events WHERE chat_id = $1", chat_id)
        if row is None:
            return None
        return EventState(
            chat_id=int(row["chat_id"]),
            status=EventStatus(row["status"]),
            current_round=int(row["current_round"]),
            recruit_round=int(row["recruit_round"]),
            recruit_deadline=(
                float(row["recruit_deadline"]) if row["recruit_deadline"] is not None else None
            ),
            requests=EventState.requests_from_dicts(json.loads(row["requests"])),
        )

    async def save_event(self, event: EventState) -> None:
        await self._p.execute(
            """
            INSERT INTO sea_events
                (chat_id, status, current_round, recruit_round, recruit_deadline, requests)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (chat_id) DO UPDATE SET
                status = EXCLUDED.status,
                current_round = EXCLUDED.current_round,
                recruit_round = EXCLUDED.recruit_round,
                recruit_deadline = EXCLUDED.recruit_deadline,
                requests = EXCLUDED.requests
            """,
            event.chat_id,
            event.status.value,
            event.current_round,
            event.recruit_round,
            event.recruit_deadline,
            json.dumps(event.requests_to_dicts(), ensure_ascii=False),
        )

    async def reset_event(self, chat_id: int) -> None:
        async with self._p.acquire() as conn, conn.transaction():
            for table in (
                "sea_events",
                "sea_registrations",
                "sea_sessions",
                "sea_player_stats",
                "sea_round_stats",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE chat_id = $1", chat_id)

    async def add_registration(
        self,
        chat_id: int,
        round_no: int,
        user_id: int,
        username: str | None,
        full_name: str | None,
        registered_at: float,
    ) -> bool:
        status = await self._p.execute(
            """
            INSERT INTO sea_registrations
                (chat_id, round_no, user_id, username, full_name, registered_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (chat_id, round_no, user_id) DO NOTHING
            """,
            chat_id,
            round_no,
            user_id,
            username,
            full_name,
            registered_at,
        )
        return _affected_rows(status) > 0

    async def count_registrations(self, chat_id: int, round_no: int) -> int:
        value = await self._p.fetchval(
            "SELECT COUNT(*) FROM sea_registrations WHERE chat_id = $1 AND round_no = $2",
            chat_id,
            round_no,
        )
        return int(value or 0)

    async def is_registered(self, chat_id: int, round_no: int, user_id: int) -> bool:
        row = await self._p.fetchrow(
            """
            SELECT 1 FROM sea_registrations
            WHERE chat_id = $1 AND round_no = $2 AND user_id = $3
            """,
            chat_id,
            round_no,
            user_id,
        )
        return row is not None

    async def remove_registration(self, chat_id: int, round_no: int, user_id: int) -> bool:
        status = await self._p.execute(
            """
            DELETE FROM sea_registrations
            WHERE chat_id = $1 AND round_no = $2 AND user_id = $3
            """,
            chat_id,
            round_no,
            user_id,
        )
        return _affected_rows(status) > 0

    async def clear_registrations(self, chat_id: int, round_no: int) -> None:
        await self._p.execute(
            "DELETE FROM sea_registrations WHERE chat_id = $1 AND round_no = $2",
            chat_id,
            round_no,
        )

    async def list_registrations(
        self, chat_id: int, round_no: int
    ) -> list[tuple[int, str | None, str | None]]:
        rows = await self._p.fetch(
            """
            SELECT user_id, username, full_name FROM sea_registrations
            WHERE chat_id = $1 AND round_no = $2
            ORDER BY registered_at ASC
            """,
            chat_id,
            round_no,
        )
        return [(int(r["user_id"]), r["username"], r["full_name"]) for r in rows]

    async def get_session(self, chat_id: int, user_id: int) -> Session | None:
        row = await self._p.fetchrow(
            "SELECT state FROM sea_sessions WHERE chat_id = $1 AND user_id = $2",
            chat_id,
            user_id,
        )
        if row is None:
            return None
        return Session.from_dict(json.loads(row["state"]))

    async def save_session(self, session: Session) -> None:
        await self._p.execute(
            """
            INSERT INTO sea_sessions (chat_id, user_id, round_no, status, state)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (chat_id, user_id) DO UPDATE SET
                round_no = EXCLUDED.round_no,
                status = EXCLUDED.status,
                state = EXCLUDED.state
            """,
            session.chat_id,
            session.user_id,
            session.round_no,
            session.status.value,
            json.dumps(session.to_dict(), ensure_ascii=False),
        )

    async def delete_session(self, chat_id: int, user_id: int) -> None:
        await self._p.execute(
            "DELETE FROM sea_sessions WHERE chat_id = $1 AND user_id = $2",
            chat_id,
            user_id,
        )

    async def bump_stats(
        self,
        chat_id: int,
        round_no: int,
        user_id: int,
        username: str | None,
        full_name: str | None,
        delta: StatsDelta,
    ) -> None:
        values = _delta_values(delta)
        async with self._p.acquire() as conn, conn.transaction():
            await conn.execute(
                f"""
                INSERT INTO sea_player_stats
                    (chat_id, user_id, username, full_name, {", ".join(_STAT_COLUMNS)})
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET
                    username = COALESCE(EXCLUDED.username, sea_player_stats.username),
                    full_name = COALESCE(EXCLUDED.full_name, sea_player_stats.full_name),
                    {", ".join(f"{c} = sea_player_stats.{c} + EXCLUDED.{c}" for c in _STAT_COLUMNS)}
                """,
                chat_id,
                user_id,
                username,
                full_name,
                *values,
            )
            await conn.execute(
                f"""
                INSERT INTO sea_round_stats
                    (chat_id, round_no, user_id, username, full_name, {", ".join(_STAT_COLUMNS)})
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (chat_id, round_no, user_id) DO UPDATE SET
                    username = COALESCE(EXCLUDED.username, sea_round_stats.username),
                    full_name = COALESCE(EXCLUDED.full_name, sea_round_stats.full_name),
                    {", ".join(f"{c} = sea_round_stats.{c} + EXCLUDED.{c}" for c in _STAT_COLUMNS)}
                """,
                chat_id,
                round_no,
                user_id,
                username,
                full_name,
                *values,
            )

    async def player_stats(self, chat_id: int) -> list[PlayerStats]:
        rows = await self._p.fetch(
            "SELECT * FROM sea_player_stats WHERE chat_id = $1 ORDER BY score DESC, user_id ASC",
            chat_id,
        )
        return [_row_to_stats(r) for r in rows]

    async def round_stats(self, chat_id: int, round_no: int) -> list[PlayerStats]:
        rows = await self._p.fetch(
            """
            SELECT * FROM sea_round_stats
            WHERE chat_id = $1 AND round_no = $2
            ORDER BY score DESC, user_id ASC
            """,
            chat_id,
            round_no,
        )
        return [_row_to_stats(r) for r in rows]


def create_sea_store(url_or_path: str) -> SeaStore:
    """Построить нужный бэкенд хранилища по строке подключения."""
    if url_or_path.startswith(("postgres://", "postgresql://")):
        return PostgresSeaStore(url_or_path)
    return SqliteSeaStore(url_or_path)
