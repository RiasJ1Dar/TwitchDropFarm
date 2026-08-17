"""Значок програми — один на вікно, трей і сам `.exe`.

Малюється кодом, а не лежить картинкою поруч: вимога проєкту — один
самодостатній файл, і зайвий `.png` біля нього цьому суперечив би. Єдиний
виняток — `icon.ico`, який потрібен збирачу; його робить `tools/make_icon.py`
із цієї ж функції, тож розходитись їм нема з чого.

Що на ньому: кайло в мовному балоні. Балон — натяк на Twitch кольором і
формою, але **не копія логотипа**: це чужий товарний знак, а репозиторій
публічний.

Про сам малюнок кайла, щоб не переробляти його втретє: лезо мусить бути
полігоном змінної товщини й уся фігура — нахиленою. Тонка дуга сталої ширини
читається як цифра «сім», а симетрична смуга без загострень — як парасолька.
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
# Малюємо збільшено й зменшуємо: на нахиленій фігурі краї інакше рвані
SUPER = 8


def _pick(size: int, blade: float, thick: float, grip: float,
          tilt: int = -32) -> Image.Image:
    """Кайло на прозорому шарі того ж розміру."""
    big = size * SUPER
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    centre_x, centre_y = big * 0.5, big * 0.62
    radius = big * blade

    outer: list[tuple[float, float]] = []
    inner: list[tuple[float, float]] = []
    steps = 60
    for step in range(steps + 1):
        part = step / steps
        angle = math.radians(20 + 140 * part)
        # товщина максимальна посередині й сходить у вістря на кінцях
        width = big * thick * math.sin(math.pi * part) ** 0.55
        for store, r in ((outer, radius), (inner, radius - width)):
            store.append((centre_x + r * math.cos(angle),
                          centre_y - r * math.sin(angle)))
    draw.polygon(outer + inner[::-1], fill=INK)

    half = big * 0.052
    draw.rectangle((centre_x - half, centre_y - radius + big * thick * 0.5,
                    centre_x + half, centre_y + big * grip), fill=INK)
    # Image.Resampling, а не Image.BICUBIC: старі імена в Pillow — застарілі
    # псевдоніми, яких уже немає в типах, і колись не стане в самій бібліотеці
    return layer.rotate(
        tilt, resample=Image.Resampling.BICUBIC, expand=False,
    ).resize((size, size), Image.Resampling.LANCZOS)


def make_icon(size: int = 64, state: str = "active") -> Image.Image:
    """Значок заданого розміру. `state` міняє лише колір тла."""
    colour = STATE_COLOURS.get(state, STATE_COLOURS["idle"])
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def at(fraction: float) -> float:
        return size * fraction

    # балон: тіло й хвостик
    draw.rounded_rectangle((at(0.05), at(0.05), at(0.95), at(0.76)),
                           radius=at(0.17), fill=colour)
    draw.polygon([(at(0.60), at(0.74)), (at(0.82), at(0.74)), (at(0.64), at(0.98))],
                 fill=colour)

    image.alpha_composite(
        _pick(size, blade=0.36, thick=0.13, grip=0.28),
        (0, -int(at(0.07))),
    )
    return image


def ico_sizes() -> list[int]:
    """Розміри, які Windows справді використовує в різних місцях."""
    return [16, 24, 32, 48, 64, 128, 256]
