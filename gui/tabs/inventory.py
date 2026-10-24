"""Вкладка «Інвентар»: список та сітка карток кампаній і дропів."""
from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from core.config import TILE_SIZE
from core.events import InventoryUpdated
from core.i18n import t

if TYPE_CHECKING:
    from core.client import MinerClient as Twitch

logger = logging.getLogger("TwitchDrops")

# Скільки карток малюємо щонайбільше. Кожна — це кілька віджетів Tk, і на
# кількох сотнях перемальовування стає помітним для ока.
TILE_LIMIT = 120

# Наскільки заокруглені кути плитки. Той самий радіус, що в `_card`, — інакше
# картки інвентаря й картки «Майнінгу» виглядали б із різних вікон.
TILE_RADIUS = 12

# Поле навколо вмісту картки. Менше — і текст притискається до межі,
# більше — у рядок влазить менше карток.
TILE_PAD = 10


def _shorten(text: str, limit: int = 34) -> str:
    """Обрізає довгий підпис із «…» посередині, щоб було видно початок і кінець."""
    if len(text) <= limit:
        return text
    half = (limit - 1) // 2
    return f"{text[:half]}…{text[-half:]}"


def rounded_points(
    x1: float, y1: float, x2: float, y2: float, radius: float
) -> list[float]:
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


class InventoryTabMixin:
    """Міксин побудови та керування вкладкою «Інвентар»."""

    palette: dict[str, str]
    cards: dict[str, str]
    view_var: tk.StringVar
    tiles_note: ttk.Label
    inv_body: ttk.Frame
    inv_list: ttk.Frame
    inv_tree: ttk.Treeview
    inv_tiles: ctk.CTkFrame
    tiles_canvas: tk.Canvas
    tiles_holder: tk.Frame
    _tiles_columns: int
    _images: dict[tuple[str, int], Any]
    _last_inventory: InventoryUpdated | None
    _twitch: Twitch

    TILE_PAD = TILE_PAD

    def _paint(self, widget: Any, **options: str) -> Any:
        raise NotImplementedError

    def _table_scrollbar(self, parent: tk.Misc, table: ttk.Treeview) -> None:
        raise NotImplementedError

    def _render_side(self) -> None:
        raise NotImplementedError

    def _export_tables(self) -> None:
        raise NotImplementedError

    @property
    def _image_size(self) -> int:
        raise NotImplementedError

    def _build_inventory_tab(self, tabs: ctk.CTkTabview) -> None:
        tab = ttk.Frame(tabs.add(t("tab_inventory")), padding=10)
        tab.pack(fill="both", expand=True)

        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text=t("view")).pack(side="left")
        self.view_var = tk.StringVar(value=self._inventory_view)
        for value, label in (("list", t("view_list")), ("tiles", t("view_tiles"))):
            ttk.Radiobutton(
                bar,
                text=label,
                value=value,
                variable=self.view_var,
                command=self._view_changed,
            ).pack(side="left", padx=(8, 0))
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
        self.inv_tree = ttk.Treeview(
            tab,
            columns=("progress", "state"),
            show="tree headings",
            style="Table.Treeview",
        )
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
        wrap = self._paint(
            ctk.CTkFrame(parent, corner_radius=0),
            fg_color="page",
        )
        self.inv_tiles = wrap
        canvas = tk.Canvas(
            wrap,
            bg=c["page"],
            highlightthickness=0,
            borderwidth=0,
        )
        scroll = self._paint(
            ctk.CTkScrollbar(wrap, command=canvas.yview, width=12),
            button_color="line",
            button_hover_color="accent",
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
        if (
            self._columns_for(width) != self._tiles_columns
            and self._last_inventory is not None
            and self._inventory_view == "tiles"
        ):
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
            if tiles and not self._twitch.settings.drop_images
            else ""
        )

    def _render_inventory(self, event: InventoryUpdated) -> None:
        # Тут не об'єкти моделі, а знімки з події: у них власні імена полів,
        # і вони навмисно не змінюються разом із внутрішньою моделлю — інакше
        # кожне перейменування в ядрі ламало б інтерфейс.
        # Tk не тримає власних посилань на картинки: якщо їх не зберегти тут,
        # збирач сміття забере зображення, і рядки лишаться порожніми.
        # Замість повного скидання кешу зберігаємо раніше створені PhotoImage для
        # актуальних кампаній і чистимо лише застарілі URL, усуваючи повторний
        # ресайз сотень картинок під час кожного оновлення.
        active_urls = {c.image for c in event.campaigns if c.image}
        for c in event.campaigns:
            for d in c.drops:
                if d.image:
                    active_urls.add(d.image)
        for key in list(self._images):
            if key[0] not in active_urls:
                self._images.pop(key, None)
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
                "",
                "end",
                text=f"{campaign.game} — {campaign.name}",
                values=(f"{campaign.claimed_drops}/{campaign.total_drops}", state),
                open=False,
                image=self._thumbnail(campaign.image),
            )
            for drop in campaign.drops:
                self.inv_tree.insert(
                    parent,
                    "end",
                    text=drop.name,
                    values=(
                        t(
                            "inv_min",
                            have=drop.current_minutes,
                            need=drop.required_minutes,
                        ),
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
                    minutes=t(
                        "inv_min",
                        have=drop.current_minutes,
                        need=drop.required_minutes,
                    ),
                    game=_shorten(campaign.game, 22),
                )
                card.grid(
                    row=shown // columns,
                    column=shown % columns,
                    sticky="n",
                    padx=4,
                    pady=4,
                )
                shown += 1
        if not shown:
            tk.Label(
                self.tiles_holder,
                text=t("tiles_empty"),
                bg=c["page"],
                fg=p["muted"],
                font=("Segoe UI", 10),
            ).pack(pady=20)
        self.tiles_canvas.yview_moveto(0)

    def _tile(
        self,
        *,
        picture: Any,
        name: str,
        minutes: str,
        game: str,
    ) -> tk.Canvas:
        """Одна картка інвентаря: заокруглена підкладка й вміст на ній.

        Уся картка — одне полотно, а не фрейм із чотирма мітками. Так виходить
        і заокруглення, якого Tk не дає жодному віджету, і вчетверо менше
        віджетів на сітку: на межі в 120 плиток це різниця між помітним
        підвисанням вікна й непомітним.

        Висота рахується по ходу малювання: назва нагороди переноситься на
        другий рядок, і наперед її висоту не знати. Тому спершу кладемо вміст,
        а підкладку домальовуємо в кінці й опускаємо під нього.

        ⚠️ Примітка (Правило 2 з twitch-gui-task.md):
        Плитки інвентаря — це Canvas. Якщо колись знадобиться підняти картку над
        іншими віджетами Tk, слід викликати tk.Misc.lift(canvas), а НЕ
        canvas.lift(), оскільки у Canvas цей метод перевизначено як псевдонім
        tag_raise (підняття фігури на полотні), і виклик без аргументів кине
        tk.TclError.
        """
        c, p = self.cards, self.palette
        pad = self.TILE_PAD
        width = TILE_SIZE + pad * 2
        canvas = tk.Canvas(
            self.tiles_holder,
            width=width,
            height=1,
            bg=c["page"],
            highlightthickness=0,
            borderwidth=0,
        )
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
            item = canvas.create_text(
                middle,
                y,
                text=text,
                width=TILE_SIZE,
                anchor="n",
                justify="center",
                fill=colour,
                font=font,
            )
            y = canvas.bbox(item)[3] + 3
        height = int(y + pad)
        canvas.configure(height=height)
        # межа малюється по центру лінії, тож півпікселя з кожного боку
        # лишаємо, інакше вона зрізається краєм полотна
        background = canvas.create_polygon(
            rounded_points(1, 1, width - 1, height - 1, TILE_RADIUS),
            smooth=True,
            splinesteps=16,
            fill=c["card"],
            outline=c["line"],
            width=1,
        )
        canvas.tag_lower(background)
        canvas.bind("<MouseWheel>", self._tiles_scroll)
        # межа світлішає під курсором: єдиний спосіб показати, що плитка — це
        # предмет, а не намальований фон, бо натискати тут нема на що
        canvas.bind(
            "<Enter>",
            lambda _e: canvas.itemconfigure(background, outline=p["accent"]),
        )
        canvas.bind(
            "<Leave>",
            lambda _e: canvas.itemconfigure(background, outline=c["line"]),
        )
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
            logger.debug(
                f"Картинка {path.name} не відкрилась: "
                f"{type(error).__name__}: {error}"
            )
            return ""
        self._images[key] = photo
        return photo


# Прямі функції-обгортки
def _build_inventory_tab(gui: InventoryTabMixin, tabs: ctk.CTkTabview) -> None:
    gui._build_inventory_tab(tabs)


def _build_inventory_list(gui: InventoryTabMixin, parent: tk.Misc) -> None:
    gui._build_inventory_list(parent)


def _build_inventory_tiles(gui: InventoryTabMixin, parent: tk.Misc) -> None:
    gui._build_inventory_tiles(parent)


def _render_inventory(gui: InventoryTabMixin, event: InventoryUpdated) -> None:
    gui._render_inventory(event)


def _render_tiles(gui: InventoryTabMixin, event: InventoryUpdated) -> None:
    gui._render_tiles(event)


def _columns_for(width: int) -> int:
    return InventoryTabMixin._columns_for(width)
