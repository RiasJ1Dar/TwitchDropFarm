"""Tkinter-інтерфейс: ttk для таблиць, CustomTkinter для вкладки «Майнінг».

Чому два набори віджетів. Заокруглень, тумблерів і пристойного скролбара ttk
не вміє взагалі, і саме через це вікно виглядало старим. CustomTkinter це дає,
але не має аналога `Treeview` — а на ньому стоять «Канали» й «Інвентар». Тому
межа проведена там, де вона дешева: екран без таблиць переїхав, екрани з
таблицями лишились на ttk. Обидва набори — це той самий tkinter, тож живуть в
одному вікні без прошарків.

Картинки нагород показуються, лише коли їх увімкнули в налаштуваннях, і
беруться з кешу на диску — сам інтерфейс у мережу не ходить.

Інтеграція з asyncio: замість `root.mainloop()` крутимо `root.update()` з
asyncio-таски. Так усе лишається однопотоковим, і не потрібні ні
`call_soon_threadsafe`, ні блокування між потоками.
"""
from __future__ import annotations

import asyncio
import logging
import tkinter as tk
import webbrowser
from datetime import datetime, timezone
from time import monotonic
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from core import autostart
from core.config import (
    GITHUB_REPO,
    MAX_IMAGE_SIZE,
    MIN_IMAGE_SIZE,
    THEME_FILE,
    TILE_SIZE,
    clamp_image_size,
    documents_dir,
)
from core.config import VERSION as __version__
from core.config import FarmMode as PriorityMode
from core.config import log_path as log_file
from core.events import (
    CampaignAppeared,
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
    ProtocolStale,
    Router,
    StatusChanged,
    UpdateAvailable,
    UpdateFailed,
    WatchingChanged,
    WatchUncounted,
    WebsocketStatus,
    WindowVisibility,
)
from core.i18n import LANGS, NAMES, t
from core.toolbox import human_size, plural
from gui.celebrate import Confetti
from gui.pulse import PulseDot, rainbow
from gui.theme import PRESETS, blended, preset, read_overrides
from gui.theme import export as export_theme

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


# Наскільки заокруглені кути плитки. Той самий радіус, що в `_card`, — інакше
# картки інвентаря й картки «Майнінгу» виглядали б із різних вікон.
TILE_RADIUS = 12


def rounded_points(x1: float, y1: float, x2: float, y2: float,
                   radius: float) -> list[float]:
    """Координати заокругленого прямокутника для `create_polygon(smooth=True)`.

    Заокруглень `Canvas` не вміє: `create_rectangle` дає гострі кути, а
    CustomTkinter, який малює їх сам, коштує на плитках усемеро дорожче
    (заміряно: 120 карток — 1400 мс проти 179 мс). Тому малюємо самі.

    Точки на прямих ділянках здубльовані навмисно. Tk згладжує полігон
    квадратичними Безьє, де кожна точка тягне криву до себе; без дубля пряма
    сторона вигиналась би всередину, і замість картки виходила б подушка.
    Кути ж, навпаки, задані однією точкою — саме вона й округлює ріг.

    Чиста функція, щоб перевірятись без вікна: рахунок тут легко зіпсувати
    непомітно, а на око крива різниця в пару пікселів не видна.
    """
    # Радіус більший за половину сторони дав би кути, що налазять один на
    # одного, — обмежуємо, а не покладаємось на те, що плитка завжди велика.
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + radius, y1, x1 + radius, y1, x2 - radius, y1, x2 - radius, y1,
        x2, y1,
        x2, y1 + radius, x2, y1 + radius, x2, y2 - radius, x2, y2 - radius,
        x2, y2,
        x2 - radius, y2, x2 - radius, y2, x1 + radius, y2, x1 + radius, y2,
        x1, y2,
        x1, y2 - radius, x1, y2 - radius, x1, y1 + radius, x1, y1 + radius,
        x1, y1,
    ]


# Індикатор у шапці: чи фарм насправді крутиться, а не лише «Дивимось».
FARM_BADGE = {
    "going": ("farm_going", "ok"),
    "stalled": ("farm_stalled", "err"),
    "uncounted": ("farm_uncounted", "err"),
    "paused": ("farm_paused", "warn"),
    "idle": ("farm_idle", "fg"),
}
# як часто прокручуємо цикл Tk; 20 к/с — непомітно для ока й дешево для CPU
TK_TICK = 0.05

DARK = {
    "bg": "#1b1b1f", "fg": "#e6e6e6", "alt": "#26262c", "muted": "#9a9aa3",
    "accent": "#9147ff", "ok": "#33cc66", "warn": "#ffb020", "err": "#ff5c5c",
}
LIGHT = {
    "bg": "#f5f5f7", "fg": "#1b1b1f", "alt": "#ffffff", "muted": "#6b6b73",
    "accent": "#772ce8", "ok": "#1a9e4b", "warn": "#b06a00", "err": "#c62828",
}

# Додаткові тони для вкладки «Майнінг»: картка мусить відрізнятись від тла, а в
# ttk-палітрі для цього не було чого взяти — `alt` там зайнятий полями вводу.
# `line` — межа картки замість рамки: тонка світліша смуга читається спокійніше
# за намальований бордюр, а тіней Tk не вміє взагалі.
CARD_DARK = {"page": "#141317", "card": "#1e1d24", "line": "#2a2933",
             "hover": "#3a2a63"}
CARD_LIGHT = {"page": "#eceaf0", "card": "#ffffff", "line": "#dcdae2",
              "hover": "#e6ddff"}

# Скільки місця лишати навколо картки. Винесено в константу, бо ті самі відступи
# повторюються в кожному блоці вкладки, і різнобій одразу видно оком.
PAD = 12
# Ширина правої колонки на вкладці «Майнінг». Вужче — назви дропів не влазять,
# ширше — з'їдає місце в журналу без користі.
SIDE_WIDTH = 330
# Скільки рядків показуємо в «Ось-ось заберемо». Більше не має сенсу: це не
# інвентар, а підказка «що трапиться найближчим часом».
SOON_LIMIT = 8


# Маршрути «подія → що показати у вікні». Оголошено до класу, бо декоратори в
# тілі класу виконуються під час його створення.
SHOW = Router()


class GUI:
    def __init__(self, twitch: Twitch):
        self._twitch = twitch
        self._closed = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        dark = twitch.settings.dark_theme
        self._load_palette(dark)

        from core.toolbox import enable_windows_dpi
        enable_windows_dpi()

        # CustomTkinter має власне поняття теми й масштабу. Масштаб вимикаємо
        # свідомо: DPI ми вже підняли самі (`enable_windows_dpi`), і другий
        # множник поверх нього роздував вікно на екранах зі 150 %.
        ctk.set_appearance_mode("dark" if dark else "light")
        ctk.deactivate_automatic_dpi_awareness()

        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.geometry("900x620")
        self.root.minsize(760, 520)
        self._apply_ui_fonts()
        # чи є куди ховатись; уточнюється, коли трей реально піднявся
        self._tray_available = False
        # питання про оновлення чекає, поки вікно намальоване й видиме
        self._pending_update: str | None = None
        self._ui_ready = False
        self._asking_update = False
        # стан кожного вебсокета окремо: індекс -> (статус, кількість топіків)
        self._ws_state: dict[int, tuple[str, int]] = {}
        self._images: dict[tuple[str, int], Any] = {}
        # CTk-віджети та ключі палітри, якими їх пофарбували: `_restyle_widgets`
        # проганяє список заново, коли міняється тема
        self._painted: list[tuple[Any, dict[str, str]]] = []
        self._last_inventory: InventoryUpdated | None = None
        # дропи, які просуваються просто зараз: назва -> (коли, гра, є, треба).
        # Турнірний канал роздає кілька кампаній одночасно, і Twitch зараховує
        # їх усі — тому показуємо всі, а не той, чий прогрес прийшов останнім.
        self._growing: dict[str, tuple[float, str, int, int]] = {}
        self._watching_name = ""
        self._farm_state = ""
        # Смуга прогресу їде до цілі плавно, а не стрибає: показане значення й
        # бажане живуть окремо, а `after` посуває перше до другого.
        self._progress_shown = 0.0
        self._progress_target = 0.0
        self._progress_job: str | None = None
        # Переливчаста смуга: окремий цикл, який крутиться лише коли її обрали.
        self._rainbow_phase = 0.0
        self._rainbow_job: str | None = None
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_x)
        self._set_window_icon()
        self._apply_theme()

        self._build_layout()
        twitch.events.subscribe(self._on_event)

    def _load_palette(self, dark: bool) -> None:
        """Вбудована палітра плюс `theme.json`, якщо людина його поклала.

        Читаємо щоразу, а не один раз при старті: так виправлену тему видно
        після перемикання темної/світлої, без перезапуску програми.
        """
        base = DARK if dark else LIGHT
        cards = CARD_DARK if dark else CARD_LIGHT
        allowed = frozenset(base) | frozenset(cards)
        # Порядок навмисний: вбудована тема → обраний набір → theme.json.
        # Файл останній, бо це ручна правка: людина, яка його написала, має
        # бачити свій колір, а не колір набору.
        chosen = preset(self._twitch.settings.theme_preset)
        custom = {**chosen, **read_overrides(THEME_FILE, allowed)}
        self.palette = blended(base, custom)
        self.cards = blended(cards, custom)

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

    def _apply_ui_fonts(self) -> None:
        """Один шрифт на ttk і tk.Label — інакше шапка виглядає розмитішою за вкладки."""
        import tkinter.font as tkfont
        for name, size in (
            ("TkDefaultFont", 10),
            ("TkTextFont", 10),
            ("TkHeadingFont", 10),
            ("TkMenuFont", 10),
        ):
            try:
                tkfont.nametofont(name).configure(family="Segoe UI", size=size)
            except tk.TclError:
                pass

    # ------------------------------------------------------------ оформлення

    def _apply_theme(self) -> None:
        p = self.palette
        self.root.configure(bg=p["bg"])
        style = ttk.Style(self.root)
        with_theme = "clam" if "clam" in style.theme_names() else style.theme_use()
        style.theme_use(with_theme)
        style.configure(".", background=p["bg"], foreground=p["fg"],
                        fieldbackground=p["alt"], borderwidth=0)
        style.configure("TFrame", background=p["bg"])
        style.configure("TLabel", background=p["bg"], foreground=p["fg"])
        style.configure("TLabelframe", background=p["bg"], foreground=p["fg"])
        style.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])
        style.configure("TButton", background=p["alt"], foreground=p["fg"], padding=6)
        style.map("TButton",
                  background=[("active", p["accent"]), ("pressed", p["accent"])],
                  foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])
        style.configure("TRadiobutton", background=p["bg"], foreground=p["fg"])
        style.configure("TCombobox", fieldbackground=p["alt"], background=p["alt"],
                        foreground=p["fg"], arrowcolor=p["fg"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", p["alt"])],
                  foreground=[("readonly", p["fg"])],
                  background=[("readonly", p["alt"])])
        # `TScrollbar`, `Horizontal.TScale`, `TCheckbutton` і `Accent.TButton`
        # звідси прибрані: віджетів, які вони фарбували, у проєкті вже немає
        # жодного. Мертвий стиль гірший за відсутній — його бачать при читанні
        # й вирішують, що місце десь оформлене, хоч насправді ні.
        self._style_tables(style)
        style.configure("TProgressbar", background=p["accent"], troughcolor=p["alt"],
                        thickness=8)
        self._restyle_widgets()

    # Наскільки рядок таблиці вищий за свій вміст. Щільні рядки видають вік
    # інтерфейсу сильніше за форму: у сучасних списках між рядками є повітря.
    ROW_PADDING = 14
    # Висота рядка без картинок. Було 24 — рівно висота тексту, тобто впритул.
    ROW_PLAIN = 32

    def _style_tables(self, style: ttk.Style) -> None:
        """Оформлення обох `Treeview`. Віджет лишається ttk — і це навмисно.

        На 198 каналах (`MAX_CHANNELS`) CustomTkinter дає 3026 мс на побудову
        списку проти 53 мс і 74 мс на оновлення однієї цифри глядачів проти
        7 мс, тримаючи 990 віджетів замість одного. Вікно крутиться в тому
        самому потоці, що й asyncio, тож це була б затримка не малювання, а
        самого фарму. Тому таблиці причісуємо стилем, а не переписуємо.

        Лишається чесний залишок: рядки прямокутні, і заокруглити їх у Tk
        неможливо. Зменшуємо контраст із рештою вікна, а не ховаємо це.
        """
        p, c = self.palette, self.cards
        # рядок трохи вищий за картинку, інакше вона обрізається зверху й знизу
        row = (self._image_size + self.ROW_PADDING
               if self._twitch.settings.drop_images else self.ROW_PLAIN)
        # Розкладки, а не кольори. Рамку поля й «підняття» заголовка малює сам
        # елемент розкладки, тож ні `borderwidth`, ні `relief` їх не прибирають:
        # по краю таблиці лишалась сіра канавка, а заголовки виглядали
        # натиснутими кнопками з дев'яностих. Прибираємо самі елементи.
        # Тип розкладки рекурсивний (елемент може мати `children` з такого ж
        # списку), і в стабах tkinter він не виражений — звідси `Any`.
        layouts: dict[str, list[tuple[str, Any]]] = {
            "Table.Treeview": [("Treeview.treearea", {"sticky": "nswe"})],
            # `cell` лишаємо — саме він малює тло заголовка; викидаємо `border`
            "Table.Treeview.Heading": [
                ("Treeheading.cell", {"sticky": "nswe"}),
                ("Treeheading.padding", {"sticky": "nswe", "children": [
                    ("Treeheading.text", {"sticky": "we"}),
                ]}),
            ],
        }
        for name, layout in layouts.items():
            try:
                style.layout(name, layout)
            except tk.TclError as error:
                # У чужій темі ttk потрібних елементів може не бути — тоді
                # лишається типова розкладка, з рамкою. Не падаємо, але й не
                # мовчимо: інакше «звідки канавка» довелось би шукати наосліп.
                logger.debug(f"Розкладку {name} не замінено: {error}")
        style.configure("Table.Treeview", background=c["card"],
                        fieldbackground=c["card"], foreground=p["fg"],
                        rowheight=row, borderwidth=0, relief="flat")
        style.configure("Table.Treeview.Heading", background=c["card"],
                        foreground=p["muted"], font=("Segoe UI", 9, "bold"),
                        relief="flat", borderwidth=0, padding=(8, 8))
        style.map("Table.Treeview.Heading",
                  background=[("active", c["hover"])],
                  foreground=[("active", p["fg"])],
                  relief=[("active", "flat"), ("pressed", "flat")])
        style.map("Table.Treeview", background=[("selected", p["accent"])],
                  foreground=[("selected", "#ffffff")])

    def _restyle_widgets(self) -> None:
        """Те, що ttk не фарбує сам: журнал, списки, шапка."""
        p = self.palette
        if getattr(self, "farm_label", None) is not None:
            previous = self._farm_state
            self._farm_state = ""
            self._set_farm_state(previous or "idle")
        if getattr(self, "log", None) is not None:
            self.log.configure(bg=self.cards["card"], fg=p["fg"],
                               insertbackground=p["fg"])
            self.log.tag_configure("time", foreground=p["muted"])
            for tag, colour in (("ok", p["ok"]), ("warn", p["warn"]), ("err", p["err"])):
                self.log.tag_configure(tag, foreground=colour)
        for name in ("prio_list", "watch_list"):
            box = getattr(self, name, None)
            if box is not None:
                box.configure(bg=p["alt"], fg=p["fg"], selectbackground=p["accent"],
                              selectforeground="#ffffff", highlightthickness=0)
        if getattr(self, "tiles_canvas", None) is not None:
            self.tiles_canvas.configure(bg=self.cards["page"])
            self.tiles_holder.configure(bg=self.cards["page"])
            # Плитки — не віджети, а намальовані фігури: `configure` до них не
            # дістанеться, тож при зміні теми їх треба покласти заново. Без
            # цього інвентар лишався б у кольорах попередньої теми до
            # наступного читання, тобто до години.
            if self._last_inventory is not None:
                self._render_tiles(self._last_inventory)
        # `conn_label` і `status_label` тут більше не згадані навмисно: вони
        # стали `CTkLabel`, фарбуються разом з рештою через `_painted` нижче,
        # а `foreground` цей віджет не знає взагалі.
        if getattr(self, "title_label", None) is not None:
            # CTkLabel: колір тексту зветься інакше, ніж у ttk
            self.title_label.configure(text_color=p["accent"])
        if getattr(self, "root", None) is not None:
            self.root.configure(bg=p["bg"])
        # CustomTkinter про нашу палітру не знає: віджети лишились би в тих
        # кольорах, з якими їх створили, і вкладка «Налаштування» після
        # перемикання теми була б чужого кольору до перезапуску
        for widget, options in getattr(self, "_painted", []):
            try:
                widget.configure(**{name: self._colour(key)
                                    for name, key in options.items()})
            except tk.TclError as error:
                # Найчастіше віджет просто знищили — тоді фарбувати нема що.
                # Але той самий виняток дає й невідомий параметр, а це вже наша
                # помилка: віджет мовчки лишиться в чужих кольорах. `debug`, бо
                # перше буває штатно, а друге треба мати змогу побачити.
                logger.debug(f"Не перефарбовано {widget!r}: {error}")

    # ------------------------------------------------------------ розкладка

    def _build_layout(self) -> None:
        pal = self.palette
        # Шапка — така сама картка, як на вкладці «Майнінг»: заокруглена, з
        # тонкою межею замість `ttk.Separator`. Роздільна лінія була єдиним, що
        # відділяло шапку від вкладок, і саме вона виглядала найстарішою.
        top = self._paint(
            ctk.CTkFrame(self.root, corner_radius=12, border_width=1),
            fg_color="card", border_color="line",
        )
        top.pack(fill="x", padx=PAD, pady=(8, 0))
        row = ctk.CTkFrame(top, fg_color="transparent")
        row.pack(fill="x", padx=PAD, pady=8)
        # Бейдж стану: жива крапка + підпис. Крапка дихає, поки фарм іде, і
        # завмирає в усіх інших станах — рух помітний боковим зором, тож не
        # доводиться вчитуватись у текст, щоб зрозуміти, чи є робота.
        #
        # Бейдж свідомо лишається на `tk.Frame` і `tk.Label`. `PulseDot` — це
        # `tk.Canvas`, який змішує колір ореолу з кольором тла й тому мусить
        # знати тло точним рядком; `_set_farm_state` перефарбовує підпис через
        # `bg`/`fg`, яких у `CTkLabel` немає взагалі. Переведення коштувало б
        # переписування обох, а виграшу не дало б: плашка без заокруглень тут
        # не видна — вона того самого кольору, що й картка навколо.
        badge = tk.Frame(row, bg=pal["alt"])
        badge.pack(side="left")
        self.farm_dot = PulseDot(badge, background=pal["alt"], colour=pal["fg"])
        self.farm_dot.pack(side="left", padx=(6, 0))
        self.farm_label = tk.Label(
            badge, text=t("farm_idle"), font=("Segoe UI", 10, "bold"),
            bg=pal["alt"], fg=pal["fg"], padx=6, pady=2,
        )
        self.farm_label.pack(side="left")
        self._set_farm_state("idle")
        self.status_var = tk.StringVar(value=t("starting"))
        self.status_label = self._paint(
            ctk.CTkLabel(row, textvariable=self.status_var, anchor="w",
                         font=("Segoe UI", 13, "bold")),
            text_color="fg",
        )
        self.status_label.pack(side="left", padx=(12, 0), fill="x", expand=True)
        self.conn_var = tk.StringVar(value="")
        self.conn_label = self._paint(
            ctk.CTkLabel(row, textvariable=self.conn_var, anchor="e",
                         font=("Segoe UI", 11)),
            text_color="muted",
        )
        self.conn_label.pack(side="right")

        # `CTkTabview` замість `ttk.Notebook`: заокруглена смуга замість
        # прямокутних язичків. Перехід дешевий саме тому, що вкладки ніде не
        # перемикаються з коду — жодного `.select()` у проєкті немає, тож
        # прив'язки до їхніх назв (а вони перекладені дев'ятьма мовами) теж.
        c = self.cards
        tabs = ctk.CTkTabview(
            self.root, corner_radius=12, fg_color=c["page"],
            # По центру — за словом людини. Щоб смуга при цьому не виглядала
            # загубленою в порожнечі, кнопки робимо помітно більшими: своєї
            # ширини `CTkTabview` задати не дає, тож єдиний важіль — шрифт,
            # за яким кнопка й розтягується.
            anchor="center",
            segmented_button_font=("Segoe UI", 16, "bold"),
            segmented_button_selected_color=self.palette["accent"],
            segmented_button_selected_hover_color=self.palette["accent"],
            segmented_button_unselected_color=c["card"],
            segmented_button_unselected_hover_color=c["hover"],
            text_color=self.palette["fg"],
        )
        tabs.pack(fill="both", expand=True, padx=PAD, pady=(8, 8))
        self._build_mining_tab(tabs)
        self._build_channels_tab(tabs)
        self._build_inventory_tab(tabs)
        self._build_settings_tab(tabs)
        # Смуга вже існує — можна вмикати перелив, якщо його обрали минулого разу
        self._apply_progress_style()

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
        card = ctk.CTkFrame(parent, corner_radius=12, fg_color=c["card"],
                            border_width=1, border_color=c["line"])
        card.pack(fill="x")
        return card

    def _block(self, parent: tk.Misc, title: str, *, top: int = 0,
               grow: bool = False) -> ctk.CTkFrame:
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
        widget.configure(**{name: self._colour(key)
                            for name, key in options.items()})
        return widget

    def _button(self, parent: tk.Misc, text: str, command: Any,
                *, accent: bool = False, width: int = 140) -> ctk.CTkButton:
        """Кнопка вкладки. Акцентна — та, яку натискають найчастіше."""
        p, c = self.palette, self.cards
        return ctk.CTkButton(
            parent, text=text, command=command, corner_radius=8, height=32,
            width=width,
            font=("Segoe UI", 12, "bold" if accent else "normal"),
            fg_color=p["accent"] if accent else c["card"],
            hover_color=p["accent"] if accent else c["hover"],
            text_color="#ffffff" if accent else p["fg"],
            border_width=0 if accent else 1, border_color=c["line"],
        )

    def _switch(self, parent: tk.Misc, text: str, variable: tk.Variable,
                command: Any) -> ctk.CTkSwitch:
        """Тумблер замість галочки: увімкнено це чи ні, видно без вчитування.

        `onvalue`/`offvalue` задані явно: типово CustomTkinter пише в змінну
        одиницю й нуль, а тут скрізь `tk.BooleanVar` — і саме її читає
        `_misc_changed`.
        """
        return self._paint(
            ctk.CTkSwitch(
                parent, text=text, variable=variable, command=command,
                onvalue=True, offvalue=False, switch_width=40, switch_height=20,
                font=("Segoe UI", 12),
            ),
            fg_color="line", progress_color="accent", button_color="muted",
            button_hover_color="fg", text_color="fg",
        )

    def _entry(self, parent: tk.Misc, variable: tk.StringVar | None = None,
               *, placeholder: str = "") -> ctk.CTkEntry:
        extra: dict[str, Any] = {}
        if variable is not None:
            extra["textvariable"] = variable
        if placeholder:
            # підказка сірим, поки поле порожнє: інакше незрозуміло, що буде,
            # якщо нічого не вписати
            extra["placeholder_text"] = placeholder
        return self._paint(
            ctk.CTkEntry(parent, corner_radius=8, height=30,
                         font=("Segoe UI", 12), **extra),
            fg_color="alt", border_color="line", text_color="fg",
        )

    def _hint(self, parent: tk.Misc, text: str, *, colour: str = "muted",
              wrap: int = 320) -> ctk.CTkLabel:
        """Мітка-пояснення. `wrap=0` — короткий підпис, який не переносять."""
        return self._paint(
            ctk.CTkLabel(parent, text=text, wraplength=wrap, justify="left",
                         anchor="w", font=("Segoe UI", 12)),
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

    # Яку частку шляху до цілі проходимо за кадр. 0.25 дає рух, який око
    # встигає простежити, але який не тягнеться: смуга доїжджає за ~10 кадрів.
    PROGRESS_STEP = 0.25
    PROGRESS_FRAME_MS = 40
    # Ближче за це до цілі — вважаємо, що приїхали, і зупиняємо анімацію.
    PROGRESS_EPSILON = 0.002

    @classmethod
    def _advance(cls, current: float, target: float) -> float:
        """Один крок до цілі. Винесено окремо, щоб перевірятись без вікна."""
        if abs(target - current) <= cls.PROGRESS_EPSILON:
            return target
        return current + (target - current) * cls.PROGRESS_STEP

    # Скільки часу забирає повний круг веселки. Півхвилини — перелив помітний,
    # але не миготить перед очима, коли на нього не дивишся.
    RAINBOW_PERIOD_MS = 30000
    RAINBOW_FRAME_MS = 60

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
            self._rainbow_job = self.root.after(self.RAINBOW_FRAME_MS,
                                                self._rainbow_tick)
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
        self._rainbow_job = self.root.after(self.RAINBOW_FRAME_MS,
                                            self._rainbow_tick)

    def _progress_colour(self) -> str:
        """Колір смуги за станом фарму.

        Смуга завжди була фіолетова, хоч програма чудово знає, іде фарм чи
        стоїть. Колір повторює мітку в шапці — око ловить його швидше, ніж
        читає слово.
        """
        p = self.palette
        return {
            "going": p["ok"], "stalled": p["err"],
            "uncounted": p["err"], "paused": p["warn"],
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
            self._progress_job = root.after(self.PROGRESS_FRAME_MS,
                                            self._progress_tick)

    def _progress_tick(self) -> None:
        self._progress_job = None
        self._progress_shown = self._advance(self._progress_shown,
                                             self._progress_target)
        self.progress.set(self._progress_shown)
        if self._progress_shown == self._progress_target:
            return
        self._progress_job = self.root.after(self.PROGRESS_FRAME_MS,
                                             self._progress_tick)

    def _build_side_column(self, side: tk.Misc) -> None:
        """Права колонка: що заберемо найближчим часом і що ось-ось згорить.

        Обидві картки живляться зрізом інвентаря, який вікно й так отримує
        (`InventoryUpdated`), — жодного нового запиту до Twitch.
        """
        soon = self._card(side, t("soon_title"))
        self.soon_rows = ctk.CTkFrame(soon, fg_color="transparent")
        self.soon_rows.pack(fill="x", padx=PAD, pady=PAD)

        ending = self._card(side, t("ending_title"))
        self.ending_rows = ctk.CTkFrame(ending, fg_color="transparent")
        self.ending_rows.pack(fill="x", padx=PAD, pady=PAD)
        self._render_side()

    def _render_side(self) -> None:
        """Перемальовує обидві картки правої колонки."""
        rows = getattr(self, "soon_rows", None)
        if rows is None:
            return
        p = self.palette
        snapshot = self._last_inventory
        campaigns = snapshot.campaigns if snapshot is not None else ()

        # ---- що ось-ось заберемо: дропи з початим прогресом, найближчі перші
        started = [
            (drop, campaign)
            for campaign in campaigns if campaign.active
            for drop in campaign.drops
            if not drop.claimed and drop.required_minutes > 0
            and drop.current_minutes > 0
        ]
        started.sort(key=lambda pair: pair[0].required_minutes - pair[0].current_minutes)

        for child in rows.winfo_children():
            child.destroy()
        if not started:
            ctk.CTkLabel(rows, text=t("soon_empty"), anchor="w",
                         text_color=p["muted"]).pack(anchor="w", fill="x")
        for drop, campaign in started[:SOON_LIMIT]:
            left = drop.required_minutes - drop.current_minutes
            line = ctk.CTkFrame(rows, fg_color="transparent")
            line.pack(fill="x", pady=2)
            ctk.CTkLabel(line, text=_shorten(drop.name, 24), anchor="w",
                         text_color=p["fg"]).pack(side="left", fill="x", expand=True)
            # лишилось хвилин — головне число, тому кольором стану
            ctk.CTkLabel(line, text=t("minutes_left_short", minutes=left),
                         anchor="e", text_color=p["ok"] if left <= 30 else p["muted"],
                         ).pack(side="right")
            # гра дрібним рядком: без неї «Drop 1» нічого не каже, а назви
            # дропів у Twitch майже завжди однакові в різних кампаніях
            ctk.CTkLabel(rows, text=_shorten(campaign.game, 28), anchor="w",
                         font=("Segoe UI", 9), text_color=p["muted"]).pack(
                anchor="w", fill="x")
            bar = ctk.CTkProgressBar(rows, height=4, corner_radius=2,
                                     progress_color=p["accent"], fg_color=self.cards["line"])
            bar.pack(fill="x", pady=(0, 6))
            bar.set(min(1.0, drop.current_minutes / drop.required_minutes))

        # ---- що ось-ось згорить: активні кампанії за часом до кінця
        ends = getattr(self, "ending_rows", None)
        if ends is None:
            return
        for child in ends.winfo_children():
            child.destroy()
        now = datetime.now(timezone.utc)
        alive = sorted(
            (c for c in campaigns
             if c.active and c.claimed_drops < c.total_drops and c.ends_at > now),
            key=lambda c: c.ends_at,
        )
        if not alive:
            ctk.CTkLabel(ends, text=t("soon_empty"), anchor="w",
                         text_color=p["muted"]).pack(anchor="w", fill="x")
        for campaign in alive[:SOON_LIMIT]:
            hours = max(0, int((campaign.ends_at - now).total_seconds() // 3600))
            line = ctk.CTkFrame(ends, fg_color="transparent")
            line.pack(fill="x", pady=2)
            ctk.CTkLabel(line, text=_shorten(campaign.game, 20), anchor="w",
                         text_color=p["fg"]).pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                line,
                text=t("hours_left_short", hours=hours),
                anchor="e",
                # менше доби — попередження: встигнути вже може не вийти
                text_color=p["warn"] if hours < 24 else p["muted"],
            ).pack(side="right")
            ctk.CTkLabel(ends, text=f"{campaign.claimed_drops}/{campaign.total_drops}",
                         anchor="w", text_color=p["muted"]).pack(anchor="w")

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
        ctk.CTkLabel(inner, textvariable=self.channel_var, anchor="w",
                     font=("Segoe UI", 15, "bold"), text_color=p["fg"]).pack(
            anchor="w", fill="x")
        # заголовок трансляції — єдине місце, де названа гра, коли категорія
        # каналу «Special Events»
        self.title_var = tk.StringVar(value="")
        self.title_label = ctk.CTkLabel(inner, textvariable=self.title_var,
                                        text_color=p["accent"], anchor="w",
                                        wraplength=820, justify="left")
        self.title_label.pack(anchor="w", fill="x")
        # одна мітка на всі рядки: дропів на каналі буває кілька, і раніше тут
        # лишався той, чий прогрес прийшов останнім — тобто випадковий. З
        # турнірної трансляції це виглядало так, ніби фармиться подія, а гра
        # невідома.
        self.drop_var = tk.StringVar(value=t("drop_unknown"))
        ctk.CTkLabel(inner, textvariable=self.drop_var, anchor="w",
                     justify="left", text_color=p["fg"]).pack(
            anchor="w", fill="x", pady=(6, 8))
        self.progress = ctk.CTkProgressBar(
            inner, height=10, corner_radius=5, progress_color=p["accent"],
            fg_color=c["line"],
        )
        self.progress.pack(fill="x")
        self._set_progress(0)
        # Накладка святкування: лежить поверх цієї ж картки й показується лише
        # на півтори секунди після клейму. Решту часу її не видно взагалі.
        self.confetti = Confetti(
            inner, background=c["card"],
            colours=(p["accent"], p["ok"], p["warn"], "#ffffff"),
        )

        controls = ctk.CTkFrame(main, fg_color="transparent")
        controls.pack(fill="x", pady=(PAD, 0))
        self.pause_btn = self._button(controls, t("pause"), self._toggle_pause,
                                      accent=True)
        self.pause_btn.pack(side="left")
        self._button(controls, t("reload_inventory"), self._reload_now).pack(
            side="left", padx=8)
        # Кнопки «Згорнути в трей» тут немає навмисно: хрестик вікна робить
        # рівно те саме і за тієї самої умови (`on_window_x` → `hide_to_tray`,
        # поки трей живий). Гірше: коли трей не піднявся, хрестик коректно
        # закриває програму, а кнопка сховала б вікно назовсім — повернути його
        # можна було б хіба що командою /show із Telegram.
        # Сам `hide_to_tray` лишається: його кличуть хрестик, меню трея і
        # запуск із `--tray`.
        self._button(controls, t("quit_miner"), self.confirm_quit).pack(
            side="right")

        log_wrap = ctk.CTkFrame(main, fg_color="transparent")
        log_wrap.pack(fill="both", expand=True, pady=(PAD, 0))
        log_box = self._card(log_wrap, t("log"))
        log_box.pack(fill="both", expand=True)
        # Журнал лишається tk.Text свідомо: у CTkTextbox інші імена кольорів,
        # а тут працюють теги (`ok`/`warn`/`err`) і перефарбування при зміні
        # теми. Сучасний вигляд дає скролбар — саме він тут і був старим.
        self.log = tk.Text(log_box, height=12, wrap="word", bg=c["card"], fg=p["fg"],
                           insertbackground=p["fg"], relief="flat",
                           font=("Segoe UI", 9), padx=PAD, pady=8,
                           highlightthickness=0, borderwidth=0)
        scroll = ctk.CTkScrollbar(log_box, command=self.log.yview, width=12,
                                  button_color=c["line"],
                                  button_hover_color=p["accent"])
        self.log.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y", pady=8, padx=(0, 6))
        self.log.pack(side="left", fill="both", expand=True)
        for tag, colour in (("ok", p["ok"]), ("warn", p["warn"]), ("err", p["err"])):
            self.log.tag_configure(tag, foreground=colour)

    def _build_channels_tab(self, tabs: ctk.CTkTabview) -> None:
        # Усередині лишається ttk: тут `Treeview`, який не переводимо (замір —
        # 3026 мс проти 53 мс на 198 рядках).
        tab = ttk.Frame(tabs.add(t("tab_channels")), padding=10)
        tab.pack(fill="both", expand=True)
        ttk.Label(
            tab, text=t("channels_hint")
        ).pack(anchor="w", pady=(0, 6))
        columns = ("name", "game", "viewers", "status")
        self.channel_tree = ttk.Treeview(tab, columns=columns, show="headings",
                                         style="Table.Treeview")
        for column, title, width in (
            ("name", t("col_channel"), 200), ("game", t("col_game"), 260),
            ("viewers", t("col_viewers"), 90), ("status", t("col_status"), 140),
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
            button_color="line", button_hover_color="accent", fg_color="card",
        )
        table.configure(yscrollcommand=scroll.set)
        # канавка того ж кольору, що й таблиця: смуга читається як її частина,
        # а не як окрема сіра колонка збоку
        scroll.pack(side="right", fill="y", padx=(2, 0))

    def _build_inventory_tab(self, tabs: ctk.CTkTabview) -> None:
        tab = ttk.Frame(tabs.add(t("tab_inventory")), padding=10)
        tab.pack(fill="both", expand=True)

        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text=t("view")).pack(side="left")
        self.view_var = tk.StringVar(value=self._inventory_view)
        for value, label in (("list", t("view_list")), ("tiles", t("view_tiles"))):
            ttk.Radiobutton(bar, text=label, value=value, variable=self.view_var,
                            command=self._view_changed).pack(side="left", padx=(8, 0))
        self.tiles_note = ttk.Label(bar, text="")
        self.tiles_note.pack(side="right")
        ttk.Button(bar, text=t("export") + "…", command=self._export_tables).pack(
            side="right", padx=(0, 8),
        )

        self.inv_body = ttk.Frame(tab)
        self.inv_body.pack(fill="both", expand=True)
        self._build_inventory_list(self.inv_body)
        self._build_inventory_tiles(self.inv_body)
        self._show_inventory_view()

    def _build_inventory_list(self, parent: tk.Misc) -> None:
        tab = ttk.Frame(parent)
        self.inv_list = tab
        self.inv_tree = ttk.Treeview(tab, columns=("progress", "state"),
                                     show="tree headings", style="Table.Treeview")
        self.inv_tree.heading("#0", text=t("col_campaign"))
        self.inv_tree.heading("progress", text=t("col_progress"))
        self.inv_tree.heading("state", text=t("col_status"))
        self.inv_tree.column("#0", width=420)
        self.inv_tree.column("progress", width=120, anchor="center")
        self.inv_tree.column("state", width=160, anchor="w")
        self._table_scrollbar(tab, self.inv_tree)
        self.inv_tree.pack(fill="both", expand=True)

    def _build_inventory_tiles(self, parent: tk.Misc) -> None:
        """Сітка карток. Tk не має готового такого віджета, тож збираємо з
        полотна й фрейма всередині: інакше вміст не прокручується.

        Полотно лишається `tk.Canvas` свідомо. `CTkScrollableFrame` із картками
        коштує на тих самих 120 плитках 1400 мс проти 179 мс і тримає втричі
        більше віджетів; заокруглення, заради яких його брали б, тут дешевше
        намалювати руками — цим займається `_tile`.
        """
        c = self.cards
        # Колір заданий явно, а не `transparent`: батько тут — `ttk.Frame`, у
        # якого CustomTkinter не може спитати тло (в ttk його тримає стиль, а
        # не сам віджет), і прозорий фрейм узяв би колір навмання.
        wrap = self._paint(ctk.CTkFrame(parent, corner_radius=0),
                           fg_color="page")
        self.inv_tiles = wrap
        canvas = tk.Canvas(wrap, bg=c["page"], highlightthickness=0,
                           borderwidth=0)
        scroll = self._paint(
            ctk.CTkScrollbar(wrap, command=canvas.yview, width=12),
            button_color="line", button_hover_color="accent",
            fg_color="page",
        )
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # `tk.Frame`, а не `ttk.Frame`: тло має точно збігатися з полотном, а
        # ttk фарбується стилем `TFrame`, тобто кольором вікна, не сторінки.
        holder = tk.Frame(canvas, bg=c["page"])
        window = canvas.create_window((0, 0), window=holder, anchor="nw")
        holder.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        # картки мусять займати всю ширину полотна, інакше сітка тулиться ліворуч
        canvas.bind("<Configure>", lambda e: self._tiles_resized(window, e.width))
        self._tiles_columns = 0
        canvas.bind("<MouseWheel>", self._tiles_scroll)
        holder.bind("<MouseWheel>", self._tiles_scroll)
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
            messagebox.showerror(WINDOW_TITLE, t("export_fail", error=error))
            return
        listing = "\n".join(str(path) for path in paths)
        messagebox.showinfo(WINDOW_TITLE, t("export_ok", listing=listing))
        self._append_log(t("export_ok", listing=listing).replace("\n", " "), "ok")

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
            text=t("tiles_off")
            if tiles and not self._twitch.settings.drop_images else ""
        )

    def _build_settings_tab(self, tabs: ctk.CTkTabview) -> None:
        settings = self._twitch.settings
        p = self.palette
        tab = tabs.add(t("tab_settings"))
        # ⚠️ Прокрутка обов'язкова: на зменшеному вікні (а програма живе в
        # треї й часто відкривається невеликою) нижні пункти — тема, «не
        # братися за безнадійне», Telegram, «Про програму» — просто
        # обрізались, і дістатись до них було нічим.
        body = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=(0, PAD))
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)

        prio_box = self._block(left, t("priority"), grow=True)
        # Списки лишаються `tk.Listbox`: рівноцінного віджета в CustomTkinter
        # немає, а фарбує його `_restyle_widgets` поіменно.
        self.prio_list = tk.Listbox(prio_box, bg=p["alt"], fg=p["fg"],
                                    relief="flat", highlightthickness=0,
                                    activestyle="none",
                                    selectbackground=p["accent"],
                                    selectforeground="#ffffff")
        self.prio_list.pack(fill="both", expand=True)
        for game in settings.priority:
            self.prio_list.insert("end", game)
        self.prio_entry = self._list_row(prio_box, self._priority_add,
                                         self._priority_remove)

        # Спостереження — окремо від пріоритету: пріоритет міняє, що фармити
        # зараз, а це лише новини про нові кампанії. Можна хотіти знати про
        # Rocket League, не перериваючи фарм WoT.
        watch_box = self._block(left, t("watch_games"), top=PAD, grow=True)
        self._hint(watch_box, t("watch_hint"), colour="accent", wrap=240).pack(
            anchor="w", fill="x")
        self.watch_list = tk.Listbox(watch_box, height=4, bg=p["alt"], fg=p["fg"],
                                     relief="flat", highlightthickness=0,
                                     activestyle="none",
                                     selectbackground=p["accent"],
                                     selectforeground="#ffffff")
        self.watch_list.pack(fill="both", expand=True, pady=(6, 0))
        for game in settings.watch_games:
            self.watch_list.insert("end", game)
        self.watch_entry = self._list_row(watch_box, self._watch_add,
                                          self._watch_remove)

        mode_box = self._block(right, t("farm_mode"))
        self.mode_var = tk.StringVar(value=settings.farm_mode.name)
        for mode, label in (
            (PriorityMode.LINKED_ONLY, t("mode_linked")),
            (PriorityMode.SOONEST_END, t("mode_soonest")),
            (PriorityMode.TIGHTEST_FIT, t("mode_tightest")),
            (PriorityMode.PRIORITY_LIST, t("mode_priority")),
        ):
            self._paint(
                ctk.CTkRadioButton(
                    mode_box, text=label, value=mode.name, variable=self.mode_var,
                    command=self._mode_changed, radiobutton_width=18,
                    radiobutton_height=18, font=("Segoe UI", 12),
                ),
                fg_color="accent", hover_color="accent", border_color="line",
                text_color="fg",
            ).pack(anchor="w", pady=3)

        lang_box = self._block(right, t("language"), top=PAD)
        self._lang_codes = ["auto", *LANGS]
        labels = [t("language_auto"), *[NAMES[code] for code in LANGS]]
        stored = settings.language or "uk"
        self.lang_var = tk.StringVar(
            value=t("language_auto") if stored == "auto"
            else NAMES.get(stored, NAMES["uk"])
        )
        # `CTkOptionMenu` сам віддає обрану назву в `command` — окремої
        # прив'язки до події, як у `ttk.Combobox`, тут не треба.
        self._paint(
            ctk.CTkOptionMenu(
                lang_box, values=labels, variable=self.lang_var,
                command=self._language_changed, corner_radius=8, height=30,
                font=("Segoe UI", 12),
            ),
            fg_color="alt", button_color="accent", button_hover_color="hover",
            text_color="fg", dropdown_fg_color="card", dropdown_text_color="fg",
            dropdown_hover_color="hover",
        ).pack(fill="x")

        misc = self._block(right, t("other"), top=PAD)
        self.badges_var = tk.BooleanVar(value=settings.farm_cosmetics)
        self._switch(misc, t("farm_cosmetics"), self.badges_var,
                     self._misc_changed).pack(anchor="w", pady=3)
        self.autostart_var = tk.BooleanVar(value=settings.start_in_tray)
        self._switch(misc, t("start_tray"), self.autostart_var,
                     self._misc_changed).pack(anchor="w", pady=3)
        # Стан читаємо з реєстру, а не з налаштувань: запис могли зняти ззовні —
        # диспетчером завдань, чистилкою автозавантаження чи іншою збіркою.
        self.boot_var = tk.BooleanVar(value=autostart.is_enabled())
        self._switch(misc, t("autostart"), self.boot_var,
                     self._autostart_changed).pack(anchor="w", pady=3)
        self.images_var = tk.BooleanVar(value=settings.drop_images)
        self._switch(misc, t("drop_images"), self.images_var,
                     self._misc_changed).pack(anchor="w", pady=3)
        self.updates_var = tk.BooleanVar(value=settings.check_updates)
        self._switch(misc, t("check_updates"), self.updates_var,
                     self._misc_changed).pack(anchor="w", pady=3)
        size_row = ctk.CTkFrame(misc, fg_color="transparent")
        size_row.pack(fill="x", pady=(8, 0))
        self._hint(size_row, t("image_size_label"), colour="fg", wrap=0).pack(
            side="left")
        self.size_var = tk.IntVar(value=self._image_size)
        self.size_label = self._hint(size_row, f"{self._image_size} px", wrap=0)
        self.size_label.pack(side="right")
        self._paint(
            ctk.CTkSlider(
                size_row, from_=MIN_IMAGE_SIZE, to=MAX_IMAGE_SIZE,
                variable=self.size_var, command=self._image_size_changed,
                height=16, button_length=10,
            ),
            fg_color="line", progress_color="accent", button_color="accent",
            button_hover_color="fg",
        ).pack(side="left", fill="x", expand=True, padx=8)
        self._hint(misc, t("image_cache_hint")).pack(anchor="w", fill="x",
                                                     pady=(4, 4))
        self.dark_var = tk.BooleanVar(value=settings.dark_theme)
        self._switch(misc, t("dark_theme"), self.dark_var,
                     self._misc_changed).pack(anchor="w", pady=3)
        # Перелив — окремим тумблером, а не заміною: колір за станом несе зміст
        # (іде / стоїть / не зараховується), і хто цим користується, той не має
        # втратити його заради краси.
        # Журнал: вести чи ні, куди складати, і кнопка «відкрити теку».
        # Без нього скарга «щось не працює» не має жодного сліду.
        self.keeplog_var = tk.BooleanVar(value=settings.keep_log)
        self._switch(misc, t("keep_log"), self.keeplog_var,
                     self._misc_changed).pack(anchor="w", pady=3)
        self.logdir_var = tk.StringVar(value=settings.log_dir)
        log_row = ctk.CTkFrame(misc, fg_color="transparent")
        log_row.pack(fill="x", pady=(2, 0))
        self._entry(log_row, self.logdir_var,
                    placeholder=str(documents_dir())).pack(
            side="left", fill="x", expand=True)
        self._button(log_row, t("log_open"), self._open_log_dir,
                     width=110).pack(side="left", padx=(8, 0))
        self._hint(misc, t("log_hint")).pack(anchor="w", fill="x", pady=(2, 6))

        # Тема: набір зі списку плюс кнопка «зберегти у файл». Правити JSON
        # руками більшість не буде — а обрати зі списку може кожен.
        self.preset_var = tk.StringVar(value=t(f"theme_{settings.theme_preset or 'builtin'}"))
        ctk.CTkOptionMenu(
            misc, variable=self.preset_var, command=self._preset_changed,
            values=[t(f"theme_{name or 'builtin'}") for name in PRESETS],
            fg_color=self.cards["card"], button_color=p["accent"],
            button_hover_color=p["accent"], text_color=p["fg"],
        ).pack(anchor="w", pady=(6, 2))
        self._button(misc, t("theme_export"), self._export_theme).pack(
            anchor="w", pady=(0, 6))

        self.hopeless_var = tk.BooleanVar(value=settings.skip_hopeless)
        self._switch(misc, t("skip_hopeless"), self.hopeless_var,
                     self._misc_changed).pack(anchor="w", pady=3)
        self._hint(misc, t("skip_hopeless_hint")).pack(anchor="w", fill="x",
                                                       pady=(0, 4))
        self.rainbow_var = tk.BooleanVar(
            value=settings.progress_style == "rainbow")
        self._switch(misc, t("progress_rainbow"), self.rainbow_var,
                     self._misc_changed).pack(anchor="w", pady=3)

        tg = self._block(right, t("telegram"), top=PAD)
        self.tg_var = tk.BooleanVar(value=settings.telegram["enabled"])
        self._switch(tg, t("telegram_on"), self.tg_var,
                     self._telegram_changed).pack(anchor="w", pady=3)
        self._button(tg, t("connect_bot"), self._open_telegram_setup).pack(
            anchor="w", pady=(8, 0))
        self.tg_hint = self._hint(tg, self._telegram_hint())
        self.tg_hint.pack(anchor="w", fill="x", pady=(6, 0))

        about = self._block(right, t("about_title"), top=PAD)
        # Окремою кнопкою, а не текстом у картці: у вузькій колонці опис
        # обрізався на півслові й читати його було нічим.
        self._button(about, t("about_open"), self._open_about).pack(
            anchor="w", pady=(2, 0))

    # ------------------------------------------------------------ дії користувача

    def _send(self, kind: CommandType, argument: str = "") -> None:
        self._twitch.control.send(Command(kind, argument))

    def _toggle_pause(self) -> None:
        if self._twitch._paused:
            self._send(CommandType.RESUME)
            self.pause_btn.configure(text=t("pause"))
            self._set_farm_state("going" if self._watching_name else "idle")
        else:
            self._send(CommandType.PAUSE)
            self.pause_btn.configure(text=t("resume"))
            self._set_farm_state("paused")

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

    def _watch_add(self) -> None:
        game = self.watch_entry.get().strip()
        if game and game not in self.watch_list.get(0, "end"):
            self.watch_list.insert("end", game)
            self.watch_entry.delete(0, "end")
            self._save_watchlist()

    def _watch_remove(self) -> None:
        selection = self.watch_list.curselection()
        if selection:
            self.watch_list.delete(selection[0])
            self._save_watchlist()

    def _save_watchlist(self) -> None:
        """Пишемо напряму, без черги команд: це налаштування, не дія ядра.

        Ядро читає список аж наступного разу, коли перечитує інвентар, — і це
        правильно: додавши гру, людина хоче знати про майбутні кампанії, а не
        отримати негайний залп про все, що вже є.
        """
        settings = self._twitch.settings
        settings.watch_games = list(self.watch_list.get(0, "end"))
        settings.touch()
        settings.save()

    def _language_changed(self, _chosen: object = None) -> None:
        label = self.lang_var.get()
        if label == t("language_auto"):
            code = "auto"
        else:
            code = next((item for item, name in NAMES.items() if name == label), "uk")
        self._twitch.settings.language = code
        self._twitch.settings.save()
        messagebox.showinfo(WINDOW_TITLE, t("language_restart"))

    def _open_log_dir(self) -> None:
        """Відкриває теку журналу в провіднику — і створює її, якщо треба.

        Кнопка потрібніша за поле вводу: людина, яка прийшла по журнал, хоче
        його побачити, а не дізнатись шлях.
        """
        folder = log_file(self._twitch.settings.log_dir).parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
            webbrowser.open(folder.as_uri())
        except OSError as error:
            messagebox.showwarning(
                WINDOW_TITLE, t("log_open_failed", error=str(error)))

    def _open_about(self) -> None:
        """Окреме вікно «Про програму»: опис, посилання, версія, автор."""
        p, c = self.palette, self.cards
        window = ctk.CTkToplevel(self.root, fg_color=c["page"])
        window.title(t("about_title"))
        window.geometry("520x360")
        window.resizable(False, False)
        window.transient(self.root)
        # ⚠️ Без цього вікно з'являється ЗА головним: CustomTkinter створює
        # Toplevel і піднімає його не одразу, тож `lift` мусить пройти після
        # того, як Tk намалює вікно.
        window.after(200, lambda: window.lift())

        body = ctk.CTkFrame(window, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=PAD * 2, pady=PAD * 2)
        ctk.CTkLabel(body, text=WINDOW_TITLE, anchor="w",
                     font=("Segoe UI", 18, "bold"),
                     text_color=p["fg"]).pack(anchor="w")
        ctk.CTkLabel(body, text=t("about_text"), anchor="w", justify="left",
                     wraplength=440, text_color=p["muted"]).pack(
            anchor="w", fill="x", pady=(8, 0))

        links = ctk.CTkFrame(body, fg_color="transparent")
        links.pack(anchor="w", fill="x", pady=(PAD, 0))
        # Вікі першою: там пояснено, як усе працює, і саме туди має піти
        # людина, а не в код.
        for label, url in (
            (t("about_wiki"), f"https://github.com/{GITHUB_REPO}/wiki"),
            (t("about_repo"), f"https://github.com/{GITHUB_REPO}"),
            (t("about_releases"), f"https://github.com/{GITHUB_REPO}/releases"),
        ):
            self._button(links, label, lambda link=url: webbrowser.open(link),
                         width=140).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(body, text=t("about_author", version=__version__), anchor="w",
                     text_color=p["muted"]).pack(anchor="w", fill="x", pady=(PAD, 0))
        ctk.CTkLabel(body, text=t("about_thanks"), anchor="w", justify="left",
                     wraplength=440, text_color=p["muted"]).pack(
            anchor="w", fill="x", pady=(6, 0))
        self._button(body, t("close"), window.destroy).pack(anchor="e", pady=(PAD, 0))

    def _preset_changed(self, _chosen: object = None) -> None:
        """Обраний набір кольорів. Застосовується одразу, без перезапуску."""
        label = self.preset_var.get()
        name = next(
            (key for key in PRESETS if t(f"theme_{key or 'builtin'}") == label), "",
        )
        self._twitch.settings.theme_preset = name
        self._twitch.settings.save()
        self._load_palette(self._twitch.settings.dark_theme)
        self._apply_theme()
        self._apply_progress_style()

    def _export_theme(self) -> None:
        """Зберігає поточні кольори у theme.json — щоб було з чого починати."""
        if export_theme(THEME_FILE, self.palette, self.cards):
            messagebox.showinfo(WINDOW_TITLE, t("theme_saved", path=str(THEME_FILE)))
        else:
            messagebox.showwarning(WINDOW_TITLE, t("theme_save_failed"))

    def _mode_changed(self) -> None:
        self._twitch.settings.farm_mode = PriorityMode[self.mode_var.get()]
        # ⚠️ Зберігаємо одразу. Раніше запис на диск траплявся сам собою: RELOAD
        # веде ядро в LOAD_INVENTORY, а там уже є `save()`. Але на паузі та
        # стадія відводиться в IDLE, і збереження не стається взагалі — вибір
        # доживав до диска тільки при штатному виході. Рядок дешевий, а
        # поведінка перестає залежати від чужого ланцюжка.
        self._twitch.settings.save()
        self._send(CommandType.RELOAD)

    def _autostart_changed(self) -> None:
        """Показуємо те, що вийшло насправді, а не те, що просили.

        Запис у реєстр може не вдатись — політика, антивірус, обмежений
        профіль. Галочка, яка стоїть, коли автозапуску немає, гірша за
        відсутність галочки взагалі.
        """
        got = autostart.apply(self.boot_var.get())
        self.boot_var.set(got)
        # Намір зберігаємо окремо від реєстру: якщо запис колись зникне,
        # програма має знати, що його ставили, і повернути на місце.
        self._twitch.settings.autostart = got
        self._append_log(
            t("boot_on") if got else t("boot_off"),
            "ok" if got == self.boot_var.get() else "warn",
        )

    def _image_size_changed(self, _value: object = None) -> None:
        """Новий розмір застосовується одразу, без перезапуску й без мережі.

        Значення беремо зі змінної, а не з аргументу: `CTkSlider` подає туди
        дробове число, а розмір картинки цілий — і в `size_var` воно вже
        округлене.
        """
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
        settings.progress_style = "rainbow" if self.rainbow_var.get() else "state"
        settings.skip_hopeless = self.hopeless_var.get()
        settings.keep_log = self.keeplog_var.get()
        settings.log_dir = self.logdir_var.get().strip()
        settings.save()
        # CustomTkinter тримає власне поняття теми, і без цього рядка його
        # віджети лишались би світлими в темному вікні (й навпаки)
        ctk.set_appearance_mode("dark" if settings.dark_theme else "light")
        self._load_palette(settings.dark_theme)
        self._apply_theme()
        self._apply_progress_style()
        if settings.drop_images != images_were and settings.drop_images:
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
            self.drop_var.set(t("drop_unknown"))
            self._set_progress(0)
            return
        # найближчий до завершення — першим: саме він заклеймиться раніше
        fresh.sort(key=lambda row: (row[3] - row[2]) if row[3] else 1 << 30)
        lines = [
            t("growing_line", name=name, game=game, have=have, need=need)
            for name, game, have, need in fresh[:self.GROWING_LINES]
        ]
        if len(fresh) > self.GROWING_LINES:
            lines.append(t("growing_more", n=len(fresh) - self.GROWING_LINES))
        self.drop_var.set("\n".join(lines))
        head = fresh[0]
        self._set_progress(
            min(100, head[2] / head[3] * 100) if head[3] > 0 else 0
        )

    def _reload_now(self) -> None:
        """Ручне «спитати Twitch просто зараз».

        Команда лягає в чергу й виконається на початку наступної ітерації —
        і поки цикл сидить у довгій стадії, кнопка на вигляд не робить нічого.
        Тому пишемо в журнал одразу: натиснуто, чекаємо. Мовчазна кнопка
        змушує тиснути її ще раз, а другий RELOAD нічого не пришвидшує.
        """
        self._append_log(t("reload_log"))
        self._send(CommandType.RELOAD)

    def _telegram_changed(self) -> None:
        self._twitch.settings.telegram["enabled"] = self.tg_var.get()
        self._twitch.settings.touch()
        # CTkLabel — не ttk.Label: у нього немає доступу за ключем, лише
        # `configure`, і синтаксис `hint["text"] = …` тут падає в рантаймі
        self.tg_hint.configure(text=self._telegram_hint())

    def _telegram_hint(self) -> str:
        """Одним рядком: чи бот узагалі готовий працювати.

        Галочка «Увімкнено» сама по собі нічого не варта — без токена й чату
        бот мовчить, і раніше про це не було сказано ніде, крім журналу.
        """
        telegram = self._twitch.settings.telegram
        if not telegram["bot_token"]:
            return t("tg_hint_none")
        if not telegram["chat_ids"]:
            return t("tg_hint_nochat")
        return t("tg_hint_ok")

    def _open_telegram_setup(self) -> None:
        from gui.telegram_setup import TelegramSetup

        window = TelegramSetup(self.root, self._twitch.settings,
                               self.palette, self.cards)
        # підказка й галочка мають наздогнати те, що майстер зберіг
        window.bind("<Destroy>", lambda _event: self._telegram_saved(), add=True)

    def _telegram_saved(self) -> None:
        try:
            telegram = self._twitch.settings.telegram
            self.tg_var.set(telegram["enabled"])
            self.tg_hint.configure(text=self._telegram_hint())
        except tk.TclError:
            pass  # вікно вже закривають

    # ------------------------------------------------------------ події ядра

    def _on_event(self, event: Event) -> None:
        try:
            self._render(event)
        except tk.TclError:
            pass  # вікно вже знищене

    def _render(self, event: Event) -> None:
        """Показує подію у вікні. Маршрути — нижче, по одному методу на подію.

        Раніше тут стояла драбина з дев'ятнадцяти `elif isinstance`, і такі
        самі драбини були в журналі та в Telegram. Нову подію доводилось
        вписувати в три місця; забути одне не заважало нічому, крім людини,
        яка так і не бачила того, про що програма хотіла сказати.
        """
        SHOW.dispatch(event, owner=self)

    @SHOW.on(WindowVisibility)
    def _show_visibility(self, event: WindowVisibility) -> None:
        # Ховати нема куди, поки трей не піднявся: вікно зникло б, а
        # повернути його не було б чим.
        if event.visible:
            self.show_window()
        elif self._tray_available:
            self.hide_to_tray()

    @SHOW.on(StatusChanged)
    def _show_status(self, event: StatusChanged) -> None:
        self.status_var.set(event.text)
        self._farm_from_status(event.text)

    @SHOW.on(LogLine)
    def _show_line(self, event: LogLine) -> None:
        self._append_log(event.text)

    @SHOW.on(LoginRequired)
    def _show_login(self, event: LoginRequired) -> None:
        self._append_log(t("login_needed", code=event.user_code), "warn")

    @SHOW.on(LoggedIn)
    def _show_logged_in(self, event: LoggedIn) -> None:
        self._append_log(t("logged_in", user_id=event.user_id), "ok")

    @SHOW.on(WatchingChanged)
    def _show_watching(self, event: WatchingChanged) -> None:
        if event.channel is None:
            self.channel_var.set("—")
            self.title_var.set("")
            self._growing.clear()
            self.drop_var.set(t("drop_unknown"))
            self._set_progress(0)
            self._set_farm_state("idle")
            return
        if event.channel.name != self._watching_name:
            # інший канал — інші дропи; старі рядки більше не про це
            self._growing.clear()
            self._watching_name = event.channel.name
        self.channel_var.set(
            f"{event.channel.name}  ·  {event.channel.game or t('no_game')}"
        )
        # Категорія «Special Events» не каже, у що грають, — гра названа
        # в заголовку трансляції. Ріжемо довгий: у турнірних заголовках
        # після назви йде перелік команд і хештеги.
        title = " ".join(event.channel.stream_title.split())
        self.title_var.set(title if len(title) <= 90 else title[:87] + "…")
        if not self._twitch._paused:
            self._set_farm_state("going")

    @SHOW.on(DropProgress)
    def _show_progress(self, event: DropProgress) -> None:
        # Кампанія попереду гри: «EWC 2026» каже, за що дроп, а «Special
        # Events» — лише те, що це подієва категорія Twitch.
        where = event.campaign or event.game
        if event.campaign and event.campaign != event.game:
            where = f"{event.campaign} · {event.game}"
        self._growing[event.drop_name] = (
            monotonic(), where,
            event.current_minutes, event.required_minutes,
        )
        self._render_growing()
        self._set_farm_state("going")

    @SHOW.on(DropClaimed)
    def _show_claimed(self, event: DropClaimed) -> None:
        self._append_log(
            t("claimed_log", rewards=event.rewards, game=event.game), "ok")
        party = getattr(self, "confetti", None)
        if party is not None:
            party.burst()

    @SHOW.on(ProtocolStale)
    def _show_protocol(self, event: ProtocolStale) -> None:
        if event.storm:
            self._append_log(t("protocol_storm_log"), "warn")
        else:
            self._append_log(
                t("protocol_stale_log", names=", ".join(event.operations)), "err",
            )

    @SHOW.on(CampaignAppeared)
    def _show_new_campaigns(self, event: CampaignAppeared) -> None:
        for item in event.campaigns:
            self._append_log(
                t("new_campaign_log",
                  name=item.name.strip(), game=item.game,
                  drops=item.total_drops,
                  unit=plural(item.total_drops,
                              t("tg_drop_one"), t("tg_drop_few"), t("tg_drop_many"))),
                "ok",
            )

    @SHOW.on(UpdateAvailable)
    def _show_update(self, event: UpdateAvailable) -> None:
        if event.files == 0:
            self._append_log(t("update_hashes", version=event.version), "ok")
            return
        what = plural(event.files, t("file_one"), t("file_few"), t("file_many"))
        size = human_size(event.bytes_to_fetch)
        self._append_log(
            t("update_ready_log", version=event.version, files=event.files,
              unit=what, size=size), "ok",
        )
        self._pending_update = t(
            "update_ask", version=event.version, files=event.files,
            unit=what, size=size)
        self._maybe_ask_update()

    @SHOW.on(UpdateFailed)
    def _show_update_failed(self, event: UpdateFailed) -> None:
        self._append_log(t("update_fail_log", reason=event.reason), "err")

    @SHOW.on(ProgressStalled)
    def _show_stalled(self, event: ProgressStalled) -> None:
        why = (
            t("stall_else", name=event.counted_elsewhere)
            if event.counted_elsewhere
            else t("stall_manual")
        )
        self._append_log(
            t("stall_log", minutes=event.minutes_without_progress,
              channel=event.channel_name, why=why), "err"
        )
        self._set_farm_state("stalled")

    @SHOW.on(WatchUncounted)
    def _show_uncounted(self, event: WatchUncounted) -> None:
        self._append_log(t("uncounted_log", channel=event.channel_name), "err")
        self._set_farm_state("uncounted")

    @SHOW.on(ConnectionLost)
    def _show_conn_lost(self, event: ConnectionLost) -> None:
        self.conn_var.set(t("conn_lost_badge"))
        self._append_log(t("conn_lost_log", reason=event.reason), "err")

    @SHOW.on(ConnectionRestored)
    def _show_conn_ok(self, event: ConnectionRestored) -> None:
        self.conn_var.set("")
        self._append_log(
            t("conn_ok_log", seconds=round(event.downtime_seconds)), "ok"
        )

    @SHOW.on(WebsocketStatus)
    def _show_websockets(self, event: WebsocketStatus) -> None:
        self._render_websockets(event)

    @SHOW.on(ChannelsUpdated)
    def _show_channels(self, event: ChannelsUpdated) -> None:
        self._render_channels(event)

    @SHOW.on(InventoryUpdated)
    def _show_inventory(self, event: InventoryUpdated) -> None:
        self._render_inventory(event)

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
            text=t(key), fg=self.palette[colour], bg=self.palette["alt"],
        )
        bar = getattr(self, "progress", None)
        # Поки смуга переливається, стан кольором не показуємо: інакше два
        # правила фарбували б її по черзі й вона мигтіла б.
        if (bar is not None and hasattr(bar, "configure")
                and self._rainbow_job is None):
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
        elif text in (t("status_waiting"), t("status_searching"), t("status_picking"),
                      t("status_inventory"), t("status_details")):
            if self._farm_state in ("stalled", "uncounted"):
                return
            self._set_farm_state("idle")

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
            state = t("ws_all", n=total)
        else:
            state = t("ws_some", ok=connected, total=total)
        self.conn_var.set(t("ws_line", state=state, topics=topics))

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
        # права колонка живиться тим самим зрізом — оновлюємо її завжди,
        # незалежно від того, який вигляд обрано в «Інвентарі»
        self._render_side()
        if self._inventory_view == "tiles":
            self._render_tiles(event)
            return
        self.inv_tree.delete(*self.inv_tree.get_children())
        now = datetime.now(timezone.utc)
        for campaign in event.campaigns:
            if campaign.expired:
                state = t("inv_past")
            elif campaign.upcoming:
                state = t("inv_soon")
            elif campaign.claimed_drops >= campaign.total_drops:
                state = t("inv_done")
            else:
                hours = max(0, int((campaign.ends_at - now).total_seconds() // 3600))
                state = t("inv_hours", hours=hours)
            parent = self.inv_tree.insert(
                "", "end", text=f"{campaign.game} — {campaign.name}",
                values=(f"{campaign.claimed_drops}/{campaign.total_drops}", state),
                open=False, image=self._thumbnail(campaign.image),
            )
            for drop in campaign.drops:
                self.inv_tree.insert(
                    parent, "end", text=drop.name,
                    values=(
                        t("inv_min", have=drop.current_minutes,
                          need=drop.required_minutes),
                        t("inv_claimed") if drop.claimed else "",
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
        c, p = self.cards, self.palette
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
                card = self._tile(
                    picture=self._thumbnail(drop.image or campaign.image, TILE_SIZE),
                    name=_shorten(drop.name),
                    minutes=t("inv_min", have=drop.current_minutes,
                              need=drop.required_minutes),
                    game=_shorten(campaign.game, 22),
                )
                card.grid(row=shown // columns, column=shown % columns,
                          sticky="n", padx=4, pady=4)
                shown += 1
        if not shown:
            tk.Label(self.tiles_holder, text=t("tiles_empty"), bg=c["page"],
                     fg=p["muted"], font=("Segoe UI", 10)).pack(pady=20)
        self.tiles_canvas.yview_moveto(0)

    # Поле навколо вмісту картки. Менше — і текст притискається до межі,
    # більше — у рядок влазить менше карток.
    TILE_PAD = 10

    def _tile(self, *, picture: Any, name: str, minutes: str,
              game: str) -> tk.Canvas:
        """Одна картка інвентаря: заокруглена підкладка й вміст на ній.

        Уся картка — одне полотно, а не фрейм із чотирма мітками. Так виходить
        і заокруглення, якого Tk не дає жодному віджету, і вчетверо менше
        віджетів на сітку: на межі в 120 плиток це різниця між помітним
        підвисанням вікна й непомітним.

        Висота рахується по ходу малювання: назва нагороди переноситься на
        другий рядок, і наперед її висоту не знати. Тому спершу кладемо вміст,
        а підкладку домальовуємо в кінці й опускаємо під нього.
        """
        c, p = self.cards, self.palette
        pad = self.TILE_PAD
        width = TILE_SIZE + pad * 2
        canvas = tk.Canvas(self.tiles_holder, width=width, height=1,
                           bg=c["page"], highlightthickness=0, borderwidth=0)
        middle = width / 2
        y: float = pad
        if picture:
            canvas.create_image(middle, y, anchor="n", image=picture)
            # `thumbnail` зберігає пропорції, тож висота буває меншою за бік:
            # брати TILE_SIZE наосліп означало б порожню смугу під широкими
            # картинками
            y += picture.height() + 8
        for text, colour, font in (
            (name, p["fg"], ("Segoe UI", 10, "bold")),
            (minutes, p["accent"], ("Segoe UI", 9, "bold")),
            (game, p["muted"], ("Segoe UI", 9)),
        ):
            item = canvas.create_text(middle, y, text=text, width=TILE_SIZE,
                                      anchor="n", justify="center", fill=colour,
                                      font=font)
            y = canvas.bbox(item)[3] + 3
        height = int(y + pad)
        canvas.configure(height=height)
        # межа малюється по центру лінії, тож півпікселя з кожного боку
        # лишаємо, інакше вона зрізається краєм полотна
        background = canvas.create_polygon(
            rounded_points(1, 1, width - 1, height - 1, TILE_RADIUS),
            smooth=True, splinesteps=16, fill=c["card"], outline=c["line"],
            width=1,
        )
        canvas.tag_lower(background)
        canvas.bind("<MouseWheel>", self._tiles_scroll)
        # межа світлішає під курсором: єдиний спосіб показати, що плитка — це
        # предмет, а не намальований фон, бо натискати тут нема на що
        canvas.bind("<Enter>",
                    lambda _e: canvas.itemconfigure(background,
                                                    outline=p["accent"]))
        canvas.bind("<Leave>",
                    lambda _e: canvas.itemconfigure(background,
                                                    outline=c["line"]))
        return canvas

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
        except Exception as error:
            # Тут свідомо `debug`, а не `warning`: метод малює кожну плитку
            # інвентаря, і при зіпсованому кеші журнал засипало б сотнями
            # однакових рядків. Але й повного мовчання бути не має — з `-vvv`
            # видно і файл, і причину.
            logger.debug(f"Картинка {path.name} не відкрилась: "
                         f"{type(error).__name__}: {error}")
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
            detail = t("quit_detail", name=drop.name, have=drop.minutes,
                       need=drop.required_minutes, left=drop.minutes_left)
        if messagebox.askyesno(
            t("quit_miner"),
            t("quit_ask", detail=detail),
            icon="warning",
            default="no",
            parent=self.root,
        ):
            self.request_close()

    def _maybe_ask_update(self) -> None:
        """Питає про оновлення тоді, коли є кому відповідати.

        ⚠️ Раніше `askyesno` викликався прямо з обробника події. Перевірка
        оновлень іде на старті майнера, тому питання виринало ПЕРЕД тим, як
        з'явиться вікно: людина бачила голий діалог посеред екрана, ще не
        побачивши самої програми. А коли програма піднімалась одразу в трей,
        питання зависало поверх чужих вікон, і звідки воно — не зрозуміло.

        Тому подію запам'ятовуємо, а питаємо, коли вікно намальоване й видиме.
        Сховане в трей не чіпаємо: спитаємо, щойно його розгорнуть.
        """
        if self._pending_update is None or not self._ui_ready:
            return
        if self._asking_update:
            return
        try:
            if not self.root.winfo_viewable():
                return
        except tk.TclError:
            return
        question = self._pending_update
        self._pending_update = None
        self._asking_update = True
        try:
            # `parent` обов'язковий: без нього діалог — окреме вікно верхнього
            # рівня, яке Windows може підняти поперед програми й показати
            # окремою кнопкою на панелі задач.
            agreed = messagebox.askyesno(WINDOW_TITLE, question,
                                         parent=self.root)
        finally:
            self._asking_update = False
        if agreed:
            self._send(CommandType.APPLY_UPDATE)

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        # людина щойно розгорнула вікно — саме час спитати те, що чекало
        self._maybe_ask_update()

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
            # перший успішний `update()` означає, що вікно вже намальоване
            self._ui_ready = True
            self._maybe_ask_update()
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
