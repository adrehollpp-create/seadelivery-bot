"""Тесты хранилища события «Морская доставка» (SQLite-бэкенд)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from seadelivery_bot import engine
from seadelivery_bot.models import (
    ActiveChallenge,
    ChallengeKind,
    EventState,
    EventStatus,
    MinesState,
    Order,
    SessionStatus,
    StatsDelta,
)
from seadelivery_bot.store import SeaStore, create_sea_store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SeaStore]:
    st = create_sea_store(str(tmp_path / "sea.db"))
    await st.connect()
    try:
        yield st
    finally:
        await st.close()


async def test_event_round_trip(store: SeaStore) -> None:
    assert await store.get_event(100) is None
    event = EventState(
        chat_id=100,
        status=EventStatus.ROUND_ACTIVE,
        current_round=2,
        recruit_round=2,
        recruit_deadline=None,
        requests=[
            Order(
                merchant="Марта",
                owned_item="Печенье Сольмма",
                needed_item="Бублик из Мёда",
                source_island="Гавань Туманов",
            )
        ],
    )
    await store.save_event(event)
    loaded = await store.get_event(100)
    assert loaded is not None
    assert loaded.status is EventStatus.ROUND_ACTIVE
    assert loaded.current_round == 2
    assert len(loaded.requests) == 1
    assert loaded.requests[0].merchant == "Марта"


async def test_registration_dedup_and_listing(store: SeaStore) -> None:
    assert await store.add_registration(1, 1, 10, "alice", "Alice", 1.0) is True
    assert await store.add_registration(1, 1, 10, "alice", "Alice", 2.0) is False
    assert await store.add_registration(1, 1, 11, "bob", "Bob", 3.0) is True
    assert await store.count_registrations(1, 1) == 2
    assert await store.is_registered(1, 1, 10) is True
    assert await store.is_registered(1, 1, 99) is False
    regs = await store.list_registrations(1, 1)
    assert [uid for uid, _, _ in regs] == [10, 11]  # порядок записи


async def test_session_round_trip_with_challenge(store: SeaStore) -> None:
    session = engine.new_session(chat_id=5, user_id=42, round_no=3)
    session.on_route = True
    session.position = 2
    session.score = 130
    session.active_order = Order(
        merchant="Аскен",
        owned_item="Цветок Пастушо",
        needed_item="Соль Медузы",
        source_island="Бухта Медуз",
    )
    session.active_challenge = ActiveChallenge(
        kind=ChallengeKind.MINES, mines=MinesState(safe=[0, 2, 4], picks=[2])
    )
    session.completed_orders = ["Марта"]
    session.bonuses = ["Сокровище +30"]
    await store.save_session(session)

    loaded = await store.get_session(5, 42)
    assert loaded is not None
    assert loaded.score == 130
    assert loaded.position == 2
    assert loaded.active_order is not None
    assert loaded.active_order.merchant == "Аскен"
    assert loaded.active_challenge is not None
    assert loaded.active_challenge.kind is ChallengeKind.MINES
    assert loaded.active_challenge.mines is not None
    assert loaded.active_challenge.mines.picks == [2]
    assert loaded.completed_orders == ["Марта"]

    await store.delete_session(5, 42)
    assert await store.get_session(5, 42) is None


async def test_bump_stats_accumulates_and_orders(store: SeaStore) -> None:
    await store.bump_stats(
        1, 1, 10, "alice", "Alice", StatsDelta(games_played=1, score=100, orders_completed=1)
    )
    await store.bump_stats(1, 1, 10, "alice", "Alice", StatsDelta(games_played=1, score=50))
    await store.bump_stats(1, 1, 11, "bob", "Bob", StatsDelta(games_played=1, score=200))

    players = await store.player_stats(1)
    assert [p.user_id for p in players] == [11, 10]  # сортировка по очкам убыв.
    alice = next(p for p in players if p.user_id == 10)
    assert alice.score == 150
    assert alice.games_played == 2
    assert alice.orders_completed == 1

    round_rows = await store.round_stats(1, 1)
    assert {r.user_id for r in round_rows} == {10, 11}


async def test_bump_stats_separates_rounds(store: SeaStore) -> None:
    await store.bump_stats(1, 1, 10, "a", "A", StatsDelta(games_played=1, score=100))
    await store.bump_stats(1, 2, 10, "a", "A", StatsDelta(games_played=1, score=70))
    assert (await store.round_stats(1, 1))[0].score == 100
    assert (await store.round_stats(1, 2))[0].score == 70
    # Общий агрегат суммирует раунды.
    assert (await store.player_stats(1))[0].score == 170


async def test_reset_event_wipes_chat(store: SeaStore) -> None:
    await store.save_event(EventState(chat_id=1, status=EventStatus.RECRUITING))
    await store.add_registration(1, 1, 10, "a", "A", 1.0)
    await store.bump_stats(1, 1, 10, "a", "A", StatsDelta(games_played=1, score=10))
    session = engine.new_session(1, 10, 1)
    session.status = SessionStatus.FINISHED
    await store.save_session(session)

    await store.reset_event(1)
    assert await store.get_event(1) is None
    assert await store.count_registrations(1, 1) == 0
    assert await store.player_stats(1) == []
    assert await store.get_session(1, 10) is None
