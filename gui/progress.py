"""Керування смугою поступу (прогрес-баром) та його анімаціями."""
from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from gui.pulse import rainbow

if TYPE_CHECKING:
    from core.client import MinerClient as Twitch


class ProgressMixin:
    """Міксин керування індикатором поступу, анімацією руху та веселкою."""

    _twitch: Twitch
    root: tk.Tk
    progress: ctk.CTkProgressBar
    palette: dict[str, str]
    _farm_state: str
    _progress_target: float
    _progress_shown: float
    _progress_job: str | None
    _rainbow_job: str | None
    _rainbow_phase: float

    # Яку частку шляху до цілі проходимо за кадр. 0.25 дає рух, який око
    # встигає простежити, але який не тягнеться: смуга доїжджає за ~10 кадрів.
    PROGRESS_STEP = 0.25
    PROGRESS_FRAME_MS = 40
    # Ближче за це до цілі — вважаємо, що приїхали, і зупиняємо анімацію.
    PROGRESS_EPSILON = 0.002

    # Скільки часу забирає повний круг веселки. Півхвилини — перелив помітний,
    # але не миготить перед очима, коли на нього не дивишся.
    RAINBOW_PERIOD_MS = 30000
    RAINBOW_FRAME_MS = 60

    @classmethod
    def _advance(cls, current: float, target: float) -> float:
        """Один крок до цілі. Винесено окремо, щоб перевірятись без вікна."""
        if abs(target - current) <= cls.PROGRESS_EPSILON:
            return target
        return current + (target - current) * cls.PROGRESS_STEP

    def _apply_progress_style(self) -> None:
        """Вмикає або гасить перелив за налаштуванням.

        Стиль «за станом» несе зміст: колір каже, іде фарм чи стоїть. Перелив
        цього не каже нічого — він просто святковий, тому типово вимкнений і
        вмикається свідомо.
        """
        bar = getattr(self, "progress", None)
        if bar is None or not hasattr(bar, "configure"):
            return
        wanted = self._twitch.settings.progress_style == "rainbow"
        if wanted and self._rainbow_job is None:
            self._rainbow_job = self.root.after(
                self.RAINBOW_FRAME_MS, self._rainbow_tick
            )
        elif not wanted and self._rainbow_job is not None:
            self.root.after_cancel(self._rainbow_job)
            self._rainbow_job = None
            bar.configure(progress_color=self._progress_colour())

    def _rainbow_tick(self) -> None:
        self._rainbow_job = None
        self._rainbow_phase += self.RAINBOW_FRAME_MS / self.RAINBOW_PERIOD_MS
        bar = getattr(self, "progress", None)
        if bar is not None:
            bar.configure(progress_color=rainbow(self._rainbow_phase))
        if self._twitch.settings.progress_style != "rainbow":
            return
        self._rainbow_job = self.root.after(
            self.RAINBOW_FRAME_MS, self._rainbow_tick
        )

    def _progress_colour(self) -> str:
        """Колір смуги за станом фарму.

        Смуга завжди була фіолетова, хоч програма чудово знає, іде фарм чи
        стоїть. Колір повторює мітку в шапці — око ловить його швидше, ніж
        читає слово.
        """
        p = self.palette
        return {
            "going": p["ok"],
            "stalled": p["err"],
            "uncounted": p["err"],
            "paused": p["warn"],
        }.get(self._farm_state, p["accent"])

    def _set_progress(self, percent: float) -> None:
        """Прогрес у відсотках. CTk рахує від 0 до 1, решта коду — у сотих."""
        self._progress_target = max(0.0, min(1.0, percent / 100))
        # Без вікна (перевірки ганяють метод на заглушці) анімувати нема чим і
        # нема навіщо — ставимо одразу, щоб тест бачив кінцеве значення.
        root = getattr(self, "root", None)
        if root is None:
            self._progress_shown = self._progress_target
            self.progress.set(self._progress_target)
            return
        if self._progress_job is None:
            self._progress_job = root.after(
                self.PROGRESS_FRAME_MS, self._progress_tick
            )

    def _progress_tick(self) -> None:
        self._progress_job = None
        self._progress_shown = self._advance(
            self._progress_shown, self._progress_target
        )
        self.progress.set(self._progress_shown)
        if self._progress_shown == self._progress_target:
            return
        self._progress_job = self.root.after(
            self.PROGRESS_FRAME_MS, self._progress_tick
        )


# Функції для прямого виклику
def _advance(current: float, target: float) -> float:
    return ProgressMixin._advance(current, target)


def _apply_progress_style(gui: ProgressMixin) -> None:
    gui._apply_progress_style()


def _set_progress(gui: ProgressMixin, percent: float) -> None:
    gui._set_progress(percent)


def _progress_colour(gui: ProgressMixin) -> str:
    return gui._progress_colour()
