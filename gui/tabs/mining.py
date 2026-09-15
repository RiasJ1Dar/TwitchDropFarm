"""Вкладка «Майнінг»: поточний фарм, прогрес, керування та журнал подій."""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from core.i18n import t
from gui.celebrate import Confetti

if TYPE_CHECKING:
    from gui.pulse import PulseDot

PAD = 12
SIDE_WIDTH = 330

FARM_BADGE = {
    "going": ("farm_going", "ok"),
    "stalled": ("farm_stalled", "err"),
    "uncounted": ("farm_uncounted", "err"),
    "paused": ("farm_paused", "warn"),
    "idle": ("farm_idle", "fg"),
}


class MiningTabMixin:
    """Міксин побудови та керування вкладкою «Майнінг»."""

    palette: dict[str, str]
    cards: dict[str, str]
    _farm_state: str
    farm_label: tk.Label
    farm_dot: PulseDot | None
    progress: ctk.CTkProgressBar
    _rainbow_job: str | None
    channel_var: tk.StringVar
    title_var: tk.StringVar
    title_label: ctk.CTkLabel
    drop_var: tk.StringVar
    confetti: Confetti
    pause_btn: ctk.CTkButton
    log: tk.Text

    def _card(self, parent: tk.Misc, title: str) -> ctk.CTkFrame:
        raise NotImplementedError

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command: Any,
        *,
        accent: bool = False,
        width: int = 140,
    ) -> ctk.CTkButton:
        raise NotImplementedError

    def _set_progress(self, percent: float) -> None:
        raise NotImplementedError

    def _progress_colour(self) -> str:
        raise NotImplementedError

    def _build_side_column(self, side: tk.Misc) -> None:
        raise NotImplementedError

    def _toggle_pause(self) -> None:
        raise NotImplementedError

    def _reload_now(self) -> None:
        raise NotImplementedError

    def confirm_quit(self) -> None:
        raise NotImplementedError

    def _build_mining_tab(self, tabs: ctk.CTkTabview) -> None:
        p, c = self.palette, self.cards
        tab = tabs.add(t("tab_mining"))
        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=PAD, pady=PAD)

        # Дві колонки. Права вужча й фіксована: без неї на широкому моніторі
        # пів вікна лишалось порожнім, бо журнал розтягувався на всю ширину,
        # маючи в собі три рядки. Дані для неї програма вже має — просто ніде
        # не показувала.
        side = ctk.CTkFrame(body, fg_color="transparent", width=SIDE_WIDTH)
        side.pack(side="right", fill="y", padx=(PAD, 0))
        side.pack_propagate(False)  # інакше колонка стиснеться під вміст
        self._build_side_column(side)

        main = ctk.CTkFrame(body, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True)

        box = self._card(main, t("farming_now"))
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=PAD, pady=PAD)

        self.channel_var = tk.StringVar(value="—")
        ctk.CTkLabel(
            inner,
            textvariable=self.channel_var,
            anchor="w",
            font=("Segoe UI", 15, "bold"),
            text_color=p["fg"],
        ).pack(anchor="w", fill="x")
        # заголовок трансляції — єдине місце, де названа гра, коли категорія
        # каналу «Special Events»
        self.title_var = tk.StringVar(value="")
        self.title_label = ctk.CTkLabel(
            inner,
            textvariable=self.title_var,
            text_color=p["accent"],
            anchor="w",
            wraplength=820,
            justify="left",
        )
        self.title_label.pack(anchor="w", fill="x")
        # одна мітка на всі рядки: дропів на каналі буває кілька, і раніше тут
        # лишався той, чий прогрес прийшов останнім — тобто випадковий. З
        # турнірної трансляції це виглядало так, ніби фармиться подія, а гра
        # невідома.
        self.drop_var = tk.StringVar(value=t("drop_unknown"))
        ctk.CTkLabel(
            inner,
            textvariable=self.drop_var,
            anchor="w",
            justify="left",
            text_color=p["fg"],
        ).pack(anchor="w", fill="x", pady=(6, 8))
        self.progress = ctk.CTkProgressBar(
            inner,
            height=10,
            corner_radius=5,
            progress_color=p["accent"],
            fg_color=c["line"],
        )
        self.progress.pack(fill="x")
        self._set_progress(0)
        # Накладка святкування: лежить поверх цієї ж картки й показується лише
        # на півтори секунди після клейму. Решту часу її не видно взагалі.
        self.confetti = Confetti(
            inner,
            background=c["card"],
            colours=(p["accent"], p["ok"], p["warn"], "#ffffff"),
        )

        controls = ctk.CTkFrame(main, fg_color="transparent")
        controls.pack(fill="x", pady=(PAD, 0))
        self.pause_btn = self._button(
            controls, t("pause"), self._toggle_pause, accent=True
        )
        self.pause_btn.pack(side="left")
        self._button(
            controls, t("reload_inventory"), self._reload_now
        ).pack(side="left", padx=8)
        # Кнопки «Згорнути в трей» тут немає навмисно: хрестик вікна робить
        # рівно те саме і за тієї самої умови (`on_window_x` → `hide_to_tray`,
        # поки трей живий). Гірше: коли трей не піднявся, хрестик коректно
        # закриває програму, а кнопка сховала б вікно назовсім — повернути його
        # можна було б хіба що командою /show із Telegram.
        # Сам `hide_to_tray` лишається: його кличуть хрестик, меню трея і
        # запуск із `--tray`.
        self._button(controls, t("quit_miner"), self.confirm_quit).pack(
            side="right"
        )

        log_wrap = ctk.CTkFrame(main, fg_color="transparent")
        log_wrap.pack(fill="both", expand=True, pady=(PAD, 0))
        log_box = self._card(log_wrap, t("log"))
        log_box.pack(fill="both", expand=True)
        # Журнал лишається tk.Text свідомо: у CTkTextbox інші імена кольорів,
        # а тут працюють теги (`ok`/`warn`/`err`) і перефарбування при зміні
        # теми. Сучасний вигляд дає скролбар — саме він тут і був старим.
        self.log = tk.Text(
            log_box,
            height=12,
            wrap="word",
            bg=c["card"],
            fg=p["fg"],
            insertbackground=p["fg"],
            relief="flat",
            font=("Segoe UI", 9),
            padx=PAD,
            pady=8,
            highlightthickness=0,
            borderwidth=0,
        )
        scroll = ctk.CTkScrollbar(
            log_box,
            command=self.log.yview,
            width=12,
            button_color=c["line"],
            button_hover_color=p["accent"],
        )
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y", pady=8, padx=(0, 6))
        self.log.pack(side="left", fill="both", expand=True)
        for tag, colour in (("ok", p["ok"]), ("warn", p["warn"]), ("err", p["err"])):
            self.log.tag_configure(tag, foreground=colour)

    def _set_farm_state(self, state: str) -> None:
        """Кольорова мітка в шапці: іде / стоїть / чекає.

        Рядок статусу каже, *що* робить цикл («Дивимось», «Шукаю канали»).
        Ця мітка — *чи є сенс*: хвилини ростуть чи фарм стоїть на місці.
        """
        if state not in FARM_BADGE or state == self._farm_state:
            return
        self._farm_state = state
        key, colour = FARM_BADGE[state]
        self.farm_label.configure(
            text=t(key),
            fg=self.palette[colour],
            bg=self.palette["alt"],
        )
        bar = getattr(self, "progress", None)
        # Поки смуга переливається, стан кольором не показуємо: інакше два
        # правила фарбували б її по черзі й вона мигтіла б.
        if (
            bar is not None
            and hasattr(bar, "configure")
            and self._rainbow_job is None
        ):
            bar.configure(progress_color=self._progress_colour())
        dot = getattr(self, "farm_dot", None)
        if dot is None:
            return
        dot.recolour(colour=self.palette[colour], background=self.palette["alt"])
        # Дихає лише «Іде». В решті станів рух означав би роботу, якої немає, —
        # а застигла крапка сама собою є сигналом.
        if state == "going":
            dot.start()
        else:
            dot.stop()

    def _farm_from_status(self, text: str) -> None:
        watching = t("status_watching", name="").rstrip()
        stalled = t("status_stalled", minutes="0").split("0")[0].strip()
        if text == t("status_paused"):
            self._set_farm_state("paused")
        elif text == t("status_uncounted"):
            self._set_farm_state("uncounted")
        elif watching and text.startswith(watching):
            self._set_farm_state("going")
        elif stalled and text.startswith(stalled):
            self._set_farm_state("stalled")
        elif text in (
            t("status_waiting"),
            t("status_searching"),
            t("status_picking"),
            t("status_inventory"),
            t("status_details"),
        ):
            if self._farm_state in ("stalled", "uncounted"):
                return
            self._set_farm_state("idle")

    def _append_log(self, text: str, tag: str = "") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  ", "time")
        self.log.insert("end", f"{text}\n", tag or ())
        # не даємо журналу рости нескінченно
        if int(self.log.index("end-1c").split(".")[0]) > 500:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")


# Прямі функції-обгортки
def _build_mining_tab(gui: MiningTabMixin, tabs: ctk.CTkTabview) -> None:
    gui._build_mining_tab(tabs)


def _set_farm_state(gui: MiningTabMixin, state: str) -> None:
    gui._set_farm_state(state)


def _farm_from_status(gui: MiningTabMixin, text: str) -> None:
    gui._farm_from_status(text)


def _append_log(gui: MiningTabMixin, text: str, tag: str = "") -> None:
    gui._append_log(text, tag)
