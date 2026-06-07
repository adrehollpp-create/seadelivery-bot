"""Тесты жизненного цикла события: набор, раунды, сессии."""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from seadelivery_bot import content, service
from seadelivery_bot.models import EventStatus
from seadelivery_bot.store import SeaStore, create_sea_store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SeaStore]:
    st = create_sea_store(str(tmp_path / "sea.db"))
    await st.connect()
    try:
        yield st
    finally:
        await st.close()


async def test_open_recruitment_sets_next_round(store: SeaStore) -> None:
    result = await service.open_recruitment(store, chat_id=1, now=1000.0)
    assert result.outcome is service.OpenOutcome.OK
    assert result.event.status is EventStatus.RECRUITING
    assert result.event.recruit_round == 1
    assert result.event.recruit_deadline == 1000.0 + content.RECRUITMENT_SECONDS


async def test_join_flow_dup_full_and_closed(store: SeaStore) -> None:
    await service.open_recruitment(store, 1, now=0.0)

    outcome, _, count = await service.join_round(store, 1, 10, "alice", "Alice", now=1.0)
    assert outcome is service.JoinOutcome.OK
    assert count == 1

    dup, _, _ = await service.join_round(store, 1, 10, "alice", "Alice", now=2.0)
    assert dup is service.JoinOutcome.ALREADY

    # Заполняем до лимита.
    for uid in range(100, 100 + content.MAX_PLAYERS_PER_ROUND - 1):
        out, _, _ = await service.join_round(store, 1, uid, f"u{uid}", None, now=3.0)
        assert out is service.JoinOutcome.OK
    full, _, _ = await service.join_round(store, 1, 999, "late", None, now=4.0)
    assert full is service.JoinOutcome.FULL


async def test_join_closed_after_deadline(store: SeaStore) -> None:
    res = await service.open_recruitment(store, 1, now=0.0)
    deadline = res.event.recruit_deadline
    assert deadline is not None
    outcome, _, _ = await service.join_round(store, 1, 10, "a", "A", now=deadline + 1.0)
    assert outcome is service.JoinOutcome.CLOSED


async def test_join_closed_when_not_recruiting(store: SeaStore) -> None:
    outcome, _, _ = await service.join_round(store, 1, 10, "a", "A", now=1.0)
    assert outcome is service.JoinOutcome.CLOSED


async def test_start_and_end_round(store: SeaStore) -> None:
    await service.open_recruitment(store, 1, now=0.0)
    started = await service.start_round(store, 1, random.Random(0))
    assert started.outcome is service.RoundOutcome.OK
    assert started.event.status is EventStatus.ROUND_ACTIVE
    assert started.event.current_round == 1
    assert len(started.event.requests) == content.REQUESTS_PER_ROUND

    # Повторный старт без нового набора запрещён.
    again = await service.start_round(store, 1, random.Random(0))
    assert again.outcome is service.RoundOutcome.NO_RECRUITMENT

    ended = await service.end_round(store, 1)
    assert ended.outcome is service.RoundOutcome.OK
    assert ended.event.status is EventStatus.IDLE
    assert ended.event.requests == []


async def test_event_finishes_after_last_round(store: SeaStore) -> None:
    # Пройдём все раунды до последнего.
    for _ in range(content.TOTAL_ROUNDS):
        await service.open_recruitment(store, 1, now=0.0)
        await service.start_round(store, 1, random.Random(1))
        ended = await service.end_round(store, 1)
    assert ended.event.current_round == content.TOTAL_ROUNDS
    assert ended.event.status is EventStatus.FINISHED

    # После финала набор открыть нельзя.
    reopened = await service.open_recruitment(store, 1, now=0.0)
    assert reopened.outcome is service.OpenOutcome.FINISHED


async def test_session_lifecycle(store: SeaStore) -> None:
    await service.open_recruitment(store, 1, now=0.0)
    await service.join_round(store, 1, 10, "alice", "Alice", now=1.0)
    await service.start_round(store, 1, random.Random(0))

    # Незаписанный игрок не может играть.
    not_reg = await service.start_or_resume_session(store, 1, 999)
    assert not_reg.outcome is service.StartSessionOutcome.NOT_REGISTERED

    new = await service.start_or_resume_session(store, 1, 10)
    assert new.outcome is service.StartSessionOutcome.NEW
    assert new.session is not None

    resumed = await service.start_or_resume_session(store, 1, 10)
    assert resumed.outcome is service.StartSessionOutcome.RESUMED

    # Завершаем — статистика обновляется, сессия становится FINISHED.
    session = resumed.session
    assert session is not None
    session.orders_completed = 1
    session.score = 100
    await service.finalize_session(store, session, "alice", "Alice")

    stats = await store.player_stats(1)
    assert stats and stats[0].score == 100 and stats[0].games_played == 1

    replayed = await service.start_or_resume_session(store, 1, 10)
    assert replayed.outcome is service.StartSessionOutcome.ALREADY_PLAYED


async def test_session_blocked_without_active_round(store: SeaStore) -> None:
    result = await service.start_or_resume_session(store, 1, 10)
    assert result.outcome is service.StartSessionOutcome.NO_ACTIVE_ROUND


async def test_goto_round_moves_pointer_and_reopens(store: SeaStore) -> None:
    # Сыграем 3 раунда.
    for _ in range(3):
        await service.open_recruitment(store, 1, now=0.0)
        await service.start_round(store, 1, random.Random(1))
        await service.end_round(store, 1)
    event = await service.get_event_or_default(store, 1)
    assert event.current_round == 3

    # Вернёмся к раунду 2.
    res = await service.goto_round(store, 1, 2)
    assert res.outcome is service.GotoOutcome.OK
    assert res.event.current_round == 1
    assert res.event.status is EventStatus.IDLE

    # Открытие набора теперь снова даёт раунд 2.
    opened = await service.open_recruitment(store, 1, now=0.0)
    assert opened.outcome is service.OpenOutcome.OK
    assert opened.event.recruit_round == 2


async def test_goto_round_keeps_stats(store: SeaStore) -> None:
    await service.open_recruitment(store, 1, now=0.0)
    await service.join_round(store, 1, 10, "alice", "Alice", now=1.0)
    await service.start_round(store, 1, random.Random(0))
    new = await service.start_or_resume_session(store, 1, 10)
    assert new.session is not None
    new.session.score = 50
    await service.finalize_session(store, new.session, "alice", "Alice")

    await service.goto_round(store, 1, 1)
    # Статистика не удаляется при откате.
    stats = await store.player_stats(1)
    assert stats and stats[0].score == 50


async def test_goto_round_invalid(store: SeaStore) -> None:
    too_big = await service.goto_round(store, 1, content.TOTAL_ROUNDS + 1)
    assert too_big.outcome is service.GotoOutcome.INVALID
    zero = await service.goto_round(store, 1, 0)
    assert zero.outcome is service.GotoOutcome.INVALID


async def test_rollback_round(store: SeaStore) -> None:
    nothing = await service.rollback_round(store, 1)
    assert nothing.outcome is service.GotoOutcome.NOTHING

    for _ in range(2):
        await service.open_recruitment(store, 1, now=0.0)
        await service.start_round(store, 1, random.Random(1))
        await service.end_round(store, 1)

    res = await service.rollback_round(store, 1)
    assert res.outcome is service.GotoOutcome.OK
    assert res.target == 2
    # После отката открытие набора снова даёт раунд 2 (переиграть последний).
    opened = await service.open_recruitment(store, 1, now=0.0)
    assert opened.event.recruit_round == 2
