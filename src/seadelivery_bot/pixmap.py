"""Пиксельная карта маршрута для события «Морская доставка».

Рисует сцену из крупных «пикселей» (низкое внутреннее разрешение + апскейл
методом ближайшего соседа): вода, домашний порт, кораблик игрока, «туман» на
неизведанных клетках впереди и остров назначения с флажком. Возвращает PNG в
виде байтов — готов к отправке как фото в Telegram.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from .models import Session

# Палитра
SEA_TOP = (58, 124, 165)
SEA_BOT = (38, 96, 138)
WAVE = (104, 164, 196)
SAND = (224, 196, 124)
SAND_DK = (196, 160, 96)
PALM = (60, 150, 80)
PALM_DK = (40, 120, 60)
TRUNK = (120, 80, 48)
HULL = (130, 84, 48)
HULL_DK = (96, 60, 34)
SAIL = (245, 245, 235)
MAST = (90, 60, 36)
FLAG = (210, 60, 60)
FOG = (150, 162, 172)
FOG_DK = (120, 134, 146)
WAKE = (170, 205, 225)

CW = 22  # ширина клетки во внутренних пикселях
CH = 30  # высота сцены во внутренних пикселях
SCALE = 8  # коэффициент апскейла → «пиксельный» вид


def _sea_background(d: ImageDraw.ImageDraw, width: int) -> None:
    for y in range(CH):
        t = y / max(1, CH - 1)
        col = tuple(int(SEA_TOP[i] + (SEA_BOT[i] - SEA_TOP[i]) * t) for i in range(3))
        d.line([(0, y), (width, y)], fill=col)


def _tile_base(d: ImageDraw.ImageDraw, x: int) -> None:
    d.point([(x + 3, CH - 8), (x + 9, CH - 6), (x + 15, CH - 8)], fill=WAVE)


def _draw_island(d: ImageDraw.ImageDraw, x: int, *, flag: bool) -> None:
    cx = x + CW // 2
    base = CH - 9
    d.ellipse([cx - 8, base - 3, cx + 8, base + 6], fill=SAND)
    d.ellipse([cx - 8, base + 1, cx + 8, base + 6], fill=SAND_DK)
    d.rectangle([cx - 1, base - 9, cx + 1, base - 2], fill=TRUNK)
    d.ellipse([cx - 6, base - 13, cx + 1, base - 8], fill=PALM)
    d.ellipse([cx - 1, base - 13, cx + 6, base - 8], fill=PALM_DK)
    if flag:
        d.rectangle([cx + 4, base - 14, cx + 5, base - 4], fill=MAST)
        d.polygon([(cx + 5, base - 14), (cx + 12, base - 12), (cx + 5, base - 10)], fill=FLAG)


def _draw_ship(d: ImageDraw.ImageDraw, x: int) -> None:
    cx = x + CW // 2
    base = CH - 8
    d.rectangle([cx, base - 16, cx + 1, base - 4], fill=MAST)
    d.polygon([(cx, base - 15), (cx, base - 5), (cx - 8, base - 5)], fill=SAIL)
    d.polygon([(cx + 2, base - 15), (cx + 2, base - 5), (cx + 9, base - 6)], fill=SAIL)
    d.polygon(
        [(cx - 9, base - 4), (cx + 10, base - 4), (cx + 6, base + 2), (cx - 5, base + 2)],
        fill=HULL,
    )
    d.rectangle([cx - 5, base + 1, cx + 6, base + 3], fill=HULL_DK)


def _draw_traveled(d: ImageDraw.ImageDraw, x: int) -> None:
    cx = x + CW // 2
    base = CH - 6
    d.point([(cx - 3, base), (cx, base + 1), (cx + 3, base)], fill=WAKE)


def _draw_fog(d: ImageDraw.ImageDraw, x: int) -> None:
    cx = x + CW // 2
    cy = CH - 14
    for dx, dy, r in ((-5, 2, 5), (0, -1, 6), (5, 2, 5), (1, 4, 6)):
        d.ellipse([cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r], fill=FOG)
    d.ellipse([cx - 6, cy + 4, cx + 6, cy + 9], fill=FOG_DK)


def _scaled(img: Image.Image) -> bytes:
    big = img.resize((img.width * SCALE, img.height * SCALE), Image.Resampling.NEAREST)
    buf = BytesIO()
    big.save(buf, format="PNG")
    return buf.getvalue()


def _render_route(board_len: int, position: int) -> Image.Image:
    n = board_len + 1  # +1 — домашний порт слева
    width = n * CW + 6
    img = Image.new("RGB", (width, CH), SEA_TOP)
    d = ImageDraw.Draw(img)
    _sea_background(d, width)
    last = board_len - 1
    for i in range(n):
        x = 3 + i * CW
        _tile_base(d, x)
        if i == 0:
            _draw_island(d, x, flag=False)  # домашний порт
            continue
        idx = i - 1
        if idx == position:
            _draw_ship(d, x)
        elif idx < position:
            _draw_traveled(d, x)
        elif idx == last:
            _draw_island(d, x, flag=True)
        else:
            _draw_fog(d, x)
    return img


def _render_port() -> Image.Image:
    """Сцена «вы в порту»: большой домашний остров, корабль пришвартован."""
    width = 6 * CW + 6
    img = Image.new("RGB", (width, CH), SEA_TOP)
    d = ImageDraw.Draw(img)
    _sea_background(d, width)
    cx = width // 2
    base = CH - 8
    # большой песчаный остров
    d.ellipse([cx - 26, base - 5, cx + 26, base + 8], fill=SAND)
    d.ellipse([cx - 26, base + 2, cx + 26, base + 8], fill=SAND_DK)
    # две пальмы
    for off in (-12, 10):
        d.rectangle([cx + off - 1, base - 12, cx + off + 1, base - 3], fill=TRUNK)
        d.ellipse([cx + off - 7, base - 17, cx + off + 1, base - 11], fill=PALM)
        d.ellipse([cx + off - 1, base - 17, cx + off + 7, base - 11], fill=PALM_DK)
    # пришвартованный корабль слева у острова
    _draw_ship(d, cx - 34)
    _tile_base(d, 4)
    _tile_base(d, width - CW)
    return img


def board_png(session: Session) -> bytes:
    """Отрисовать карту текущего состояния сессии в PNG (байты)."""
    if session.on_route and session.board:
        img = _render_route(len(session.board), session.position)
    else:
        img = _render_port()
    return _scaled(img)
