"""Приватний API Twitch: усе, що ми про нього знаємо.

Цей модуль свідомо відокремлений від решти, бо його вміст — **факти**, а не рішення.
Хеші persisted-запитів, назви PubSub-топіків, форма події `minute-watched`, ідентифікатори
клієнтів — усе це спостерігається в інструментах розробника будь-якого браузера на
twitch.tv. Їх не можна «написати краще»: будь-яка зміна символу перетворює запит
на `PersistedQueryNotFound`.

Решта коду може змінюватись як завгодно; цей файл змінюється лише тоді, коли
змінюється сам Twitch.
"""
from __future__ import annotations

import gzip
import json
import random
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

JsonDict = dict[str, Any]

# ---------------------------------------------------------------- адреси

GQL_ENDPOINT = "https://gql.twitch.tv/gql"
PUBSUB_ENDPOINT = "wss://pubsub-edge.twitch.tv/v1"
TWITCH_HOME = "https://www.twitch.tv"
OAUTH_DEVICE = "https://id.twitch.tv/oauth2/device"
OAUTH_TOKEN = "https://id.twitch.tv/oauth2/token"
OAUTH_VALIDATE = "https://id.twitch.tv/oauth2/validate"
TELEGRAM_ENDPOINT = "https://api.telegram.org/bot{token}/{method}"


# ---------------------------------------------------------------- клієнти

@dataclass(frozen=True)
class TwitchClient:
    """Ким ми відрекомендовуємось Twitch.

    ANDROID — робочий вибір, і не з естетичних міркувань: веб-клієнт вимагає
    заголовок `Client-Integrity`, а токен, отриманий програмно, Twitch позначає
    `is_bad_bot` і анулює запити з ним. Android-клієнт integrity не вимагає взагалі.
    """

    client_id: str
    user_agents: tuple[str, ...]
    home: str = TWITCH_HOME

    def pick_user_agent(self) -> str:
        return random.choice(self.user_agents)


ANDROID = TwitchClient(
    client_id="kd1unb4b3q4t58fwlpcbzcbnm76a8fp",
    user_agents=(
        "Dalvik/2.1.0 (Linux; U; Android 16; SM-S911B Build/TP1A.220624.014) "
        "tv.twitch.android.app/25.3.0/2503006",
        "Dalvik/2.1.0 (Linux; U; Android 16; SM-S938B Build/BP2A.250605.031) "
        "tv.twitch.android.app/25.3.0/2503006",
        "Dalvik/2.1.0 (Linux; Android 16; SM-X716N Build/UP1A.231005.007) "
        "tv.twitch.android.app/25.3.0/2503006",
        "Dalvik/2.1.0 (Linux; U; Android 15; SM-G990B Build/AP3A.240905.015.A2) "
        "tv.twitch.android.app/25.3.0/2503006",
        "Dalvik/2.1.0 (Linux; U; Android 15; SM-G970F Build/AP3A.241105.008) "
        "tv.twitch.android.app/25.3.0/2503006",
    ),
)

WEB = TwitchClient(
    client_id="kimne78kx3ncx6brgo4mv6wki5h1ko",
    user_agents=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    ),
)


# ---------------------------------------------------------------- GQL

@dataclass(frozen=True)
class Query:
    """Persisted-запит Twitch: ім'я операції плюс sha256 її тексту.

    Виклик екземпляра будує готовий payload. Так змінні не «прилипають» до
    визначення запиту — кожен виклик самодостатній, і немає спокуси випадково
    поділити стан між двома запитами.
    """

    operation: str
    sha256: str
    defaults: JsonDict = field(default_factory=dict)

    def __call__(self, **variables: Any) -> JsonDict:
        merged = _deep_merge(self.defaults, variables)
        payload: JsonDict = {
            "operationName": self.operation,
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": self.sha256}},
        }
        if merged:
            payload["variables"] = merged
        _assert_filled(merged, self.operation)
        return payload


def _deep_merge(base: JsonDict, override: JsonDict) -> JsonDict:
    """Зливає словники вглиб, не чіпаючи вихідні."""
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = value
    return result


REQUIRED = ...  # маркер обов'язкової змінної запиту


def _assert_filled(variables: JsonDict, operation: str, path: str = "") -> None:
    """Ловить незаповнену обов'язкову змінну тут, а не у вигляді 400 від Twitch."""
    for key, value in variables.items():
        where = f"{path}.{key}" if path else key
        if value is REQUIRED:
            raise ValueError(f"{operation}: не задано змінну «{where}»")
        if isinstance(value, dict):
            _assert_filled(value, operation, where)


# Хеші нижче — фіксовані значення Twitch. Ім'я зліва наше, решта — його.
INVENTORY = Query(
    "Inventory",
    "8337eb8541b314040b0edde0c09c5c7a2783ba1960aa9edfbf3bac16d0fec404",
    {"fetchRewardCampaigns": False},
)
CAMPAIGN_LIST = Query(
    "ViewerDropsDashboard",
    "d9cae7761dafab85908c85e6683cb4201b449e66ac3bb5e894f15ff12aeafaa7",
    {"fetchRewardCampaigns": False},
)
CAMPAIGN_DETAILS = Query(
    "DropCampaignDetails",
    "039277bf98f3130929262cc7c6efd9c141ca3749cb6dca442fc8ead9a53f77c1",
    {"channelLogin": REQUIRED, "dropID": REQUIRED},
)
CURRENT_DROP = Query(
    "DropCurrentSessionContext",
    "4d06b702d25d652afb9ef835d2a550031f1cf762b193523a92166f40ea3d142b",
    {"channelID": REQUIRED, "channelLogin": ""},
)
CLAIM_DROP = Query(
    "DropsPage_ClaimDropRewards",
    "a455deea71bdc9015b78eb49f4acfbce8baa7ccbedd28e549bb025bd0f751930",
    {"input": {"dropInstanceID": REQUIRED}},
)
CHANNEL_DROPS = Query(
    "DropsHighlightService_AvailableDrops",
    "782dad0f032942260171d2d80a654f88bdd0c5a9dddc392e9bc92218a0f42d20",
    {"channelID": REQUIRED},
)
STREAM_INFO = Query(
    "VideoPlayerStreamInfoOverlayChannel",
    "198492e0857f6aedead9665c81c5a06d67b25b58034649687124083ff288597d",
    {"channel": REQUIRED},
)
GAME_DIRECTORY = Query(
    "DirectoryPage_Game",
    "86bcceb4e8b1a51256ff8eed8bd8aae4acacf80d737efe904f84f3aeadf8cafd",
    {
        "limit": 30,
        "slug": REQUIRED,
        "imageWidth": 50,
        "includeCostreaming": False,
        "sortTypeIsRecency": False,
        "options": {
            "broadcasterLanguages": [],
            "freeformTags": None,
            "includeRestricted": ["SUB_ONLY_LIVE"],
            "recommendationsContext": {"platform": "web"},
            "sort": "RELEVANCE",
            "systemFilters": [],
            "tags": [],
            "requestID": "JIRA-VXP-2397",
        },
    },
)
CLAIM_POINTS = Query(
    "ClaimCommunityPoints",
    "46aaeebe02c99afdf4fc97c7c0cba964124bf6b0af229395f1f6d1feed05b3d0",
    {"input": {"claimID": REQUIRED, "channelID": REQUIRED}},
)
DROP_NOTIFICATION_DELETE = Query(
    "OnsiteNotifications_DeleteNotification",
    "13d463c831f28ffe17dccf55b3148ed8b3edbbd0ebadd56352f1ff0160616816",
    {"input": {"id": REQUIRED}},
)


# ---------------------------------------------------------------- PubSub

# Топіки рівня користувача — підписка одна на весь застосунок.
USER_TOPICS = {
    "drops": "user-drop-events",
    "notifications": "onsite-notifications",
    "points": "community-points-user-v1",
}
# Топіки рівня каналу — по дві підписки на кожен канал у полі зору.
CHANNEL_TOPICS = {
    "state": "video-playback-by-id",
    "settings": "broadcast-settings-update",
}


def topic_name(group: dict[str, str], key: str, target_id: int) -> str:
    return f"{group[key]}.{target_id}"


# ---------------------------------------------------------------- перегляд

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def watch_event(
    *, broadcast_id: int, channel_id: int, channel_login: str,
    game_id: int | None, game_name: str | None, user_id: int,
) -> list[JsonDict]:
    """Подія «переглянуто хвилину» — та сама, яку шле звичайний плеєр.

    Саме вона й змушує Twitch зараховувати час. Склад полів фіксований: зайве
    або пропущене поле обертається мовчазним нулем у лічильнику.
    """
    return [{
        "event": "minute-watched",
        "properties": {
            "broadcast_id": str(broadcast_id),
            "channel_id": str(channel_id),
            "channel": channel_login,
            "client_time": _now_iso(),
            "game": game_name or "",
            "game_id": str(game_id) if game_id is not None else "",
            "hidden": False,
            "is_live": True,
            "live": True,
            "logged_in": True,
            "minutes_logged": 1,
            "muted": False,
            "user_id": user_id,
        },
    }]


def _compact(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"))


def spade_body(event: list[JsonDict]) -> JsonDict:
    """Тіло для spade-ендпоінта: base64 від компактного JSON."""
    return {"data": b64encode(_compact(event).encode("utf8")).decode("utf8")}


def spade_mutation(event: list[JsonDict]) -> JsonDict:
    """Той самий перегляд, але через GQL — запасний шлях, коли spade недоступний."""
    return {
        "query": (
            "\n mutation SendEvents($input: SendSpadeEventsInput!) "
            "{\n sendSpadeEvents(input: $input) {\n statusCode\n}\n}\n"
        ),
        "variables": {
            "input": {
                "data": b64encode(gzip.compress(_compact(event).encode("utf8"))).decode("utf8"),
                "repository": "twilight",
                "encoding": "GZIP_B64",
            }
        },
    }


# Витяг адреси spade зі сторінки стрімера: або прямо в HTML, або в підключеному
# конфігу. Twitch періодично міняє, де саме вона лежить, тому шукаємо обидва.
SPADE_IN_PAGE = r'"spade_?url": ?"(https://[.\w\-/]+)"'
SETTINGS_SCRIPT = r'src="(https://[\w.]+/config/settings\.[0-9a-f]{32}\.js)"'

# Ігри, для яких Twitch не прив'язує кампанію до конкретного каналу.
GAMES_WITHOUT_CHANNEL_LIMIT = frozenset({509663, 509672})
