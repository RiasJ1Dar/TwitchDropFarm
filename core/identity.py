"""Хто ми для Twitch: токен, ідентифікатори пристрою та сесії.

Виділено з майнера окремо, бо це самодостатня річ: має власний життєвий цикл
(отримати, перевірити, забути) і власне сховище.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from core import protocol
from core.config import TOKEN_FILE
from core.toolbox import HEX_LOWER, random_token, save_json

if TYPE_CHECKING:
    from core.api import TwitchApi

log = logging.getLogger("TwitchDrops")

_EMPTY = {"access_token": "", "user_id": 0}
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _dpapi_protect(plain: bytes) -> bytes:
    """Шифрує байти ключем обліковки Windows. Без діалогу."""
    if sys.platform != "win32":
        raise OSError("DPAPI є лише на Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(plain, len(plain))
    incoming = DATA_BLOB(len(plain), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(incoming),
        "TwitchDropFarm",
        None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(outgoing),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI є лише на Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(blob, len(blob))
    incoming = DATA_BLOB(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(incoming),
        None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(outgoing),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.pbData)


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
    def _read_store() -> dict[str, Any]:
        """Сирий JSON, без шаблону: інакше `protected`/`blob` викине `_conform`."""
        for candidate in (TOKEN_FILE.with_suffix(TOKEN_FILE.suffix + ".new"), TOKEN_FILE):
            if not candidate.exists():
                continue
            try:
                with candidate.open("r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(raw, dict):
                return raw
        return {}

    @staticmethod
    def _load() -> tuple[str, int]:
        stored = Identity._read_store()
        if stored.get("protected"):
            try:
                inner = json.loads(_dpapi_unprotect(
                    base64.b64decode(stored.get("blob") or "")
                ))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                log.warning(f"Не вдалося розшифрувати auth.json: {error}")
                return "", 0
            if not isinstance(inner, dict):
                return "", 0
            return str(inner.get("access_token") or ""), int(inner.get("user_id") or 0)
        return str(stored.get("access_token") or ""), int(stored.get("user_id") or 0)

    def _persist(self) -> None:
        payload = {"access_token": self.token, "user_id": self.user_id}
        if sys.platform == "win32" and self.token:
            try:
                blob = _dpapi_protect(json.dumps(payload).encode("utf-8"))
                save_json(TOKEN_FILE, {
                    "protected": True,
                    "blob": base64.b64encode(blob).decode("ascii"),
                })
                return
            except OSError as error:
                log.warning(f"DPAPI не записав токен, лишаю відкритим: {error}")
        save_json(TOKEN_FILE, payload)

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
