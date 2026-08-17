"""Мінімальний клієнт Chrome DevTools Protocol поверх aiohttp.

Замінює Playwright. Причина — вимога «без додаткових рантаймів»: Playwright тягне
власний Node.js-драйвер, який доводиться пакувати в .exe. Нам від браузера потрібно
рівно чотири речі — відкрити сторінку, дочекатись завантаження, виконати JS і прочитати
cookie, — а це кілька викликів CDP.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any

import aiohttp

from core.exceptions import BrowserException

logger = logging.getLogger("TwitchDrops")


class CDPSession:
    """З'єднання з однією вкладкою браузера."""

    # `[Any]`: у aiohttp 3.14 `ClientWebSocketResponse` став генериком за типом
    # автопінгу, і `ws_connect` віддає `[Literal[True]]`. Нам цей параметр
    # байдужий — ми лише шлемо й читаємо кадри.
    def __init__(self, session: aiohttp.ClientSession,
                 ws: aiohttp.ClientWebSocketResponse[Any]):
        self._session = session
        self._ws = ws
        self._next_id = 0

    async def call(self, method: str, params: dict[str, Any] | None = None,
                   *, timeout: float = 30.0) -> dict[str, Any]:
        """Виклик методу CDP із очікуванням відповіді саме на нього."""
        self._next_id += 1
        want = self._next_id
        await self._ws.send_json({"id": want, "method": method, "params": params or {}})
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise BrowserException(f"CDP: таймаут очікування відповіді на {method}")
            raw = await asyncio.wait_for(self._ws.receive(), timeout=remaining)
            if raw.type is not aiohttp.WSMsgType.TEXT:
                raise BrowserException(f"CDP: з'єднання закрито під час {method}")
            data = json.loads(raw.data)
            # події browser'а нам нецікаві — чекаємо саме свій id
            if data.get("id") != want:
                continue
            if "error" in data:
                raise BrowserException(f"CDP {method}: {data['error']}")
            return data.get("result", {})

    async def enable_domains(self) -> None:
        await self.call("Page.enable")
        await self.call("Runtime.enable")

    async def evaluate(self, expression: str, *, timeout: float = 30.0) -> Any:
        """Виконує JS у сторінці й повертає значення."""
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            raise BrowserException(f"JS помилка: {details.get('text')}")
        return result.get("result", {}).get("value")

    async def navigate(self, url: str) -> None:
        await self.call("Page.navigate", {"url": url})

    async def current_url(self) -> str:
        return await self.evaluate("location.href") or ""

    async def wait_ready(self, timeout: float = 60.0) -> bool:
        """Чекає document.readyState == 'complete'."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                if await self.evaluate("document.readyState") == "complete":
                    return True
            except BrowserException:
                pass  # сторінка саме перезавантажується
            await asyncio.sleep(0.25)
        return False

    async def wait_for(self, js_condition: str, timeout: float,
                       poll: float = 1.0) -> bool:
        """Чекає, поки JS-вираз стане істинним. Повертає False на таймауті."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                if await self.evaluate(f"Boolean({js_condition})"):
                    return True
            except BrowserException:
                pass
            await asyncio.sleep(poll)
        return False

    async def get_cookies(self, urls: list[str]) -> dict[str, str]:
        result = await self.call("Network.getCookies", {"urls": urls})
        return {c["name"]: c["value"] for c in result.get("cookies", [])}

    async def close(self) -> None:
        with suppress(Exception):
            await self._ws.close()


# JS, який заповнює поле так, щоб React це помітив.
# Просте `input.value = "..."` React ігнорує: він читає значення через власний
# дескриптор, тому потрібен нативний сеттер плюс подія 'input'. Перевірено на
# живій сторінці twitch.tv/activate.
SET_REACT_INPUT = """
(function(selector, value) {
  const el = document.querySelector(selector);
  if (!el) return false;
  const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
})
"""
