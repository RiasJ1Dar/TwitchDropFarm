"""Компактний Tkinter-інтерфейс.

Свідомо мінімальний: без власних канвасів для прогрес-барів, без локалізації.
Усе, що потрібно, дає штатний ttk. Картинки нагород показуються, лише коли їх
увімкнули в налаштуваннях, і беруться з кешу на диску — сам інтерфейс у мережу
не ходить.

Інтеграція з asyncio: замість `root.mainloop()` крутимо `root.update()` з
asyncio-таски. Так усе лишається однопотоковим, і не потрібні ні
`call_soon_threadsafe`, ні блокування між потоками.
"""
from __future__ import annotations

import asyncio
import logging
import tkinter as tk
from datetime import datetime, timezone
from time import monotonic
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any

from core import autostart
from core.config import MAX_IMAGE_SIZE, MIN_IMAGE_SIZE, TILE_SIZE, clamp_image_size
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
    UpdateAvailable,
    UpdateFailed,
    WatchingChanged,
    WatchUncounted,
    WebsocketStatus,
    WindowVisibility,
)

if TYPE_CHECKING:
    from core.miner import Miner as Twitch

logger = logging.getLogger("TwitchDrops")

WINDOW_TITLE = f"Twitch Drop Farm v{__version__}"
# Скільки карток малюємо щонайбільше. Кожна — це кілька віджетів Tk, і на
# кількох сотнях перемальовування стає помітним для ока.
TILE_LIMIT = 120


def _shorten(text: str, limit: int = 34) -> str:
    """Довгі назви нагород ламають сітку — рівні картки читаються краще."""
    return text if len(text) <= limit else text[: limit - 1] + "…"
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
        self._images: dict[tuple[str, int], Any] = {}
        self._last_inventory: InventoryUpdated | None = None
        # дропи, які просуваються просто зараз: назва -> (коли, гра, є, треба).
        # Турнірний канал роздає кілька кампаній одночасно, і Twitch зараховує
        # їх усі — тому показуємо всі, а не той, чий прогрес прийшов останнім.
        self._growing: dict[str, tuple[float, str, int, int]] = {}
        self._watching_name = ""
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_x)
        self._set_window_icon()
        self._apply_theme()

        self._build_layout()
        twitch.events.subscribe(self._on_event)

    def _set_window_icon(self) -> None:
        """Значок у заголовку й на панелі завдань.

        Малюємо той самий, що в треї, — інакше програма мала б два різні
        обличчя. Посилання доводиться тримати в атрибуті: Tk його не зберігає,
        і без цього збирач сміття забере картинку разом із значком.
        """
        try:
            from PIL import ImageTk

            from gui.icon import make_icon
            self._icon_image = ImageTk.PhotoImage(make_icon(64))
            # стаби tkinter не знають про `ImageTk.PhotoImage` від Pillow, хоч
            # саме його `iconphoto` і приймає
            self.root.iconphoto(True, self._icon_image)  # type: ignore[arg-type]
        except Exception as error:
            # без значка програма працює так само — падати тут нема за що
            logger.debug(f"Значок вікна не встановлено: {error}")

    @property
    def _image_size(self) -> int:
        return clamp_image_size(self._twitch.settings.image_size)

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
        # рядок трохи вищий за картинку, інакше вона обрізається зверху й знизу;
        # без картинок висота лишається звичайною, щоб список не був розрідженим
        row = self._image_size + 4 if self._twitch.settings.drop_images else 24
        style.configure("Treeview", background=p["alt"], fieldbackground=p["alt"],
                        foreground=p["fg"], rowheight=row, borderwidth=0)
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
        # заголовок трансляції — єдине місце, де названа гра, коли категорія
        # каналу «Special Events»
        self.title_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.title_var,
                  foreground=p["accent"], wraplength=820,
                  justify="left").pack(anchor="w")
        # justify="left" і одна мітка на всі рядки: дропів на каналі буває
        # кілька, і раніше тут лишався той, чий прогрес прийшов останнім —
        # тобто випадковий. З турнірної трансляції це виглядало так, ніби
        # фармиться подія, а гра невідома.
        self.drop_var = tk.StringVar(value="Дроп не визначено")
        ttk.Label(box, textvariable=self.drop_var, justify="left").pack(
            anchor="w", pady=(4, 2),
        )
        self.progress = ttk.Progressbar(box, maximum=100)
        self.progress.pack(fill="x", pady=(4, 0))

        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=8)
        self.pause_btn = ttk.Button(controls, text="Призупинити", command=self._toggle_pause)
        self.pause_btn.pack(side="left")
        ttk.Button(controls, text="Перечитати інвентар",
                   command=self._reload_now).pack(side="left", padx=6)
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

        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Вигляд:").pack(side="left")
        self.view_var = tk.StringVar(value=self._inventory_view)
        for value, label in (("list", "Список"), ("tiles", "Плитки")):
            ttk.Radiobutton(bar, text=label, value=value, variable=self.view_var,
                            command=self._view_changed).pack(side="left", padx=(8, 0))
        self.tiles_note = ttk.Label(bar, text="")
        self.tiles_note.pack(side="right")
        ttk.Button(bar, text="Експорт…", command=self._export_tables).pack(
            side="right", padx=(0, 8),
        )

        self.inv_body = ttk.Frame(tab)
        self.inv_body.pack(fill="both", expand=True)
        self._build_inventory_list(self.inv_body)
        self._build_inventory_tiles(self.inv_body)
        self._show_inventory_view()

    def _build_inventory_list(self, parent: ttk.Frame) -> None:
        tab = ttk.Frame(parent)
        self.inv_list = tab
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

    def _build_inventory_tiles(self, parent: ttk.Frame) -> None:
        """Сітка карток. Tk не має готового такого віджета, тож збираємо з
        полотна й фрейма всередині: інакше вміст не прокручується."""
        wrap = ttk.Frame(parent)
        self.inv_tiles = wrap
        canvas = tk.Canvas(wrap, bg=self.palette["alt"], highlightthickness=0)
        scroll = ttk.Scrollbar(wrap, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        holder = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=holder, anchor="nw")
        holder.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        # картки мусять займати всю ширину полотна, інакше сітка тулиться ліворуч
        canvas.bind("<Configure>", lambda e: self._tiles_resized(window, e.width))
        self._tiles_columns = 0
        canvas.bind_all("<MouseWheel>", self._tiles_scroll)
        self.tiles_canvas = canvas
        self.tiles_holder = holder

    def _tiles_resized(self, window: int, width: int) -> None:
        """Полотно змінило ширину — у сітку може влізти інша кількість карток."""
        self.tiles_canvas.itemconfigure(window, width=width)
        if (self._columns_for(width) != self._tiles_columns
                and self._last_inventory is not None
                and self._inventory_view == "tiles"):
            self._render_tiles(self._last_inventory)

    @staticmethod
    def _columns_for(width: int) -> int:
        """Скільки карток влізе в рядок.

        Ширина приходить від Tk, а він для ще не показаного віджета віддає
        одиницю — не нуль. Саме через це сітка спершу малювалась одним
        стовпчиком: 1 // 120 дає нуль колонок, і лишалась одна.
        """
        if width <= 100:
            width = 800
        return max(1, width // (TILE_SIZE + 28))

    def _tiles_scroll(self, event: Any) -> None:
        # колесо крутить плитки лише тоді, коли вони на екрані
        if self.inv_tiles.winfo_ismapped():
            self.tiles_canvas.yview_scroll(-int(event.delta / 120), "units")

    @property
    def _inventory_view(self) -> str:
        view = self._twitch.settings.inventory_view
        return view if view in ("list", "tiles") else "list"

    def _export_tables(self) -> None:
        """Пише CSV і HTML в теку стану — туди ж, де історія і журнал."""
        from core import export
        from core.config import STATE_DIR

        try:
            paths = export.write_all(
                STATE_DIR,
                entries=self._twitch.history.entries(),
                campaigns=self._twitch.campaigns,
            )
        except OSError as error:
            messagebox.showerror(WINDOW_TITLE, f"Не вдалося зберегти:\n{error}")
            return
        listing = "\n".join(str(path) for path in paths)
        messagebox.showinfo(WINDOW_TITLE, f"Збережено:\n{listing}")
        self._append_log(f"Експорт: {listing}", "ok")

    def _view_changed(self) -> None:
        self._twitch.settings.inventory_view = self.view_var.get()
        self._twitch.settings.save()
        self._show_inventory_view()
        # перемальовуємо з того, що вже маємо: чекати на наступне читання
        # інвентаря заради зміни вигляду безглуздо
        if self._last_inventory is not None:
            self._render_inventory(self._last_inventory)

    def _show_inventory_view(self) -> None:
        tiles = self._inventory_view == "tiles"
        self.inv_list.pack_forget()
        self.inv_tiles.pack_forget()
        (self.inv_tiles if tiles else self.inv_list).pack(fill="both", expand=True)
        self.tiles_note.configure(
            text="Картинки вимкнені — увімкніть у налаштуваннях"
            if tiles and not self._twitch.settings.drop_images else ""
        )

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
        # Стан читаємо з реєстру, а не з налаштувань: запис могли зняти ззовні —
        # диспетчером завдань, чистилкою автозавантаження чи іншою збіркою.
        self.boot_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(
            misc, text="Запускати разом із Windows",
            variable=self.boot_var, command=self._autostart_changed,
        ).pack(anchor="w")
        self.images_var = tk.BooleanVar(value=settings.drop_images)
        ttk.Checkbutton(
            misc, text="Завантажувати зображення дропів",
            variable=self.images_var, command=self._misc_changed,
        ).pack(anchor="w")
        self.updates_var = tk.BooleanVar(value=settings.check_updates)
        ttk.Checkbutton(
            misc, text="Перевіряти оновлення (лише змінені файли, за хешем)",
            variable=self.updates_var, command=self._misc_changed,
        ).pack(anchor="w")
        size_row = ttk.Frame(misc)
        size_row.pack(fill="x", pady=(2, 0))
        ttk.Label(size_row, text="Розмір:").pack(side="left")
        self.size_var = tk.IntVar(value=self._image_size)
        self.size_label = ttk.Label(size_row, text=f"{self._image_size} px", width=7)
        self.size_label.pack(side="right")
        ttk.Scale(
            size_row, from_=MIN_IMAGE_SIZE, to=MAX_IMAGE_SIZE, orient="horizontal",
            variable=self.size_var, command=self._image_size_changed,
        ).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(
            misc, text="Картинки беруться раз і лишаються на диску; "
                       "розмір можна міняти будь-коли, качати наново не треба.",
            wraplength=320, justify="left",
        ).pack(anchor="w", pady=(0, 4))
        self.dark_var = tk.BooleanVar(value=settings.dark_theme)
        ttk.Checkbutton(
            misc, text="Темна тема (застосується після перезапуску)",
            variable=self.dark_var, command=self._misc_changed,
        ).pack(anchor="w")

        tg = ttk.LabelFrame(right, text="Telegram", padding=8)
        tg.pack(fill="x", pady=(8, 0))
        self.tg_var = tk.BooleanVar(value=settings.telegram["enabled"])
        ttk.Checkbutton(
            tg, text="Увімкнено", variable=self.tg_var,
            command=self._telegram_changed,
        ).pack(anchor="w")
        ttk.Button(
            tg, text="Підключити бота…", command=self._open_telegram_setup,
        ).pack(anchor="w", pady=(6, 0))
        self.tg_hint = ttk.Label(tg, text=self._telegram_hint(), wraplength=320)
        self.tg_hint.pack(anchor="w", pady=(4, 0))

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

    def _autostart_changed(self) -> None:
        """Показуємо те, що вийшло насправді, а не те, що просили.

        Запис у реєстр може не вдатись — політика, антивірус, обмежений
        профіль. Галочка, яка стоїть, коли автозапуску немає, гірша за
        відсутність галочки взагалі.
        """
        got = autostart.apply(self.boot_var.get())
        self.boot_var.set(got)
        self._append_log(
            "Запуск разом із Windows увімкнено" if got
            else "Запуск разом із Windows вимкнено",
            "ok" if got == self.boot_var.get() else "warn",
        )

    def _image_size_changed(self, _value: str = "") -> None:
        """Новий розмір застосовується одразу, без перезапуску й без мережі."""
        size = self.size_var.get()
        if size == self._twitch.settings.image_size:
            return
        self._twitch.settings.image_size = size
        self._twitch.settings.save()
        self.size_label.configure(text=f"{self._image_size} px")
        # мініатюри вже готові під старий розмір — доведеться зібрати наново
        self._images.clear()
        self._apply_theme()
        self._send(CommandType.RELOAD)

    def _misc_changed(self) -> None:
        settings = self._twitch.settings
        settings.farm_cosmetics = self.badges_var.get()
        settings.start_in_tray = self.autostart_var.get()
        settings.dark_theme = self.dark_var.get()
        images_were = settings.drop_images
        settings.drop_images = self.images_var.get()
        settings.check_updates = self.updates_var.get()
        settings.save()
        if settings.drop_images != images_were:
            # висота рядка залежить від того, чи є картинки
            self._apply_theme()
            if settings.drop_images:
                # щойно ввімкнули — перечитуємо інвентар, інакше картинки
                # з'явились би аж за годину, разом із наступним оновленням
                self._send(CommandType.RELOAD)

    # Скільки тримаємо дроп у списку «зараз фармимо» без нового прогресу.
    # Хвилина — крок підтвердження, тож три дає запас на повтори, але не
    # настільки великий, щоб у списку висіли кампанії з минулого каналу.
    GROWING_WINDOW = 3 * 60
    GROWING_LINES = 4

    def _render_growing(self, *, now: float | None = None) -> None:
        """Рядок «зараз фармимо» — усі дропи, що справді просуваються.

        Раніше тут лишався останній надісланий прогрес, і на трансляції EWC це
        показувало «EWC Platinum — Special Events», хоч паралельно росла ще й
        Rocket League. Питання «яка гра фармиться» не мало відповіді у вікні.

        `now` параметром — щоб перевірка могла подати свій годинник, як це вже
        робить `_check_stall` у ядрі.
        """
        now = monotonic() if now is None else now
        fresh = [
            (name, game, have, need)
            for name, (at, game, have, need) in self._growing.items()
            if now - at <= self.GROWING_WINDOW
        ]
        for name, (at, *_rest) in list(self._growing.items()):
            if now - at > self.GROWING_WINDOW:
                del self._growing[name]
        if not fresh:
            self.drop_var.set("Дроп не визначено")
            self.progress["value"] = 0
            return
        # найближчий до завершення — першим: саме він заклеймиться раніше
        fresh.sort(key=lambda row: (row[3] - row[2]) if row[3] else 1 << 30)
        lines = [
            f"{name} — {game} ({have}/{need} хв)"
            for name, game, have, need in fresh[:self.GROWING_LINES]
        ]
        if len(fresh) > self.GROWING_LINES:
            lines.append(f"…і ще {len(fresh) - self.GROWING_LINES}")
        self.drop_var.set("\n".join(lines))
        head = fresh[0]
        self.progress["value"] = (
            min(100, head[2] / head[3] * 100) if head[3] > 0 else 0
        )

    def _reload_now(self) -> None:
        """Ручне «спитати Twitch просто зараз».

        Команда лягає в чергу й виконається на початку наступної ітерації —
        і поки цикл сидить у довгій стадії, кнопка на вигляд не робить нічого.
        Тому пишемо в журнал одразу: натиснуто, чекаємо. Мовчазна кнопка
        змушує тиснути її ще раз, а другий RELOAD нічого не пришвидшує.
        """
        self._append_log("Перечитую інвентар за вашим запитом…")
        self._send(CommandType.RELOAD)

    def _telegram_changed(self) -> None:
        self._twitch.settings.telegram["enabled"] = self.tg_var.get()
        self._twitch.settings.alter()
        self.tg_hint["text"] = self._telegram_hint()

    def _telegram_hint(self) -> str:
        """Одним рядком: чи бот узагалі готовий працювати.

        Галочка «Увімкнено» сама по собі нічого не варта — без токена й чату
        бот мовчить, і раніше про це не було сказано ніде, крім журналу.
        """
        telegram = self._twitch.settings.telegram
        if not telegram["bot_token"]:
            return "Бот не підключений — натисни «Підключити бота…»."
        if not telegram["chat_ids"]:
            return "Токен є, але невідомо, кому писати. Пройди майстер до кінця."
        return "Бот підключений. Зміни діють після перезапуску."

    def _open_telegram_setup(self) -> None:
        from gui.telegram_setup import TelegramSetup

        window = TelegramSetup(self.root, self._twitch.settings)
        # підказка й галочка мають наздогнати те, що майстер зберіг
        window.bind("<Destroy>", lambda _event: self._telegram_saved(), add=True)

    def _telegram_saved(self) -> None:
        try:
            telegram = self._twitch.settings.telegram
            self.tg_var.set(telegram["enabled"])
            self.tg_hint["text"] = self._telegram_hint()
        except tk.TclError:
            pass  # вікно вже закривають

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
                self.title_var.set("")
                self._growing.clear()
                self.drop_var.set("Дроп не визначено")
                self.progress["value"] = 0
            else:
                if event.channel.name != self._watching_name:
                    # інший канал — інші дропи; старі рядки більше не про це
                    self._growing.clear()
                    self._watching_name = event.channel.name
                self.channel_var.set(
                    f"{event.channel.name}  ·  {event.channel.game or 'без гри'}"
                )
                # Категорія «Special Events» не каже, у що грають, — гра названа
                # в заголовку трансляції. Ріжемо довгий: у турнірних заголовках
                # після назви йде перелік команд і хештеги.
                title = " ".join(event.channel.stream_title.split())
                self.title_var.set(
                    title if len(title) <= 90 else title[:87] + "…"
                )
        elif isinstance(event, DropProgress):
            self._growing[event.drop_name] = (
                monotonic(), event.game,
                event.current_minutes, event.required_minutes,
            )
            self._render_growing()
        elif isinstance(event, DropClaimed):
            self._append_log(f"Отримано: {event.rewards} ({event.game})", "ok")
        elif isinstance(event, UpdateAvailable):
            if event.files == 0:
                self._append_log(
                    f"Оновлення {event.version}: локальні хеші вже збігаються", "ok",
                )
                return
            self._append_log(
                f"Є оновлення {event.version}: {event.files} файл(и), "
                f"{event.bytes_to_fetch // 1024} КБ",
                "ok",
            )
            if messagebox.askyesno(
                WINDOW_TITLE,
                f"Доступна версія {event.version}.\n"
                f"Скачати {event.files} змінених файлів "
                f"({event.bytes_to_fetch // 1024} КБ) і перезапустити?",
            ):
                self._send(CommandType.APPLY_UPDATE)
        elif isinstance(event, UpdateFailed):
            self._append_log(f"Оновлення не встало: {event.reason}", "err")
        elif isinstance(event, ProgressStalled):
            why = (
                f"Twitch зараховує «{event.counted_elsewhere}» — інший дроп "
                f"цього ж каналу"
                if event.counted_elsewhere
                else "можливо, Twitch відкритий вручну"
            )
            self._append_log(
                f"Прогрес стоїть {event.minutes_without_progress} хв на "
                f"{event.channel_name} — {why}", "err"
            )
        elif isinstance(event, WatchUncounted):
            self._append_log(
                f"Перегляд не зараховується на {event.channel_name} — "
                f"хвилина не дійшла до Twitch",
                "err",
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
        # Tk не тримає власних посилань на картинки: якщо їх не зберегти тут,
        # збирач сміття забере зображення, і рядки лишаться порожніми. Скидаємо
        # разом зі списком, інакше набір ріс би з кожним оновленням інвентаря.
        self._images = {}
        self._last_inventory = event
        if self._inventory_view == "tiles":
            self._render_tiles(event)
            return
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
                open=False, image=self._thumbnail(campaign.image),
            )
            for drop in campaign.drops:
                self.inv_tree.insert(
                    parent, "end", text=drop.name,
                    values=(
                        f"{drop.current_minutes}/{drop.required_minutes} хв",
                        "отримано" if drop.claimed else "",
                    ),
                    image=self._thumbnail(drop.image),
                )

    def _render_tiles(self, event: InventoryUpdated) -> None:
        """Картки з нагородами. Тут головна саме картинка, а не рядок тексту.

        Показуємо лише те, що ще має сенс: минулі кампанії й забрані дропи в
        плитках лише заважали б — заради них картинки не завантажують.
        """
        for child in self.tiles_holder.winfo_children():
            child.destroy()
        p = self.palette
        columns = self._columns_for(self.tiles_canvas.winfo_width())
        self._tiles_columns = columns
        # рівні колонки: інакше картки з довгими назвами розтягують сусідів
        for column in range(columns):
            self.tiles_holder.columnconfigure(column, weight=1, uniform="tile")
        shown = 0
        for campaign in event.campaigns:
            if campaign.expired:
                continue
            for drop in campaign.drops:
                if drop.claimed or shown >= TILE_LIMIT:
                    continue
                card = ttk.Frame(self.tiles_holder, padding=6)
                card.grid(row=shown // columns, column=shown % columns,
                          sticky="n", padx=4, pady=4)
                picture = self._thumbnail(drop.image or campaign.image, TILE_SIZE)
                if picture:
                    ttk.Label(card, image=picture).pack()
                ttk.Label(card, text=_shorten(drop.name), wraplength=TILE_SIZE + 20,
                          justify="center").pack(pady=(4, 0))
                ttk.Label(card, text=f"{drop.current_minutes}/{drop.required_minutes} хв",
                          foreground=p["accent"]).pack()
                ttk.Label(card, text=_shorten(campaign.game, 22),
                          foreground=p["fg"]).pack()
                shown += 1
        if not shown:
            ttk.Label(self.tiles_holder,
                      text="Немає незабраних дропів в активних кампаніях").pack(pady=20)
        self.tiles_canvas.yview_moveto(0)

    def _thumbnail(self, url: str, size: int | None = None) -> Any:
        """Мініатюра з кешу або порожньо, якщо картинки немає.

        Порожній рядок — саме те, що Treeview очікує замість зображення, тож
        вимкнені картинки не потребують окремої гілки при вставці рядка.
        """
        if not url or not self._twitch.settings.drop_images:
            return ""
        side = size or self._image_size
        key = (url, side)
        if key in self._images:
            return self._images[key]
        path = self._twitch.images.ready(url)
        if path is None:
            return ""
        try:
            from PIL import Image, ImageTk
            with Image.open(path) as picture:
                picture.thumbnail((side, side))
                photo = ImageTk.PhotoImage(picture.convert("RGBA"))
        except Exception:
            return ""
        self._images[key] = photo
        return photo

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
