"""Побудова вкладки «Налаштування» для вікна GUI."""
from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from core import autostart
from core.config import MAX_IMAGE_SIZE, MIN_IMAGE_SIZE, documents_dir
from core.config import FarmMode as PriorityMode
from core.i18n import LANGS, NAMES, t
from gui.theme import PRESETS

if TYPE_CHECKING:
    from gui.app import GUI

PAD = 12


class SettingsTab:
    """Вкладка налаштувань, яка володіє власними віджетами та змінними стану."""

    _lang_codes: list[str]
    autostart_var: tk.BooleanVar
    badges_var: tk.BooleanVar
    block_entry: ctk.CTkEntry
    block_list: tk.Listbox
    boot_var: tk.BooleanVar
    dark_var: tk.BooleanVar
    discord_entry: ctk.CTkEntry
    hints_var: tk.BooleanVar
    hopeless_var: tk.BooleanVar
    images_var: tk.BooleanVar
    keeplog_var: tk.BooleanVar
    lang_var: tk.StringVar
    logdir_var: tk.StringVar
    mode_var: tk.StringVar
    preset_var: tk.StringVar
    prio_entry: ctk.CTkEntry
    prio_list: tk.Listbox
    rainbow_var: tk.BooleanVar
    size_label: ctk.CTkLabel
    size_var: tk.IntVar
    tg_hint: ctk.CTkLabel
    tg_var: tk.BooleanVar
    updates_var: tk.BooleanVar
    watch_entry: ctk.CTkEntry
    watch_list: tk.Listbox

    def __init__(self, gui: GUI, tabs: ctk.CTkTabview) -> None:
        self.gui = gui
        settings = gui._twitch.settings
        p = gui.palette
        self.tab = tabs.add(t("tab_settings"))

        # ⚠️ Прокрутка обов'язкова: на зменшеному вікні (а програма живе в
        # треї й часто відкривається невеликою) нижні пункти — тема, «не
        # братися за безнадійне», Telegram, «Про програму» — просто
        # обрізались, і дістатись до них було нічим.
        self.body = ctk.CTkScrollableFrame(self.tab, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        left = ctk.CTkFrame(self.body, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=(0, PAD))
        right = ctk.CTkFrame(self.body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)

        prio_box = gui._block(left, t("priority"), grow=True)
        # Списки лишаються `tk.Listbox`: рівноцінного віджета в CustomTkinter
        # немає, а фарбує його `_restyle_widgets` поіменно.
        self.prio_list = tk.Listbox(
            prio_box,
            bg=p["alt"],
            fg=p["fg"],
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            selectbackground=p["accent"],
            selectforeground="#ffffff",
        )
        self.prio_list.pack(fill="both", expand=True)
        for game in settings.priority:
            self.prio_list.insert("end", game)
        self.prio_entry = gui._list_row(
            prio_box, gui._priority_add, gui._priority_remove
        )

        # Спостереження — окремо від пріоритету: пріоритет міняє, що фармити
        # зараз, а це лише новини про нові кампанії. Можна хотіти знати про
        # Rocket League, не перериваючи фарм WoT.
        watch_box = gui._block(left, t("watch_games"), top=PAD, grow=True)
        gui._hint(watch_box, t("watch_hint"), colour="accent", wrap=240).pack(
            anchor="w", fill="x"
        )
        self.watch_list = tk.Listbox(
            watch_box,
            height=4,
            bg=p["alt"],
            fg=p["fg"],
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            selectbackground=p["accent"],
            selectforeground="#ffffff",
        )
        self.watch_list.pack(fill="both", expand=True, pady=(6, 0))
        for game in settings.watch_games:
            self.watch_list.insert("end", game)
        self.watch_entry = gui._list_row(
            watch_box, gui._watch_add, gui._watch_remove
        )

        # Чорний список. Бекенд був готовий давно — `settings.exclude` чесно
        # відсіює ігри в `_pick_games`, і команди EXCLUDE_ADD/REMOVE ядро вже
        # розуміло. Не було рівно одного: способу дістатись до нього з вікна.
        # Людина 06.09 просила «чорний список ігор, які не фармити», не знаючи,
        # що він у неї вже є — просто лише в боті.
        block_box = gui._block(left, t("blocked"), top=PAD, grow=True)
        gui._hint(block_box, t("blocked_hint"), colour="muted", wrap=240).pack(
            anchor="w", fill="x"
        )
        self.block_list = tk.Listbox(
            block_box,
            height=4,
            bg=p["alt"],
            fg=p["fg"],
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            selectbackground=p["accent"],
            selectforeground="#ffffff",
        )
        self.block_list.pack(fill="both", expand=True, pady=(6, 0))
        for game in settings.exclude:
            self.block_list.insert("end", game)
        self.block_entry = gui._list_row(
            block_box, gui._block_add, gui._block_remove
        )

        mode_box = gui._block(right, t("farm_mode"))
        self.mode_var = tk.StringVar(value=settings.farm_mode.name)
        for mode, label in (
            (PriorityMode.LINKED_ONLY, t("mode_linked")),
            (PriorityMode.SOONEST_END, t("mode_soonest")),
            (PriorityMode.TIGHTEST_FIT, t("mode_tightest")),
            (PriorityMode.PRIORITY_LIST, t("mode_priority")),
        ):
            gui._paint(
                ctk.CTkRadioButton(
                    mode_box,
                    text=label,
                    value=mode.name,
                    variable=self.mode_var,
                    command=gui._mode_changed,
                    radiobutton_width=18,
                    radiobutton_height=18,
                    font=("Segoe UI", 12),
                ),
                fg_color="accent",
                hover_color="accent",
                border_color="line",
                text_color="fg",
            ).pack(anchor="w", pady=3)

        lang_box = gui._block(right, t("language"), top=PAD)
        self._lang_codes = ["auto", *LANGS]
        labels = [t("language_auto"), *[NAMES[code] for code in LANGS]]
        stored = settings.language or "uk"
        self.lang_var = tk.StringVar(
            value=t("language_auto")
            if stored == "auto"
            else NAMES.get(stored, NAMES["uk"])
        )
        gui._paint(
            ctk.CTkOptionMenu(
                lang_box,
                values=labels,
                variable=self.lang_var,
                command=gui._language_changed,
                corner_radius=8,
                height=30,
                font=("Segoe UI", 12),
            ),
            fg_color="alt",
            button_color="accent",
            button_hover_color="hover",
            text_color="fg",
            dropdown_fg_color="card",
            dropdown_text_color="fg",
            dropdown_hover_color="hover",
        ).pack(fill="x")

        misc = gui._block(right, t("other"), top=PAD)
        self.badges_var = tk.BooleanVar(value=settings.farm_cosmetics)
        gui._switch(
            misc, t("farm_cosmetics"), self.badges_var, gui._misc_changed
        ).pack(anchor="w", pady=3)
        self.autostart_var = tk.BooleanVar(value=settings.start_in_tray)
        gui._switch(
            misc, t("start_tray"), self.autostart_var, gui._misc_changed
        ).pack(anchor="w", pady=3)
        self.boot_var = tk.BooleanVar(value=autostart.is_enabled())
        gui._switch(
            misc, t("autostart"), self.boot_var, gui._autostart_changed
        ).pack(anchor="w", pady=3)
        self.images_var = tk.BooleanVar(value=settings.drop_images)
        gui._switch(
            misc, t("drop_images"), self.images_var, gui._misc_changed
        ).pack(anchor="w", pady=3)
        self.hints_var = tk.BooleanVar(value=settings.hint_links)
        gui._switch(
            misc, t("hint_links"), self.hints_var, gui._misc_changed
        ).pack(anchor="w", pady=3)
        self.updates_var = tk.BooleanVar(value=settings.check_updates)
        gui._switch(
            misc, t("check_updates"), self.updates_var, gui._misc_changed
        ).pack(anchor="w", pady=3)
        size_row = ctk.CTkFrame(misc, fg_color="transparent")
        size_row.pack(fill="x", pady=(8, 0))
        gui._hint(size_row, t("image_size_label"), colour="fg", wrap=0).pack(
            side="left"
        )
        self.size_var = tk.IntVar(value=gui._image_size)
        self.size_label = gui._hint(size_row, f"{gui._image_size} px", wrap=0)
        self.size_label.pack(side="right")
        gui._paint(
            ctk.CTkSlider(
                size_row,
                from_=MIN_IMAGE_SIZE,
                to=MAX_IMAGE_SIZE,
                variable=self.size_var,
                command=gui._image_size_changed,
                height=16,
                button_length=10,
            ),
            fg_color="line",
            progress_color="accent",
            button_color="accent",
            button_hover_color="fg",
        ).pack(side="left", fill="x", expand=True, padx=8)
        gui._hint(misc, t("image_cache_hint")).pack(
            anchor="w", fill="x", pady=(4, 4)
        )
        self.dark_var = tk.BooleanVar(value=settings.dark_theme)
        gui._switch(misc, t("dark_theme"), self.dark_var, gui._misc_changed).pack(
            anchor="w", pady=3
        )

        self.keeplog_var = tk.BooleanVar(value=settings.keep_log)
        gui._switch(misc, t("keep_log"), self.keeplog_var, gui._misc_changed).pack(
            anchor="w", pady=3
        )
        self.logdir_var = tk.StringVar(value=settings.log_dir)
        log_row = ctk.CTkFrame(misc, fg_color="transparent")
        log_row.pack(fill="x", pady=(2, 0))
        gui._entry(
            log_row, self.logdir_var, placeholder=str(documents_dir())
        ).pack(side="left", fill="x", expand=True)
        gui._button(
            log_row, t("log_open"), gui._open_log_dir, width=110
        ).pack(side="left", padx=(8, 0))
        gui._hint(misc, t("log_hint")).pack(anchor="w", fill="x", pady=(2, 6))

        self.preset_var = tk.StringVar(
            value=t(f"theme_{settings.theme_preset or 'builtin'}")
        )
        ctk.CTkOptionMenu(
            misc,
            variable=self.preset_var,
            command=gui._preset_changed,
            values=[t(f"theme_{name or 'builtin'}") for name in PRESETS],
            fg_color=gui.cards["card"],
            button_color=p["accent"],
            button_hover_color=p["accent"],
            text_color=p["fg"],
        ).pack(anchor="w", pady=(6, 2))
        gui._button(misc, t("theme_export"), gui._export_theme).pack(
            anchor="w", pady=(0, 6)
        )

        self.hopeless_var = tk.BooleanVar(value=settings.skip_hopeless)
        gui._switch(
            misc, t("skip_hopeless"), self.hopeless_var, gui._misc_changed
        ).pack(anchor="w", pady=3)
        gui._hint(misc, t("skip_hopeless_hint")).pack(
            anchor="w", fill="x", pady=(0, 4)
        )
        self.rainbow_var = tk.BooleanVar(
            value=settings.progress_style == "rainbow"
        )
        gui._switch(
            misc, t("progress_rainbow"), self.rainbow_var, gui._misc_changed
        ).pack(anchor="w", pady=3)

        tg = gui._block(right, t("telegram"), top=PAD)
        self.tg_var = tk.BooleanVar(value=settings.telegram["enabled"])
        gui._switch(tg, t("telegram_on"), self.tg_var, gui._telegram_changed).pack(
            anchor="w", pady=3
        )
        gui._button(tg, t("connect_bot"), gui._open_telegram_setup).pack(
            anchor="w", pady=(8, 0)
        )
        self.tg_hint = gui._hint(tg, gui._telegram_hint())
        self.tg_hint.pack(anchor="w", fill="x", pady=(6, 0))

        dis = gui._block(right, t("discord"), top=PAD)
        gui._hint(dis, t("discord_hint"), colour="muted", wrap=240).pack(
            anchor="w", fill="x"
        )
        self.discord_entry = ctk.CTkEntry(
            dis, placeholder_text=t("discord_url")
        )
        self.discord_entry.pack(fill="x", pady=(6, 0))
        self.discord_entry.insert(0, settings.discord_webhook or "")
        self.discord_entry.bind("<FocusOut>", gui._discord_changed)
        self.discord_entry.bind("<Return>", gui._discord_changed)

        about = gui._block(right, t("about_title"), top=PAD)
        gui._button(about, t("about_open"), gui._open_about).pack(
            anchor="w", pady=(2, 0)
        )


def build_settings_tab(gui: GUI, tabs: ctk.CTkTabview) -> SettingsTab:
    """Для сумісності зі старим інтерфейсом функції."""
    return SettingsTab(gui, tabs)
