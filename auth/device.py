"""OAuth device code flow під Android-клієнт Twitch.

Чому саме device flow, а не «взяти cookie з браузера»: cookie `auth-token`, який
видає звичайний вхід на twitch.tv, прив'язаний до веб-client ID, а той вимагає
заголовок `Client-Integrity` з анти-ботом. Android-client ID integrity не вимагає,
і токен для нього видається саме цим шляхом.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, NamedTuple, Protocol

from yarl import URL

from core.protocol import OAUTH_DEVICE as AUTH_DEVICE_URL
from core.protocol import OAUTH_TOKEN as AUTH_TOKEN_URL

if TYPE_CHECKING:
    from core.miner import Miner as Twitch

    JsonType = dict


class ClientInfo(Protocol):
    """Те, що цей модуль просить від опису клієнта — не більше.

    Раніше тут стояв `core.protocol.TwitchClient`, і це була неправда: у нього
    поля `client_id` / `user_agents`, а звертаємось ми до `CLIENT_ID`,
    `CLIENT_URL`, `USER_AGENT`. Код працював лише тому, що `Miner._client_type`
    віддає шим саме з такими іменами — модуль входу писався для першої версії.
    Протокол фіксує справжній контракт, і тепер перевірка типів стежить за
    обома сторонами, а не мовчить про розбіжність.
    """

    CLIENT_ID: str
    CLIENT_URL: str
    USER_AGENT: str

logger = logging.getLogger("TwitchDrops")


class DeviceCode(NamedTuple):
    device_code: str
    user_code: str
    verification_uri: URL
    interval: int
    expires_at: datetime


def _headers(client_info: ClientInfo, device_id: str) -> JsonType:
    return {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US",
        "Cache-Control": "no-cache",
        "Client-Id": client_info.CLIENT_ID,
        "Host": "id.twitch.tv",
        "Origin": str(client_info.CLIENT_URL),
        "Pragma": "no-cache",
        "Referer": str(client_info.CLIENT_URL),
        "User-Agent": client_info.USER_AGENT,
        "X-Device-Id": device_id,
    }


async def request_device_code(twitch: Twitch) -> DeviceCode:
    client_info: ClientInfo = twitch._client_type
    device_id: str = twitch._auth_state.device_id
    now = datetime.now(timezone.utc)
    async with twitch.request(
        "POST",
        AUTH_DEVICE_URL,
        headers=_headers(client_info, device_id),
        data={"client_id": client_info.CLIENT_ID, "scopes": ""},
    ) as response:
        payload: JsonType = await response.json()
    return DeviceCode(
        device_code=payload["device_code"],
        user_code=payload["user_code"],
        verification_uri=URL(payload["verification_uri"]),
        interval=int(payload["interval"]),
        expires_at=now + timedelta(seconds=int(payload["expires_in"])),
    )


async def poll_for_token(twitch: Twitch, code: DeviceCode) -> str:
    """Опитує Twitch, доки користувач не підтвердить код.

    Поки код не підтверджено, Twitch відповідає 400 — це нормальний стан очікування,
    а не помилка.
    """
    client_info: ClientInfo = twitch._client_type
    device_id: str = twitch._auth_state.device_id
    payload = {
        "client_id": client_info.CLIENT_ID,
        "device_code": code.device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    while True:
        await asyncio.sleep(code.interval)
        async with twitch.request(
            "POST",
            AUTH_TOKEN_URL,
            headers=_headers(client_info, device_id),
            data=payload,
            valid_until=code.expires_at,
        ) as response:
            if response.status != 200:
                continue
            data: JsonType = await response.json()
        logger.info("Токен доступу отримано")
        return data["access_token"]
