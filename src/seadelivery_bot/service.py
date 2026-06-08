"""Логика жизненного цикла события: набор, раунды, финализация сессий.

Тонкая прослойка между обработчиками aiogram и хранилищем/движком. Не зависит от
aiogram, поэтому легко тестируется.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import StrEnum

from . import content, engine
from .models import EventState, EventStatus, Session, SessionStatus
from .store import SeaStore


async def get_event_or_default(store: SeaStore, chat_id: int) -> EventState:
    event = await store.get_event(chat_id)
    if event is None:
        event = EventState(chat_id=chat_id)
    return event


# ---------- набор игроков ----------


class OpenOutcome(StrEnum):
    OK = "ok"
    FINISHED = "finished"  # все раунды сыграны
    BUSY = "busy"  # раунд уже идёт


@dataclass
class OpenResult:
    outcome: OpenOutcome
    event: EventState


async def open_recruitment(store: SeaStore, chat_id: int, now: float | None = None) -> OpenResult:
    """Открыть набор на следующий раунд."""
    now = time.time() if now is None else now
    event = await get_event_or_default(store, chat_id)
    if event.current_round >= content.TOTAL_ROUNDS:
        return OpenResult(OpenOutcome.FINISHED, event)
    if event.status is EventStatus.ROUND_ACTIVE:
        return OpenResult(OpenOutcome.BUSY, event)

    event.status = EventStatus.RECRUITING
    event.recruit_round = event.current_round + 1
    event.recruit_deadline = now + content.RECRUITMENT_SECONDS
    event.requests = []
    # Каждый набор начинается с чистого списка: убираем регистрации этого раунда,
    # если они остались от прошлого набора/события (иначе игроки «уже записаны»).
    await store.clear_registrations(chat_id, event.recruit_round)
    await store.save_event(event)
    return OpenResult(OpenOutcome.OK, event)


def recruitment_open(event: EventState, registered_count: int, now: float | None = None) -> bool:
    """Открыт ли ещё набор (не вышло время и есть свободные места)."""
    if event.status is not EventStatus.RECRUITING:
        return False
    if registered_count >= content.MAX_PLAYERS_PER_ROUND:
        return False
    now = time.time() if now is None else now
    if event.recruit_deadline is not None and now > event.recruit_deadline:
        return False
    return True


class JoinOutcome(StrEnum):
    OK = "ok"
    CLOSED = "closed"  # набор не идёт / время вышло
    FULL = "full"
    ALREADY = "already"


async def join_round(
    store: SeaStore,
    chat_id: int,
    user_id: int,
    username: str | None,
    full_name: str | None,
    now: float | None = None,
) -> tuple[JoinOutcome, EventState, int]:
    now = time.time() if now is None else now
    event = await get_event_or_default(store, chat_id)
    if event.status is not EventStatus.RECRUITING:
        count = await store.count_registrations(chat_id, event.recruit_round)
        return JoinOutcome.CLOSED, event, count

    round_no = event.recruit_round
    if await store.is_registered(chat_id, round_no, user_id):
        count = await store.count_registrations(chat_id, round_no)
        return JoinOutcome.ALREADY, event, count

    count = await store.count_registrations(chat_id, round_no)
    if count >= content.MAX_PLAYERS_PER_ROUND:
        return JoinOutcome.FULL, event, count
    if event.recruit_deadline is not None and now > event.recruit_deadline:
        return JoinOutcome.CLOSED, event, count

    await store.add_registration(chat_id, round_no, user_id, username, full_name, now)
    count = await store.count_registrations(chat_id, round_no)
    return JoinOutcome.OK, event, count


class LeaveOutcome(StrEnum):
    OK = "ok"
    NOT_REGISTERED = "not_registered"  # игрок и так не записан
    CLOSED = "closed"  # набор не идёт (раунд уже запущен/не открыт)


async def leave_round(
    store: SeaStore,
    chat_id: int,
    user_id: int,
) -> tuple[LeaveOutcome, EventState, int]:
    """Снять игрока с набора текущего раунда, пока набор ещё открыт."""
    event = await get_event_or_default(store, chat_id)
    round_no = event.recruit_round
    if event.status is not EventStatus.RECRUITING:
        count = await store.count_registrations(chat_id, round_no)
        return LeaveOutcome.CLOSED, event, count

    removed = await store.remove_registration(chat_id, round_no, user_id)
    count = await store.count_registrations(chat_id, round_no)
    if not removed:
        return LeaveOutcome.NOT_REGISTERED, event, count
    return LeaveOutcome.OK, event, count


# ---------- раунды ----------


class RoundOutcome(StrEnum):
    OK = "ok"
    NO_RECRUITMENT = "no_recruitment"  # набор не открыт
    FINISHED = "finished"


@dataclass
class RoundResult:
    outcome: RoundOutcome
    event: EventState


async def start_round(
    store: SeaStore, chat_id: int, rng: random.Random | None = None
) -> RoundResult:
    """Запустить раунд, на который шёл набор, и опубликовать запросы торговцев."""
    rng = random.Random() if rng is None else rng
    event = await get_event_or_default(store, chat_id)
    if event.recruit_round <= event.current_round or event.recruit_round == 0:
        return RoundResult(RoundOutcome.NO_RECRUITMENT, event)
    if event.recruit_round > content.TOTAL_ROUNDS:
        return RoundResult(RoundOutcome.FINISHED, event)

    event.current_round = event.recruit_round
    event.status = EventStatus.ROUND_ACTIVE
    event.requests = engine.pick_round_requests(rng)
    event.recruit_deadline = None
    await store.save_event(event)
    return RoundResult(RoundOutcome.OK, event)


class GotoOutcome(StrEnum):
    OK = "ok"
    INVALID = "invalid"  # номер раунда вне диапазона 1..TOTAL_ROUNDS
    NOTHING = "nothing"  # откатывать нечего (раунды ещё не запускались)


@dataclass
class GotoResult:
    outcome: GotoOutcome
    event: EventState
    target: int = 0


async def goto_round(store: SeaStore, chat_id: int, target: int) -> GotoResult:
    """Перевести событие к раунду ``target`` (1..TOTAL_ROUNDS), не трогая статистику.

    Сдвигается только указатель раунда: выставляется ``current_round = target - 1`` и
    статус ``IDLE``, чтобы администратор мог заново открыть набор именно на ``target``.
    Сыгранная статистика, регистрации и сессии не удаляются.
    """
    event = await get_event_or_default(store, chat_id)
    if target < 1 or target > content.TOTAL_ROUNDS:
        return GotoResult(GotoOutcome.INVALID, event, target)

    event.current_round = target - 1
    event.recruit_round = target - 1
    event.status = EventStatus.IDLE
    event.requests = []
    event.recruit_deadline = None
    await store.save_event(event)
    return GotoResult(GotoOutcome.OK, event, target)


async def rollback_round(store: SeaStore, chat_id: int) -> GotoResult:
    """Откатиться на один раунд назад: переоткрыть последний запущенный раунд."""
    event = await get_event_or_default(store, chat_id)
    if event.current_round < 1:
        return GotoResult(GotoOutcome.NOTHING, event, 0)
    return await goto_round(store, chat_id, event.current_round)


async def end_round(store: SeaStore, chat_id: int) -> RoundResult:
    """Завершить текущий раунд (после пятого — событие финализируется)."""
    event = await get_event_or_default(store, chat_id)
    if event.status is not EventStatus.ROUND_ACTIVE:
        return RoundResult(RoundOutcome.NO_RECRUITMENT, event)

    event.requests = []
    if event.current_round >= content.TOTAL_ROUNDS:
        event.status = EventStatus.FINISHED
    else:
        event.status = EventStatus.IDLE
    await store.save_event(event)
    return RoundResult(RoundOutcome.OK, event)


# ---------- сессии ----------


class StartSessionOutcome(StrEnum):
    NEW = "new"
    RESUMED = "resumed"
    ALREADY_PLAYED = "already_played"
    NOT_REGISTERED = "not_registered"
    NO_ACTIVE_ROUND = "no_active_round"


@dataclass
class StartSessionResult:
    outcome: StartSessionOutcome
    session: Session | None
    event: EventState


async def start_or_resume_session(
    store: SeaStore, chat_id: int, user_id: int
) -> StartSessionResult:
    event = await get_event_or_default(store, chat_id)
    if event.status is not EventStatus.ROUND_ACTIVE:
        return StartSessionResult(StartSessionOutcome.NO_ACTIVE_ROUND, None, event)

    round_no = event.current_round
    existing = await store.get_session(chat_id, user_id)
    if existing is not None and existing.round_no == round_no:
        if existing.status is SessionStatus.ACTIVE:
            return StartSessionResult(StartSessionOutcome.RESUMED, existing, event)
        return StartSessionResult(StartSessionOutcome.ALREADY_PLAYED, existing, event)

    if not await store.is_registered(chat_id, round_no, user_id):
        return StartSessionResult(StartSessionOutcome.NOT_REGISTERED, None, event)

    session = engine.new_session(chat_id, user_id, round_no)
    await store.save_session(session)
    return StartSessionResult(StartSessionOutcome.NEW, session, event)


async def finalize_session(
    store: SeaStore,
    session: Session,
    username: str | None,
    full_name: str | None,
) -> None:
    """Завершить сессию: применить статистику и сохранить финальное состояние."""
    if session.status is SessionStatus.FINISHED:
        return
    delta = engine.finish_session(session)
    await store.bump_stats(
        session.chat_id, session.round_no, session.user_id, username, full_name, delta
    )
    await store.save_session(session)
