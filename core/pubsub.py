"""Підписки на події Twitch через PubSub.

Twitch обмежує кількість топіків на одне з'єднання, тому їх доводиться тримати
кілька й розподіляти підписки. Пул сам відкриває нові з'єднання під потребу і
згортає зайві, коли підписок меншає.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any

import aiohttp

from core import protocol
from core.config import (
    PING_PERIOD,
    PONG_DEADLINE,
    TOPICS_PER_SOCKET,
    WEBSOCKET_LIMIT,
)
from core.toolbox import ALPHANUMERIC, Backoff, Slot, TaskKeeper, batched, guard_task, random_token

if TYPE_CHECKING:
    from core.events import EventBus

log = logging.getLogger("TwitchDrops")
wire = logging.getLogger("TwitchDrops.pubsub")


class SocketClosed(Exception):
    """З'єднання закрилось. `by_peer` — це зробив Twitch, а не ми."""

    def __init__(self, *, by_peer: bool = False):
        super().__init__("PubSub-з'єднання закрито")
        self.by_peer = by_peer


@dataclass(frozen=True, slots=True)
class Subscription:
    """Топік плюс обробник його повідомлень."""

    name: str
    target_id: int
    handle: Callable[[int, dict[str, Any]], Any]

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Subscription):
            return other.name == self.name
        return other == self.name if isinstance(other, str) else NotImplemented

    def __hash__(self) -> int:
        return hash(self.name)

    def deliver(self, message: dict[str, Any]) -> Any:
        return self.handle(self.target_id, message)


def user_subscription(kind: str, user_id: int,
                      handler: Callable[[int, dict[str, Any]], Any]) -> Subscription:
    return Subscription(protocol.topic_name(protocol.USER_TOPICS, kind, user_id),
                        user_id, handler)


def channel_subscription(kind: str, channel_id: int,
                         handler: Callable[[int, dict[str, Any]], Any]) -> Subscription:
    return Subscription(protocol.topic_name(protocol.CHANNEL_TOPICS, kind, channel_id),
                        channel_id, handler)


def channel_topic_names(channel_ids: Iterable[int]) -> list[str]:
    """Імена топіків каналів — для відписки."""
    return [
        protocol.topic_name(protocol.CHANNEL_TOPICS, kind, channel_id)
        for channel_id in channel_ids
        for kind in protocol.CHANNEL_TOPICS
    ]


class Socket:
    """Одне з'єднання з PubSub."""

    def __init__(self, pool: SocketPool, index: int):
        self._pool = pool
        self.index = index
        self.subscriptions: dict[str, Subscription] = {}
        self._live: Slot[aiohttp.ClientWebSocketResponse] = Slot()
        self._sent: set[str] = set()
        self._changed = asyncio.Event()
        self._reconnect = asyncio.Event()
        self._stopping = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._tasks = TaskKeeper()
        self._ping_due = monotonic()
        self._pong_due = self._ping_due + PONG_DEADLINE.total_seconds()
        self._state = "відключено"
        self._publish_state()

    # ------------------------------------------------------------ стан

    def _publish_state(self, text: str | None = None) -> None:
        """`text=None` означає «змінилась лише кількість топіків».

        Без цього кожна зміна підписок затирала слово «підключено», і в
        інтерфейсі лишався порожній рядок.
        """
        if text is not None:
            self._state = text
        self._pool.report_state(self.index, self._state, len(self.subscriptions))

    @property
    def connected(self) -> bool:
        return self._live.filled

    def wait_connected(self):
        return self._live.wait()

    # ------------------------------------------------------------ життя

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.ensure_future(self._run())

    async def start_and_wait(self) -> None:
        self.start()
        await self.wait_connected()

    async def stop(self, *, forget_topics: bool = False) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        if (socket := self._live.peek()) is not None:
            self._publish_state("відключаємось")
            await socket.close()
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._worker, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
            self._worker = None
        self._tasks.cancel_all()
        if forget_topics:
            self.subscriptions.clear()
            self._changed.set()

    def ask_reconnect(self) -> None:
        self._ping_due = monotonic()
        self._reconnect.set()

    @guard_task(vital=True)
    async def _run(self) -> None:
        self._publish_state("ініціалізація")
        await self._pool.wait_until_authorised()
        self._stopping.clear()
        self._publish_state("підключення")

        backoff = Backoff(ceiling=300.0)
        session = await self._pool.http_session()
        proxy = self._pool.proxy or None

        while not self._stopping.is_set():
            try:
                async with session.ws_connect(protocol.PUBSUB_ENDPOINT, proxy=proxy) as socket:
                    backoff.reset()
                    self._live.put(socket)
                    self._reconnect.clear()
                    self._publish_state("підключено")
                    wire.info(f"PubSub[{self.index}] підключено")
                    try:
                        await self._serve()
                    finally:
                        self._live.clear()
                        self._sent.clear()
                        # наступне з'єднання має перепідписатись на все
                        self._changed.set()
            except SocketClosed as closed:
                if self._stopping.is_set():
                    self._publish_state("відключено")
                    return
                if closed.by_peer:
                    wire.warning(f"PubSub[{self.index}]: закрито з боку Twitch")
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as error:
                delay = next(backoff)
                wire.info(f"PubSub[{self.index}]: {type(error).__name__}, пауза {delay:.0f}с")
                self._publish_state(f"перепідключення через {delay:.0f}с")
                await asyncio.sleep(delay)
                continue
            except RuntimeError:
                wire.warning(f"PubSub[{self.index}]: сесія закрита, зупиняюсь")
                return
            except Exception:
                wire.exception(f"PubSub[{self.index}]: несподівана помилка")
            if not self._stopping.is_set():
                self._publish_state("перепідключення")

    async def _serve(self) -> None:
        while not self._reconnect.is_set() and not self._stopping.is_set():
            await self._keep_alive()
            await self._sync_subscriptions()
            await self._receive()

    # ------------------------------------------------------------ протокол

    async def _keep_alive(self) -> None:
        now = monotonic()
        if now >= self._ping_due:
            self._ping_due = now + PING_PERIOD.total_seconds()
            self._pong_due = now + PONG_DEADLINE.total_seconds()
            await self._send({"type": "PING"})
        elif now >= self._pong_due:
            wire.warning(f"PubSub[{self.index}]: PONG не прийшов, перепідключаюсь")
            self.ask_reconnect()

    async def _sync_subscriptions(self) -> None:
        if not self._changed.is_set():
            return
        self._changed.clear()
        self._publish_state()
        token = await self._pool.access_token()
        wanted = set(self.subscriptions)

        for names, verb in ((self._sent - wanted, "UNLISTEN"), (wanted - self._sent, "LISTEN")):
            if not names:
                continue
            for portion in batched(sorted(names), 20):
                await self._send({
                    "type": verb,
                    "data": {"topics": portion, "auth_token": token},
                })
            if verb == "UNLISTEN":
                self._sent -= names
            else:
                self._sent |= names

    async def _receive(self) -> None:
        socket = self._live.peek()
        if socket is None:
            raise SocketClosed()
        inbox: list[dict[str, Any]] = []
        try:
            while True:
                frame = await socket.receive(timeout=0.5)
                if frame.type is aiohttp.WSMsgType.TEXT:
                    inbox.append(json.loads(frame.data))
                elif frame.type is aiohttp.WSMsgType.CLOSE:
                    raise SocketClosed(by_peer=True)
                elif frame.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    raise SocketClosed()
        except asyncio.TimeoutError:
            pass
        except aiohttp.ClientConnectionError as error:
            raise SocketClosed() from error

        for message in inbox:
            self._dispatch(message)

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "MESSAGE":
            body = message["data"]
            if (subscription := self.subscriptions.get(body["topic"])) is not None:
                # окремою таскою, щоб довгий обробник не гальмував приймання;
                # посилання тримається, інакше збирач сміття може її прибрати
                self._tasks.launch(subscription.deliver(json.loads(body["message"])))
        elif kind == "PONG":
            self._pong_due = self._ping_due
        elif kind == "RECONNECT":
            wire.warning(f"PubSub[{self.index}]: Twitch просить перепідключитись")
            self.ask_reconnect()
        elif kind != "RESPONSE":
            wire.warning(f"PubSub[{self.index}]: невідоме повідомлення {kind}")

    async def _send(self, message: dict[str, Any]) -> None:
        socket = self._live.peek()
        if socket is None:
            raise SocketClosed()
        if message["type"] != "PING":
            message["nonce"] = random_token(30, ALPHANUMERIC)
        try:
            await socket.send_json(message)
        except aiohttp.ClientConnectionError as error:
            raise SocketClosed() from error

    # ------------------------------------------------------------ підписки

    def take_subscriptions(self, offered: set[Subscription]) -> None:
        """Забирає з набору стільки, скільки вміщає. Набір змінюється на місці."""
        room = TOPICS_PER_SOCKET - len(self.subscriptions)
        if room <= 0 or not offered:
            return
        for subscription in list(offered)[:room]:
            self.subscriptions[subscription.name] = subscription
            offered.discard(subscription)
        self._changed.set()

    def release_subscriptions(self, names: set[str]) -> None:
        """Прибирає названі топіки. Набір змінюється на місці."""
        mine = names & set(self.subscriptions)
        if not mine:
            return
        for name in mine:
            del self.subscriptions[name]
        names -= mine
        self._changed.set()


class SocketPool:
    """Кілька з'єднань, між якими розкладені підписки."""

    def __init__(self, *, events: EventBus, session_factory, token_source,
                 login_gate, proxy: str | None = None):
        self._events = events
        self._session_factory = session_factory
        self._token_source = token_source
        self._login_gate = login_gate
        self.proxy = proxy
        self._running = asyncio.Event()
        self.sockets: list[Socket] = []
        self._closing = TaskKeeper()

    # ------------------------------------------------------------ послуги сокетам

    async def http_session(self):
        return await self._session_factory()

    async def access_token(self) -> str:
        return await self._token_source()

    async def wait_until_authorised(self) -> None:
        await self._login_gate()

    def report_state(self, index: int, state: str, topics: int) -> None:
        from core.events import WebsocketStatus
        self._events.emit(WebsocketStatus(index=index, status=state, topics=topics))

    # ------------------------------------------------------------ керування

    @property
    def running(self) -> bool:
        return self._running.is_set()

    async def start(self) -> None:
        self._running.set()
        if self.sockets:
            await asyncio.gather(*(s.start_and_wait() for s in self.sockets))

    async def stop(self, *, forget_topics: bool = False) -> None:
        self._running.clear()
        await asyncio.gather(
            *(s.stop(forget_topics=forget_topics) for s in self.sockets),
            return_exceptions=True,
        )

    def subscribe(self, subscriptions: Iterable[Subscription]) -> None:
        pending = set(subscriptions)
        for socket in self.sockets:
            pending -= set(socket.subscriptions.values())
        if not pending:
            return

        for index in range(WEBSOCKET_LIMIT):
            if index >= len(self.sockets):
                socket = Socket(self, index)
                if self.running:
                    socket.start()
                self.sockets.append(socket)
            self.sockets[index].take_subscriptions(pending)
            if not pending:
                return
        raise RuntimeError(
            f"Не вистачило місця для {len(pending)} топіків — "
            f"перевір USER_TOPIC_COUNT у config"
        )

    def unsubscribe(self, names: Iterable[str]) -> None:
        pending = set(names)
        if not pending:
            return
        for socket in self.sockets:
            socket.release_subscriptions(pending)
        self._shrink()

    def _shrink(self) -> None:
        """Згортає зайві з'єднання, перекладаючи їхні підписки на решту."""
        recycled: list[Subscription] = []
        while len(self.sockets) > 1:
            total = sum(len(s.subscriptions) for s in self.sockets)
            if total > (len(self.sockets) - 1) * TOPICS_PER_SOCKET:
                break
            spare = self.sockets.pop()
            recycled.extend(spare.subscriptions.values())
            # посилання тримає TaskKeeper: без нього збирач сміття може прибрати
            # закриття з'єднання посеред виконання
            self._closing.launch(spare.stop(forget_topics=True))
        if recycled:
            self.subscribe(recycled)

    @property
    def topic_count(self) -> int:
        return sum(len(s.subscriptions) for s in self.sockets)
