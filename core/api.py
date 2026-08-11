"""Мережевий шар: HTTP, GraphQL і живучість при обривах.

У попередній версії все це жило всередині класу майнера разом зі стан-машиною
та авторизацією. Тут воно окремо, і це дає дві речі: логіку повторів можна
перевіряти без Twitch, а майнер не мусить знати про заголовки й backoff.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import monotonic
from typing import Any

import aiohttp

from core import protocol
from core.config import COOKIE_FILE, GQL_RETRIES, TRACE
from core.toolbox import Backoff, Throttle

log = logging.getLogger("TwitchDrops")
gql_log = logging.getLogger("TwitchDrops.gql")

# Помилки Twitch, які минають самі. `PersistedQueryNotFound` не поломка,
# а нормальний стан кешу запитів — і приходить сплеском одразу на всі
# паралельні запити, тому однієї повторної спроби замало.
TRANSIENT = frozenset({
    "service error",
    "service timeout",
    "service unavailable",
    "context deadline exceeded",
    "PersistedQueryNotFound",
})
# Помилка, після якої відповідь усе одно придатна: гілку, на яку вказує
# `path`, треба занулити й читати решту.
PARTIAL = "server error"

NETWORK_FAULTS = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError,
    OSError,
)


class ApiError(Exception):
    """Twitch відповів помилкою, з якою нічого не вдієш."""


class Aborted(Exception):
    """Запит перервано, бо застосунок завершується."""


class Stale(Exception):
    """Запит утратив сенс: те, заради чого він робився, протухло."""


def _loose_json(text: str) -> Any:
    """Twitch іноді дописує сміття після валідного JSON."""
    decoder = json.JSONDecoder()
    value, _end = decoder.raw_decode(text)
    return value


class TwitchApi:
    """Клієнт приватного API Twitch."""

    def __init__(
        self,
        *,
        client: protocol.TwitchClient,
        proxy: str = "",
        on_network_lost: Callable[[str, int], None] | None = None,
        on_network_back: Callable[[float, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        auth_headers: Callable[[], Awaitable[dict[str, str]]] | None = None,
    ):
        self.client = client
        self.user_agent = client.pick_user_agent()
        self.proxy = proxy or None
        self._session: aiohttp.ClientSession | None = None
        # Twitch болісно реагує на перевищення ліміту й відмовляє всьому
        # клієнту, а не окремому запиту. Значення підібрані з запасом.
        self._gql_gate = Throttle(limit=5, window=1.0)
        self._auth_headers = auth_headers
        self._on_lost = on_network_lost
        self._on_back = on_network_back
        self._should_stop = should_stop or (lambda: False)
        self._down_since: float | None = None
        self._failures = 0

    # ------------------------------------------------------------ сесія

    async def session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        jar = aiohttp.CookieJar()
        try:
            if COOKIE_FILE.exists():
                jar.load(COOKIE_FILE)
        except Exception:
            jar.clear()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(sock_connect=10, total=20),
            connector=aiohttp.TCPConnector(limit=50),
            cookie_jar=jar,
            headers={"User-Agent": self.user_agent},
        )
        return self._session

    async def close(self) -> None:
        if self._session is None:
            return
        jar = self._session.cookie_jar
        try:
            # aiohttp сам не прибирає порожні записи перед збереженням
            for key, value in list(jar._cookies.items()):  # type: ignore[attr-defined]
                if not value:
                    del jar._cookies[key]  # type: ignore[attr-defined]
            jar.save(COOKIE_FILE)
        except Exception:
            log.log(TRACE, "Не вдалося зберегти cookie")
        await self._session.close()
        self._session = None

    def forget_cookies(self) -> None:
        if self._session is not None:
            self._session.cookie_jar.clear()
        COOKIE_FILE.unlink(missing_ok=True)

    def cookie(self, name: str) -> str | None:
        if self._session is None:
            return None
        jar = self._session.cookie_jar.filter_cookies(protocol.TWITCH_HOME)
        found = jar.get(name)
        return found.value if found else None

    # ------------------------------------------------------------ стан мережі

    def _network_ok(self) -> None:
        if self._down_since is None:
            return
        downtime = monotonic() - self._down_since
        attempts = self._failures
        self._down_since = None
        self._failures = 0
        log.info(f"Зв'язок відновлено після {downtime:.0f}с")
        if self._on_back:
            self._on_back(downtime, attempts)

    def _network_failed(self, reason: str) -> None:
        self._failures += 1
        if self._down_since is None:
            self._down_since = monotonic()
        # перший збій буває випадковим; тривогу б'ємо з другого
        if self._failures == 2 and self._on_lost:
            log.warning(f"Втрачено зв'язок: {reason}")
            self._on_lost(reason, self._failures)

    @property
    def offline(self) -> bool:
        return self._down_since is not None

    # ------------------------------------------------------------ запити

    @asynccontextmanager
    async def request(self, method: str, url: str, *,
                      valid_until: datetime | None = None, **kwargs: Any):
        """HTTP-запит із повторами. Мережеві збої не пропускаються нагору."""
        session = await self.session()
        if self.proxy and "proxy" not in kwargs:
            kwargs["proxy"] = self.proxy
        budget = session.timeout.total or 0
        backoff = Backoff(ceiling=300.0)

        for delay in backoff:
            if self._should_stop():
                raise Aborted()
            if valid_until is not None:
                # враховуємо, що запит може завершитись уже після протухання
                deadline = valid_until.timestamp() - budget
                if datetime.now(timezone.utc).timestamp() >= deadline:
                    raise Stale()

            response = None
            try:
                response = await session.request(method.upper(), url, **kwargs)
                if response.status < 500:
                    await response.read()  # дочитуємо в межах контексту
                    self._network_ok()
                    yield response
                    return
                log.warning(f"Twitch віддав {response.status}, повтор через {delay:.0f}с")
            except aiohttp.ClientConnectorCertificateError:
                raise  # проблема з сертифікатом: повторювати марно
            except NETWORK_FAULTS as error:
                self._network_failed(type(error).__name__)
                log.log(TRACE, f"{type(error).__name__} на {url}, повтор через {delay:.0f}с")
            finally:
                if response is not None:
                    response.release()
            await asyncio.sleep(delay)

    async def fetch_text(self, url: str) -> str:
        async with self.request("GET", url) as response:
            return await response.text(encoding="utf8")

    async def post_form(self, url: str, body: dict[str, Any]) -> int:
        async with self.request("POST", url, data=body) as response:
            return response.status

    # ------------------------------------------------------------ GraphQL

    async def graphql(self, payload: Any) -> Any:
        """Запит (або пакет запитів) до GraphQL з розбором помилок Twitch."""
        retries = GQL_RETRIES
        backoff = Backoff(start=1.0, ceiling=60.0)

        for delay in backoff:
            async with self._gql_gate:
                headers = await self._auth_headers() if self._auth_headers else {}
                async with self.request(
                    "POST", protocol.GQL_ENDPOINT, json=payload, headers=headers
                ) as response:
                    body = await response.json(loads=_loose_json)

            verdict = self._inspect(body, retries)
            if verdict is None:
                return body
            retries -= 1
            gql_log.warning(f"Повторюю через «{verdict}» (лишилось {retries})")
            await asyncio.sleep(max(delay, 3.0))
        raise ApiError("Вичерпано спроби GraphQL")

    def _inspect(self, body: Any, retries_left: int) -> str | None:
        """None — відповідь придатна. Рядок — причина повторити."""
        for item in (body if isinstance(body, list) else [body]):
            if not isinstance(item, dict):
                continue
            if "error" in item:
                raise ApiError(f"{item['error']}: {item.get('message')}")
            problems = item.get("errors")
            if not problems:
                continue
            for problem in problems:
                message = problem.get("message")
                if message is None:
                    continue
                if message in TRANSIENT:
                    if retries_left > 0:
                        return message
                    raise ApiError(f"Twitch не відповідає: {message}")
                if message == PARTIAL:
                    self._blank_out(item.get("data"), problem.get("path") or [])
                    break
            else:
                raise ApiError(str(problems))
        return None

    @staticmethod
    def _blank_out(data: Any, path: list[str]) -> None:
        """Занулює гілку, на яку вказала помилка, щоб читати решту відповіді."""
        if not path or not isinstance(data, dict):
            return
        node = data
        for step in path[:-1]:
            node = node.get(step) if isinstance(node, dict) else None
            if node is None:
                return
        if isinstance(node, dict):
            node[path[-1]] = None
