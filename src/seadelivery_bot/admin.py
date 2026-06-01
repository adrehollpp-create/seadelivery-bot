"""Административный роутер события «Морская доставка».

Управление набором и раундами, просмотр статистики (общей и по раундам)
и финальный рейтинг. Доступно владельцу/администраторам чата
(а также супер-владельцу из ``OWNER_ID``).
"""

from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import content, service, ui
from .config import Config
from .models import EventStatus, PlayerStats
from .permissions import is_chat_admin
from .service import OpenOutcome, RoundOutcome
from .store import SeaStore

logger = logging.getLogger(__name__)

ADMIN = ui.ADMIN_PREFIX


def _player_label(stat: PlayerStats) -> str:
    if stat.username:
        return f"@{stat.username}"
    if stat.full_name:
        return escape(stat.full_name)
    return f"ID {stat.user_id}"


_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
_THIN = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"
_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _rank_badge(idx: int) -> str:
    return _MEDALS.get(idx, f"<b>{idx}.</b>")


def _win_rate(st: PlayerStats) -> str:
    total = st.challenges_won + st.challenges_failed
    if total == 0:
        return "—"
    return f"{round(st.challenges_won * 100 / total)}%"


def _format_player_card(idx: int, st: PlayerStats) -> str:
    return (
        f"{_rank_badge(idx)} <b>{_player_label(st)}</b> · 💰 <b>{st.score}</b> очк.\n"
        f"   🎮 игр: {st.games_played}   📦 заказов: {st.orders_completed}\n"
        f"   ⚔️ испытания: ✅ {st.challenges_won} / ❌ {st.challenges_failed} ({_win_rate(st)})\n"
        f"   👣 ходов: {st.moves_used}   🚢 переходов: {st.island_travels}"
    )


def _format_stats_table(title: str, rows: list[PlayerStats]) -> str:
    if not rows:
        return f"📊 <b>{title}</b>\n\n<i>Пока нет данных.</i>"
    cards = "\n".join(
        _format_player_card(idx, st) + ("" if idx == len(rows) else f"\n{_THIN}")
        for idx, st in enumerate(rows, start=1)
    )
    totals = (
        f"Σ игр: {sum(s.games_played for s in rows)} · "
        f"заказов: {sum(s.orders_completed for s in rows)} · "
        f"испыт.: ✅ {sum(s.challenges_won for s in rows)} / "
        f"❌ {sum(s.challenges_failed for s in rows)}"
    )
    return (
        f"📊 <b>{title}</b>\n"
        f"<i>участников: {len(rows)}</i>\n"
        f"{_DIVIDER}\n"
        f"{cards}\n"
        f"{_DIVIDER}\n"
        f"{totals}"
    )


def _format_final(rows: list[PlayerStats]) -> str:
    if not rows:
        return "🏆 <b>Финальный рейтинг «Морской доставки»</b>\n\n<i>Статистики пока нет.</i>"
    lines = ["🏆 <b>Финальный рейтинг «Морской доставки»</b>", _DIVIDER]
    for idx, st in enumerate(rows[:20], start=1):
        lines.append(f"{_rank_badge(idx)} <b>{_player_label(st)}</b> — 💰 <b>{st.score}</b> очк.")
    lines.append(_DIVIDER)
    lines.append("🎉 Поздравляем победителей!")
    return "\n".join(lines)


def _panel_keyboard() -> InlineKeyboardMarkup:
    def btn(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"{ADMIN}:{action}")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("📣 Открыть набор", "open"), btn("▶️ Запустить раунд", "round")],
            [btn("⏹ Завершить раунд", "endround"), btn("👥 Игроки", "players")],
            [btn("📊 Статистика", "stats"), btn("🏆 Финал", "final")],
            [btn("🆕 Новое событие", "newevent")],
        ]
    )


def make_sea_admin_router(store: SeaStore, config: Config) -> Router:
    router = Router(name="sea_delivery_admin")

    async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
        if config.super_owner_id is not None and user_id == config.super_owner_id:
            return True
        return await is_chat_admin(bot, chat_id, user_id)

    async def _guard(message: Message, bot: Bot) -> bool:
        if message.from_user is None or message.chat.type == "private":
            return False
        if not await _is_admin(bot, message.chat.id, message.from_user.id):
            return False
        return True

    # ---------- команды ----------

    @router.message(Command("sea_panel"))
    async def cmd_panel(message: Message, bot: Bot) -> None:
        if not await _guard(message, bot):
            return
        await message.answer(
            "🛠 <b>Админ-панель «Морская доставка»</b>",
            reply_markup=_panel_keyboard(),
            parse_mode="HTML",
        )

    @router.message(Command("sea_newevent"))
    async def cmd_newevent(message: Message, bot: Bot) -> None:
        if not await _guard(message, bot):
            return
        await store.reset_event(message.chat.id)
        await service.get_event_or_default(store, message.chat.id)
        await message.answer(
            "🆕 Новое событие создано. Вся прежняя статистика по чату очищена.\n"
            "Откройте набор: <code>/sea_open</code>.",
            parse_mode="HTML",
        )

    @router.message(Command("sea_open"))
    async def cmd_open(message: Message, bot: Bot) -> None:
        if not await _guard(message, bot):
            return
        result = await service.open_recruitment(store, message.chat.id)
        if result.outcome is OpenOutcome.FINISHED:
            await message.answer(
                "Все 5 раундов уже сыграны. Запустите новое событие: <code>/sea_newevent</code>.",
                parse_mode="HTML",
            )
            return
        if result.outcome is OpenOutcome.BUSY:
            await message.answer(
                "Сейчас идёт раунд. Сначала завершите его: <code>/sea_endround</code>.",
                parse_mode="HTML",
            )
            return
        await message.answer(
            ui.render_lobby(result.event, 0),
            reply_markup=ui.lobby_keyboard(result.event.recruit_round),
            parse_mode="HTML",
        )

    @router.message(Command("sea_round"))
    async def cmd_round(message: Message, bot: Bot) -> None:
        if not await _guard(message, bot):
            return
        result = await service.start_round(store, message.chat.id)
        if result.outcome is RoundOutcome.NO_RECRUITMENT:
            await message.answer(
                "Сначала откройте набор: <code>/sea_open</code>.", parse_mode="HTML"
            )
            return
        if result.outcome is RoundOutcome.FINISHED:
            await message.answer("Все раунды уже сыграны.", parse_mode="HTML")
            return
        count = await store.count_registrations(message.chat.id, result.event.current_round)
        await message.answer(
            f"▶️ <b>Раунд {result.event.current_round} начался!</b> Игроков: {count}.\n\n"
            + ui.format_requests(result.event)
            + f"\n\nИгроки, напишите боту с {content.HASHTAG}, чтобы отправиться в путь.",
            parse_mode="HTML",
        )

    @router.message(Command("sea_endround"))
    async def cmd_endround(message: Message, bot: Bot) -> None:
        if not await _guard(message, bot):
            return
        result = await service.end_round(store, message.chat.id)
        if result.outcome is RoundOutcome.NO_RECRUITMENT:
            await message.answer("Сейчас нет активного раунда.", parse_mode="HTML")
            return
        round_no = result.event.current_round
        rows = await store.round_stats(message.chat.id, round_no)
        text = _format_stats_table(f"Итоги раунда {round_no}", rows[:15])
        if result.event.status is EventStatus.FINISHED:
            text += "\n\n🏁 Событие завершено! Финальный рейтинг: <code>/sea_final</code>."
        else:
            text += "\n\nСледующий набор: <code>/sea_open</code>."
        await message.answer(text, parse_mode="HTML")

    @router.message(Command("sea_players"))
    async def cmd_players(message: Message, bot: Bot) -> None:
        if not await _guard(message, bot):
            return
        event = await service.get_event_or_default(store, message.chat.id)
        round_no = event.recruit_round if event.recruit_round else event.current_round
        regs = await store.list_registrations(message.chat.id, round_no)
        if not regs:
            await message.answer(f"На раунд {round_no} пока никто не записан.", parse_mode="HTML")
            return
        header = (
            f"<b>Записаны на раунд {round_no}</b> ({len(regs)}/{content.MAX_PLAYERS_PER_ROUND}):"
        )
        lines = [header]
        for uid, username, full_name in regs:
            label = (
                f"@{username}" if username else (escape(full_name) if full_name else f"ID {uid}")
            )
            lines.append(f"• {label}")
        await message.answer("\n".join(lines), parse_mode="HTML")

    @router.message(Command("sea_stats"))
    async def cmd_stats(message: Message, bot: Bot) -> None:
        if not await _guard(message, bot):
            return
        rows = await store.player_stats(message.chat.id)
        await message.answer(
            _format_stats_table("Статистика игроков (всё событие)", rows[:20]),
            parse_mode="HTML",
        )

    @router.message(Command("sea_roundstats"))
    async def cmd_roundstats(message: Message, command: CommandObject, bot: Bot) -> None:
        if not await _guard(message, bot):
            return
        event = await service.get_event_or_default(store, message.chat.id)
        round_no = event.current_round
        if command.args:
            try:
                round_no = int(command.args.strip().split()[0])
            except ValueError:
                pass
        rows = await store.round_stats(message.chat.id, round_no)
        await message.answer(
            _format_stats_table(f"Статистика раунда {round_no}", rows[:20]),
            parse_mode="HTML",
        )

    @router.message(Command("sea_final"))
    async def cmd_final(message: Message, bot: Bot) -> None:
        # Финальный рейтинг доступен всем — это итоги события.
        rows = await store.player_stats(message.chat.id)
        await message.answer(_format_final(rows), parse_mode="HTML")

    # ---------- callbacks панели ----------

    @router.callback_query(F.data.startswith(f"{ADMIN}:"))
    async def cb_panel(query: CallbackQuery, bot: Bot) -> None:
        if query.message is None or query.from_user is None or query.data is None:
            await query.answer()
            return
        if not isinstance(query.message, Message):
            await query.answer()
            return
        chat_id = query.message.chat.id
        if not await _is_admin(bot, chat_id, query.from_user.id):
            await query.answer("Только для администраторов.", show_alert=True)
            return

        action = query.data.split(":", 1)[1]
        await query.answer()
        if action == "open":
            open_res = await service.open_recruitment(store, chat_id)
            if open_res.outcome is OpenOutcome.OK:
                await query.message.answer(
                    ui.render_lobby(open_res.event, 0),
                    reply_markup=ui.lobby_keyboard(open_res.event.recruit_round),
                    parse_mode="HTML",
                )
            else:
                await query.message.answer(
                    "Нельзя открыть набор: раунд идёт или событие завершено.",
                    parse_mode="HTML",
                )
        elif action == "round":
            round_res = await service.start_round(store, chat_id)
            if round_res.outcome is RoundOutcome.OK:
                await query.message.answer(
                    f"▶️ <b>Раунд {round_res.event.current_round} начался!</b>\n\n"
                    + ui.format_requests(round_res.event),
                    parse_mode="HTML",
                )
            else:
                await query.message.answer("Сначала откройте набор (/sea_open).", parse_mode="HTML")
        elif action == "endround":
            end_res = await service.end_round(store, chat_id)
            if end_res.outcome is RoundOutcome.OK:
                rows = await store.round_stats(chat_id, end_res.event.current_round)
                await query.message.answer(
                    _format_stats_table(f"Итоги раунда {end_res.event.current_round}", rows[:15]),
                    parse_mode="HTML",
                )
            else:
                await query.message.answer("Сейчас нет активного раунда.", parse_mode="HTML")
        elif action == "players":
            event = await service.get_event_or_default(store, chat_id)
            round_no = event.recruit_round if event.recruit_round else event.current_round
            regs = await store.list_registrations(chat_id, round_no)
            if not regs:
                await query.message.answer(f"На раунд {round_no} никто не записан.")
            else:
                lines = [f"Записаны на раунд {round_no} ({len(regs)}):"]
                for uid, username, full_name in regs:
                    label = (
                        f"@{username}"
                        if username
                        else (escape(full_name) if full_name else f"ID {uid}")
                    )
                    lines.append(f"• {label}")
                await query.message.answer("\n".join(lines), parse_mode="HTML")
        elif action == "stats":
            rows = await store.player_stats(chat_id)
            await query.message.answer(
                _format_stats_table("Статистика игроков", rows[:20]), parse_mode="HTML"
            )
        elif action == "final":
            rows = await store.player_stats(chat_id)
            await query.message.answer(_format_final(rows), parse_mode="HTML")
        elif action == "newevent":
            await store.reset_event(chat_id)
            await query.message.answer(
                "🆕 Событие сброшено. Откройте набор: /sea_open.", parse_mode="HTML"
            )

    return router
