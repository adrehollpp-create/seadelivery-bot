"""Игровой роутер участников события «Морская доставка».

Перемещение по маршруту («плыть») управляется текстом: каждое сообщение с
хештегом :data:`content.HASHTAG` продвигает корабль на один ход. Через кнопки
идут только мини-игры (испытания) и служебные действия (взять заказ, обновить,
завершить). Кнопки игрока в групповом чате может нажимать лишь владелец сессии
(id зашит в callback-данные).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    User,
)

from . import content, engine, pixmap, service, ui
from .engine import (
    FishingOutcome,
    MinesOutcome,
    SailOutcome,
    SharkOutcome,
    StartOutcome,
)
from .models import ChallengeKind, EventState, EventStatus, Session, SessionStatus
from .service import JoinOutcome, LeaveOutcome, StartSessionOutcome
from .store import SeaStore

logger = logging.getLogger(__name__)


def _has_hashtag(text: str | None) -> bool:
    return bool(text) and content.HASHTAG.lower() in (text or "").lower()


def _user_fields(user: User | None) -> tuple[str | None, str | None]:
    if user is None:
        return None, None
    return user.username, user.full_name


def make_sea_router(store: SeaStore) -> Router:
    """Собрать роутер участников события."""
    router = Router(name="sea_delivery")

    async def _show_state(message: Message, user: User) -> None:
        """Отрисовать актуальное состояние новым сообщением (вход через текст/команду)."""
        chat_id = message.chat.id
        event = await service.get_event_or_default(store, chat_id)

        if event.status is EventStatus.RECRUITING:
            count = await store.count_registrations(chat_id, event.recruit_round)
            if service.recruitment_open(event, count):
                await message.answer(
                    ui.render_lobby(event, count),
                    reply_markup=ui.lobby_keyboard(event.recruit_round),
                    parse_mode="HTML",
                )
                return
            await message.answer(
                "Набор на раунд закрыт (время вышло или нет мест). "
                "Дождитесь запуска раунда администратором.",
                parse_mode="HTML",
            )
            return

        if event.status is EventStatus.ROUND_ACTIVE:
            result = await service.start_or_resume_session(store, chat_id, user.id)
            await _send_session_menu(message, result.session, event, result.outcome)
            return

        if event.status is EventStatus.FINISHED:
            await message.answer(
                "🏁 Событие завершено. Итоговый рейтинг — командой <code>/sea_final</code>.",
                parse_mode="HTML",
            )
            return

        await message.answer(
            "🌊 <b>Морская доставка</b> сейчас не активна.\n"
            "Дождитесь, пока администратор откроет набор.\n\n" + ui.hashtag_hint(),
            parse_mode="HTML",
        )

    async def _send_session_menu(
        message: Message,
        session: Session | None,
        event: EventState,
        outcome: StartSessionOutcome,
    ) -> None:
        if outcome is StartSessionOutcome.NOT_REGISTERED:
            await message.answer(
                "Вы не записаны на этот раунд. Запись открывается перед каждым раундом.",
                parse_mode="HTML",
            )
            return
        if outcome is StartSessionOutcome.ALREADY_PLAYED and session is not None:
            await message.answer(
                "Вы уже отыграли свою сессию в этом раунде. Дождитесь следующего раунда.\n\n"
                + ui.render_session(session, event),
                parse_mode="HTML",
            )
            return
        if session is None:
            await message.answer("Сейчас играть нельзя.", parse_mode="HTML")
            return

        note = (
            f"🎉 <b>Игра началась!</b> У вас <b>{content.MOVES_PER_SESSION} ходов</b>."
            if outcome is StartSessionOutcome.NEW
            else ""
        )
        sent = await message.answer_photo(
            _board_photo(session),
            caption=_caption(note, session, event),
            reply_markup=ui.session_keyboard(session, event),
            parse_mode="HTML",
        )
        session.menu_chat_id = sent.chat.id
        session.menu_message_id = sent.message_id
        await store.save_session(session)

    async def _post_menu(
        message: Message, note: str, session: Session, *, final: bool = False
    ) -> None:
        """Отправить новое фото-меню с состоянием и обновить указатель меню."""
        event = await service.get_event_or_default(store, session.chat_id)
        markup = None if final else ui.session_keyboard(session, event)
        sent = await message.answer_photo(
            _board_photo(session),
            caption=_caption(note, session, event),
            reply_markup=markup,
            parse_mode="HTML",
        )
        session.menu_chat_id = sent.chat.id
        session.menu_message_id = sent.message_id
        await store.save_session(session)

    async def _sail_via_message(
        message: Message, bot: Bot, session: Session, user: User
    ) -> None:
        """Один ход «плыть» по сообщению с хештегом (текстовое управление)."""
        rng = random.Random()
        result = engine.sail_forward(session, rng)
        if result.outcome is SailOutcome.BLOCKED:
            await message.answer("Сейчас плыть нельзя.", parse_mode="HTML")
            return

        note = _sail_note(result)
        if result.outcome is SailOutcome.CHALLENGE and result.challenge_kind is not None:
            note = f"{note}\n\n{ui.challenge_intro(result.challenge_kind)}"
            await _post_menu(message, note, session)
            if result.challenge_kind is ChallengeKind.FISHING:
                _schedule_fishing(bot, store, session.chat_id, session.user_id)
            return

        if engine.is_exhausted(session):
            username, full_name = _user_fields(user)
            await service.finalize_session(store, session, username, full_name)
            await _post_menu(message, note + "\n\n🏁 Сессия завершена.", session, final=True)
            return
        await _post_menu(message, note, session)

    # ---------- точки входа: команда и хештег ----------

    @router.message(Command("sea", "seadelivery"))
    async def cmd_sea(message: Message) -> None:
        if message.from_user is None:
            return
        await _show_state(message, message.from_user)

    @router.message(F.text.func(_has_hashtag) | F.caption.func(_has_hashtag))
    async def on_hashtag(message: Message, bot: Bot) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        user = message.from_user
        chat_id = message.chat.id
        event = await service.get_event_or_default(store, chat_id)
        # Во время активного раунда сообщение с хештегом = «плыть на 1 ход»,
        # если игрок уже в пути и не проходит мини-игру.
        if event.status is EventStatus.ROUND_ACTIVE:
            session = await store.get_session(chat_id, user.id)
            if session is not None and session.status is SessionStatus.ACTIVE:
                if session.active_challenge is not None:
                    await message.answer(
                        "⚠️ Идёт испытание — решайте его кнопками в меню выше.",
                        parse_mode="HTML",
                    )
                    return
                if session.on_route:
                    await _sail_via_message(message, bot, session, user)
                    return
        await _show_state(message, user)

    # ---------- запись в набор ----------

    async def _refresh_lobby(message: Message, event: EventState, count: int) -> None:
        """Перерисовать сообщение лобби с актуальным числом записавшихся."""
        with suppress(TelegramBadRequest):
            await message.edit_text(
                ui.render_lobby(event, count),
                reply_markup=ui.lobby_keyboard(event.recruit_round),
                parse_mode="HTML",
            )

    @router.callback_query(F.data.startswith(f"{ui.CB_PREFIX}:join:"))
    async def cb_join(query: CallbackQuery) -> None:
        if query.message is None or query.from_user is None or query.data is None:
            await query.answer()
            return
        if not isinstance(query.message, Message):
            await query.answer()
            return
        chat_id = query.message.chat.id
        username, full_name = _user_fields(query.from_user)
        outcome, event, count = await service.join_round(
            store, chat_id, query.from_user.id, username, full_name
        )
        messages = {
            JoinOutcome.OK: f"⚓ Вы записаны! Игроков: {count}/{content.MAX_PLAYERS_PER_ROUND}.",
            JoinOutcome.ALREADY: "Вы уже записаны на этот раунд.",
            JoinOutcome.FULL: "Мест больше нет (набрано 12 игроков).",
            JoinOutcome.CLOSED: "Набор закрыт.",
        }
        await query.answer(messages[outcome], show_alert=outcome is not JoinOutcome.OK)
        # Всегда обновляем лобби свежим числом — чтобы счётчик не «застывал».
        await _refresh_lobby(query.message, event, count)

    @router.callback_query(F.data.startswith(f"{ui.CB_PREFIX}:leave:"))
    async def cb_leave(query: CallbackQuery) -> None:
        if query.message is None or query.from_user is None or query.data is None:
            await query.answer()
            return
        if not isinstance(query.message, Message):
            await query.answer()
            return
        chat_id = query.message.chat.id
        outcome, event, count = await service.leave_round(store, chat_id, query.from_user.id)
        left_msg = f"🚪 Вы вышли из раунда. Игроков: {count}/{content.MAX_PLAYERS_PER_ROUND}."
        messages = {
            LeaveOutcome.OK: left_msg,
            LeaveOutcome.NOT_REGISTERED: "Вы и так не записаны на этот раунд.",
            LeaveOutcome.CLOSED: "Набор уже закрыт — выйти нельзя.",
        }
        await query.answer(messages[outcome], show_alert=outcome is not LeaveOutcome.OK)
        await _refresh_lobby(query.message, event, count)

    # ---------- игровые действия ----------

    async def _load_owned_session(query: CallbackQuery, owner_id: int) -> Session | None:
        """Проверить владельца кнопки и загрузить его активную сессию."""
        if query.from_user is None or query.message is None:
            return None
        if query.from_user.id != owner_id:
            await query.answer(
                "Это игровое меню другого игрока. Напишите боту с "
                f"{content.HASHTAG}, чтобы играть самому.",
                show_alert=True,
            )
            return None
        chat_id = query.message.chat.id
        session = await store.get_session(chat_id, owner_id)
        if session is None or session.status is not SessionStatus.ACTIVE:
            await query.answer("Активной сессии нет.", show_alert=True)
            return None
        return session

    async def _rerender(query: CallbackQuery, session: Session, note: str) -> None:
        event = await service.get_event_or_default(store, session.chat_id)
        if isinstance(query.message, Message):
            session.menu_chat_id = query.message.chat.id
            session.menu_message_id = query.message.message_id
        await store.save_session(session)
        if isinstance(query.message, Message):
            with suppress(TelegramBadRequest):
                await query.message.edit_media(
                    InputMediaPhoto(
                        media=_board_photo(session),
                        caption=_caption(note, session, event),
                        parse_mode="HTML",
                    ),
                    reply_markup=ui.session_keyboard(session, event),
                )

    async def _finish_and_render(query: CallbackQuery, session: Session, note: str) -> None:
        username, full_name = _user_fields(query.from_user)
        await service.finalize_session(store, session, username, full_name)
        event = await service.get_event_or_default(store, session.chat_id)
        if isinstance(query.message, Message):
            with suppress(TelegramBadRequest):
                await query.message.edit_media(
                    InputMediaPhoto(
                        media=_board_photo(session),
                        caption=_caption(note + "\n\n🏁 Сессия завершена.", session, event),
                        parse_mode="HTML",
                    ),
                    reply_markup=None,
                )

    @router.callback_query(F.data.regexp(rf"^{ui.CB_PREFIX}:refresh:\d+$"))
    async def cb_refresh(query: CallbackQuery) -> None:
        owner_id = _parse_owner(query.data)
        session = await _load_owned_session(query, owner_id) if owner_id else None
        if session is None:
            return
        await query.answer("Обновлено.")
        await _rerender(query, session, "")

    @router.callback_query(F.data.regexp(rf"^{ui.CB_PREFIX}:noop:\d+$"))
    async def cb_noop(query: CallbackQuery) -> None:
        await query.answer()

    @router.callback_query(F.data.regexp(rf"^{ui.CB_PREFIX}:finish:\d+$"))
    async def cb_finish(query: CallbackQuery) -> None:
        owner_id = _parse_owner(query.data)
        session = await _load_owned_session(query, owner_id) if owner_id else None
        if session is None:
            return
        await query.answer("Завершаем сессию.")
        await _finish_and_render(query, session, "Вы завершили экспедицию.")

    @router.callback_query(F.data.regexp(rf"^{ui.CB_PREFIX}:order:\d+:\d+$"))
    async def cb_order(query: CallbackQuery) -> None:
        owner_id, arg = _parse_owner_arg(query.data)
        session = await _load_owned_session(query, owner_id) if owner_id else None
        if session is None:
            return
        event = await service.get_event_or_default(store, session.chat_id)
        if arg is None or arg >= len(event.requests):
            await query.answer("Заказ недоступен.", show_alert=True)
            return
        order = event.requests[arg]
        rng = random.Random()
        result = engine.start_route(session, order, rng)
        if result.outcome is StartOutcome.OK:
            await query.answer(f"Отплываем на {order.source_island}!")
            await _rerender(
                query,
                session,
                f"📦 Берём заказ: везём <b>{order.needed_item}</b> для <b>{order.merchant}</b>.\n"
                f"🧭 Плывём на остров <b>{order.source_island}</b>.\n"
                f"✍️ Пишите сообщения с <b>{content.HASHTAG}</b> — каждое = шаг вперёд.",
            )
            return
        alerts = {
            StartOutcome.NO_MOVES: "Ходы закончились.",
            StartOutcome.NO_TRAVELS: "Больше поездок за товаром нет (лимит 7).",
            StartOutcome.BUSY: "Сначала доплывите до текущего места.",
            StartOutcome.ALREADY_DONE: "Этот заказ уже выполнен.",
        }
        await query.answer(alerts[result.outcome], show_alert=True)
        if result.outcome is StartOutcome.NO_MOVES and engine.is_exhausted(session):
            await _finish_and_render(query, session, "Ходы закончились.")

    @router.callback_query(F.data.regexp(rf"^{ui.CB_PREFIX}:sail:\d+$"))
    async def cb_sail(query: CallbackQuery, bot: Bot) -> None:
        owner_id = _parse_owner(query.data)
        session = await _load_owned_session(query, owner_id) if owner_id else None
        if session is None:
            return
        rng = random.Random()
        result = engine.sail_forward(session, rng)
        if result.outcome is SailOutcome.BLOCKED:
            await query.answer("Сейчас плыть нельзя.", show_alert=True)
            return
        await query.answer()

        note = _sail_note(result)
        if result.outcome is SailOutcome.CHALLENGE and result.challenge_kind is not None:
            note = f"{note}\n\n{ui.challenge_intro(result.challenge_kind)}"
            await _rerender(query, session, note)
            if result.challenge_kind is ChallengeKind.FISHING:
                _schedule_fishing(bot, store, session.chat_id, session.user_id)
            return

        if engine.is_exhausted(session):
            await _finish_and_render(query, session, note)
            return
        await _rerender(query, session, note)

    # ---------- мини-игра №1: мины ----------

    @router.callback_query(F.data.regexp(rf"^{ui.CB_PREFIX}:mines:\d+:\d+$"))
    async def cb_mines(query: CallbackQuery) -> None:
        owner_id, cell = _parse_owner_arg(query.data)
        session = await _load_owned_session(query, owner_id) if owner_id else None
        if session is None or cell is None:
            return
        result = engine.mines_pick(session, cell, random.Random())
        if result.outcome is MinesOutcome.BLOCKED:
            await query.answer()
            return
        if result.outcome is MinesOutcome.SAFE:
            await query.answer(f"Безопасно! {result.safe_found}/{result.safe_total}")
            await _rerender(query, session, "")
            return
        if result.outcome is MinesOutcome.WIN:
            await query.answer("Все безопасные клетки найдены! 🎉")
            await _after_challenge(query, session, "✅ Испытание пройдено: путь свободен.")
            return
        # BOOM
        await query.answer("💥 Бомба! Шаг назад.", show_alert=True)
        await _after_challenge(query, session, "💥 Бомба! Испытание провалено, шаг назад.")

    # ---------- мини-игра №2: рыбалка ----------

    @router.callback_query(F.data.regexp(rf"^{ui.CB_PREFIX}:fish:\d+$"))
    async def cb_fish(query: CallbackQuery) -> None:
        owner_id = _parse_owner(query.data)
        session = await _load_owned_session(query, owner_id) if owner_id else None
        if session is None:
            return
        outcome = engine.fishing_press(session, time.time())
        if outcome is FishingOutcome.BLOCKED:
            await query.answer()
            return
        if outcome is FishingOutcome.WIN:
            await query.answer("Поймали! 🐟")
            await _after_challenge(query, session, "✅ Рыба поймана: путь свободен.")
            return
        if outcome is FishingOutcome.TOO_EARLY:
            await query.answer("Рано! Сорвалось.", show_alert=True)
            await _after_challenge(query, session, "💨 Поторопились — рыба сорвалась. Шаг назад.")
            return
        await query.answer("Не успели!", show_alert=True)
        await _after_challenge(query, session, "💨 Не успели нажать — рыба ушла. Шаг назад.")

    # ---------- мини-игра №3: акула ----------

    @router.callback_query(F.data.regexp(rf"^{ui.CB_PREFIX}:shark:\d+:(left|straight|right)$"))
    async def cb_shark(query: CallbackQuery) -> None:
        owner_id, direction = _parse_owner_dir(query.data)
        session = await _load_owned_session(query, owner_id) if owner_id else None
        if session is None or direction is None:
            return
        result = engine.shark_pick(session, direction)
        if result.outcome is SharkOutcome.BLOCKED:
            await query.answer()
            return
        attack_label = content.SHARK_DIRECTION_LABELS.get(result.attack or "", "?")
        if result.outcome is SharkOutcome.SURVIVED:
            await query.answer(f"Увернулись! Этап {result.stage}/{result.total_stages}")
            await _rerender(query, session, f"🦈 Акула метнулась {attack_label} — вы ушли! Дальше…")
            return
        if result.outcome is SharkOutcome.WIN:
            await query.answer("Оторвались от акулы! 🎉")
            await _after_challenge(query, session, "✅ Вы ушли от акулы: путь свободен.")
            return
        await query.answer("Акула достала вас!", show_alert=True)
        await _after_challenge(
            query, session, f"🦈 Акула атаковала {attack_label} — провал. Шаг назад."
        )

    async def _after_challenge(query: CallbackQuery, session: Session, note: str) -> None:
        if engine.is_exhausted(session):
            await _finish_and_render(query, session, note)
            return
        await _rerender(query, session, note)

    return router


# ---------- фоновый таймер рыбалки ----------


def _schedule_fishing(bot: Bot, store: SeaStore, chat_id: int, user_id: int) -> None:
    """Запланировать появление кнопки «ЖМИ!» и таймаут реакции."""

    async def _runner() -> None:
        rng = random.Random()
        delay = rng.uniform(content.FISHING_MIN_DELAY, content.FISHING_MAX_DELAY)
        await asyncio.sleep(delay)
        session = await store.get_session(chat_id, user_id)
        if session is None or session.active_challenge is None:
            return
        ch = session.active_challenge
        if ch.kind is not ChallengeKind.FISHING or ch.fishing is None or ch.fishing.armed:
            return
        if not engine.fishing_arm(session, time.time()):
            return
        await store.save_session(session)
        event = await service.get_event_or_default(store, chat_id)
        await _edit_menu(bot, session, "🎣 Поклёвка! ЖМИ!", event)

        await asyncio.sleep(content.FISHING_REACTION_SECONDS + 0.5)
        session2 = await store.get_session(chat_id, user_id)
        if session2 is None:
            return
        if not engine.fishing_timeout(session2):
            return
        note = "💨 Не успели нажать — рыба ушла. Шаг назад."
        event2 = await service.get_event_or_default(store, chat_id)
        if engine.is_exhausted(session2):
            await service.finalize_session(store, session2, None, None)
            await _edit_menu(
                bot,
                session2,
                note + "\n\n🏁 Сессия завершена.",
                event2,
                final=True,
            )
            return
        await store.save_session(session2)
        await _edit_menu(bot, session2, note, event2)

    asyncio.create_task(_runner())  # noqa: RUF006  (fire-and-forget игровой таймер)


async def _edit_menu(
    bot: Bot, session: Session, note: str, event: EventState, *, final: bool = False
) -> None:
    if session.menu_chat_id is None or session.menu_message_id is None:
        return
    markup: InlineKeyboardMarkup | None = None
    if not final:
        markup = ui.session_keyboard(session, event)
    with suppress(TelegramBadRequest):
        await bot.edit_message_media(
            media=InputMediaPhoto(
                media=_board_photo(session),
                caption=_caption(note, session, event),
                parse_mode="HTML",
            ),
            chat_id=session.menu_chat_id,
            message_id=session.menu_message_id,
            reply_markup=markup,
        )


# ---------- вспомогательные функции рендера/парсинга ----------


_CAPTION_LIMIT = 1024


def _compose(note: str, session: Session, event: EventState) -> str:
    body = ui.render_session(session, event)
    if note:
        return f"{note}\n\n{body}"
    return body


def _caption(note: str, session: Session, event: EventState) -> str:
    """Подпись к фото-карте. Обрезаем по целым строкам, чтобы не порвать HTML."""
    text = _compose(note, session, event)
    if len(text) <= _CAPTION_LIMIT:
        return text
    lines = text.split("\n")
    while lines and len("\n".join(lines)) > _CAPTION_LIMIT:
        lines.pop()
    return "\n".join(lines)


def _board_photo(session: Session) -> BufferedInputFile:
    return BufferedInputFile(pixmap.board_png(session), filename="map.png")


def _plural_cells(n: int) -> str:
    """Согласование слова «клетка» с числом (1 клетку, 2 клетки, 5 клеток)."""
    if n % 10 == 1 and n % 100 != 11:
        return "клетку"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "клетки"
    return "клеток"


def _sail_note(result: engine.SailResult) -> str:
    if result.outcome is SailOutcome.EMPTY:
        return "🌊 Пустая вода — плывём дальше."
    if result.outcome is SailOutcome.TREASURE:
        steps = result.treasure_steps
        return (
            f"💰 Сокровище! +{result.treasure_gain} очков, "
            f"корабль рванул вперёд на {steps} {_plural_cells(steps)}."
        )
    if result.outcome is SailOutcome.DELIVERED:
        return f"🎁 Заказ доставлен для {result.delivered_merchant}! +{result.reward} очков."
    if result.outcome is SailOutcome.CHALLENGE:
        return "⚠️ Впереди испытание!"
    return ""


def _parse_owner(data: str | None) -> int | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def _parse_owner_arg(data: str | None) -> tuple[int | None, int | None]:
    if not data:
        return None, None
    parts = data.split(":")
    if len(parts) < 4:
        return _parse_owner(data), None
    try:
        return int(parts[2]), int(parts[3])
    except ValueError:
        return None, None


def _parse_owner_dir(data: str | None) -> tuple[int | None, str | None]:
    if not data:
        return None, None
    parts = data.split(":")
    if len(parts) < 4:
        return _parse_owner(data), None
    try:
        return int(parts[2]), parts[3]
    except ValueError:
        return None, None
