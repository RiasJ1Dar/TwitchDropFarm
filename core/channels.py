"""Канали, трансляції та доставка події перегляду.

Доставка винесена в окремий `WatchReporter`: у нього власний стан (підписана
адреса HLS-плейлиста), і каналу знати про це не потрібно — його справа
відповідати, хто зараз в етері та з якою грою.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol
from urllib.parse import urlencode, urljoin, urlsplit

from core import protocol
from core.config import STREAM_UP_DELAY, TRACE, WATCH_ATTEMPTS
from core.toolbox import Game

log = logging.getLogger("TwitchDrops")

class Backend(Protocol):
    """Те, що канал очікує від ядра."""

    async def graphql(self, payload: Any) -> Any: ...
    async def fetch_text(self, url: str, **kwargs: Any) -> str: ...
    async def head(self, url: str, **kwargs: Any) -> int: ...
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
    """Доставляє перегляд через HLS, не завантажуючи відео.

    23.10 spade відповідав 204, але прогрес шість хвилин стояв на 8/30.
    HEAD до сегментів тієї самої трансляції одразу дав 9, 10 хвилин.
    Тому успішний spade більше не є запасним доказом доставки перегляду.
    """

    __slots__ = ("_backend", "_broadcast_id", "_playlist_url")

    def __init__(self, backend: Backend):
        self._backend = backend
        self._playlist_url: str | None = None
        self._broadcast_id: int | None = None

    @staticmethod
    def _urls(text: str, base: str) -> list[str]:
        if not text.lstrip().startswith("#EXTM3U"):
            raise ValueError("Відповідь не є HLS-плейлистом")
        return [
            url for raw_line in text.splitlines()
            if (line := raw_line.strip()) and not line.startswith("#")
            if urlsplit(url := urljoin(base, line)).scheme in ("http", "https")
        ]

    async def _resolve_playlist(self, channel: Channel) -> str:
        answer = await self._backend.graphql(protocol.PLAYBACK_ACCESS(login=channel.login))
        access = answer["data"]["streamPlaybackAccessToken"]
        query = urlencode({
            "sig": access["signature"], "token": access["value"],
            "allow_source": "true", "allow_audio_only": "true",
        })
        master_url = f"https://usher.ttvnw.net/api/channel/hls/{channel.login}.m3u8?{query}"
        master = await self._backend.fetch_text(
            master_url, attempts=WATCH_ATTEMPTS, count_as_network=False,
        )
        variants = self._urls(master, master_url)
        if not variants:
            raise ValueError("У HLS немає доступної якості")
        return variants[-1]

    async def report(self, channel: Channel) -> bool:
        try:
            # Обмежуємо весь прохід, а не кожен із п'ятнадцяти HEAD окремо.
            return await asyncio.wait_for(self._report_hls(channel), timeout=18)
        except Exception as error:
            self._playlist_url = None
            # Виняток aiohttp містить підписаний URL із playback-токеном.
            # Назви класу достатньо; сам URL у журнал не потрапляє.
            log.warning(
                f"HLS-перегляд {channel.login} не доставлено ({type(error).__name__})"
            )
            return False

    async def _report_hls(self, channel: Channel) -> bool:
        stream = channel.stream
        if stream is None:
            return False
        if self._broadcast_id != stream.broadcast_id:
            self._playlist_url = None
            self._broadcast_id = stream.broadcast_id
        if self._playlist_url is None:
            self._playlist_url = await self._resolve_playlist(channel)
        playlist = await self._backend.fetch_text(
            self._playlist_url, attempts=WATCH_ATTEMPTS,
            count_as_network=False, headers={"Connection": "close"},
        )
        chunks = self._urls(playlist, self._playlist_url)
        if not chunks or "#EXT-X-ENDLIST" in playlist:
            raise ValueError("Трансляція не має живих сегментів")
        for chunk in chunks:
            status = await self._backend.head(
                chunk, attempts=WATCH_ATTEMPTS, count_as_network=False,
            )
            if status != 200:
                raise ValueError(f"Сегмент HLS віддав HTTP {status}")
        log.log(TRACE, f"HLS-перегляд {channel.login}: HEAD {len(chunks)} сегментів, HTTP 200")
        return True


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
