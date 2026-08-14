"""Іконка в системному треї.

pystray крутить власний цикл у окремому потоці, тому будь-яка дія з меню
не викликає код ядра напряму, а перекидається в asyncio через
`call_soon_threadsafe` — інакше отримаємо гонитву з головним циклом.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from core.events import (
    CampaignFinished,
    Command,
    CommandType,
    ConnectionLost,
    ConnectionRestored,
    DeadlineRisk,
    DropClaimed,
    Event,
    LoginRequired,
    MinerError,
    MinerStarted,
    MinerStopped,
    ProgressStalled,
    WatchingChanged,
    WatchUncounted,
)
from gui.icon import make_icon

if TYPE_CHECKING:
    from PIL import Image

    from core.miner import Miner as Twitch
    from gui.app import GUI

logger = logging.getLogger("TwitchDrops")

def _make_icon(state: str) -> Image.Image:
    """Той самий значок, що у вікна й у `.exe`, лише з кольором стану.

    Раніше тут малювалось власне коло — і трей був єдиним місцем, де програма
    хоч якось себе позначала. Тепер малюнок один на всіх, у `gui/icon.py`.
    """
    return make_icon(64, state)


class Tray:
    def __init__(self, twitch: Twitch, gui: GUI):
        self._twitch = twitch
        self._gui = gui
        self._icon: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._state = "idle"

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
        # Без цієї підписки колір іконки й сповіщення лишаються мертвим кодом:
        # `set_state` і `notify` не викликав ніхто, тож іконка назавжди сіра,
        # а `tray_notifications` у налаштуваннях нічим не керує.
        self._twitch.events.subscribe(self._on_event)

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

    def _on_event(self, event: Event) -> None:
        """Перекладає події ядра в колір іконки та спливні сповіщення.

        Сповіщаємо лише про те, заради чого варто відривати людину від справ:
        нагороду, завершену кампанію, застій, помилку й потребу увійти.
        Рутина (зміна каналу, приріст хвилин) міняє хіба що колір.
        """
        if isinstance(event, WatchingChanged):
            self.set_state("idle" if event.channel is None else "active")
        elif isinstance(event, ConnectionLost):
            self.set_state("error")
        elif isinstance(event, ConnectionRestored):
            self.set_state("active")
        elif isinstance(event, MinerStarted):
            # при старті одразу в трей вікна не видно, і без цього незрозуміло,
            # чи програма взагалі піднялась
            if event.tray:
                self.notify(f"Працюю у фоні, версія {event.version}")
        elif isinstance(event, MinerStopped):
            self.set_state("idle")
            self.notify(f"Майнер зупинено: {event.reason}")
        elif isinstance(event, DropClaimed):
            self.notify(f"{event.rewards} — {event.game}", "Отримано дроп")
        elif isinstance(event, CampaignFinished):
            self.notify(f"{event.campaign_name} ({event.game})", "Кампанію завершено")
        elif isinstance(event, DeadlineRisk):
            first = event.campaigns[0]
            tail = f" і ще {len(event.campaigns) - 1}" if len(event.campaigns) > 1 else ""
            self.notify(f"{first.name} ({first.game}){tail}", "Не встигаємо закрити")
        elif isinstance(event, ProgressStalled):
            self.notify(
                f"{event.minutes_without_progress} хв без приросту на "
                f"{event.channel_name}",
                "Прогрес стоїть",
            )
        elif isinstance(event, WatchUncounted):
            self.set_state("error")
            self.notify(
                f"Хвилина не доходить до Twitch на {event.channel_name}",
                "Перегляд не зараховується",
            )
        elif isinstance(event, MinerError):
            self.set_state("error")
            self.notify(event.message, "Помилка")
        elif isinstance(event, LoginRequired):
            self.notify(f"Код: {event.user_code}", "Потрібен вхід")

    def set_state(self, state: str) -> None:
        # WatchingChanged приходить часто, а перемальовування іконки безглузде,
        # поки стан той самий
        if self._icon is None or state == self._state:
            return
        self._state = state
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
