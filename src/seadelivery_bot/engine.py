"""Чистая игровая логика «Морской доставки».

Модуль не зависит от aiogram и БД: он принимает/возвращает датаклассы из
:mod:`.models` и использует инъекцию ГСЧ (:class:`random.Random`), что делает
всю механику детерминированной и легко тестируемой.

Никакого пользовательского текста здесь нет — только структурированные
результаты; рендеринг живёт в :mod:`.ui`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum

from . import content
from .models import (
    ActiveChallenge,
    CellType,
    ChallengeKind,
    FishingState,
    MinesState,
    Order,
    Session,
    SessionStatus,
    SharkState,
    StatsDelta,
)

# ---------- выбор заказов раунда ----------


def pick_round_requests(rng: random.Random) -> list[Order]:
    """Случайно выбрать :data:`REQUESTS_PER_ROUND` торговцев, нуждающихся в помощи."""
    chosen = rng.sample(content.MERCHANT_TABLE, content.REQUESTS_PER_ROUND)
    return [
        Order(
            merchant=m.name,
            owned_item=m.owns,
            needed_item=m.needs,
            source_island=content.source_island_for(m.needs),
        )
        for m in chosen
    ]


# ---------- создание сессии и поля ----------


def new_session(chat_id: int, user_id: int, round_no: int) -> Session:
    """Создать свежую игровую сессию с дефолтными лимитами."""
    return Session(
        chat_id=chat_id,
        user_id=user_id,
        round_no=round_no,
        status=SessionStatus.ACTIVE,
        moves_left=content.MOVES_PER_SESSION,
        travels_used=0,
        challenges_remaining=content.MAX_CHALLENGES_PER_SESSION,
        position=0,
        score=0,
        current_island=content.MAIN_ISLAND,
        board=[],
        on_route=False,
    )


def generate_board(rng: random.Random, challenges_budget: int) -> list[CellType]:
    """Сгенерировать случайный маршрут.

    Первая клетка — старт (пустая), последняя — точка сдачи заказа (пустая).
    Между ними случайно расставляются сокровища и испытания; число испытаний
    ограничено бюджетом сессии (``challenges_budget``), чтобы за всю сессию их
    было не больше :data:`MAX_CHALLENGES_PER_SESSION`.
    """
    length = rng.randint(content.BOARD_MIN_LEN, content.BOARD_MAX_LEN)
    board: list[CellType] = [CellType.EMPTY] * length
    middle = list(range(1, length - 1))
    rng.shuffle(middle)

    challenges_left = max(0, challenges_budget)
    for idx in middle:
        roll = rng.random()
        if roll < 0.30 and challenges_left > 0:
            board[idx] = CellType.CHALLENGE
            challenges_left -= 1
        elif roll < 0.55:
            board[idx] = CellType.TREASURE
        else:
            board[idx] = CellType.EMPTY
    return board


def board_challenge_count(board: list[CellType]) -> int:
    """Сколько клеток-испытаний на маршруте."""
    return sum(1 for cell in board if cell is CellType.CHALLENGE)


# ---------- старт маршрута / перемещение между островами ----------


class StartOutcome(StrEnum):
    OK = "ok"
    NO_MOVES = "no_moves"
    NO_TRAVELS = "no_travels"
    BUSY = "busy"  # уже в пути или идёт испытание
    ALREADY_DONE = "already_done"


@dataclass
class StartResult:
    outcome: StartOutcome
    order: Order | None = None


def start_route(session: Session, order: Order, rng: random.Random) -> StartResult:
    """Отправиться на остров за товаром и проложить маршрут (стоит 1 перемещение).

    Ход за взятие заказа НЕ списывается — ходы тратятся только когда игрок плывёт
    (сообщение с хештегом) и в мини-играх.
    """
    if session.status is not SessionStatus.ACTIVE:
        return StartResult(StartOutcome.BUSY)
    if session.on_route or session.active_challenge is not None:
        return StartResult(StartOutcome.BUSY)
    if order.merchant in session.completed_orders:
        return StartResult(StartOutcome.ALREADY_DONE)
    if session.moves_left <= 0:
        return StartResult(StartOutcome.NO_MOVES)
    if session.travels_used >= content.MAX_ISLAND_TRAVELS:
        return StartResult(StartOutcome.NO_TRAVELS)

    session.travels_used += 1
    session.on_route = True
    session.position = 0
    session.active_order = order
    session.current_island = order.source_island
    session.board = generate_board(rng, session.challenges_remaining)
    return StartResult(StartOutcome.OK, order=order)


# ---------- движение по полю ----------


class SailOutcome(StrEnum):
    EMPTY = "empty"
    TREASURE = "treasure"
    CHALLENGE = "challenge"
    DELIVERED = "delivered"
    BLOCKED = "blocked"  # некорректное действие (нет хода / идёт испытание / не в пути)


@dataclass
class SailResult:
    outcome: SailOutcome
    treasure_gain: int = 0
    delivered_merchant: str | None = None
    reward: int = 0
    challenge_kind: ChallengeKind | None = None


def _make_challenge(rng: random.Random) -> ActiveChallenge:
    kind = ChallengeKind(rng.choice([k.value for k in ChallengeKind]))
    if kind is ChallengeKind.MINES:
        safe = sorted(rng.sample(range(content.MINES_TOTAL_CELLS), content.MINES_SAFE_CELLS))
        return ActiveChallenge(kind=kind, mines=MinesState(safe=safe, picks=[]))
    if kind is ChallengeKind.SHARK:
        attacks = [rng.choice(content.SHARK_DIRECTIONS) for _ in range(content.SHARK_STAGES)]
        return ActiveChallenge(kind=kind, shark=SharkState(attacks=attacks, picks=[]))
    # FISHING: окно реакции «вооружается» обработчиком после случайной задержки.
    return ActiveChallenge(kind=kind, fishing=FishingState(deadline=0.0, armed=False))


def _deliver(session: Session) -> SailResult:
    order = session.active_order
    merchant = order.merchant if order else "?"
    session.score += content.REWARD_PER_ORDER
    session.orders_completed += 1
    if order is not None and order.merchant not in session.completed_orders:
        session.completed_orders.append(order.merchant)
    session.on_route = False
    session.active_order = None
    session.position = 0
    session.board = []
    session.current_island = content.MAIN_ISLAND
    return SailResult(
        SailOutcome.DELIVERED, delivered_merchant=merchant, reward=content.REWARD_PER_ORDER
    )


def _resolve_cell(session: Session, rng: random.Random) -> SailResult:
    """Разрешить эффект клетки, на которой оказался игрок (без расхода хода)."""
    # Достигли конца маршрута — заказ доставлен.
    if session.position >= len(session.board) - 1:
        return _deliver(session)

    cell = session.board[session.position]
    if cell is CellType.TREASURE:
        # Сокровище — разовый бонус очков на этой клетке. Никаких «рывков» вперёд:
        # одно сообщение = ровно один шаг, чтобы движение было предсказуемым.
        session.score += content.TREASURE_BONUS
        session.bonuses.append(f"Сокровище +{content.TREASURE_BONUS}")
        return SailResult(SailOutcome.TREASURE, treasure_gain=content.TREASURE_BONUS)
    if cell is CellType.CHALLENGE:
        session.active_challenge = _make_challenge(rng)
        session.challenges_remaining = max(0, session.challenges_remaining - 1)
        return SailResult(SailOutcome.CHALLENGE, challenge_kind=session.active_challenge.kind)
    return SailResult(SailOutcome.EMPTY)


def sail_forward(session: Session, rng: random.Random) -> SailResult:
    """Проплыть на одну клетку вперёд (стоит 1 ход) и разрешить эффект клетки."""
    if session.status is not SessionStatus.ACTIVE:
        return SailResult(SailOutcome.BLOCKED)
    if not session.on_route or session.active_challenge is not None:
        return SailResult(SailOutcome.BLOCKED)
    if session.moves_left <= 0:
        return SailResult(SailOutcome.BLOCKED)

    session.moves_left -= 1
    session.moves_used += 1
    session.position += 1
    return _resolve_cell(session, rng)


# ---------- мини-игра №1: поиск безопасных клеток ----------


class MinesOutcome(StrEnum):
    SAFE = "safe"  # безопасная клетка, продолжаем
    WIN = "win"  # найдены все безопасные
    BOOM = "boom"  # бомба — провал
    BLOCKED = "blocked"


@dataclass
class MinesResult:
    outcome: MinesOutcome
    safe_found: int = 0
    safe_total: int = content.MINES_SAFE_CELLS
    bomb_index: int | None = None


def mines_pick(session: Session, index: int, rng: random.Random) -> MinesResult:
    ch = session.active_challenge
    if ch is None or ch.kind is not ChallengeKind.MINES or ch.mines is None:
        return MinesResult(MinesOutcome.BLOCKED)
    state = ch.mines
    if index in state.picks or not (0 <= index < content.MINES_TOTAL_CELLS):
        return MinesResult(MinesOutcome.BLOCKED, safe_found=len(state.picks))

    if index not in state.safe:
        _fail_challenge(session)
        return MinesResult(MinesOutcome.BOOM, bomb_index=index)

    state.picks.append(index)
    if len(state.picks) >= content.MINES_SAFE_CELLS:
        _win_challenge(session)
        return MinesResult(MinesOutcome.WIN, safe_found=len(state.picks))
    return MinesResult(MinesOutcome.SAFE, safe_found=len(state.picks))


# ---------- мини-игра №2: рыбалка ----------


def fishing_arm(session: Session, now: float) -> bool:
    """«Вооружить» окно реакции: показать «ЖМИ!» с дедлайном. Возвращает успех."""
    ch = session.active_challenge
    if ch is None or ch.kind is not ChallengeKind.FISHING or ch.fishing is None:
        return False
    ch.fishing.armed = True
    ch.fishing.deadline = now + content.FISHING_REACTION_SECONDS
    return True


class FishingOutcome(StrEnum):
    WIN = "win"
    TOO_EARLY = "too_early"  # нажал до появления «ЖМИ!»
    TOO_LATE = "too_late"  # не успел
    BLOCKED = "blocked"


def fishing_press(session: Session, now: float) -> FishingOutcome:
    ch = session.active_challenge
    if ch is None or ch.kind is not ChallengeKind.FISHING or ch.fishing is None:
        return FishingOutcome.BLOCKED
    state = ch.fishing
    if not state.armed:
        _fail_challenge(session)
        return FishingOutcome.TOO_EARLY
    if now <= state.deadline:
        _win_challenge(session)
        return FishingOutcome.WIN
    _fail_challenge(session)
    return FishingOutcome.TOO_LATE


def fishing_timeout(session: Session) -> bool:
    """Игрок не успел нажать вовремя — провал. Возвращает ``True``, если применено."""
    ch = session.active_challenge
    if ch is None or ch.kind is not ChallengeKind.FISHING or ch.fishing is None:
        return False
    if not ch.fishing.armed:
        return False
    _fail_challenge(session)
    return True


# ---------- мини-игра №3: побег от акулы ----------


class SharkOutcome(StrEnum):
    SURVIVED = "survived"  # пережил этап, продолжаем
    WIN = "win"  # пережил все этапы
    CAUGHT = "caught"  # пойман акулой — провал
    BLOCKED = "blocked"


@dataclass
class SharkResult:
    outcome: SharkOutcome
    attack: str | None = None
    stage: int = 0
    total_stages: int = content.SHARK_STAGES


def shark_pick(session: Session, direction: str) -> SharkResult:
    ch = session.active_challenge
    if ch is None or ch.kind is not ChallengeKind.SHARK or ch.shark is None:
        return SharkResult(SharkOutcome.BLOCKED)
    state = ch.shark
    stage = len(state.picks)
    if direction not in content.SHARK_DIRECTIONS or stage >= len(state.attacks):
        return SharkResult(SharkOutcome.BLOCKED, stage=stage)

    attack = state.attacks[stage]
    state.picks.append(direction)
    if direction == attack:
        _fail_challenge(session)
        return SharkResult(SharkOutcome.CAUGHT, attack=attack, stage=stage + 1)
    if len(state.picks) >= len(state.attacks):
        _win_challenge(session)
        return SharkResult(SharkOutcome.WIN, attack=attack, stage=stage + 1)
    return SharkResult(SharkOutcome.SURVIVED, attack=attack, stage=stage + 1)


# ---------- завершение испытаний и сессии ----------


def _win_challenge(session: Session) -> None:
    session.challenges_won += 1
    session.score += content.CHALLENGE_WIN_BONUS
    session.bonuses.append(f"Испытание пройдено +{content.CHALLENGE_WIN_BONUS}")
    session.active_challenge = None


def _fail_challenge(session: Session) -> None:
    session.challenges_failed += 1
    session.active_challenge = None
    # Провал испытания отбрасывает на одну клетку назад.
    session.position = max(0, session.position - 1)


def is_exhausted(session: Session) -> bool:
    """Ходы закончились и активного испытания нет — сессию пора завершать."""
    return session.moves_left <= 0 and session.active_challenge is None


def finish_session(session: Session) -> StatsDelta:
    """Завершить сессию и вернуть приращение статистики для агрегатов."""
    session.status = SessionStatus.FINISHED
    session.on_route = False
    session.active_challenge = None
    return StatsDelta(
        games_played=1,
        orders_completed=session.orders_completed,
        challenges_won=session.challenges_won,
        challenges_failed=session.challenges_failed,
        moves_used=session.moves_used,
        island_travels=session.travels_used,
        score=session.score,
    )
