"""Тесты подписи карточки: пометка владельца сессии."""

from __future__ import annotations

from seadelivery_bot import handlers
from seadelivery_bot.models import Session


def _session(**kw: object) -> Session:
    return Session(chat_id=-100123, user_id=777, round_no=1, **kw)  # type: ignore[arg-type]


def test_owner_line_mentions_name_and_username() -> None:
    session = _session(owner_name="Капитан Джек", owner_username="jack")
    line = handlers._owner_line(session)
    assert '<a href="tg://user?id=777">Капитан Джек</a>' in line
    assert "(@jack)" in line


def test_owner_line_escapes_html() -> None:
    session = _session(owner_name="<b>Зло</b> & Co", owner_username=None)
    line = handlers._owner_line(session)
    assert "&lt;b&gt;\u0417\u043b\u043e&lt;/b&gt; &amp; Co" in line
    assert "<b>Зло</b>" not in line


def test_owner_line_empty_without_owner() -> None:
    assert handlers._owner_line(_session()) == ""
