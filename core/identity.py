"""Хто ми для Twitch: токен, ідентифікатори пристрою та сесії.

Виділено з майнера окремо, бо це самодостатня річ: має власний життєвий цикл
(отримати, перевірити, забути) і власне сховище.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from core import protocol
from core.config import TOKEN_FILE
from core.toolbox import HEX_LOWER, load_json, random_token, save_json

if TYPE_CHECKING:
    from core.api import TwitchApi

log = logging.getLogger("TwitchDrops")

_EMPTY = {"access_token": "", "user_id": 0}


class Identity:
    """Токен доступу та все, що йде з ним у заголовках."""

    def __init__(self, api: TwitchApi,
                 obtain_token: Callable[[], Awaitable[str]] | None = None):
        self._api = api
        self._obtain = obtain_token
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self.token: str = ""
        self.user_id: int = 0
        self.device_id: str = ""
        self.session_id: str = random_token(16, HEX_LOWER)

    # ------------------------------------------------------------ сховище

    @staticmethod
    def _load() -> tuple[str, int]:
        stored = load_json(TOKEN_FILE, _EMPTY)
        return stored["access_token"], int(stored["user_id"] or 0)

    def _persist(self) -> None:
        save_json(TOKEN_FILE, {"access_token": self.token, "user_id": self.user_id})

    @staticmethod
    def forget_stored() -> None:
        TOKEN_FILE.unlink(missing_ok=True)

    # ------------------------------------------------------------ заголовки

    def headers(self, *, for_graphql: bool = False) -> dict[str, str]:
        head = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-US",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Client-Id": self._api.client.client_id,
            "Client-Session-Id": self.session_id,
        }
        if self.device_id:
            head["X-Device-Id"] = self.device_id
        if for_graphql:
            head["Origin"] = protocol.TWITCH_HOME
            head["Referer"] = protocol.TWITCH_HOME
            head["Authorization"] = f"OAuth {self.token}"
        return head

    async def graphql_headers(self) -> dict[str, str]:
        await self.ensure()
        return self.headers(for_graphql=True)

    # ------------------------------------------------------------ готовність

    @property
    def known(self) -> bool:
        return bool(self.token and self.user_id)

    def wait_ready(self) -> Awaitable[bool]:
        return self._ready.wait()

    async def access_token(self) -> str:
        await self.ensure()
        return self.token

    def invalidate(self, *, drop_cookies: bool = False) -> None:
        self.token = ""
        self.user_id = 0
        self._ready.clear()
        self.forget_stored()
        if drop_cookies:
            self._api.forget_cookies()

    def clear(self) -> None:
        self.token = ""
        self.user_id = 0
        self.device_id = ""
        self._ready.clear()

    # ------------------------------------------------------------ перевірка

    async def ensure(self) -> None:
        async with self._lock:
            await self._ensure_unlocked()

    async def _ensure_unlocked(self) -> None:
        if self.known:
            self._ready.set()
            return

        await self._ensure_device_id()

        # Дві спроби: перша зі збереженим токеном, друга — після свіжого входу.
        for attempt in range(2):
            if not self.token:
                stored, _user = self._load()
                if stored and attempt == 0:
                    log.info("Відновлюю сесію зі збереженого токена")
                    self.token = stored
                else:
                    if self._obtain is None:
                        raise RuntimeError("Немає способу отримати токен")
                    self.token = await self._obtain()

            checked = await self._validate(self.token)
            if checked is None:
                log.info("Токен недійсний — потрібен новий вхід")
                self.token = ""
                self.forget_stored()
                continue
            if checked["client_id"] != self._api.client.client_id:
                # токен виданий іншому клієнту: з ним Twitch поведеться інакше
                log.info("Токен належить іншому client ID — перелогінююсь")
                self.token = ""
                self.forget_stored()
                self._api.forget_cookies()
                continue

            self.user_id = int(checked["user_id"])
            self._persist()
            log.info(f"Авторизація успішна, user ID: {self.user_id}")
            self._ready.set()
            return

        raise RuntimeError("Не вдалося підтвердити авторизацію")

    async def _ensure_device_id(self) -> None:
        """Звичайний перегляд twitch.tv залишає cookie `unique_id` — це й є пристрій."""
        if self.device_id:
            return
        async with self._api.request(
            "GET", protocol.TWITCH_HOME, headers=self.headers()
        ) as response:
            await response.text("utf8")
        self.device_id = self._api.cookie("unique_id") or random_token(32, HEX_LOWER)

    async def _validate(self, token: str) -> dict[str, Any] | None:
        async with self._api.request(
            "GET", protocol.OAUTH_VALIDATE,
            headers={"Authorization": f"OAuth {token}"},
        ) as response:
            if response.status == 200:
                return await response.json()
            if response.status == 401:
                return None
            raise RuntimeError(f"Twitch відповів {response.status} на перевірку токена")
