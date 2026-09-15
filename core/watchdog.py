"""Сторожові механізми ядра Twitch Drop Farm.

Модуль відповідає за:
- виявлення затримки/застою нарахування хвилин (`StallDetector`, `check_stall`)
- тимчасовий відстійник для проблемних каналів (`ChannelBench`)
- реєстрацію збоїв доставки перегляду (`note_delivery_failed`, `note_delivery_ok`)
- перевірку життєздатності persisted-запитів до GraphQL Twitch (`ProtocolWatchdog`)
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from time import monotonic
from typing import Any

from core import protocol
from core.api import Aborted, ApiError
from core.config import (
    CHANNEL_BENCH_SECONDS,
    PROTOCOL_PROBE_PAUSE,
    STALL_LIMIT,
    TRACE,
)
from core.events import ProgressStalled, ProtocolStale, WatchUncounted
from core.i18n import t
from core.toolbox import describe_exception

log = logging.getLogger("TwitchDrops")


class ChannelBench:
    """Відстійник для каналів, які підвели при доставці перегляду."""

    def __init__(self, benched: dict[str, float] | None = None) -> None:
        self.benched: dict[str, float] = benched if benched is not None else {}

    def is_benched(self, channel_name: str, *, now: float | None = None) -> bool:
        """Чи сидить канал у відстійнику після того, як підвів."""
        until = self.benched.get(channel_name)
        if until is None:
            return False
        current_time = monotonic() if now is None else now
        if current_time >= until:
            del self.benched[channel_name]
            return False
        return True

    def bench(
        self,
        channel_name: str,
        *,
        duration: int = CHANNEL_BENCH_SECONDS,
        now: float | None = None,
    ) -> None:
        """Відкладає канал: він щойно довів, що хвилини через нього не йдуть."""
        current_time = monotonic() if now is None else now
        self.benched[channel_name] = current_time + duration
        log.warning(
            f"Канал {channel_name} відкладено на "
            f"{duration // 60} хв: хвилини не зараховуються"
        )


class StallDetector:
    """Детектор застою прогресу (хвилини не нараховуються)."""

    def __init__(
        self,
        stall_since: float | None = None,
        stall_alerted: bool = False,
    ) -> None:
        self.stall_since = stall_since
        self.stall_alerted = stall_alerted

    def reset(self) -> None:
        self.stall_since = None
        self.stall_alerted = False

    def check(
        self,
        before: int | None,
        after: int | None,
        channel_name: str,
        events: Any,
        counted_elsewhere: str = "",
        *,
        now: float | None = None,
    ) -> bool:
        """Перевіряє, чи не зупинився прогрес на поточному каналі.

        Повертає True, якщо зафіксовано нове сповіщення про застій.
        """
        if after is None:
            return False  # нема чого фармити — це не застій
        clock = monotonic() if now is None else now
        # before is None — фарм щойно почався, порівнювати ще нема з чим
        if before is None or after != before:
            self.stall_since = clock
            self.stall_alerted = False
            return False
        started = self.stall_since
        if started is None:
            self.stall_since = clock
            return False
        elapsed = clock - started
        if elapsed >= STALL_LIMIT * 60 and not self.stall_alerted:
            self.stall_alerted = True
            minutes = max(STALL_LIMIT, int(elapsed // 60))
            events.status(t("status_stalled", minutes=minutes))
            events.emit(ProgressStalled(
                minutes_without_progress=minutes,
                channel_name=channel_name,
                counted_elsewhere=counted_elsewhere,
            ))
            return True
        return False


def note_delivery_failed(
    channel_name: str,
    current_failures: int,
    events: Any,
) -> tuple[int, bool]:
    """Реєструє збій доставки хвилини перегляду.

    Повертає кортеж (новий_лічильник, чи_потрібно_відкласти_канал).
    """
    failures = current_failures + 1
    log.warning(f"Хвилина не зарахувалась: {channel_name}")
    events.status(t("status_uncounted"))
    should_bench = False
    if failures == 2:
        events.emit(WatchUncounted(
            channel_name=channel_name,
            consecutive=failures,
        ))
        should_bench = True
    return failures, should_bench


def note_delivery_ok(
    channel_name: str,
    current_failures: int,
    events: Any,
) -> int:
    """Скидає лічильник невдалих спроб доставки при успіху."""
    if not current_failures:
        return 0
    events.status(t("status_watching", name=channel_name))
    return 0


def build_probe_variables(
    identity: Any,
    campaigns: Sequence[Any],
    watching_channel: Any | None,
    fallback_channel: Any | None,
) -> dict[str, dict[str, Any]]:
    """Живі значення для запитів, яким потрібні аргументи."""
    found: dict[str, dict[str, Any]] = {}
    login = str(getattr(identity, "user_id", ""))
    campaign = next((c for c in campaigns if getattr(c, "available_to_me", False)), None)
    if campaign is not None:
        found[protocol.CAMPAIGN_DETAILS.operation] = {
            "channelLogin": login,
            "dropID": campaign.id,
        }
        found[protocol.GAME_DIRECTORY.operation] = {
            "slug": campaign.game.slug,
        }
    channel = watching_channel or fallback_channel
    if channel is not None:
        found[protocol.CURRENT_DROP.operation] = {"channelID": str(channel.id)}
        found[protocol.CHANNEL_DROPS.operation] = {"channelID": str(channel.id)}
        found[protocol.STREAM_INFO.operation] = {"channel": channel.login}
    return found


async def query_alive(
    graphql_fn: Callable[..., Awaitable[Any]],
    query: protocol.Query,
    variables: dict[str, dict[str, Any]],
) -> bool:
    """False — Twitch не знає цього хеша просто зараз."""
    try:
        await graphql_fn(
            query(**variables.get(query.operation, {})), retries=0,
        )
    except ApiError as error:
        if "PersistedQueryNotFound" in str(error):
            return False
        # інші помилки — не про хеші: немає прав, немає даних, мережа
        log.log(TRACE, f"{query.operation}: {error}")
    except Aborted:
        raise
    except Exception as error:
        log.log(TRACE, f"{query.operation}: {describe_exception(error)}")
    return True


async def probe_protocol(
    graphql_fn: Callable[..., Awaitable[Any]],
    variables: dict[str, dict[str, Any]],
    pause: timedelta = PROTOCOL_PROBE_PAUSE,
) -> tuple[list[str], list[str]]:
    """Чи знає Twitch наші persisted-запити. Повертає (мертві, пропущені)."""
    suspects: list[protocol.Query] = []
    skipped: list[str] = []
    for query in protocol.READ_ONLY_QUERIES:
        needed = query.operation in {
            protocol.CAMPAIGN_DETAILS.operation,
            protocol.GAME_DIRECTORY.operation,
            protocol.CURRENT_DROP.operation,
            protocol.CHANNEL_DROPS.operation,
            protocol.STREAM_INFO.operation,
        }
        if needed and query.operation not in variables:
            skipped.append(query.operation)
            continue
        if not await query_alive(graphql_fn, query, variables):
            suspects.append(query)
    if not suspects:
        return [], skipped

    log.log(TRACE, f"Перепитую підозрюваних: {[q.operation for q in suspects]}")
    await asyncio.sleep(pause.total_seconds())
    dead = [
        query.operation for query in suspects
        if not await query_alive(graphql_fn, query, variables)
    ]
    return dead, skipped


async def watch_protocol(
    graphql_fn: Callable[..., Awaitable[Any]],
    variables: dict[str, dict[str, Any]],
    events: Any,
    pause: timedelta = PROTOCOL_PROBE_PAUSE,
) -> tuple[list[str], list[str]]:
    """Виконує перевірку та надсилає ProtocolStale у разі невідповідності хешів."""
    dead, skipped = await probe_protocol(graphql_fn, variables, pause=pause)
    if skipped:
        log.log(TRACE, f"Не перевірено (немає стану): {skipped}")
    if not dead:
        log.log(TRACE, "Запити Twitch на місці")
        return dead, skipped
    checked = len(protocol.READ_ONLY_QUERIES) - len(skipped)
    storm = checked > 1 and len(dead) == checked
    if storm:
        log.warning(
            "Twitch не знає жодного з наших запитів — схоже на скид кешу "
            "на його боці, а не на зміну хешів"
        )
    else:
        log.error(
            f"Twitch не знає запитів: {', '.join(dead)}. "
            f"Хеші в protocol.py треба оновити"
        )
    events.emit(ProtocolStale(operations=tuple(dead), storm=storm))
    return dead, skipped
