"""Запуск системного браузера з відкритим CDP-портом.

Використовуємо той браузер, який у користувача вже встановлений (Edge, потім Chrome).
Нічого не завантажуємо — це прямо випливає з вимоги «без додаткових рантаймів».
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
from contextlib import suppress
from pathlib import Path

import aiohttp

from auth.cdp import CDPSession
from core.config import BROWSER_LOCATIONS as BROWSER_CANDIDATES
from core.config import BROWSER_PROFILE
from core.exceptions import BrowserException

logger = logging.getLogger("TwitchDrops")


def find_browser(preferred: str = "") -> Path:
    """Знаходить виконуваний файл браузера. `preferred` — шлях із налаштувань."""
    if preferred:
        path = Path(preferred)
        if path.is_file():
            return path
        raise BrowserException(f"Вказаний браузер не знайдено: {preferred}")
    for candidate in BROWSER_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return Path(candidate)
    raise BrowserException(
        "Не знайдено ні Edge, ні Chrome. Вкажи шлях до браузера в налаштуваннях "
        "(browser_path у settings.json)."
    )


def _free_port() -> int:
    """Просимо в ОС вільний порт, щоб не конфліктувати з чужим debug-портом."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Browser:
    """Керований екземпляр браузера з увімкненим CDP.

    Профіль зберігається між запусками — це те, завдяки чому повторна авторизація
    не потребує повторного введення пароля.
    """

    def __init__(self, executable: Path, *, headless: bool = False):
        self._executable = executable
        self._headless = headless
        self._port = _free_port()
        self._process: subprocess.Popen[bytes] | None = None
        self._http: aiohttp.ClientSession | None = None
        self.page: CDPSession | None = None

    async def __aenter__(self) -> Browser:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
        args = [
            str(self._executable),
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={BROWSER_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,AutomationControlled",
            "--disable-popup-blocking",
            "about:blank",
        ]
        if self._headless:
            args.insert(1, "--headless=new")
        logger.info(f"Запускаю браузер: {self._executable.name} (порт {self._port})")
        self._process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self._http = aiohttp.ClientSession()
        page_ws = await self._wait_for_page_target()
        ws = await self._http.ws_connect(page_ws, max_msg_size=0)
        self.page = CDPSession(self._http, ws)
        await self.page.enable_domains()

    async def _wait_for_page_target(self, timeout: float = 30.0) -> str:
        """Чекає, поки браузер підніме debug-endpoint, і повертає URL вкладки."""
        assert self._http is not None
        base = f"http://127.0.0.1:{self._port}"
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise BrowserException(
                    f"Браузер завершився одразу (код {self._process.returncode}). "
                    "Найімовірніша причина — той самий профіль уже відкритий в іншому вікні."
                )
            try:
                async with self._http.get(f"{base}/json/list") as response:
                    targets = await response.json()
                for target in targets:
                    if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                        return target["webSocketDebuggerUrl"]
            except (aiohttp.ClientError, OSError):
                pass
            await asyncio.sleep(0.2)
        raise BrowserException("Браузер не відкрив debug-порт вчасно")

    async def stop(self) -> None:
        if self.page is not None:
            await self.page.close()
            self.page = None
        if self._http is not None:
            await self._http.close()
            self._http = None
        if self._process is not None:
            with suppress(Exception):
                self._process.terminate()
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            self._process = None
