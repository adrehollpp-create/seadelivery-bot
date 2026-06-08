"""Типы данных игры: перечисления и (JSON-сериализуемые) состояния.

Сессия игрока целиком сериализуется в JSON и хранится в БД, поэтому все
вложенные структуры умеют конвертироваться в ``dict`` и обратно. Перечисления
наследуются от ``str``, чтобы напрямую сериализоваться в JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CellType(StrEnum):
    """Тип клетки игрового поля."""

    EMPTY = "empty"
    TREASURE = "treasure"
    CHALLENGE = "challenge"


class ChallengeKind(StrEnum):
    """Вид испытания (мини-игры)."""

    MINES = "mines"  # поиск безопасных клеток
    FISHING = "fishing"  # рыбалка «ЖМИ!»
    SHARK = "shark"  # побег от акулы


class SessionStatus(StrEnum):
    ACTIVE = "active"
    FINISHED = "finished"


class EventStatus(StrEnum):
    IDLE = "idle"  # событие создано, набор не открыт
    RECRUITING = "recruiting"  # идёт набор игроков
    ROUND_ACTIVE = "round_active"  # раунд запущен, можно играть
    FINISHED = "finished"  # все 5 раундов завершены


@dataclass
class Order:
    """Активный заказ торговца, который выполняет игрок."""

    merchant: str
    owned_item: str
    needed_item: str
    source_island: str

    def to_dict(self) -> dict[str, str]:
        return {
            "merchant": self.merchant,
            "owned_item": self.owned_item,
            "needed_item": self.needed_item,
            "source_island": self.source_island,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Order:
        return cls(
            merchant=str(data["merchant"]),
            owned_item=str(data["owned_item"]),
            needed_item=str(data["needed_item"]),
            source_island=str(data["source_island"]),
        )


@dataclass
class MinesState:
    """Состояние мини-игры «поиск безопасных клеток»."""

    safe: list[int]  # индексы безопасных клеток (3 шт.)
    picks: list[int]  # уже нажатые клетки

    def to_dict(self) -> dict[str, list[int]]:
        return {"safe": list(self.safe), "picks": list(self.picks)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MinesState:
        return cls(
            safe=[int(x) for x in data.get("safe", [])],
            picks=[int(x) for x in data.get("picks", [])],
        )


@dataclass
class FishingState:
    """Состояние мини-игры «рыбалка»."""

    deadline: float  # unix-время, до которого нужно успеть нажать «ЖМИ!»
    armed: bool  # появилась ли уже кнопка «ЖМИ!»

    def to_dict(self) -> dict[str, Any]:
        return {"deadline": self.deadline, "armed": self.armed}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FishingState:
        return cls(deadline=float(data["deadline"]), armed=bool(data["armed"]))


@dataclass
class SharkState:
    """Состояние мини-игры «побег от акулы»."""

    attacks: list[str]  # заранее заготовленные атаки акулы по этапам
    picks: list[str]  # выбранные игроком направления

    def to_dict(self) -> dict[str, list[str]]:
        return {"attacks": list(self.attacks), "picks": list(self.picks)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SharkState:
        return cls(
            attacks=[str(x) for x in data.get("attacks", [])],
            picks=[str(x) for x in data.get("picks", [])],
        )


@dataclass
class ActiveChallenge:
    """Текущее активное испытание. Заполнено ровно одно поле состояния."""

    kind: ChallengeKind
    mines: MinesState | None = None
    fishing: FishingState | None = None
    shark: SharkState | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind.value}
        if self.mines is not None:
            data["mines"] = self.mines.to_dict()
        if self.fishing is not None:
            data["fishing"] = self.fishing.to_dict()
        if self.shark is not None:
            data["shark"] = self.shark.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActiveChallenge:
        return cls(
            kind=ChallengeKind(data["kind"]),
            mines=MinesState.from_dict(data["mines"]) if "mines" in data else None,
            fishing=FishingState.from_dict(data["fishing"]) if "fishing" in data else None,
            shark=SharkState.from_dict(data["shark"]) if "shark" in data else None,
        )


@dataclass
class Session:
    """Полное состояние игровой сессии участника (одна активная на игрока)."""

    chat_id: int
    user_id: int
    round_no: int
    status: SessionStatus = SessionStatus.ACTIVE
    moves_left: int = 0
    travels_used: int = 0
    challenges_remaining: int = 0
    position: int = 0
    score: int = 0
    current_island: str = ""
    board: list[CellType] = field(default_factory=list)
    on_route: bool = False
    active_order: Order | None = None
    active_challenge: ActiveChallenge | None = None
    completed_orders: list[str] = field(default_factory=list)
    bonuses: list[str] = field(default_factory=list)
    menu_chat_id: int | None = None
    menu_message_id: int | None = None
    # Имя/username владельца — для пометки в подписи карточки (чья строка).
    owner_name: str | None = None
    owner_username: str | None = None
    # Счётчики статистики, накопленные за сессию.
    orders_completed: int = 0
    challenges_won: int = 0
    challenges_failed: int = 0
    moves_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "round_no": self.round_no,
            "status": self.status.value,
            "moves_left": self.moves_left,
            "travels_used": self.travels_used,
            "challenges_remaining": self.challenges_remaining,
            "position": self.position,
            "score": self.score,
            "current_island": self.current_island,
            "board": [cell.value for cell in self.board],
            "on_route": self.on_route,
            "active_order": self.active_order.to_dict() if self.active_order else None,
            "active_challenge": (
                self.active_challenge.to_dict() if self.active_challenge else None
            ),
            "completed_orders": list(self.completed_orders),
            "bonuses": list(self.bonuses),
            "menu_chat_id": self.menu_chat_id,
            "menu_message_id": self.menu_message_id,
            "owner_name": self.owner_name,
            "owner_username": self.owner_username,
            "orders_completed": self.orders_completed,
            "challenges_won": self.challenges_won,
            "challenges_failed": self.challenges_failed,
            "moves_used": self.moves_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        order = data.get("active_order")
        challenge = data.get("active_challenge")
        return cls(
            chat_id=int(data["chat_id"]),
            user_id=int(data["user_id"]),
            round_no=int(data["round_no"]),
            status=SessionStatus(data.get("status", SessionStatus.ACTIVE.value)),
            moves_left=int(data.get("moves_left", 0)),
            travels_used=int(data.get("travels_used", 0)),
            challenges_remaining=int(data.get("challenges_remaining", 0)),
            position=int(data.get("position", 0)),
            score=int(data.get("score", 0)),
            current_island=str(data.get("current_island", "")),
            board=[CellType(value) for value in data.get("board", [])],
            on_route=bool(data.get("on_route", False)),
            active_order=Order.from_dict(order) if order else None,
            active_challenge=ActiveChallenge.from_dict(challenge) if challenge else None,
            completed_orders=[str(x) for x in data.get("completed_orders", [])],
            bonuses=[str(x) for x in data.get("bonuses", [])],
            menu_chat_id=(
                int(data["menu_chat_id"]) if data.get("menu_chat_id") is not None else None
            ),
            menu_message_id=(
                int(data["menu_message_id"]) if data.get("menu_message_id") is not None else None
            ),
            owner_name=(
                str(data["owner_name"]) if data.get("owner_name") is not None else None
            ),
            owner_username=(
                str(data["owner_username"]) if data.get("owner_username") is not None else None
            ),
            orders_completed=int(data.get("orders_completed", 0)),
            challenges_won=int(data.get("challenges_won", 0)),
            challenges_failed=int(data.get("challenges_failed", 0)),
            moves_used=int(data.get("moves_used", 0)),
        )


@dataclass
class EventState:
    """Состояние события в конкретном чате."""

    chat_id: int
    status: EventStatus = EventStatus.IDLE
    current_round: int = 0  # последний запущенный раунд (0 — ни одного)
    recruit_round: int = 0  # раунд, на который идёт/шёл набор (0 — нет)
    recruit_deadline: float | None = None
    requests: list[Order] = field(default_factory=list)

    def requests_to_dicts(self) -> list[dict[str, str]]:
        return [order.to_dict() for order in self.requests]

    @staticmethod
    def requests_from_dicts(data: list[dict[str, Any]]) -> list[Order]:
        return [Order.from_dict(item) for item in data]


@dataclass(frozen=True)
class StatsDelta:
    """Приращение статистики, применяемое к агрегатам игрока и раунда."""

    games_played: int = 0
    orders_completed: int = 0
    challenges_won: int = 0
    challenges_failed: int = 0
    moves_used: int = 0
    island_travels: int = 0
    score: int = 0


@dataclass(frozen=True)
class PlayerStats:
    """Агрегированная статистика игрока (по событию или по раунду)."""

    user_id: int
    username: str | None
    full_name: str | None
    games_played: int
    orders_completed: int
    challenges_won: int
    challenges_failed: int
    moves_used: int
    island_travels: int
    score: int
