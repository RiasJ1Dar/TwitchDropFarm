"""Значок програми — один на вікно, трей і сам `.exe`.

Малюється кодом, а не лежить картинкою поруч: вимога проєкту — один
самодостатній файл, і зайвий `.png` біля нього цьому суперечив би. Єдиний
виняток — `icon.ico`, який потрібен збирачу; його робить `tools/make_icon.py`
із цієї ж функції, тож розходитись їм нема з чого.

Що на ньому: крапля падає у скриню. `drops` — це буквально краплі, і програма
саме ловить те, що падає, поки людини немає за комп'ютером. Перший підхід був
із кайлом, бо в назві стоїть «miner», але нічого не копають — і значок брехав
про те, що робить програма.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

# Колір несе значення, а не красу: у треї він показує, чи йде фарм.
STATE_COLOURS = {
    "active": (145, 71, 255),   # фіолетовий Twitch — дивимось і рахуємо хвилини
    "idle": (110, 110, 120),    # сірий — нічого не фармимо
    "error": (200, 60, 60),     # червоний — зв'язок утрачено або збій
}
INK = (255, 255, 255, 245)
# Малюємо збільшено й зменшуємо: діагоналі краплі інакше виходять драбинкою
SUPER = 8
# Нижче цього розміру дрібні деталі скрині зливаються в пляму, тож їх немає
DETAIL_FROM = 24


def make_icon(size: int = 64, state: str = "active") -> Image.Image:
    """Значок заданого розміру. `state` міняє лише колір тла."""
    colour = STATE_COLOURS.get(state, STATE_COLOURS["idle"])
    big = size * SUPER
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def at(fraction: float) -> float:
        return big * fraction

    # Тло — скруглений квадрат, а не коло: у дрібних розмірах він дає більше
    # площі під силует і краще тримає форму серед круглих сусідів у треї.
    draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=at(0.24), fill=colour)

    # ---- крапля: коло знизу, вістря вгорі
    centre_x, centre_y, radius = at(0.50), at(0.30), at(0.13)
    draw.ellipse((centre_x - radius, centre_y - radius,
                  centre_x + radius, centre_y + radius), fill=INK)
    flank = math.radians(52)
    draw.polygon([
        (centre_x, centre_y - radius * 2.5),
        (centre_x - radius * math.cos(flank), centre_y - radius * math.sin(flank)),
        (centre_x + radius * math.cos(flank), centre_y - radius * math.sin(flank)),
    ], fill=INK)

    # ---- скриня, у яку падає нагорода
    top, bottom = at(0.56), at(0.84)
    draw.rounded_rectangle((at(0.20), top, at(0.80), bottom),
                           radius=at(0.03), fill=INK)
    if size >= DETAIL_FROM:
        # прорізь кришки й замок — лише там, де їх видно
        draw.rectangle((at(0.20), top + at(0.07), at(0.80), top + at(0.11)),
                       fill=colour)
        draw.rectangle((at(0.46), top + at(0.04), at(0.54), top + at(0.15)),
                       fill=colour)

    return image.resize((size, size), Image.LANCZOS)


def ico_sizes() -> list[int]:
    """Розміри, які Windows справді використовує в різних місцях."""
    return [16, 24, 32, 48, 64, 128, 256]
