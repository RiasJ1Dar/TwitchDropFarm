"""Шина подій і шина команд.

Ядро емітить типізовані події й нічого не знає про споживачів. GUI, Telegram і логер —
три рівноправні підписники. Зворотний напрямок (команди від Telegram чи GUI до ядра) іде
окремою `ControlBus`, щоб залежність лишалась односторонньою.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from yarl import URL

logger = logging.getLogger("TwitchDrops")


# ================================================================ події

@dataclass(frozen=True)
class Event:
    """Базовий клас усіх подій."""

    at: datetime = field(default_factory=datetime.now, init=False, compare=False)


# ---- життєвий цикл і авторизація

@dataclass(frozen=True)
class StatusChanged(Event):
    """Одноряд­ковий стан для рядка статусу: «дивимось X», «оновлюємо інвентар»…"""
    text: str


@dataclass(frozen=True)
class LogLine(Event):
    """Рядок для журналу в інтерфейсі."""
    text: str


@dataclass(frozen=True)
class LoginRequired(Event):
    """Потрібна участь людини: відкрити браузер і підтвердити код."""
    verification_uri: URL | str
    user_code: str


@dataclass(frozen=True)
class LoggedIn(Event):
    user_id: int


@dataclass(frozen=True)
class MinerStarted(Event):
    """Майнер піднявся. Потрібна саме як подія: при автозапуску в трей вікна не
    видно, і без сповіщення незрозуміло, чи програма взагалі стартувала."""
    version: str
    tray: bool


@dataclass(frozen=True)
class MinerError(Event):
    message: str
    traceback: str = ""


@dataclass(frozen=True)
class MinerStopped(Event):
    reason: str


@dataclass(frozen=True)
class ConnectionLost(Event):
    """Мережа зникла. Майнер не падає — переходить у режим перепідключення."""
    reason: str
    attempt: int


@dataclass(frozen=True)
class ConnectionRestored(Event):
    """Мережа повернулась; ядро перечитує інвентар, бо за час простою все могло змінитись."""
    downtime_seconds: float
    attempts: int


# ---- канали і перегляд

@dataclass(frozen=True)
class ChannelsUpdated(Event):
    """Повний перелік каналів у полі зору. Кожен елемент — легкий знімок, не сам об'єкт."""
    channels: tuple[ChannelSnapshot, ...]


@dataclass(frozen=True)
class WatchingChanged(Event):
    """None означає, що ми перестали дивитись будь-що."""
    channel: ChannelSnapshot | None


@dataclass(frozen=True)
class StreamOffline(Event):
    channel_name: str


# ---- дропи

@dataclass(frozen=True)
class DropProgress(Event):
    drop_name: str
    game: str
    current_minutes: int
    required_minutes: int


@dataclass(frozen=True)
class DropClaimed(Event):
    drop_name: str
    game: str
    rewards: str


@dataclass(frozen=True)
class CampaignFinished(Event):
    campaign_name: str
    game: str


@dataclass(frozen=True)
class InventoryUpdated(Event):
    campaigns: tuple[CampaignSnapshot, ...]


@dataclass(frozen=True)
class ProgressStalled(Event):
    """Кілька хвилин поспіль жоден із рівнів відліку не дав приросту.

    Прямо адресує баг, через який майнер тихо крутиться без результату.
    """
    minutes_without_progress: int
    channel_name: str


@dataclass(frozen=True)
class WatchUncounted(Event):
    """Хвилина не вийшла з машини — ні через spade, ні через запасний GQL.

    Окремо від застою: той ловить «Twitch мовчить про прогрес», а це —
    «перегляд взагалі не доставили». Типова причина — блокувальник ріже
    `spade.twitch.tv`, а GraphQL при цьому живий, тож вікно показує
    «зв'язок є».
    """
    channel_name: str
    consecutive: int


@dataclass(frozen=True)
class DeadlineRisk(Event):
    """Кампанії, які вже не встигнути закрити до кінця їх вікна.

    Рахунок робився й раніше — `slack` менший за одиницю означає, що часу
    лишилось менше, ніж потрібно хвилин перегляду. Але бачив його лише
    сортувальник режиму TIGHTEST_FIT, а людина дізнавалась про програш аж тоді,
    коли кампанія тихо зникала з інвентаря.
    """
    campaigns: tuple[RiskSnapshot, ...]


@dataclass(frozen=True)
class WindowVisibility(Event):
    """Просимо інтерфейс сховати вікно в трей або дістати його назад.

    Ядро не викликає вікно напряму: воно про інтерфейс нічого не знає, і саме
    тому команда з Telegram доходить сюди подією, а не викликом методу GUI.
    """
    visible: bool


@dataclass(frozen=True)
class WebsocketStatus(Event):
    index: int
    status: str
    topics: int


# ---- знімки станів (щоб не тягнути живі об'єкти ядра в інтерфейси)

@dataclass(frozen=True)
class RiskSnapshot:
    """Кампанія під загрозою: скільки хвилин ще треба і скільки часу лишилось.

    `id` потрібен не інтерфейсу, а історії: за ним після перезапуску видно,
    про яку кампанію вже попереджали, і та сама новина не приходить удруге.
    """
    id: str
    name: str
    game: str
    minutes_needed: int
    minutes_available: int


@dataclass(frozen=True)
class ChannelSnapshot:
    id: int
    name: str
    game: str | None
    viewers: int
    online: bool
    drops_enabled: bool
    acl_based: bool


@dataclass(frozen=True)
class DropSnapshot:
    id: str
    name: str
    current_minutes: int
    required_minutes: int
    claimed: bool
    can_claim: bool
    # адреса картинки нагороди; порожня, якщо Twitch її не дав
    image: str = ""


@dataclass(frozen=True)
class CampaignSnapshot:
    id: str
    name: str
    game: str
    active: bool
    upcoming: bool
    expired: bool
    ends_at: datetime
    claimed_drops: int
    total_drops: int
    drops: tuple[DropSnapshot, ...]
    # обкладинка гри
    image: str = ""


# ================================================================ шина подій

Handler = Callable[[Event], Any | Awaitable[Any]]


class EventBus:
    """Проста шина «опублікував і забув».

    Свідоме рішення: помилка підписника ніколи не валить того, хто емітив. Якщо Telegram
    ліг, майнінг має продовжуватись — сповіщення це зручність, а не частина фарму.
    """

    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._tasks: set[asyncio.Task[Any]] = set()

    def subscribe(self, handler: Handler) -> Callable[[], None]:
        """Підписує обробник. Повертає функцію відписки."""
        self._handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    def emit(self, event: Event) -> None:
        for handler in list(self._handlers):
            try:
                result = handler(event)
            except Exception:
                logger.exception(
                    f"Підписник {getattr(handler, '__qualname__', handler)} впав "
                    f"на події {type(event).__name__}"
                )
                continue
            if inspect.isawaitable(result):
                self._spawn(result)

    def _spawn(self, coro: Awaitable[Any]) -> None:
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError:
            # немає активного циклу — емісія поза asyncio; просто ігноруємо асинхронну частину
            return
        self._tasks.add(task)
        task.add_done_callback(self._reap)

    def _reap(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception("Асинхронний підписник впав", exc_info=exc)

    async def drain(self) -> None:
        """Дочекатись поточних асинхронних обробників — потрібно при завершенні роботи."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # зручні скорочення, щоб не імпортувати класи подій усюди
    def status(self, text: str) -> None:
        self.emit(StatusChanged(text))

    def log(self, text: str) -> None:
        logger.info(text)
        self.emit(LogLine(text))


# ================================================================ шина команд

class CommandType(Enum):
    PAUSE = auto()
    RESUME = auto()
    RELOAD = auto()
    # повний перезапуск процесу, на відміну від RELOAD (лише перечитати інвентар)
    REBOOT = auto()
    SWITCH = auto()
    # згорнути вікно в трей або дістати його назад
    SHOW_WINDOW = auto()
    HIDE_WINDOW = auto()
    PRIORITY_ADD = auto()
    PRIORITY_REMOVE = auto()
    EXCLUDE_ADD = auto()
    EXCLUDE_REMOVE = auto()
    SHUTDOWN = auto()


@dataclass(frozen=True)
class Command:
    type: CommandType
    argument: str = ""
    # куди відповісти про результат; заповнює відправник (напр. Telegram — своїм chat_id)
    reply_to: Any = None


class ControlBus:
    """Черга команд ззовні до ядра.

    Черга, а не прямі виклики, — щоб команда з Telegram-потоку не змінювала стан майнера
    посеред ітерації головного циклу.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Command] = asyncio.Queue()
        # Сигнал «є що забрати». Потрібен, щоб головний цикл прокидався на команду,
        # а не опитував чергу за таймером: опитування змушувало його щосекунди
        # переграти поточний стан і засмічувало інтерфейс повтореннями статусу.
        self._signal = asyncio.Event()

    def send(self, command: Command) -> None:
        self._queue.put_nowait(command)
        self._signal.set()

    def wait(self) -> Coroutine[Any, Any, bool]:
        """Чекає появи хоча б однієї команди."""
        return self._signal.wait()

    def get_nowait(self) -> Command | None:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def get(self) -> Command:
        return await self._queue.get()

    def drain_pending(self) -> list[Command]:
        """Забирає всі накопичені команди за раз — так їх обробляє головний цикл."""
        commands: list[Command] = []
        while (command := self.get_nowait()) is not None:
            commands.append(command)
        self._signal.clear()
        return commands
