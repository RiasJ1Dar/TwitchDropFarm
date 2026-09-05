"""Коротке святкування, коли дроп нарешті забрано.

Навіщо. Заради цієї миті програма й працює годинами, а виглядала вона як ще
один рядок у журналі — такий самий, як «читаю інвентар». Півтори секунди
конфеті над смугою прогресу коштують нічого, а момент стає видно навіть краєм
ока.

Чому власний Canvas, а не готовий віджет. Анімацій у Tk немає взагалі, а
прозорості для окремих елементів — тим паче. Тому смуга малюється поверх
картки з тим самим фоном: поки конфеті летить, під ним нічого важливого немає,
а щойно долетіло — накладка ховається й картка знову ціла.

Тримається на `after`, як і `pulse.PulseDot`: анімація зупиняється разом із
віджетом і не потребує, щоб її хтось смикав ззовні.
"""
from __future__ import annotations

import logging
import random
import tkinter as tk

log = logging.getLogger("TwitchDrops")

# Кадр анімації. 25 к/с — рух плавний, а перемальовування двох десятків
# дрібних овалів раз на 40 мс не помітне навіть на слабкій машині.
FRAME_MS = 40
# Скільки живе святкування. Довше починає дратувати, коротше — не встигаєш
# помітити.
LIFETIME_MS = 1400
PIECES = 22
# Прискорення вниз, у пікселях за кадр у квадраті. Підібране так, щоб частинки
# встигали піднятись і впасти за час життя.
GRAVITY = 0.35


class Confetti(tk.Canvas):
    """Смуга, якою зрідка сиплеться конфеті. Решту часу її не видно."""

    def __init__(self, parent: tk.Misc, *, background: str,
                 colours: tuple[str, ...], height: int = 46) -> None:
        super().__init__(parent, height=height, highlightthickness=0,
                         borderwidth=0, background=background)
        self._background = background
        self._colours = colours
        self._height = height
        self._pieces: list[tuple[int, float, float, float, float]] = []
        self._job: str | None = None
        self._left = 0

    def recolour(self, *, background: str | None = None,
                 colours: tuple[str, ...] | None = None) -> None:
        if background is not None:
            self._background = background
            self.configure(background=background)
        if colours is not None:
            self._colours = colours

    def burst(self) -> None:
        """Запускає святкування. Повторний виклик під час нього — не заважає."""
        try:
            self._burst()
        except Exception:
            # Свято, яке не вдалося, мусить зникнути без сліду: інакше
            # напівстворена накладка лишається поверх картки й перекриває
            # текст. Саме так і виглядав дефект, який це навчило.
            log.exception("Святкування не вдалося показати")
            self._stop()

    def _burst(self) -> None:
        if self._job is not None:
            self._stop()
        width = max(self.winfo_width(), 200)
        self.delete("all")
        self._pieces = []
        for _ in range(PIECES):
            size = random.uniform(3.0, 6.0)
            x = random.uniform(0.0, width)
            y = self._height - random.uniform(0.0, 8.0)
            item = self.create_oval(x, y, x + size, y + size, outline="",
                                    fill=random.choice(self._colours))
            # вгору й трохи вбік: летить угору, гравітація повертає донизу
            self._pieces.append((item, x, y,
                                 random.uniform(-1.8, 1.8),
                                 random.uniform(-5.5, -3.0)))
        self.place(relx=0, rely=0, relwidth=1.0)
        # ⚠️ `tk.Misc.lift(self)`, а НЕ `self.lift()`. У `Canvas` метод `lift`
        # перевизначений як псевдонім `tag_raise` — підняти намальований
        # елемент над іншими, — і без аргументу він падає з
        # «wrong # args: should be ".!confetti raise tagOrId ?aboveThis?"».
        # Тут потрібне зовсім інше: підняти сам віджет над сусідами.
        #
        # Ціна помилки була саме такою, якою буває в анімаціях: виняток летів
        # ПІСЛЯ `place()`, але ДО першого `after()`. Накладка з частинками вже
        # лежала поверх картки, а анімація не стартувала жодного разу — тобто
        # `_stop()` не викликався ніколи, і конфеті лишалось на екрані
        # назавжди, перекриваючи назву каналу. Виглядало як «артефакти
        # малювання», хоча малювання тут ні до чого.
        tk.Misc.lift(self)
        self._left = LIFETIME_MS // FRAME_MS
        self._job = self.after(FRAME_MS, self._tick)

    def _tick(self) -> None:
        try:
            self._frame()
        except Exception:
            log.exception("Кадр святкування не намалювався")
            self._stop()

    def _frame(self) -> None:
        self._left -= 1
        moved: list[tuple[int, float, float, float, float]] = []
        for item, x, y, dx, dy in self._pieces:
            dy += GRAVITY
            x += dx
            y += dy
            self.move(item, dx, dy)
            moved.append((item, x, y, dx, dy))
        self._pieces = moved
        if self._left <= 0:
            self._stop()
            return
        self._job = self.after(FRAME_MS, self._tick)

    def _stop(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self.delete("all")
        self._pieces = []
        # ховаємо накладку: поки її не видно, картка під нею ціла
        self.place_forget()

    def destroy(self) -> None:
        # без цього `after` пробує малювати на знищеному Canvas і сипле
        # винятками в консоль при закритті вікна
        self._stop()
        super().destroy()
