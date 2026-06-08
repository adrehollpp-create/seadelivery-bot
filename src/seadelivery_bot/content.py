"""Статичный контент и игровые константы события «Морская доставка».

Здесь собраны имена острова/торговцев/предметов, связь «торговец → его товар»,
острова-источники нужных предметов, а также числовые параметры баланса игры.
Эти значения сознательно вынесены отдельно, чтобы их было удобно править без
правки игровой логики.
"""

from __future__ import annotations

from dataclasses import dataclass

# Хештег, без которого бот не реагирует на сообщения участников.
HASHTAG: str = "#морскаядоставка"

# Главный (домашний) порт, где живут торговцы и куда возвращаются с товаром.
MAIN_ISLAND: str = "Золотая Гавань"

# Пять торговцев домашнего порта.
MERCHANTS: tuple[str, ...] = ("Аскен", "Марта", "Эролез", "Пасец", "Иискал")

# Восемь предметов события.
ITEMS: tuple[str, ...] = (
    "цветок пастушо",
    "печенье сольмма",
    "артефакт бездушный",
    "маковый плащ",
    "соль медузы",
    "акриловый столб",
    "съедобное лего",
    "бублик из мёда",
)

# Острова-источники, откуда игрок привозит нужные товары.
SOURCE_ISLANDS: tuple[str, ...] = (
    "Туманная гавань",
    "Коралловый риф",
    "Соляные отмели",
    "Остров Бурь",
    "Жемчужная бухта",
    "Янтарный берег",
)


@dataclass(frozen=True)
class Merchant:
    """Торговец: владеет одним предметом и нуждается в другом."""

    name: str
    owns: str
    needs: str


# Каждый торговец владеет одним предметом и нуждается в товаре «с другого
# острова» (предмет, которым он не владеет). Связка фиксирована, чтобы события
# были воспроизводимы, а «нужный» предмет всегда отличался от собственного.
MERCHANT_TABLE: tuple[Merchant, ...] = (
    Merchant(name="Аскен", owns="цветок пастушо", needs="соль медузы"),
    Merchant(name="Марта", owns="печенье сольмма", needs="бублик из мёда"),
    Merchant(name="Эролез", owns="артефакт бездушный", needs="акриловый столб"),
    Merchant(name="Пасец", owns="маковый плащ", needs="съедобное лего"),
    Merchant(name="Иискал", owns="соль медузы", needs="артефакт бездушный"),
)

# Где находится нужный предмет (остров-источник). Индекс совпадает с позицией
# предмета в :data:`ITEMS`, чтобы маппинг был детерминированным.
ITEM_SOURCE: dict[str, str] = {
    item: SOURCE_ISLANDS[idx % len(SOURCE_ISLANDS)] for idx, item in enumerate(ITEMS)
}


def merchant_by_name(name: str) -> Merchant | None:
    """Найти торговца по имени (без учёта регистра)."""
    lowered = name.strip().lower()
    for merchant in MERCHANT_TABLE:
        if merchant.name.lower() == lowered:
            return merchant
    return None


def source_island_for(item: str) -> str:
    """Остров, на котором добывается нужный предмет."""
    return ITEM_SOURCE.get(item, SOURCE_ISLANDS[0])


# ---------- параметры баланса ----------

TOTAL_ROUNDS: int = 5
MAX_PLAYERS_PER_ROUND: int = 12
RECRUITMENT_SECONDS: int = 60 * 60  # набор длится час
REQUESTS_PER_ROUND: int = 2  # сколько торговцев нуждаются в помощи за раунд

MOVES_PER_SESSION: int = 17
MAX_ISLAND_TRAVELS: int = 7  # без учёта бонусов
MAX_CHALLENGES_PER_SESSION: int = 3

BOARD_MIN_LEN: int = 6
BOARD_MAX_LEN: int = 9

# Награды/бонусы (в очках).
REWARD_PER_ORDER: int = 100
TREASURE_BONUS: int = 30
CHALLENGE_WIN_BONUS: int = 15

# Параметры мини-игр.
MINES_TOTAL_CELLS: int = 6
MINES_SAFE_CELLS: int = 3  # столько же бомб (6 - 3)
FISHING_MIN_DELAY: float = 2.0
FISHING_MAX_DELAY: float = 6.0
FISHING_REACTION_SECONDS: float = 3.0  # сколько есть на нажатие «ЖМИ!»
SHARK_STAGES: int = 3
SHARK_DIRECTIONS: tuple[str, ...] = ("left", "straight", "right")
SHARK_DIRECTION_LABELS: dict[str, str] = {
    "left": "◀️ Влево",
    "straight": "⬆️ Прямо",
    "right": "▶️ Вправо",
}
