"""Жива крапка стану: ореол дихає, поки фарм справді йде.

Навіщо окремий віджет. Мітка «Іде» відповідає на питання, чи ростуть хвилини,
але статичний текст однаково виглядає як напис — його читають раз і більше не
помічають. Пульсація помітна боковим зором: якщо крапка дихає, цикл живий;
якщо завмерла — щось не так, і це видно, не вчитуючись.

Чому Canvas, а не готовий віджет. У Tk немає ні альфа-каналу для окремих
елементів, ні анімацій. Прозорість ореолу імітуємо змішуванням його кольору з
кольором тла: тло тут завжди відоме (ми самі його задаємо), тож результат
візуально не відрізнити від справжнього згасання.

Анімація живе на `after()`, а не в загальному циклі вікна: так вона
зупиняється разом із віджетом і не потребує, щоб її хтось смикав ззовні.
"""
from __future__ import annotations

import colorsys
import tkinter as tk

# Період одного «вдиху». 1,6 с — повільніше за пульс, тому читається як
# спокійна робота, а не як тривога. Коротший період починає дратувати.
PERIOD_MS = 1600
# 25 кадрів на секунду: рух уже плавний, а перемальовування двох кіл раз на
# 40 мс не помітне для CPU навіть на слабкій машині.
FRAME_MS = 40


def rainbow(phase: float, *, saturation: float = 0.62,
            value: float = 0.95) -> str:
    """Колір веселки за фазою 0..1. Використовується для «переливчастої» смуги.

    Насиченість свідомо не повна: чисті кольори на темному тлі ріжуть око, а
    приглушені лишаються святковими, але не заважають читати цифри поруч.
    """
    red, green, blue = colorsys.hsv_to_rgb(phase % 1.0, saturation, value)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def blend(colour: str, background: str, amount: float) -> str:
    """Змішує `colour` із `background`. amount=0 — чистий колір, 1 — саме тло.

    Заміна прозорості: Tk не вміє малювати напівпрозорі фігури на Canvas.
    """
    amount = max(0.0, min(1.0, amount))
    parts = []
    for channel in (1, 3, 5):
        top = int(colour[channel:channel + 2], 16)
        bottom = int(background[channel:channel + 2], 16)
        parts.append(round(top + (bottom - top) * amount))
    red, green, blue = parts
    return f"#{red:02x}{green:02x}{blue:02x}"


class PulseDot(tk.Canvas):
    """Крапка з ореолом. `start()` оживляє, `stop()` лишає статичну крапку."""

    def __init__(self, parent: tk.Misc, *, background: str, colour: str,
                 size: int = 18, radius: int = 4) -> None:
        super().__init__(parent, width=size, height=size, highlightthickness=0,
                         borderwidth=0, background=background)
        self._background = background
        self._colour = colour
        self._radius = radius
        self._centre = size / 2
        self._phase = 0.0
        self._job: str | None = None
        # порядок важливий: ореол малюється першим, щоб крапка лягла поверх
        self._halo = self.create_oval(0, 0, 0, 0, outline="", fill=background)
        self._dot = self.create_oval(0, 0, 0, 0, outline="", fill=colour)
        self._draw()

    # ----------------------------------------------------------- малювання

    def _draw(self) -> None:
        c, r = self._centre, self._radius
        self.coords(self._dot, c - r, c - r, c + r, c + r)
        self.itemconfigure(self._dot, fill=self._colour)

        if self._job is None:
            # спокій: ореолу немає, інакше він завмер би на випадковій фазі й
            # виглядав як недомальована пляма
            self.coords(self._halo, c, c, c, c)
            self.itemconfigure(self._halo, fill=self._background)
            return

        # ореол росте й розчиняється у тлі — разом це читається як «дихання»
        grown = r * (1.0 + 1.4 * self._phase)
        self.coords(self._halo, c - grown, c - grown, c + grown, c + grown)
        self.itemconfigure(self._halo, fill=blend(self._colour, self._background,
                                                  0.35 + 0.65 * self._phase))

    def _tick(self) -> None:
        self._phase += FRAME_MS / PERIOD_MS
        if self._phase >= 1.0:
            self._phase = 0.0
        self._draw()
        self._job = self.after(FRAME_MS, self._tick)

    # ------------------------------------------------------------ керування

    def start(self) -> None:
        if self._job is not None:
            return
        self._phase = 0.0
        self._job = self.after(FRAME_MS, self._tick)

    def stop(self) -> None:
        if self._job is None:
            return
        self.after_cancel(self._job)
        self._job = None
        self._draw()

    def recolour(self, *, colour: str | None = None,
                 background: str | None = None) -> None:
        """Зміна теми або стану. Анімацію не перериває."""
        if colour is not None:
            self._colour = colour
        if background is not None:
            self._background = background
            self.configure(background=background)
        self._draw()

    def destroy(self) -> None:
        # без цього after() пробує малювати на вже знищеному Canvas і сипле
        # винятками в консоль при закритті вікна
        self.stop()
        super().destroy()
