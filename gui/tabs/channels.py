"""Вкладка «Канали»: список доступних стрімів та їхній статус."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

import customtkinter as ctk

from core.events import ChannelsUpdated, CommandType
from core.i18n import t


class ChannelsTabMixin:
    """Міксин побудови та керування вкладкою «Канали»."""

    channel_tree: ttk.Treeview

    def _paint(self, widget: Any, **options: str) -> Any:
        raise NotImplementedError

    def _send(self, kind: CommandType, argument: str = "") -> None:
        raise NotImplementedError

    def _build_channels_tab(self, tabs: ctk.CTkTabview) -> None:
        # Усередині лишається ttk: тут `Treeview`, який не переводимо (замір —
        # 3026 мс проти 53 мс на 198 рядках).
        tab = ttk.Frame(tabs.add(t("tab_channels")), padding=10)
        tab.pack(fill="both", expand=True)
        ttk.Label(
            tab, text=t("channels_hint")
        ).pack(anchor="w", pady=(0, 6))
        columns = ("name", "game", "viewers", "status")
        self.channel_tree = ttk.Treeview(
            tab, columns=columns, show="headings", style="Table.Treeview"
        )
        for column, title, width in (
            ("name", t("col_channel"), 200),
            ("game", t("col_game"), 260),
            ("viewers", t("col_viewers"), 90),
            ("status", t("col_status"), 140),
        ):
            self.channel_tree.heading(column, text=title)
            self.channel_tree.column(
                column, width=width, anchor="e" if column == "viewers" else "w",
            )
        self.channel_tree.bind("<Double-1>", self._on_channel_activate)
        self._table_scrollbar(tab, self.channel_tree)
        self.channel_tree.pack(fill="both", expand=True)

    def _table_scrollbar(self, parent: tk.Misc, table: ttk.Treeview) -> None:
        """`CTkScrollbar` замість `ttk.Scrollbar` — перевірено, з `Treeview`
        працює: той самий протокол `yview` / `yscrollcommand`, що й у журналі
        вкладки «Майнінг». Стара смуга зі стрілками й канавкою була найпомітнішим
        старим елементом на обох вкладках зі списками."""
        scroll = self._paint(
            ctk.CTkScrollbar(parent, command=table.yview, width=12),
            button_color="line",
            button_hover_color="accent",
            fg_color="card",
        )
        table.configure(yscrollcommand=scroll.set)
        # канавка того ж кольору, що й таблиця: смуга читається як її частина,
        # а не як окрема сіра колонка збоку
        scroll.pack(side="right", fill="y", padx=(2, 0))

    def _on_channel_activate(self, _event: tk.Event) -> None:
        selection = self.channel_tree.selection()
        if selection:
            self._send(CommandType.SWITCH, self.channel_tree.item(selection[0], "values")[0])

    def _render_channels(self, event: ChannelsUpdated) -> None:
        selected = self.channel_tree.selection()
        keep = self.channel_tree.item(selected[0], "values")[0] if selected else None
        self.channel_tree.delete(*self.channel_tree.get_children())
        for channel in event.channels:
            if channel.online:
                state = t("ch_online_drops") if channel.drops_enabled else t("ch_online")
            else:
                state = t("ch_offline")
            iid = self.channel_tree.insert(
                "",
                "end",
                values=(channel.name, channel.game or "—", channel.viewers, state),
            )
            if keep == channel.name:
                self.channel_tree.selection_set(iid)


# Прямі функції-обгортки
def _build_channels_tab(gui: ChannelsTabMixin, tabs: ctk.CTkTabview) -> None:
    gui._build_channels_tab(tabs)


def _table_scrollbar(gui: ChannelsTabMixin, parent: tk.Misc, table: ttk.Treeview) -> None:
    gui._table_scrollbar(parent, table)


def _render_channels(gui: ChannelsTabMixin, event: ChannelsUpdated) -> None:
    gui._render_channels(event)


def _on_channel_activate(gui: ChannelsTabMixin, _event: tk.Event) -> None:
    gui._on_channel_activate(_event)
