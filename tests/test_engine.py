"""Тесты чистой игровой логики «Морской доставки»."""

from __future__ import annotations

import random
import time

from seadelivery_bot import content, engine
from seadelivery_bot.models import (
    ActiveChallenge,
    CellType,
    ChallengeKind,
    FishingState,
    MinesState,
    Order,
    Session,
    SessionStatus,
    SharkState,
)


def _route_session(board: list[CellType], order: Order | None = None) -> Session:
    session = engine.new_session(chat_id=1, user_id=2, round_no=1)
    session.on_route = True
    session.position = 0
    session.board = board
    session.active_order = order or Order(
        merchant="Аскен",
        owned_item="Цветок Пастушо",
        needed_item="Соль Медузы",
        source_island="Бухта Медуз",
    )
    return session


# ---------- заказы раунда ----------


def test_pick_round_requests_distinct_and_valid() -> None:
    rng = random.Random(7)
    orders = engine.pick_round_requests(rng)
    assert len(orders) == content.REQUESTS_PER_ROUND
    assert len({o.merchant for o in orders}) == content.REQUESTS_PER_ROUND
    for order in orders:
        assert order.needed_item != order.owned_item
        assert order.source_island


# ---------- генерация поля ----------


def test_generate_board_respects_bounds_and_challenge_budget() -> None:
    rng = random.Random(123)
    for _ in range(50):
        board = engine.generate_board(rng, challenges_budget=2)
        assert content.BOARD_MIN_LEN <= len(board) <= content.BOARD_MAX_LEN
        assert board[0] is CellType.EMPTY
        assert board[-1] is CellType.EMPTY
        assert engine.board_challenge_count(board) <= 2


def test_generate_board_zero_budget_has_no_challenges() -> None:
    rng = random.Random(1)
    for _ in range(20):
        board = engine.generate_board(rng, challenges_budget=0)
        assert engine.board_challenge_count(board) == 0


# ---------- старт маршрута ----------


def test_start_route_consumes_travel_only() -> None:
    session = engine.new_session(1, 2, 1)
    order = engine.pick_round_requests(random.Random(0))[0]
    result = engine.start_route(session, order, random.Random(0))
    assert result.outcome is engine.StartOutcome.OK
    assert session.on_route is True
    # Взятие заказа не тратит ход — только перемещение.
    assert session.moves_left == content.MOVES_PER_SESSION
    assert session.moves_used == 0
    assert session.travels_used == 1
    assert session.board


def test_start_route_blocked_when_busy_or_no_resources() -> None:
    order = engine.pick_round_requests(random.Random(0))[0]
    busy = engine.new_session(1, 2, 1)
    busy.on_route = True
    assert engine.start_route(busy, order, random.Random()).outcome is engine.StartOutcome.BUSY

    no_moves = engine.new_session(1, 2, 1)
    no_moves.moves_left = 0
    assert (
        engine.start_route(no_moves, order, random.Random()).outcome is engine.StartOutcome.NO_MOVES
    )

    no_travels = engine.new_session(1, 2, 1)
    no_travels.travels_used = content.MAX_ISLAND_TRAVELS
    assert (
        engine.start_route(no_travels, order, random.Random()).outcome
        is engine.StartOutcome.NO_TRAVELS
    )

    done = engine.new_session(1, 2, 1)
    done.completed_orders = [order.merchant]
    assert (
        engine.start_route(done, order, random.Random()).outcome is engine.StartOutcome.ALREADY_DONE
    )


# ---------- движение по полю ----------


def test_sail_empty_then_deliver() -> None:
    session = _route_session([CellType.EMPTY, CellType.EMPTY, CellType.EMPTY])
    first = engine.sail_forward(session, random.Random())
    assert first.outcome is engine.SailOutcome.EMPTY
    assert session.position == 1

    second = engine.sail_forward(session, random.Random())
    assert second.outcome is engine.SailOutcome.DELIVERED
    assert second.reward == content.REWARD_PER_ORDER
    assert session.orders_completed == 1
    assert session.on_route is False
    assert "Аскен" in session.completed_orders
    assert session.score == content.REWARD_PER_ORDER


def test_sail_treasure_advances_two_and_scores() -> None:
    board = [CellType.EMPTY, CellType.TREASURE, CellType.EMPTY, CellType.EMPTY, CellType.EMPTY]
    session = _route_session(board)
    result = engine.sail_forward(session, random.Random())
    assert result.outcome is engine.SailOutcome.TREASURE
    assert result.treasure_gain == content.TREASURE_BONUS
    # 1 (шаг) + 2 (рывок сокровища) = клетка 3
    assert session.position == 3
    assert session.score == content.TREASURE_BONUS


def test_sail_treasure_can_complete_delivery() -> None:
    board = [CellType.EMPTY, CellType.EMPTY, CellType.TREASURE, CellType.EMPTY]
    session = _route_session(board)
    engine.sail_forward(session, random.Random())  # -> позиция 1 (пусто)
    result = engine.sail_forward(session, random.Random())  # -> позиция 2 -> рывок до конца
    assert result.outcome is engine.SailOutcome.DELIVERED
    assert session.orders_completed == 1


def test_sail_challenge_sets_active_challenge() -> None:
    board = [CellType.EMPTY, CellType.CHALLENGE, CellType.EMPTY, CellType.EMPTY]
    session = _route_session(board)
    before = session.challenges_remaining
    result = engine.sail_forward(session, random.Random(3))
    assert result.outcome is engine.SailOutcome.CHALLENGE
    assert session.active_challenge is not None
    assert session.challenges_remaining == before - 1


def test_sail_blocked_when_not_on_route_or_no_moves() -> None:
    idle = engine.new_session(1, 2, 1)
    assert engine.sail_forward(idle, random.Random()).outcome is engine.SailOutcome.BLOCKED

    stuck = _route_session([CellType.EMPTY, CellType.EMPTY])
    stuck.moves_left = 0
    assert engine.sail_forward(stuck, random.Random()).outcome is engine.SailOutcome.BLOCKED


# ---------- мини-игра: мины ----------


def test_mines_win_path() -> None:
    session = _route_session([CellType.EMPTY, CellType.EMPTY, CellType.EMPTY])
    session.position = 1
    session.active_challenge = ActiveChallenge(
        kind=ChallengeKind.MINES, mines=MinesState(safe=[0, 1, 2], picks=[])
    )
    assert engine.mines_pick(session, 0, random.Random()).outcome is engine.MinesOutcome.SAFE
    assert engine.mines_pick(session, 1, random.Random()).outcome is engine.MinesOutcome.SAFE
    win = engine.mines_pick(session, 2, random.Random())
    assert win.outcome is engine.MinesOutcome.WIN
    assert session.active_challenge is None
    assert session.challenges_won == 1
    assert session.score == content.CHALLENGE_WIN_BONUS


def test_mines_bomb_fails_and_steps_back() -> None:
    session = _route_session([CellType.EMPTY, CellType.EMPTY, CellType.EMPTY])
    session.position = 2
    session.active_challenge = ActiveChallenge(
        kind=ChallengeKind.MINES, mines=MinesState(safe=[0, 1, 2], picks=[])
    )
    boom = engine.mines_pick(session, 5, random.Random())
    assert boom.outcome is engine.MinesOutcome.BOOM
    assert session.active_challenge is None
    assert session.challenges_failed == 1
    assert session.position == 1  # шаг назад


# ---------- мини-игра: рыбалка ----------


def test_fishing_press_win_and_misses() -> None:
    session = _route_session([CellType.EMPTY, CellType.EMPTY])
    session.position = 1
    session.active_challenge = ActiveChallenge(
        kind=ChallengeKind.FISHING, fishing=FishingState(deadline=0.0, armed=False)
    )
    # Раннее нажатие — провал.
    assert engine.fishing_press(session, time.time()) is engine.FishingOutcome.TOO_EARLY
    assert session.challenges_failed == 1

    session.position = 1
    session.active_challenge = ActiveChallenge(
        kind=ChallengeKind.FISHING, fishing=FishingState(deadline=0.0, armed=False)
    )
    now = 1000.0
    assert engine.fishing_arm(session, now) is True
    assert engine.fishing_press(session, now + 0.5) is engine.FishingOutcome.WIN
    assert session.challenges_won == 1


def test_fishing_timeout_after_arm() -> None:
    session = _route_session([CellType.EMPTY, CellType.EMPTY])
    session.position = 1
    session.active_challenge = ActiveChallenge(
        kind=ChallengeKind.FISHING, fishing=FishingState(deadline=0.0, armed=False)
    )
    engine.fishing_arm(session, 10.0)
    assert engine.fishing_timeout(session) is True
    assert session.challenges_failed == 1
    assert session.active_challenge is None


# ---------- мини-игра: акула ----------


def test_shark_survive_until_win() -> None:
    session = _route_session([CellType.EMPTY, CellType.EMPTY])
    session.position = 1
    session.active_challenge = ActiveChallenge(
        kind=ChallengeKind.SHARK,
        shark=SharkState(attacks=["left", "left", "left"], picks=[]),
    )
    assert engine.shark_pick(session, "right").outcome is engine.SharkOutcome.SURVIVED
    assert engine.shark_pick(session, "straight").outcome is engine.SharkOutcome.SURVIVED
    win = engine.shark_pick(session, "right")
    assert win.outcome is engine.SharkOutcome.WIN
    assert session.challenges_won == 1
    assert session.active_challenge is None


def test_shark_caught_steps_back() -> None:
    session = _route_session([CellType.EMPTY, CellType.EMPTY, CellType.EMPTY])
    session.position = 2
    session.active_challenge = ActiveChallenge(
        kind=ChallengeKind.SHARK,
        shark=SharkState(attacks=["left", "right", "straight"], picks=[]),
    )
    caught = engine.shark_pick(session, "left")
    assert caught.outcome is engine.SharkOutcome.CAUGHT
    assert session.challenges_failed == 1
    assert session.position == 1


# ---------- завершение ----------


def test_is_exhausted_and_finish_delta() -> None:
    session = _route_session([CellType.EMPTY, CellType.EMPTY])
    session.moves_left = 0
    assert engine.is_exhausted(session) is True

    session.orders_completed = 2
    session.challenges_won = 1
    session.challenges_failed = 1
    session.moves_used = 17
    session.travels_used = 3
    session.score = 245
    delta = engine.finish_session(session)
    assert session.status is SessionStatus.FINISHED
    assert delta.games_played == 1
    assert delta.orders_completed == 2
    assert delta.challenges_won == 1
    assert delta.challenges_failed == 1
    assert delta.moves_used == 17
    assert delta.island_travels == 3
    assert delta.score == 245
