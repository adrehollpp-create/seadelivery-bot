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
        return "Сейчас заказов нет."
    lines = [f"📋 <b>Заказы торговцев · раунд {event.current_round}</b>"]
    for order in event.requests:
        lines.append(
            f"• <b>{escape(order.merchant)}</b> ждёт <b>{escape(order.needed_item)}</b> "
            f"— забрать на острове <b>{escape(order.source_island)}</b>"
        )
    return "\n".join(lines)


def _progress_line(session: Session) -> str:
    """Короткая подпись к картинке-карте: где игрок и сколько ещё плыть."""
    board = session.board
    if not session.on_route or not board:
        return "—"
    last = len(board) - 1
    pos_no = session.position + 1
    to_go = max(0, last - session.position)
    if to_go > 0:
        return (
            f"📍 Клетка <b>{pos_no}</b> из <b>{len(board)}</b> "
            f"· плыть ещё <b>{to_go}</b> ▶️"
        )
    return f"📍 Клетка <b>{pos_no}</b> из <b>{len(board)}</b> · 🏁 вы на месте!"


# ---------- лобби / набор ----------


def render_lobby(event: EventState, registered_count: int) -> str:
    lines = [
        "🌊 <b>Морская доставка</b> — набор открыт!",
        "",
        f"🚢 Раунд <b>{event.recruit_round}</b> из {content.TOTAL_ROUNDS}",
        f"👥 Записалось: <b>{registered_count} из {content.MAX_PLAYERS_PER_ROUND}</b>",
        "",
        "Набор идёт до часа или пока не наберётся 12 игроков.",
        "⚓ Жмите <b>«Записаться»</b> ниже. Передумали — <b>«Выйти из раунда»</b>.",
    ]
    return "\n".join(lines)


def lobby_keyboard(round_no: int) -> InlineKeyboardMarkup:
    join_button = InlineKeyboardButton(
        text="⚓ Записаться", callback_data=f"{CB_PREFIX}:join:{round_no}"
    )
    leave_button = InlineKeyboardButton(
        text="🚪 Выйти из раунда", callback_data=f"{CB_PREFIX}:leave:{round_no}"
    )
    return InlineKeyboardMarkup(inline_keyboard=[[join_button], [leave_button]])


# ---------- статус сессии ----------


def render_session(session: Session, event: EventState) -> str:
    order = session.active_order
    place = escape(session.current_island or content.MAIN_ISLAND)
    lines = [
        f"🧭 <b>Морская доставка</b> · раунд <b>{session.round_no}</b>",
        "",
        f"🚩 <b>Ходов осталось: {session.moves_left} из {content.MOVES_PER_SESSION}</b>",
        f"⭐ Очки: <b>{session.score}</b>",
        f"📍 Где вы: <b>{place}</b>",
    ]
    if session.on_route and order is not None:
        lines.append(
            f"📦 Везёте <b>{escape(order.needed_item)}</b> для <b>{escape(order.merchant)}</b>"
        )
        lines.append("")
        lines.append("🗺 <b>Карта маршрута — на картинке выше.</b>")
        lines.append(_progress_line(session))
        lines.append("")
        lines.append(
            f"✍️ <b>Чтобы плыть дальше — напишите сообщение с {content.HASHTAG}.</b>\n"
            "Одно сообщение = один шаг вперёд."
        )
    else:
        lines.append(
            f"🏝 Вы в порту <b>{escape(content.MAIN_ISLAND)}</b>. "
            "Нажмите кнопку ниже и выберите, кому везём заказ."
        )

    completed = (
        ", ".join(escape(m) for m in session.completed_orders) if session.completed_orders else "—"
    )
    lines.append("")
    lines.append(
        f"🚢 Поездок за товаром: <b>{session.travels_used} из {content.MAX_ISLAND_TRAVELS}</b>"
    )
    lines.append(f"✅ Доставлено заказов: <b>{session.orders_completed}</b> ({completed})")
    lines.append(
        f"🎮 Испытания: выиграно <b>{session.challenges_won}</b>, "
        f"проиграно <b>{session.challenges_failed}</b>"
    )
    bonuses = "; ".join(escape(b) for b in session.bonuses[-4:]) if session.bonuses else "—"
    lines.append(f"🎁 Последние бонусы: {bonuses}")
    lines.append("")
    lines.append(format_requests(event))
    return "\n".join(lines)


def session_keyboard(session: Session, event: EventState) -> InlineKeyboardMarkup:
    uid = session.user_id
    rows: list[list[InlineKeyboardButton]] = []

    if session.active_challenge is not None:
        return challenge_keyboard(session)

    if not session.on_route:
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
            "⚠️ <b>Испытание: минное поле</b>\n"
            f"Откройте <b>{content.MINES_SAFE_CELLS}</b> безопасные клетки из "
            f"{content.MINES_TOTAL_CELLS}. Попадёте на бомбу — провал и шаг назад."
        )
    if kind is ChallengeKind.FISHING:
        return (
            "🎣 <b>Испытание: рыбалка</b>\n"
            "Ждите поклёвки. Как появится кнопка <b>«ЖМИ!»</b> — жмите как можно быстрее!"
        )
    return (
        "🦈 <b>Испытание: побег от акулы</b>\n"
        f"Пройдите <b>{content.SHARK_STAGES}</b> этапа: выбирайте направление, "
        "куда <b>НЕ</b> метит акула."
    )
