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

import random
import tkinter as tk

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
        # У справжньому tkinter це `Misc.lift(aboveThis=None)` — «підняти над
        # усіма сусідами», аргумент необов'язковий. Стаби для `Canvas` цього не
        # знають і вимагають `str | int`, тому тут глушимо саме їх, а не
        # переписуємо робочий виклик під неточний опис.
        self.lift()  # type: ignore[call-arg]
        self._left = LIFETIME_MS // FRAME_MS
        self._job = self.after(FRAME_MS, self._tick)

    def _tick(self) -> None:
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
