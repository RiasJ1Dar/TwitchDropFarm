"""Права бічна колонка: що заберемо найближчим часом, що згорить, та динаміка росту дропів."""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone
from time import monotonic
from typing import TYPE_CHECKING

import customtkinter as ctk

from core.i18n import t

if TYPE_CHECKING:
    from core.events import InventoryUpdated

# Скільки місця лишати навколо картки. Винесено в константу, бо ті самі відступи
# повторюються в кожному блоці вкладки, і різнобій одразу видно оком.
PAD = 12

# Ширина правої колонки на вкладці «Майнінг». Вужче — назви дропів не влазять,
# ширше — з'їдає місце в журналу без користі.
SIDE_WIDTH = 330

# Скільки рядків показуємо в «Ось-ось заберемо». Більше не має сенсу: це не
# інвентар, а підказка «що трапиться найближчим часом».
SOON_LIMIT = 8

# Скільки тримаємо дроп у списку «зараз фармимо» без нового прогресу.
# Хвилина — крок підтвердження, тож три дає запас на повтори, але не
# настільки великий, щоб у списку висіли кампанії з минулого каналу.
GROWING_WINDOW = 3 * 60
GROWING_LINES = 4


def _shorten(text: str, limit: int = 34) -> str:
    """Обрізає довгий підпис із «…» посередині, щоб було видно початок і кінець."""
    if len(text) <= limit:
        return text
    half = (limit - 1) // 2
    return f"{text[:half]}…{text[-half:]}"


class SideColumnMixin:
    """Міксин правої бічної колонки та динаміки росту дропів."""

    GROWING_WINDOW: int = GROWING_WINDOW
    GROWING_LINES: int = GROWING_LINES
    SIDE_WIDTH: int = SIDE_WIDTH
    SOON_LIMIT: int = SOON_LIMIT

    palette: dict[str, str]
    cards: dict[str, str]
    _last_inventory: InventoryUpdated | None
    _growing: dict[str, tuple[float, str, int, int]]
    drop_var: tk.StringVar
    soon_rows: ctk.CTkFrame
    ending_rows: ctk.CTkFrame

    def _card(self, parent: tk.Misc, title: str) -> ctk.CTkFrame:
        raise NotImplementedError

    def _set_progress(self, percent: float) -> None:
        raise NotImplementedError

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
        # ⚠️ `c.linked` тут обов'язковий. Без нього картка показувала кампанії,
        # до яких не прив'язаний ігровий акаунт, — програма їх не фармить і
        # ніколи не візьме, а список читався як «ось що ми робимо». 06.09 туди
        # потрапили Albion Online, Black Desert, Out of the Park Baseball, і
        # людина слушно спитала, чому фармиться те, до чого вона не підключена.
        alive = sorted(
            (c for c in campaigns
             if c.active and c.linked and c.claimed_drops < c.total_drops
             and c.ends_at > now),
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
