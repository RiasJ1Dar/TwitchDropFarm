"""Канали, трансляції та доставка події перегляду.

Доставка винесена в окремий `WatchReporter`: у нього власний стан (адреса spade,
чи зламався основний шлях), і каналу знати про це не потрібно — його справа
відповідати, хто зараз в етері та з якою грою.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

from core import protocol
from core.config import SPADE_ATTEMPTS, STREAM_UP_DELAY, TRACE
from core.toolbox import Game

log = logging.getLogger("TwitchDrops")


class Backend(Protocol):
    """Те, що канал очікує від ядра."""

    async def graphql(self, payload: Any) -> Any: ...
    async def fetch_text(self, url: str, **kwargs: Any) -> str: ...
    async def post_form(self, url: str, body: dict[str, Any], **kwargs: Any) -> int: ...
    def channel_state_changed(self, channel: Channel, was_live: bool) -> None: ...
    def channel_display_changed(self, channel: Channel) -> None: ...
    def campaign_by_id(self, campaign_id: str) -> Any: ...
    @property
    def verify_drops_enabled(self) -> bool: ...
    @property
    def user_id(self) -> int: ...


class Stream:
    """Поточна трансляція каналу."""

    __slots__ = ("broadcast_id", "drops_on", "game", "title", "viewers")

    def __init__(self, *, broadcast_id: int, viewers: int, title: str,
                 game: dict[str, Any] | None, drops_on: bool):
        self.broadcast_id = broadcast_id
        self.viewers = viewers
        self.title = title
        self.game = Game(game) if game else None
        self.drops_on = drops_on

    @classmethod
    def from_channel_query(cls, payload: dict[str, Any], *, assume_drops: bool) -> Stream:
        live = payload["stream"]
        settings = payload["broadcastSettings"]
        return cls(
            broadcast_id=int(live["id"]),
            viewers=live["viewersCount"],
            title=settings["title"],
            game=settings["game"],
            drops_on=assume_drops,
        )

    @classmethod
    def from_directory(cls, payload: dict[str, Any], *, drops_on: bool) -> Stream:
        return cls(
            broadcast_id=int(payload["id"]),
            viewers=payload["viewersCount"],
            title=payload["title"],
            game=payload["game"],
            drops_on=drops_on,
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Stream) and other.broadcast_id == self.broadcast_id

    def __hash__(self) -> int:
        return self.broadcast_id


class WatchReporter:
    """Повідомляє Twitch, що ми дивимось хвилину.

    Основний шлях — spade-ендпоінт. Його адресу треба виколупувати зі сторінки
    стрімера, і Twitch регулярно змінює, де саме вона лежить, тож коли витяг не
    вдається — мовчки переходимо на GQL-мутацію. Фолбек увімкнений завжди, а не
    «про запас»: у попередній версії він був написаний, але вимкнений, і кожен
    злам розмітки зупиняв фарм.
    """

    __slots__ = ("_backend", "_spade_url", "_use_mutation")

    def __init__(self, backend: Backend):
        self._backend = backend
        self._spade_url: str | None = None
        self._use_mutation = False

    async def _resolve_spade_url(self, channel_url: str) -> str | None:
        page = await self._backend.fetch_text(
            channel_url, attempts=SPADE_ATTEMPTS, count_as_network=False,
        )
        if found := re.search(protocol.SPADE_IN_PAGE, page, re.IGNORECASE):
            return found.group(1)
        script = re.search(protocol.SETTINGS_SCRIPT, page, re.IGNORECASE)
        if script is None:
            return None
        config = await self._backend.fetch_text(
            script.group(1), attempts=SPADE_ATTEMPTS, count_as_network=False,
        )
        found = re.search(protocol.SPADE_IN_PAGE, config, re.IGNORECASE)
        return found.group(1) if found else None

    async def report(self, channel: Channel) -> bool:
        stream = channel.stream
        if stream is None:
            return False
        event = protocol.watch_event(
            broadcast_id=stream.broadcast_id,
            channel_id=channel.id,
            channel_login=channel.login,
            game_id=stream.game.id if stream.game else None,
            game_name=stream.game.name if stream.game else None,
            user_id=self._backend.user_id,
        )

        if not self._use_mutation:
            if self._spade_url is None:
                try:
                    self._spade_url = await self._resolve_spade_url(channel.url)
                except Exception as error:
                    log.warning(
                        f"Сторінка {channel.login} недоступна "
                        f"({type(error).__name__}) — переходжу на GQL"
                    )
                    self._use_mutation = True
                else:
                    if self._spade_url is None:
                        log.warning("Адресу spade не знайдено — переходжу на GQL")
                        self._use_mutation = True
            if self._spade_url is not None and not self._use_mutation:
                try:
                    status = await self._backend.post_form(
                        self._spade_url, protocol.spade_body(event),
                        attempts=SPADE_ATTEMPTS, count_as_network=False,
                    )
                except Exception as error:
                    # Раніше виняток обривав метод і GQL навіть не пробували.
                    # Якщо блокувальник ріже лише spade, запасний шлях живий.
                    log.warning(
                        f"spade недоступний ({type(error).__name__}) — переходжу на GQL"
                    )
                    self._use_mutation = True
                else:
                    if status == 204:
                        return True
                    log.warning(f"spade відповів {status} — переходжу на GQL")
                    self._use_mutation = True

        try:
            answer = await self._backend.graphql(protocol.spade_mutation(event))
            return answer["data"]["sendSpadeEvents"]["statusCode"] == 204
        except Exception:
            return False


class Channel:
    """Канал Twitch у полі зору майнера."""

    __slots__ = (
        "_backend",
        "_going_live",
        "_reporter",
        "_title",
        "from_allowlist_flag",
        "id",
        "login",
        "stream",
    )

    def __init__(self, backend: Backend, *, channel_id: int, login: str,
                 title: str | None = None, from_allowlist: bool = False):
        self._backend = backend
        self._reporter = WatchReporter(backend)
        self.id = channel_id
        self.login = login
        self._title = title
        self.stream: Stream | None = None
        self._going_live: asyncio.Task[None] | None = None
        # канали зі списку кампанії розглядаються першими й не чистяться просто так
        self.from_allowlist_flag = from_allowlist

    @classmethod
    def from_allowlist(cls, backend: Backend, payload: dict[str, Any]) -> Channel:
        return cls(
            backend,
            channel_id=int(payload["id"]),
            login=payload["name"],
            title=payload.get("displayName"),
            from_allowlist=True,
        )

    @classmethod
    def from_directory(cls, backend: Backend, payload: dict[str, Any], *,
                       drops_on: bool) -> Channel:
        who = payload["broadcaster"]
        channel = cls(
            backend,
            channel_id=int(who["id"]),
            login=who["login"],
            title=who["displayName"],
        )
        channel.stream = Stream.from_directory(payload, drops_on=drops_on)
        return channel

    # ------------------------------------------------------------ властивості

    @property
    def name(self) -> str:
        return self._title or self.login

    @property
    def url(self) -> str:
        return f"{protocol.TWITCH_HOME}/{self.login}"

    @property
    def live(self) -> bool:
        return self.stream is not None

    @property
    def dark(self) -> bool:
        """Не в етері й не збирається."""
        return self.stream is None and self._going_live is None

    @property
    def warming_up(self) -> bool:
        """Twitch сказав «зараз почнеться», але трансляції ще немає."""
        return self.stream is None and self._going_live is not None

    @property
    def game(self) -> Game | None:
        return self.stream.game if self.stream else None

    @property
    def viewers(self) -> int:
        return self.stream.viewers if self.stream else 0

    @viewers.setter
    def viewers(self, value: int) -> None:
        if self.stream is not None:
            self.stream.viewers = value

    @property
    def drops_on(self) -> bool:
        return self.stream.drops_on if self.stream else False

    # ------------------------------------------------------------ стан етеру

    def announce(self) -> None:
        self._backend.channel_display_changed(self)

    async def load_stream(self) -> Stream | None:
        answer = await self._backend.graphql(protocol.STREAM_INFO(channel=self.login))
        body = answer["data"]["user"]
        if not body:
            return None
        if self._title is None:
            self._title = body["displayName"]
        if not body["stream"]:
            return None

        assume = not self._backend.verify_drops_enabled
        stream = Stream.from_channel_query(body, assume_drops=assume)
        if not stream.drops_on:
            stream.drops_on = await self._check_drops_enabled()
        return stream

    async def _check_drops_enabled(self) -> bool:
        try:
            answer = await self._backend.graphql(
                protocol.CHANNEL_DROPS(channelID=str(self.id))
            )
            listed = answer["data"]["channel"]["viewerDropCampaigns"] or []
        except Exception:
            log.log(TRACE, f"Не вдалося спитати про дропи каналу {self.login}")
            return False
        return self._matches_our_campaigns(listed)

    def _matches_our_campaigns(self, listed: list[dict[str, Any]]) -> bool:
        for item in listed:
            campaign = self._backend.campaign_by_id(item["id"])
            if campaign is not None and campaign.farmable(self, ignore_channel_state=True):
                return True
        return False

    def apply_bulk_update(self, payload: dict[str, Any],
                          drops_listed: list[dict[str, Any]]) -> None:
        """Оновлення з масової перевірки — без окремого запиту на канал."""
        if not payload["stream"]:
            self.stream = None
            return
        assume = not self._backend.verify_drops_enabled
        stream = Stream.from_channel_query(payload, assume_drops=assume)
        if not stream.drops_on:
            stream.drops_on = self._matches_our_campaigns(drops_listed)
        self.stream = stream

    async def refresh(self) -> bool:
        was_live = self.live
        self.stream = await self.load_stream()
        self._backend.channel_state_changed(self, was_live)
        return self.live

    async def _confirm_live_later(self) -> None:
        """Подія «стрім піднявся» випереджає реальний старт — чекаємо й перевіряємо."""
        await asyncio.sleep(STREAM_UP_DELAY.total_seconds())
        self._going_live = None
        await self.refresh()

    def expect_live(self) -> None:
        """Twitch натякнув, що канал ось-ось в етері."""
        if self._going_live is None:
            self._going_live = asyncio.ensure_future(self._confirm_live_later())
            self.announce()

    def mark_dark(self) -> None:
        """Канал точно не в етері."""
        pending = self._going_live is not None
        if pending:
            self._going_live.cancel()  # type: ignore[union-attr]
            self._going_live = None
        if self.live:
            self.stream = None
            self._backend.channel_state_changed(self, True)
        elif pending:
            self.announce()

    def forget(self) -> None:
        if self._going_live is not None:
            self._going_live.cancel()
            self._going_live = None

    # ------------------------------------------------------------ перегляд

    async def report_watching(self) -> bool:
        return await self._reporter.report(self)

    # ------------------------------------------------------------ службове

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Channel) and other.id == self.id

    def __hash__(self) -> int:
        return self.id

    def __repr__(self) -> str:
        where = "в етері" if self.live else "офлайн"
        return f"Channel({self.name!r}, {where})"
