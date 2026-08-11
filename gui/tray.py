"""Іконка в системному треї.

pystray крутить власний цикл у окремому потоці, тому будь-яка дія з меню
не викликає код ядра напряму, а перекидається в asyncio через
`call_soon_threadsafe` — інакше отримаємо гонитву з головним циклом.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw

from core.events import Command, CommandType

if TYPE_CHECKING:
    from core.miner import Miner as Twitch
    from gui.app import GUI

logger = logging.getLogger("TwitchDrops")

ICON_COLOURS = {
    "active": (145, 71, 255),
    "idle": (110, 110, 120),
    "error": (200, 60, 60),
}


def _make_icon(state: str) -> Image.Image:
    """Малюємо іконку кодом, щоб не тягнути .ico у збірку."""
    colour = ICON_COLOURS.get(state, ICON_COLOURS["idle"])
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, 58, 58), fill=colour)
    draw.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 230))
    return image


class Tray:
    def __init__(self, twitch: Twitch, gui: GUI):
        self._twitch = twitch
        self._gui = gui
        self._icon: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        try:
            import pystray
        except ImportError:
            logger.info("pystray недоступний — працюємо без трею")
            return
        self._loop = asyncio.get_running_loop()
        menu = pystray.Menu(
            pystray.MenuItem("Показати вікно", self._show, default=True),
            pystray.MenuItem("Сховати вікно", self._hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Призупинити", self._pause),
            pystray.MenuItem("Продовжити", self._resume),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Вийти", self._quit),
        )
        self._icon = pystray.Icon(
            "twitch_drop_farm", _make_icon("idle"), "Twitch Drop Farm", menu
        )
        # тепер вікну є куди ховатись, тож хрестик може згортати, а не закривати
        self._gui._tray_available = True
        # pystray блокує потік, тому віддаємо його виконавцю
        self._loop.run_in_executor(None, self._icon.run)

    def _dispatch(self, func: Any) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(func)

    def _show(self, *_: Any) -> None:
        self._dispatch(self._gui.show_window)

    def _hide(self, *_: Any) -> None:
        self._dispatch(self._gui.hide_to_tray)

    def _pause(self, *_: Any) -> None:
        self._dispatch(lambda: self._twitch.control.send(Command(CommandType.PAUSE)))

    def _resume(self, *_: Any) -> None:
        self._dispatch(lambda: self._twitch.control.send(Command(CommandType.RESUME)))

    def _quit(self, *_: Any) -> None:
        self._dispatch(self._gui.request_close)
        self.stop()

    def set_state(self, state: str) -> None:
        if self._icon is not None:
            try:
                self._icon.icon = _make_icon(state)
            except Exception:
                pass

    def notify(self, message: str, title: str = "Twitch Drop Farm") -> None:
        if self._icon is not None and self._twitch.settings.tray_notifications:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass  # не всі середовища підтримують сповіщення

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
