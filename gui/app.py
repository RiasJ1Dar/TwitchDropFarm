"""Компактний Tkinter-інтерфейс.

Свідомо мінімальний: без власних канвасів для прогрес-барів, без локалізації,
без завантаження картинок кампаній із CDN. Усе, що потрібно, дає штатний ttk.

Інтеграція з asyncio: замість `root.mainloop()` крутимо `root.update()` з
asyncio-таски. Так усе лишається однопотоковим, і не потрібні ні
`call_soon_threadsafe`, ні блокування між потоками.
"""
from __future__ import annotations

import asyncio
import logging
import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from core.config import VERSION as __version__
from core.config import FarmMode as PriorityMode
from core.events import (
    ChannelsUpdated,
    Command,
    CommandType,
    ConnectionLost,
    ConnectionRestored,
    DropClaimed,
    DropProgress,
    Event,
    InventoryUpdated,
    LoggedIn,
    LoginRequired,
    LogLine,
    ProgressStalled,
    StatusChanged,
    WatchingChanged,
    WebsocketStatus,
    WindowVisibility,
)

if TYPE_CHECKING:
    from core.miner import Miner as Twitch

logger = logging.getLogger("TwitchDrops")

WINDOW_TITLE = f"Twitch Drop Farm v{__version__}"
# як часто прокручуємо цикл Tk; 20 к/с — непомітно для ока й дешево для CPU
TK_TICK = 0.05

DARK = {
    "bg": "#1b1b1f", "fg": "#e6e6e6", "alt": "#26262c",
    "accent": "#9147ff", "ok": "#33cc66", "warn": "#ffb020", "err": "#ff5c5c",
}
LIGHT = {
    "bg": "#f5f5f7", "fg": "#1b1b1f", "alt": "#ffffff",
    "accent": "#772ce8", "ok": "#1a9e4b", "warn": "#b06a00", "err": "#c62828",
}


class GUI:
    def __init__(self, twitch: Twitch):
        self._twitch = twitch
        self._closed = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self.palette = DARK if twitch.settings.dark_theme else LIGHT

        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.geometry("900x620")
        self.root.minsize(760, 520)
        # чи є куди ховатись; уточнюється, коли трей реально піднявся
        self._tray_available = False
        # стан кожного вебсокета окремо: індекс -> (статус, кількість топіків)
        self._ws_state: dict[int, tuple[str, int]] = {}
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_x)
        self._apply_theme()

        self._build_layout()
        twitch.events.subscribe(self._on_event)

    # ------------------------------------------------------------ оформлення

    def _apply_theme(self) -> None:
        p = self.palette
        self.root.configure(bg=p["bg"])
        style = ttk.Style(self.root)
        with_theme = "clam" if "clam" in style.theme_names() else style.theme_use()
        style.theme_use(with_theme)
        style.configure(".", background=p["bg"], foreground=p["fg"],
                        fieldbackground=p["alt"], borderwidth=0)
        style.configure("TNotebook", background=p["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=p["alt"], foreground=p["fg"],
                        padding=(14, 7))
        style.map("TNotebook.Tab", background=[("selected", p["accent"])],
                  foreground=[("selected", "#ffffff")])
        style.configure("TFrame", background=p["bg"])
        style.configure("TLabel", background=p["bg"], foreground=p["fg"])
        style.configure("TLabelframe", background=p["bg"], foreground=p["fg"])
        style.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])
        style.configure("TButton", background=p["alt"], foreground=p["fg"], padding=6)
        style.map("TButton", background=[("active", p["accent"])])
        style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"])
        style.configure("Treeview", background=p["alt"], fieldbackground=p["alt"],
                        foreground=p["fg"], rowheight=24, borderwidth=0)
        style.configure("Treeview.Heading", background=p["bg"], foreground=p["fg"])
        style.map("Treeview", background=[("selected", p["accent"])])
        style.configure("TProgressbar", background=p["accent"], troughcolor=p["alt"])

    # ------------------------------------------------------------ розкладка

    def _build_layout(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        self.status_var = tk.StringVar(value="Запуск…")
        ttk.Label(top, textvariable=self.status_var,
                  font=("Segoe UI", 11, "bold")).pack(side="left")
        self.conn_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.conn_var).pack(side="right")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._build_mining_tab(notebook)
        self._build_channels_tab(notebook)
        self._build_inventory_tab(notebook)
        self._build_settings_tab(notebook)

    def _build_mining_tab(self, notebook: ttk.Notebook) -> None:
        p = self.palette
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Майнінг")

        box = ttk.LabelFrame(tab, text="Зараз фармимо", padding=10)
        box.pack(fill="x")
        self.channel_var = tk.StringVar(value="—")
        ttk.Label(box, textvariable=self.channel_var,
                  font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.drop_var = tk.StringVar(value="Дроп не визначено")
        ttk.Label(box, textvariable=self.drop_var).pack(anchor="w", pady=(4, 2))
        self.progress = ttk.Progressbar(box, maximum=100)
        self.progress.pack(fill="x", pady=(4, 0))

        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=8)
        self.pause_btn = ttk.Button(controls, text="Призупинити", command=self._toggle_pause)
        self.pause_btn.pack(side="left")
        ttk.Button(controls, text="Перечитати інвентар",
                   command=lambda: self._send(CommandType.RELOAD)).pack(side="left", padx=6)
        ttk.Button(controls, text="Згорнути в трей",
                   command=self.hide_to_tray).pack(side="right")
        ttk.Button(controls, text="Вимкнути майнер",
                   command=self.confirm_quit).pack(side="right", padx=6)

        log_box = ttk.LabelFrame(tab, text="Журнал", padding=6)
        log_box.pack(fill="both", expand=True)
        self.log = tk.Text(log_box, height=12, wrap="word", bg=p["alt"], fg=p["fg"],
                           insertbackground=p["fg"], relief="flat")
        scroll = ttk.Scrollbar(log_box, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for tag, colour in (("ok", p["ok"]), ("warn", p["warn"]), ("err", p["err"])):
            self.log.tag_configure(tag, foreground=colour)

    def _build_channels_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Канали")
        ttk.Label(
            tab, text="Подвійний клік — перемкнутись на канал"
        ).pack(anchor="w", pady=(0, 6))
        columns = ("name", "game", "viewers", "status")
        self.channel_tree = ttk.Treeview(tab, columns=columns, show="headings")
        for column, title, width in (
            ("name", "Канал", 200), ("game", "Гра", 260),
            ("viewers", "Глядачі", 90), ("status", "Стан", 140),
        ):
            self.channel_tree.heading(column, text=title)
            self.channel_tree.column(column, width=width, anchor="w")
        self.channel_tree.bind("<Double-1>", self._on_channel_activate)
        scroll = ttk.Scrollbar(tab, command=self.channel_tree.yview)
        self.channel_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.channel_tree.pack(fill="both", expand=True)

    def _build_inventory_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Інвентар")
        self.inv_tree = ttk.Treeview(tab, columns=("progress", "state"), show="tree headings")
        self.inv_tree.heading("#0", text="Кампанія / дроп")
        self.inv_tree.heading("progress", text="Прогрес")
        self.inv_tree.heading("state", text="Стан")
        self.inv_tree.column("#0", width=420)
        self.inv_tree.column("progress", width=120, anchor="center")
        self.inv_tree.column("state", width=160, anchor="w")
        scroll = ttk.Scrollbar(tab, command=self.inv_tree.yview)
        self.inv_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.inv_tree.pack(fill="both", expand=True)

    def _build_settings_tab(self, notebook: ttk.Notebook) -> None:
        settings = self._twitch.settings
        tab = ttk.Frame(notebook, padding=10)
        notebook.add(tab, text="Налаштування")

        prio_box = ttk.LabelFrame(tab, text="Пріоритет ігор", padding=8)
        prio_box.pack(fill="both", expand=True, side="left", padx=(0, 8))
        self.prio_list = tk.Listbox(prio_box, bg=self.palette["alt"],
                                    fg=self.palette["fg"], relief="flat",
                                    selectbackground=self.palette["accent"])
        self.prio_list.pack(fill="both", expand=True)
        for game in settings.priority:
            self.prio_list.insert("end", game)
        entry_row = ttk.Frame(prio_box)
        entry_row.pack(fill="x", pady=(6, 0))
        self.prio_entry = ttk.Entry(entry_row)
        self.prio_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(entry_row, text="+", width=3,
                   command=self._priority_add).pack(side="left", padx=(4, 0))
        ttk.Button(entry_row, text="−", width=3,
                   command=self._priority_remove).pack(side="left", padx=(2, 0))

        right = ttk.Frame(tab)
        right.pack(fill="both", expand=True, side="left")

        mode_box = ttk.LabelFrame(right, text="Режим пріоритету", padding=8)
        mode_box.pack(fill="x")
        self.mode_var = tk.StringVar(value=settings.farm_mode.name)
        for mode, label in (
            (PriorityMode.LINKED_ONLY, "Усе, до чого привʼязаний акаунт"),
            (PriorityMode.SOONEST_END, "Спершу ті, що скоро закінчаться"),
            (PriorityMode.TIGHTEST_FIT, "Спершу ті, що ледве встигають"),
            (PriorityMode.PRIORITY_LIST, "Тільки зі списку пріоритету"),
        ):
            ttk.Radiobutton(
                mode_box, text=label, value=mode.name, variable=self.mode_var,
                command=self._mode_changed,
            ).pack(anchor="w")

        misc = ttk.LabelFrame(right, text="Інше", padding=8)
        misc.pack(fill="x", pady=(8, 0))
        self.badges_var = tk.BooleanVar(value=settings.farm_cosmetics)
        ttk.Checkbutton(
            misc, text="Фармити значки та емоції", variable=self.badges_var,
            command=self._misc_changed,
        ).pack(anchor="w")
        self.autostart_var = tk.BooleanVar(value=settings.start_in_tray)
        ttk.Checkbutton(
            misc, text="Стартувати одразу згорнутим у трей",
            variable=self.autostart_var, command=self._misc_changed,
        ).pack(anchor="w")
        self.dark_var = tk.BooleanVar(value=settings.dark_theme)
        ttk.Checkbutton(
            misc, text="Темна тема (застосується після перезапуску)",
            variable=self.dark_var, command=self._misc_changed,
        ).pack(anchor="w")

        tg = ttk.LabelFrame(right, text="Telegram", padding=8)
        tg.pack(fill="x", pady=(8, 0))
        self.tg_var = tk.BooleanVar(value=settings.telegram["enabled"])
        ttk.Checkbutton(
            tg, text="Увімкнено (налаштування — у settings.json)",
            variable=self.tg_var, command=self._telegram_changed,
        ).pack(anchor="w")
        ttk.Label(
            tg, text="Потрібні bot_token і chat_ids; зміни діють після перезапуску.",
            wraplength=320,
        ).pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------ дії користувача

    def _send(self, kind: CommandType, argument: str = "") -> None:
        self._twitch.control.send(Command(kind, argument))

    def _toggle_pause(self) -> None:
        if self._twitch._paused:
            self._send(CommandType.RESUME)
            self.pause_btn.configure(text="Призупинити")
        else:
            self._send(CommandType.PAUSE)
            self.pause_btn.configure(text="Продовжити")

    def _on_channel_activate(self, _event: tk.Event) -> None:
        selection = self.channel_tree.selection()
        if selection:
            self._send(CommandType.SWITCH, self.channel_tree.item(selection[0], "values")[0])

    def _priority_add(self) -> None:
        game = self.prio_entry.get().strip()
        if game and game not in self.prio_list.get(0, "end"):
            self.prio_list.insert("end", game)
            self.prio_entry.delete(0, "end")
            self._send(CommandType.PRIORITY_ADD, game)

    def _priority_remove(self) -> None:
        selection = self.prio_list.curselection()
        if selection:
            game = self.prio_list.get(selection[0])
            self.prio_list.delete(selection[0])
            self._send(CommandType.PRIORITY_REMOVE, game)

    def _mode_changed(self) -> None:
        self._twitch.settings.farm_mode = PriorityMode[self.mode_var.get()]
        self._send(CommandType.RELOAD)

    def _misc_changed(self) -> None:
        settings = self._twitch.settings
        settings.farm_cosmetics = self.badges_var.get()
        settings.start_in_tray = self.autostart_var.get()
        settings.dark_theme = self.dark_var.get()
        settings.save()

    def _telegram_changed(self) -> None:
        self._twitch.settings.telegram["enabled"] = self.tg_var.get()
        self._twitch.settings.alter()

    # ------------------------------------------------------------ події ядра

    def _on_event(self, event: Event) -> None:
        try:
            self._render(event)
        except tk.TclError:
            pass  # вікно вже знищене

    def _render(self, event: Event) -> None:
        if isinstance(event, WindowVisibility):
            # Ховати нема куди, поки трей не піднявся: вікно зникло б, а
            # повернути його не було б чим.
            if event.visible:
                self.show_window()
            elif self._tray_available:
                self.hide_to_tray()
        elif isinstance(event, StatusChanged):
            self.status_var.set(event.text)
        elif isinstance(event, LogLine):
            self._append_log(event.text)
        elif isinstance(event, LoginRequired):
            self._append_log(f"Потрібна авторизація, код {event.user_code}", "warn")
        elif isinstance(event, LoggedIn):
            self._append_log(f"Вхід виконано (user ID {event.user_id})", "ok")
        elif isinstance(event, WatchingChanged):
            if event.channel is None:
                self.channel_var.set("—")
                self.drop_var.set("Дроп не визначено")
                self.progress["value"] = 0
            else:
                self.channel_var.set(
                    f"{event.channel.name}  ·  {event.channel.game or 'без гри'}"
                )
        elif isinstance(event, DropProgress):
            self.drop_var.set(
                f"{event.drop_name} — {event.game} "
                f"({event.current_minutes}/{event.required_minutes} хв)"
            )
            if event.required_minutes > 0:
                self.progress["value"] = min(
                    100, event.current_minutes / event.required_minutes * 100
                )
        elif isinstance(event, DropClaimed):
            self._append_log(f"Отримано: {event.rewards} ({event.game})", "ok")
        elif isinstance(event, ProgressStalled):
            self._append_log(
                f"Прогрес стоїть {event.minutes_without_progress} хв на "
                f"{event.channel_name} — можливо, Twitch відкритий вручну", "err"
            )
        elif isinstance(event, ConnectionLost):
            self.conn_var.set("● немає зв'язку")
            self._append_log(f"Втрачено зв'язок: {event.reason}", "err")
        elif isinstance(event, ConnectionRestored):
            self.conn_var.set("")
            self._append_log(
                f"Зв'язок відновлено за {round(event.downtime_seconds)}с", "ok"
            )
        elif isinstance(event, WebsocketStatus):
            self._render_websockets(event)
        elif isinstance(event, ChannelsUpdated):
            self._render_channels(event)
        elif isinstance(event, InventoryUpdated):
            self._render_inventory(event)

    def _render_websockets(self, event: WebsocketStatus) -> None:
        """Зводить стан усіх зʼєднань в один рядок.

        Раніше кожен із восьми вебсокетів писав у це саме поле, і видно було лише
        того, хто озвався останнім — рядок стрибав і показував випадкові 3 топіки
        замість реальної картини.
        """
        self._ws_state[event.index] = (event.status, event.topics)
        topics = sum(topics for _status, topics in self._ws_state.values())
        connected = sum(
            1 for status, _t in self._ws_state.values() if status == "підключено"
        )
        total = len(self._ws_state)
        if connected == total:
            state = f"{total} зʼєднань"
        else:
            state = f"{connected}/{total} зʼєднань"
        self.conn_var.set(f"{state} · {topics} топіків")

    def _append_log(self, text: str, tag: str = "") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  {text}\n", tag or ())
        # не даємо журналу рости нескінченно
        if int(self.log.index("end-1c").split(".")[0]) > 500:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _render_channels(self, event: ChannelsUpdated) -> None:
        selected = self.channel_tree.selection()
        keep = self.channel_tree.item(selected[0], "values")[0] if selected else None
        self.channel_tree.delete(*self.channel_tree.get_children())
        for channel in event.channels:
            if channel.online:
                state = "онлайн" + (" · дропи" if channel.drops_enabled else "")
            else:
                state = "офлайн"
            iid = self.channel_tree.insert(
                "", "end",
                values=(channel.name, channel.game or "—", channel.viewers, state),
            )
            if keep == channel.name:
                self.channel_tree.selection_set(iid)

    def _render_inventory(self, event: InventoryUpdated) -> None:
        # Тут не об'єкти моделі, а знімки з події: у них власні імена полів,
        # і вони навмисно не змінюються разом із внутрішньою моделлю — інакше
        # кожне перейменування в ядрі ламало б інтерфейс.
        self.inv_tree.delete(*self.inv_tree.get_children())
        now = datetime.now(timezone.utc)
        for campaign in event.campaigns:
            if campaign.expired:
                state = "минула"
            elif campaign.upcoming:
                state = "скоро"
            elif campaign.claimed_drops >= campaign.total_drops:
                state = "завершено"
            else:
                hours = max(0, int((campaign.ends_at - now).total_seconds() // 3600))
                state = f"ще {hours} год"
            parent = self.inv_tree.insert(
                "", "end", text=f"{campaign.game} — {campaign.name}",
                values=(f"{campaign.claimed_drops}/{campaign.total_drops}", state),
                open=False,
            )
            for drop in campaign.drops:
                self.inv_tree.insert(
                    parent, "end", text=drop.name,
                    values=(
                        f"{drop.current_minutes}/{drop.required_minutes} хв",
                        "отримано" if drop.claimed else "",
                    ),
                )

    # ------------------------------------------------------------ цикл Tk

    def hide_to_tray(self) -> None:
        """Ховає вікно, лишаючи майнер працювати."""
        self.root.withdraw()

    def confirm_quit(self) -> None:
        """Питає підтвердження перед зупинкою.

        Кнопка стоїть поряд зі «Згорнути в трей», а наслідки в них протилежні:
        промах коштував би недофармленого дропа. Показуємо, скільки саме
        втрачається, щоб вибір був свідомим.
        """
        campaign = self._twitch.active_campaign()
        detail = ""
        if campaign is not None and (drop := campaign.next_drop) is not None:
            detail = (
                f"\n\nЗараз фармиться «{drop.name}»: "
                f"{drop.minutes}/{drop.required_minutes} хв, "
                f"лишилось {drop.minutes_left}."
            )
        if messagebox.askyesno(
            "Вимкнути майнер",
            f"Зупинити фарм і закрити програму?{detail}",
            icon="warning",
            default="no",
            parent=self.root,
        ):
            self.request_close()

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_window_x(self) -> None:
        """Хрестик згортає в трей, а не вбиває майнер.

        Поки трей живий, закриття вікна не має зупиняти фарм: користувач майже
        завжди хоче прибрати вікно з очей, а не втратити години перегляду.
        Вихід — через меню трея або кнопку в ньому.
        """
        if self._tray_available:
            self.hide_to_tray()
        else:
            self.request_close()

    def request_close(self) -> None:
        self._closed.set()
        self._twitch.request_stop()

    @property
    def close_requested(self) -> bool:
        return self._closed.is_set()

    def start(self) -> None:
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll())

    async def _poll(self) -> None:
        """Крутить цикл подій Tk усередині asyncio замість mainloop()."""
        while not self._closed.is_set():
            try:
                self.root.update()
            except tk.TclError:
                break  # вікно закрили
            await asyncio.sleep(TK_TICK)

    async def wait_until_closed(self) -> None:
        await self._closed.wait()

    def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass
