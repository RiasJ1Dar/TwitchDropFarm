"""Дрібні незалежні інструменти: очікування, повтори, збереження стану.

Нічого, зав'язаного на Twitch чи на інтерфейс, тут бути не повинно — усе, що
лежить у цьому файлі, має бути придатним до вживання в будь-якому іншому проєкті.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import re
import string
import sys
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Mapping
from datetime import datetime, timezone
from enum import Enum
from functools import cached_property, wraps
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar

from yarl import URL

log = logging.getLogger("TwitchDrops")


def rotating_log_handler(
    path: Path, *, max_bytes: int, backups: int, formatter: logging.Formatter
) -> logging.Handler:
    """Файловий журнал зі стелею розміру.

    Окремою функцією, а не рядком у `main`, з двох причин. По-перше, це єдиний
    спосіб перевірити ротацію виконанням — без неї лишається вірити, що
    параметри правильні. По-друге, на Windows перейменування під час ротації
    може не вдатись, якщо файл ще тримає інший процес: так буває на `/reboot`,
    коли новий екземпляр уже піднявся, а старий ще не дописав. `logging` таку
    помилку не пропускає нагору, тож фарм від неї не постраждає — але знати про
    неї варто саме тут.
    """
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    return handler


T = TypeVar("T")
D = TypeVar("D")

ALPHANUMERIC = string.ascii_letters + string.digits
HEX_LOWER = string.digits + "abcdef"


# ---------------------------------------------------------------- дрібниці

def random_token(length: int, alphabet: str = HEX_LOWER) -> str:
    """Випадковий рядок для nonce та ідентифікаторів сесії."""
    return "".join(random.choices(alphabet, k=length))


def batched(items: Iterable[T], size: int) -> Iterator[list[T]]:
    """Ріже послідовність на шматки заданого розміру."""
    buffer: list[T] = []
    for item in items:
        buffer.append(item)
        if len(buffer) == size:
            yield buffer
            buffer = []
    if buffer:
        yield buffer


def parse_timestamp(text: str) -> datetime:
    """Розбирає час Twitch. Дробові секунди бувають, а бувають і ні."""
    for pattern in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Незрозумілий час від Twitch: {text!r}")


def describe_exception(error: BaseException) -> str:
    """Трасування у вигляді рядка.

    Передаємо сам виняток, а не трійку (тип, значення, трасування): стара
    трипараметрична форма з пропущеним третім аргументом на Python 3.10+ кидає
    ValueError — тобто код, що мав записати причину аварії, падав сам.
    """
    return "".join(traceback.format_exception(error))


def forget_cached(instance: object, *names: str) -> None:
    """Скидає значення `cached_property`."""
    for name in names:
        instance.__dict__.pop(name, None)


# ---------------------------------------------------------------- одна копія

def claim_single_instance(path: Path) -> tuple[bool, io.TextIOWrapper]:
    """Захоплює файл-замок. False означає, що програма вже запущена."""
    handle = path.open("w", encoding="utf8")
    handle.write("lock")
    handle.flush()
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False, handle
    return True, handle


# ---------------------------------------------------------------- фонові таски

def guard_task(
    func: Callable[..., Awaitable[Any]] | None = None, *, vital: bool = False
):
    """Логує падіння фонової таски; для `vital` — валить увесь застосунок.

    Мовчазна смерть фонової таски — найгірший сценарій: програма ніби працює,
    а хвилини не йдуть. Тому падіння завжди помітне, а критичне ще й фатальне.
    """
    def decorate(target: Callable[..., Awaitable[Any]]):
        @wraps(target)
        async def guarded(*args: Any, **kwargs: Any):
            try:
                return await target(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(f"Впала таска {target.__name__}")
                if vital:
                    owner = args[0] if args else None
                    stop = getattr(owner, "request_stop", None) or getattr(
                        getattr(owner, "_miner", None), "request_stop", None
                    )
                    if callable(stop):
                        stop()
                raise
        return guarded

    return decorate if func is None else decorate(func)


class TaskKeeper:
    """Тримає посилання на «випущені» таски до їхнього завершення.

    Без цього збирач сміття має право прибрати таску посеред виконання — а це
    втрачене повідомлення PubSub, тобто незабраний дроп.
    """

    def __init__(self) -> None:
        self._alive: set[asyncio.Task[Any]] = set()

    def launch(self, coro: Awaitable[Any]) -> asyncio.Task[Any] | None:
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError:
            return None  # немає активного циклу
        self._alive.add(task)
        task.add_done_callback(self._retire)
        return task

    def _retire(self, task: asyncio.Task[Any]) -> None:
        self._alive.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error("Фонова таска впала", exc_info=error)

    def cancel_all(self) -> None:
        for task in list(self._alive):
            task.cancel()

    async def drain(self) -> None:
        if self._alive:
            await asyncio.gather(*list(self._alive), return_exceptions=True)


async def race(*awaitables: Awaitable[Any]) -> Any:
    """Повертає результат того, хто завершився першим; решту скасовує."""
    tasks = [asyncio.ensure_future(item) for item in awaitables]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    return await next(iter(done))


# ---------------------------------------------------------------- очікування

class Slot(Generic[T]):
    """Комірка для значення, появи якого можна дочекатись."""

    def __init__(self) -> None:
        self._value: T | None = None
        self._filled = asyncio.Event()

    @property
    def filled(self) -> bool:
        return self._filled.is_set()

    def put(self, value: T) -> None:
        self._value = value
        self._filled.set()

    def clear(self) -> None:
        self._filled.clear()
        self._value = None

    def peek(self, default: D = None) -> T | D:  # type: ignore[assignment]
        return self._value if self._filled.is_set() else default

    async def take(self) -> T:
        await self._filled.wait()
        return self._value  # type: ignore[return-value]

    def wait(self) -> Awaitable[bool]:
        return self._filled.wait()


class Backoff:
    """Зростаюча пауза між спробами, з розкидом.

    Розкид тут не косметичний: без нього всі паралельні запити після збою
    прокидаються одночасно й б'ють по Twitch одним залпом.
    """

    def __init__(self, *, start: float = 1.0, factor: float = 2.0,
                 ceiling: float = 300.0, jitter: float = 0.1):
        self.start = start
        self.factor = factor
        self.ceiling = ceiling
        self.jitter = jitter
        self.attempt = 0

    def reset(self) -> None:
        self.attempt = 0

    def __iter__(self) -> Iterator[float]:
        return self

    def __next__(self) -> float:
        raw = self.start * (self.factor ** self.attempt)
        spread = random.uniform(1 - self.jitter, 1 + self.jitter)
        delay = min(raw * spread, self.ceiling)
        if raw < self.ceiling:
            self.attempt += 1
        return delay


class Throttle:
    """Не більше `limit` операцій за `window` секунд.

    Потрібен саме для GQL: Twitch болісно реагує на перевищення й відмовляє
    в обслуговуванні всьому клієнту, а не окремому запиту.
    """

    def __init__(self, *, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._used = 0
        self._gate = asyncio.Condition()
        self._reset_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> None:
        async with self._gate:
            await self._gate.wait_for(lambda: self._used < self.limit)
            self._used += 1
            if self._reset_task is None or self._reset_task.done():
                self._reset_task = asyncio.ensure_future(self._reset_after_window())

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def _reset_after_window(self) -> None:
        await asyncio.sleep(self.window)
        async with self._gate:
            self._used = 0
            self._gate.notify_all()


async def sleep_unless(event: asyncio.Event, seconds: float) -> bool:
    """Спить, доки не мине час або не спрацює подія. True — подія випередила."""
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


# ---------------------------------------------------------------- збереження

_TYPE_KEY = "__type"
_DROPPED = object()

_ENCODERS: dict[type, Callable[[Any], Any]] = {
    set: list,
    URL: str,
}
_DECODERS: dict[str, Callable[[Any], Any]] = {
    "set": set,
    "URL": URL,
    "datetime": lambda raw: datetime.fromtimestamp(raw, timezone.utc),
}


def register_enum(enum_type: type[Enum]) -> None:
    """Дозволяє зберігати конкретний Enum у налаштуваннях."""
    _DECODERS[enum_type.__name__] = enum_type


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return {_TYPE_KEY: "datetime", "data": stamp.timestamp()}
    if isinstance(value, Enum):
        return {_TYPE_KEY: type(value).__name__, "data": value.value}
    for kind, encoder in _ENCODERS.items():
        if isinstance(value, kind):
            return {_TYPE_KEY: kind.__name__, "data": encoder(value)}
    raise TypeError(f"Не знаю, як зберегти {type(value).__name__}")


def _decode(raw: dict[str, Any]) -> Any:
    kind = raw.get(_TYPE_KEY)
    if kind is None:
        return raw
    decoder = _DECODERS.get(kind)
    return decoder(raw["data"]) if decoder else _DROPPED


def _prune(value: Any) -> Any:
    """Викидає значення, які не вдалося розпізнати при читанні."""
    if isinstance(value, dict):
        cleaned = {k: _prune(v) for k, v in value.items() if v is not _DROPPED}
        return {k: v for k, v in cleaned.items() if v is not _DROPPED}
    return value


def _conform(value: Any, template: Any) -> Any:
    """Приводить прочитане до форми шаблону: чуже викидає, відсутнє додає."""
    if not isinstance(template, dict) or not isinstance(value, dict):
        return value if type(value) is type(template) else template
    return {
        key: _conform(value[key], default) if key in value else default
        for key, default in template.items()
    }


def load_json(path: Path, template: Mapping[str, Any]) -> dict[str, Any]:
    """Читає файл, віддаючи перевагу `.new` — захист від обриву на записі."""
    for candidate in (path.with_suffix(path.suffix + ".new"), path):
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf8") as handle:
                raw = json.load(handle, object_hook=_decode)
        except (json.JSONDecodeError, OSError):
            continue
        return _conform(_prune(raw), dict(template))
    return dict(template)


def save_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Пише атомарно: спершу у сусідній файл, потім підміна."""
    staging = path.with_suffix(path.suffix + ".new")
    with staging.open("w", encoding="utf8") as handle:
        json.dump(payload, handle, default=_encode, indent=4,
                  sort_keys=True, ensure_ascii=False)
    staging.replace(path)


# ---------------------------------------------------------------- гра

class Game:
    """Гра, як її бачить Twitch."""

    UNRESTRICTED: ClassVar[frozenset[int]] = frozenset()

    __slots__ = ("__dict__", "id", "name")

    def __init__(self, payload: Mapping[str, Any]):
        self.id = int(payload["id"])
        self.name: str = payload.get("displayName") or payload["name"]
        if slug := payload.get("slug"):
            self.__dict__["slug"] = slug

    @cached_property
    def slug(self) -> str:
        """Назва у вигляді, придатному для адрес каталогу Twitch."""
        text = self.name.lower().replace("'", "")
        text = re.sub(r"\W+", "-", text)
        return re.sub(r"-{2,}", "-", text).strip("-")

    @property
    def any_channel(self) -> bool:
        """Кампанії цієї гри зараховуються з будь-якого каналу."""
        return self.id in self.UNRESTRICTED

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Game) and other.id == self.id

    def __hash__(self) -> int:
        return self.id

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"Game({self.id}, {self.name!r})"


__all__ = [
    "ALPHANUMERIC", "HEX_LOWER", "AsyncIterator", "Backoff", "Game", "Slot",
    "TaskKeeper", "Throttle", "batched", "claim_single_instance",
    "describe_exception", "forget_cached", "guard_task", "load_json",
    "parse_timestamp", "race", "random_token", "register_enum", "save_json",
    "sleep_unless",
]
