"""Рендеринг текста и инлайн-клавиатур для события «Морская доставка».

Здесь нет игровой логики — только построение сообщений и клавиатур из текущего
состояния. Callback-данные имеют формат ``sea:<action>:<owner_id>[:arg]`` для
игроков и ``seaadm:<action>`` для админ-панели. ``owner_id`` нужен, чтобы кнопки
игрока в групповом чате мог нажимать только владелец сессии.
"""

from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import content
from .models import ChallengeKind, EventState, Session

CB_PREFIX = "sea"
ADMIN_PREFIX = "seaadm"


# ---------- общие тексты ----------


def hashtag_hint() -> str:
    return (
        f"Чтобы участвовать в событии, добавляйте хештег <b>{content.HASHTAG}</b> "
        "к своему сообщению — без него бот не реагирует."
    )


def format_requests(event: EventState) -> str:
    if not event.requests:
        return "Активных запросов нет."
    lines = [f"<b>Запросы торговцев (раунд {event.current_round}):</b>"]
    for order in event.requests:
        lines.append(
            f"• <b>{escape(order.merchant)}</b> (везёт «{escape(order.owned_item)}») "
            f"просит доставить «{escape(order.needed_item)}» "
            f"с острова <i>{escape(order.source_island)}</i>"
        )
    return "\n".join(lines)


def _board_strip(session: Session) -> str:
    if not session.on_route or not session.board:
        return "—"
    cells: list[str] = []
    for idx in range(len(session.board)):
        if idx == len(session.board) - 1:
            cells.append("🏝️" if idx != session.position else "🚤")
        elif idx == session.position:
            cells.append("🚤")
        elif idx < session.position:
            cells.append("·")
        else:
            cells.append("🌊")
    return "".join(cells)


# ---------- лобби / набор ----------


def render_lobby(event: EventState, registered_count: int) -> str:
    lines = [
        "🌊 <b>Морская доставка</b> — событие открыто!",
        "",
        f"Идёт набор на раунд <b>{event.recruit_round}</b> из {content.TOTAL_ROUNDS}.",
        f"Записалось: <b>{registered_count}/{content.MAX_PLAYERS_PER_ROUND}</b>.",
        "Набор длится до часа или пока не наберётся максимум игроков.",
        "",
        "Нажмите кнопку ниже, чтобы записаться.",
    ]
    return "\n".join(lines)


def lobby_keyboard(round_no: int) -> InlineKeyboardMarkup:
    join_button = InlineKeyboardButton(
        text="⚓ Записаться", callback_data=f"{CB_PREFIX}:join:{round_no}"
    )
    return InlineKeyboardMarkup(inline_keyboard=[[join_button]])


# ---------- статус сессии ----------


def render_session(session: Session, event: EventState) -> str:
    order = session.active_order
    lines = [
        "🧭 <b>Морская доставка</b>",
        f"Раунд: <b>{session.round_no}</b>",
        f"Ходов осталось: <b>{session.moves_left}</b> / {content.MOVES_PER_SESSION}",
        (
            f"Перемещений между островами: <b>{session.travels_used}</b>"
            f" / {content.MAX_ISLAND_TRAVELS}"
        ),
        f"Текущее место: <b>{escape(session.current_island or content.MAIN_ISLAND)}</b>",
        f"Очки: <b>{session.score}</b>",
    ]
    if session.on_route and order is not None:
        lines.append(
            f"Заказ: доставить «{escape(order.needed_item)}» для <b>{escape(order.merchant)}</b>"
        )
        lines.append(f"Маршрут: {_board_strip(session)}")
        lines.append(f"Позиция: клетка {session.position + 1} из {len(session.board)}")
    else:
        lines.append(f"Вы на острове <b>{escape(content.MAIN_ISLAND)}</b>. Выберите заказ.")

    completed = (
        ", ".join(escape(m) for m in session.completed_orders) if session.completed_orders else "—"
    )
    lines.append(f"Выполнено заказов: {session.orders_completed} ({completed})")
    lines.append(f"Испытания: ✅ {session.challenges_won} / ❌ {session.challenges_failed}")
    bonuses = "; ".join(escape(b) for b in session.bonuses[-4:]) if session.bonuses else "—"
    lines.append(f"Бонусы: {bonuses}")
    lines.append("")
    lines.append(format_requests(event))
    return "\n".join(lines)


def session_keyboard(session: Session, event: EventState) -> InlineKeyboardMarkup:
    uid = session.user_id
    rows: list[list[InlineKeyboardButton]] = []

    if session.active_challenge is not None:
        return challenge_keyboard(session)

    if session.on_route:
        rows.append(
            [InlineKeyboardButton(text="⛵ Плыть вперёд", callback_data=f"{CB_PREFIX}:sail:{uid}")]
        )
    else:
        for idx, order in enumerate(event.requests):
            if order.merchant in session.completed_orders:
                label = f"✅ {order.merchant} — выполнено"
                rows.append(
                    [InlineKeyboardButton(text=label, callback_data=f"{CB_PREFIX}:noop:{uid}")]
                )
            else:
                label = f"📦 За товаром для {order.merchant}"
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=label, callback_data=f"{CB_PREFIX}:order:{uid}:{idx}"
                        )
                    ]
                )

    rows.append(
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"{CB_PREFIX}:refresh:{uid}"),
            InlineKeyboardButton(text="🏁 Завершить", callback_data=f"{CB_PREFIX}:finish:{uid}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- клавиатуры испытаний ----------


def challenge_keyboard(session: Session) -> InlineKeyboardMarkup:
    ch = session.active_challenge
    uid = session.user_id
    if ch is None:
        return session_keyboard(session, EventState(chat_id=session.chat_id))
    if ch.kind is ChallengeKind.MINES and ch.mines is not None:
        picks = set(ch.mines.picks)
        buttons: list[InlineKeyboardButton] = []
        for i in range(content.MINES_TOTAL_CELLS):
            text = "✅" if i in picks else f"🔲 {i + 1}"
            data = f"{CB_PREFIX}:noop:{uid}" if i in picks else f"{CB_PREFIX}:mines:{uid}:{i}"
            buttons.append(InlineKeyboardButton(text=text, callback_data=data))
        rows = [buttons[:3], buttons[3:]]
        return InlineKeyboardMarkup(inline_keyboard=rows)
    if ch.kind is ChallengeKind.FISHING and ch.fishing is not None:
        if ch.fishing.armed:
            return InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🎣 ЖМИ!", callback_data=f"{CB_PREFIX}:fish:{uid}")]
                ]
            )
        waiting = InlineKeyboardButton(
            text="…ждём поклёвки…", callback_data=f"{CB_PREFIX}:noop:{uid}"
        )
        return InlineKeyboardMarkup(inline_keyboard=[[waiting]])
    if ch.kind is ChallengeKind.SHARK and ch.shark is not None:
        row = [
            InlineKeyboardButton(
                text=content.SHARK_DIRECTION_LABELS[d],
                callback_data=f"{CB_PREFIX}:shark:{uid}:{d}",
            )
            for d in content.SHARK_DIRECTIONS
        ]
        return InlineKeyboardMarkup(inline_keyboard=[row])
    return InlineKeyboardMarkup(inline_keyboard=[])


def challenge_intro(kind: ChallengeKind) -> str:
    if kind is ChallengeKind.MINES:
        return (
            "⚠️ <b>Испытание: поиск безопасных клеток</b>\n"
            f"Найдите все {content.MINES_SAFE_CELLS} безопасные клетки из "
            f"{content.MINES_TOTAL_CELLS}. Попадёте на бомбу — провал и шаг назад."
        )
    if kind is ChallengeKind.FISHING:
        return (
            "🎣 <b>Испытание: рыбалка</b>\n"
            "Ждите поклёвки. Как появится кнопка «ЖМИ!» — жмите как можно быстрее!"
        )
    return (
        "🦈 <b>Испытание: побег от акулы</b>\n"
        f"Пройдите {content.SHARK_STAGES} этапа: выбирайте направление, куда НЕ метит акула."
    )
