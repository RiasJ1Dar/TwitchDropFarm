"""Фабрики типових віджетів та елементів інтерфейсу GUI."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

import customtkinter as ctk

PAD = 12


class WidgetsMixin:
    """Міксин базових віджетів для головного вікна GUI."""

    palette: dict[str, str]
    cards: dict[str, str]
    _painted: list[tuple[Any, dict[str, str]]]

    def _card(self, parent: tk.Misc, title: str) -> ctk.CTkFrame:
        """Заокруглена картка з підписом-шапкою.

        Заміна `ttk.LabelFrame`: той малює прямокутну рамку з врізаним у неї
        текстом, і саме ця рамка найбільше видавала вік вікна. Тут підпис
        стоїть над вмістом приглушеним кольором, а межу тримає тонка лінія.
        """
        c = self.cards
        ttk.Label(parent, text=title, foreground=self.palette["muted"]).pack(
            anchor="w", padx=4, pady=(0, 4),
        )
        card = ctk.CTkFrame(
            parent,
            corner_radius=12,
            fg_color=c["card"],
            border_width=1,
            border_color=c["line"],
        )
        card.pack(fill="x")
        return card

    def _block(
        self,
        parent: tk.Misc,
        title: str,
        *,
        top: int = 0,
        grow: bool = False,
    ) -> ctk.CTkFrame:
        """Картка з відступом зверху; повертає її нутрощі.

        `_card` пакує підпис і рамку одне за одним, тож відступ між сусідніми
        картками ставиться на обгортці — інакше підпис наступної прилипає до
        попередньої. Вміст лягає в прозорий фрейм із полями, щоб кожен рядок
        не носив власний `padx`.
        """
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="both" if grow else "x", expand=grow, pady=(top, 0))
        card = self._card(wrap, title)
        if grow:
            card.pack(fill="both", expand=True)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both" if grow else "x", expand=grow, padx=PAD, pady=PAD)
        return inner

    def _colour(self, key: str) -> str:
        """Ключ палітри → колір. Імена в `palette` і `cards` не перетинаються."""
        return self.palette.get(key) or self.cards[key]

    def _paint(self, widget: Any, **options: str) -> Any:
        """Пофарбувати CTk-віджет і запам'ятати, чим саме.

        Значення тут — не кольори, а ключі палітри: саме тому те саме
        оформлення вдається накласти вдруге, коли людина перемикає тему.
        """
        self._painted.append((widget, options))
        widget.configure(**{name: self._colour(key) for name, key in options.items()})
        return widget

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command: Any,
        *,
        accent: bool = False,
        width: int = 140,
    ) -> ctk.CTkButton:
        """Кнопка вкладки. Акцентна — та, яку натискають найчастіше."""
        p, c = self.palette, self.cards
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            corner_radius=8,
            height=32,
            width=width,
            font=("Segoe UI", 12, "bold" if accent else "normal"),
            fg_color=p["accent"] if accent else c["card"],
            hover_color=p["accent"] if accent else c["hover"],
            text_color="#ffffff" if accent else p["fg"],
            border_width=0 if accent else 1,
            border_color=c["line"],
        )

    def _switch(
        self,
        parent: tk.Misc,
        text: str,
        variable: tk.Variable,
        command: Any,
    ) -> ctk.CTkSwitch:
        """Тумблер замість галочки: увімкнено це чи ні, видно без вчитування.

        `onvalue`/`offvalue` задані явно: типово CustomTkinter пише в змінну
        одиницю й нуль, а тут скрізь `tk.BooleanVar` — і саме її читає
        `_misc_changed`.
        """
        return self._paint(
            ctk.CTkSwitch(
                parent,
                text=text,
                variable=variable,
                command=command,
                onvalue=True,
                offvalue=False,
                switch_width=40,
                switch_height=20,
                font=("Segoe UI", 12),
            ),
            fg_color="line",
            progress_color="accent",
            button_color="muted",
            button_hover_color="fg",
            text_color="fg",
        )

    def _entry(
        self,
        parent: tk.Misc,
        variable: tk.StringVar | None = None,
        *,
        placeholder: str = "",
    ) -> ctk.CTkEntry:
        extra: dict[str, Any] = {}
        if variable is not None:
            extra["textvariable"] = variable
        if placeholder:
            # підказка сірим, поки поле порожнє: інакше незрозуміло, що буде,
            # якщо нічого не вписати
            extra["placeholder_text"] = placeholder
        return self._paint(
            ctk.CTkEntry(
                parent,
                corner_radius=8,
                height=30,
                font=("Segoe UI", 12),
                **extra,
            ),
            fg_color="alt",
            border_color="line",
            text_color="fg",
        )

    def _hint(
        self,
        parent: tk.Misc,
        text: str,
        *,
        colour: str = "muted",
        wrap: int = 320,
    ) -> ctk.CTkLabel:
        """Мітка-пояснення. `wrap=0` — короткий підпис, який не переносять."""
        return self._paint(
            ctk.CTkLabel(
                parent,
                text=text,
                wraplength=wrap,
                justify="left",
                anchor="w",
                font=("Segoe UI", 12),
            ),
            text_color=colour,
        )

    def _list_row(self, parent: tk.Misc, add: Any, remove: Any) -> ctk.CTkEntry:
        """Поле вводу з «+» і «−» — однаковий рядок під обома списками ігор."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        entry = self._entry(row)
        entry.pack(side="left", fill="x", expand=True)
        self._button(row, "+", add, width=34).pack(side="left", padx=(6, 0))
        self._button(row, "−", remove, width=34).pack(side="left", padx=(4, 0))
        return entry


# Функції для прямого виклику з передачею екземпляра GUI
def _card(gui: WidgetsMixin, parent: tk.Misc, title: str) -> ctk.CTkFrame:
    return gui._card(parent, title)


def _block(
    gui: WidgetsMixin,
    parent: tk.Misc,
    title: str,
    *,
    top: int = 0,
    grow: bool = False,
) -> ctk.CTkFrame:
    return gui._block(parent, title, top=top, grow=grow)


def _button(
    gui: WidgetsMixin,
    parent: tk.Misc,
    text: str,
    command: Any,
    *,
    accent: bool = False,
    width: int = 140,
) -> ctk.CTkButton:
    return gui._button(parent, text, command, accent=accent, width=width)


def _switch(
    gui: WidgetsMixin,
    parent: tk.Misc,
    text: str,
    variable: tk.Variable,
    command: Any,
) -> ctk.CTkSwitch:
    return gui._switch(parent, text, variable, command)


def _entry(
    gui: WidgetsMixin,
    parent: tk.Misc,
    variable: tk.StringVar | None = None,
    *,
    placeholder: str = "",
) -> ctk.CTkEntry:
    return gui._entry(parent, variable, placeholder=placeholder)


def _hint(
    gui: WidgetsMixin,
    parent: tk.Misc,
    text: str,
    *,
    colour: str = "muted",
    wrap: int = 320,
) -> ctk.CTkLabel:
    return gui._hint(parent, text, colour=colour, wrap=wrap)


def _list_row(
    gui: WidgetsMixin,
    parent: tk.Misc,
    add: Any,
    remove: Any,
) -> ctk.CTkEntry:
    return gui._list_row(parent, add, remove)


def _paint(gui: WidgetsMixin, widget: Any, **options: str) -> Any:
    return gui._paint(widget, **options)


def _colour(gui: WidgetsMixin, key: str) -> str:
    return gui._colour(key)
