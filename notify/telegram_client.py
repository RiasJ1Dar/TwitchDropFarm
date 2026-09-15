"""Клієнт Telegram Bot API: транспорт, HTTP-сесія, long-polling getUpdates."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from time import time
from typing import Any

import aiohttp

from core.config import TRACE as CALL
from core.i18n import t
from core.protocol import TELEGRAM_ENDPOINT as TELEGRAM_API
from core.toolbox import Backoff

logger = logging.getLogger("TwitchDrops")

MESSAGE_LIMIT = 3900


def _split_message(text: str) -> list[str]:
    """Ріже довгий текст по межах рядків, щоб не ламати HTML-розмітку."""
    if len(text) <= MESSAGE_LIMIT:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MESSAGE_LIMIT and current:
            parts.append(current)
            current = ""
        current += (line + "\n")
    if current.strip():
        parts.append(current)
    return parts


class TelegramClient:
    """Транспортний клієнт Telegram Bot API.

    Тримає власний стан: токен, сесію aiohttp, polling-таску, offset оновлень,
    кеш біографії та інтервали тротлінгу. Не знає про нутрощі TelegramNotifier
    чи стан майнера — спілкується через параметри та зворотні виклики.
    """

    def __init__(
        self,
        token: str = "",
        *,
        allowed_chat_ids: Callable[[], list[int]] | Sequence[int] | None = None,
        on_command: Callable[[int, str], Awaitable[None]] | None = None,
        default_keyboard: Callable[[], dict[str, Any]] | None = None,
        commands_provider: Callable[[], Sequence[tuple[str, str, str]]] | None = None,
        session: aiohttp.ClientSession | None = None,
        timeout: float = 70.0,
    ) -> None:
        self._token = token.strip()
        self._allowed_chat_ids = allowed_chat_ids
        self._on_command = on_command
        self._default_keyboard = default_keyboard
        self._commands_provider = commands_provider
        self._session = session
        self._timeout = timeout
        self._poll_task: asyncio.Task[None] | None = None
        self._offset: int = 0
        self._bio = ""
        self._last_routine: dict[str, float] = {}
        self._api_handler: Any = None

    # ------------------------------------------------------------ стан та властивості

    @property
    def token(self) -> str:
        return self._token

    @token.setter
    def token(self, value: str) -> None:
        self._token = value.strip()

    @property
    def allowed_chat_ids(self) -> list[int]:
        if callable(self._allowed_chat_ids):
            return list(self._allowed_chat_ids())
        if self._allowed_chat_ids is not None:
            return list(self._allowed_chat_ids)
        return []

    @property
    def session(self) -> aiohttp.ClientSession | None:
        return self._session

    @session.setter
    def session(self, value: aiohttp.ClientSession | None) -> None:
        self._session = value

    @property
    def offset(self) -> int:
        return self._offset

    @offset.setter
    def offset(self, value: int) -> None:
        self._offset = value

    @property
    def poll_task(self) -> asyncio.Task[None] | None:
        return self._poll_task

    @poll_task.setter
    def poll_task(self, value: asyncio.Task[None] | None) -> None:
        self._poll_task = value

    @property
    def bio(self) -> str:
        return self._bio

    # ------------------------------------------------------------ життєвий цикл

    async def start(self) -> None:
        """Відкриває клієнтську сесію, якщо її ще немає."""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )

    async def stop(self) -> None:
        """Зупиняє polling і закриває сесію."""
        self.stop_polling()
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------ HTTP Bot API

    @property
    def _api(self) -> Any:
        return self._api_handler if self._api_handler is not None else self._call_api

    @_api.setter
    def _api(self, value: Any) -> None:
        self._api_handler = value

    async def _call_api(self, method: str, **payload: Any) -> Any:
        """Виклик Bot API. `Any`, а не `dict`, бо result буває списком/булевим."""
        if self._session is None:
            return None
        url = TELEGRAM_API.format(token=self._token, method=method)
        try:
            async with self._session.post(url, json=payload) as response:
                data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(f"Telegram {method} не вдався: {type(exc).__name__}: {exc}")
            return None
        if not data.get("ok"):
            logger.warning(f"Telegram {method}: {data.get('description')}")
            return None
        return data.get("result")

    # ------------------------------------------------------------ надсилання

    async def send(
        self,
        text: str,
        *,
        chat_id: int | None = None,
        keyboard: bool = False,
        markup: dict[str, Any] | None = None,
    ) -> None:
        targets = [chat_id] if chat_id is not None else self.allowed_chat_ids
        payload: dict[str, Any] = {
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if markup is not None:
            payload["reply_markup"] = markup
        elif keyboard and self._default_keyboard is not None:
            payload["reply_markup"] = self._default_keyboard()
        for target in targets:
            for part in _split_message(text):
                chunk_payload = dict(payload, text=part, chat_id=target)
                if part is not text:
                    chunk_payload.pop("reply_markup", None)
                await self._api("sendMessage", **chunk_payload)

    async def register_commands(self) -> bool:
        """Реєструє команди бота в Telegram."""
        if self._commands_provider is None:
            return False
        catalog = self._commands_provider()
        result = await self._api(
            "setMyCommands",
            commands=[
                {"command": name, "description": description}
                for name, description, _args in catalog
            ],
        )
        if result:
            logger.info(f"Меню Telegram оновлено: {len(catalog)} команд")
            return True
        return False

    async def discover_chat_ids(self) -> list[int]:
        """Хто вже писав боту через getUpdates."""
        updates = await self._api("getUpdates", timeout=0)
        if not updates:
            return []
        found: list[int] = []
        for update in updates:
            message = update.get("message") or update.get("edited_message")
            if message and (cid := message["chat"]["id"]) not in found:
                found.append(cid)
        return found

    async def send_test(self, chat_ids: list[int] | None = None) -> None:
        targets = chat_ids if chat_ids is not None else self.allowed_chat_ids
        if not targets:
            found = await self.discover_chat_ids()
            print(
                "chat_id не задано. Підключи бота через Налаштування → "
                "Підключити бота… — майстер запише власника після перевірки."
            )
            if found:
                print(f"Хто вже писав боту (ще не власники): {found}")
            return
        await self.send(t("tg_test_ok"))
        print(f"Тестове повідомлення надіслано у чати: {targets}")

    async def send_document(self, data: bytes, *, filename: str, chat_id: int) -> None:
        """Кладе файл у чат через multipart/form-data."""
        if self._session is None or not data:
            return
        url = TELEGRAM_API.format(token=self._token, method="sendDocument")
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        form.add_field(
            "document", data,
            filename=filename,
            content_type="text/csv",
        )
        try:
            async with self._session.post(url, data=form) as response:
                answer = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            logger.warning(f"Telegram sendDocument не вдався: {type(exc).__name__}: {exc}")
            return
        if not answer.get("ok"):
            logger.warning(f"Telegram sendDocument: {answer.get('description')}")

    async def set_profile_photo(self, jpeg: bytes) -> bool:
        """Встановлює аватар бота через setMyProfilePhoto."""
        if self._session is None or not jpeg:
            return False
        url = TELEGRAM_API.format(token=self._token, method="setMyProfilePhoto")
        form = aiohttp.FormData()
        form.add_field(
            "photo",
            json.dumps({"type": "static", "photo": "attach://icon"}),
            content_type="application/json",
        )
        form.add_field(
            "icon", jpeg, filename="icon.jpg", content_type="image/jpeg",
        )
        try:
            async with self._session.post(url, data=form) as response:
                answer = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            logger.warning(
                f"Telegram setMyProfilePhoto не вдався: {type(exc).__name__}: {exc}"
            )
            return False
        if not answer.get("ok"):
            logger.warning(f"Telegram setMyProfilePhoto: {answer.get('description')}")
            return False
        return True

    # ------------------------------------------------------------ опис та біо

    async def set_bio(self, text: str) -> None:
        formatted = f"● {text}"[:120]
        if formatted == self._bio:
            return
        old_dot, new_dot = self._bio.find("·"), formatted.find("·")
        same_kind = (
            old_dot >= 0 and new_dot >= 0
            and self._bio[: old_dot + 1] == formatted[: new_dot + 1]
        )
        if same_kind and self._bio and not self._routine_allowed("bio", 15.0):
            return
        self._bio = formatted
        await self._api("setMyShortDescription", short_description=formatted)

    async def set_description(self, description: str | None = None) -> None:
        desc = description if description is not None else t("tg_description")[:512]
        await self._api("setMyDescription", description=desc)

    def _routine_allowed(self, key: str, min_interval: float = 60.0) -> bool:
        now = time()
        if now - self._last_routine.get(key, 0.0) < min_interval:
            return False
        self._last_routine[key] = now
        return True

    # ------------------------------------------------------------ polling

    def start_polling(self) -> None:
        """Запускає цикл команд під наглядом."""
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._poll_task.add_done_callback(self._poll_task_died)

    def stop_polling(self) -> None:
        """Зупиняє цикл команд."""
        if self._poll_task is not None:
            self._poll_task.remove_done_callback(self._poll_task_died)
            self._poll_task.cancel()
            self._poll_task = None

    def _poll_task_died(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            logger.error("Цикл команд Telegram завершився без причини — перезапускаю")
        else:
            logger.error("Цикл команд Telegram упав — перезапускаю", exc_info=exc)
        if self._session is not None and not self._session.closed:
            asyncio.get_running_loop().call_later(5, self._restart_poll)

    def _restart_poll(self) -> None:
        if self._session is not None and not self._session.closed:
            self.start_polling()

    async def _poll_loop(self) -> None:
        """Long-polling getUpdates."""
        backoff = Backoff(start=2.0, ceiling=60.0, factor=2.0)
        while True:
            try:
                updates = await self._api("getUpdates", offset=self._offset, timeout=60)
                if updates is None:
                    delay = next(backoff)
                    logger.debug(f"Telegram polling retry via backoff in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue
                backoff.reset()
                if not updates:
                    await asyncio.sleep(1)
                    continue
                logger.log(CALL, f"Telegram: отримано оновлень — {len(updates)}")
                for update in updates:
                    self._offset = max(self._offset, update.get("update_id", 0) + 1)
                    try:
                        await self._process_update(update)
                    except Exception:
                        logger.exception("Помилка обробки оновлення Telegram")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Помилка полінгу Telegram")
                await asyncio.sleep(5)

    async def _process_update(self, update: dict[str, Any]) -> None:
        if (callback := update.get("callback_query")) is not None:
            await self._process_callback(callback)
            return
        message = update.get("message") or update.get("edited_message")
        if not message or "text" not in message:
            return
        chat_id = message["chat"]["id"]
        if chat_id not in self.allowed_chat_ids:
            logger.warning(
                f"Відхилено команду з чужого chat_id {chat_id}: "
                f"{message['text'][:60]!r}"
            )
            return
        text = message["text"].strip()
        if self._on_command is not None:
            await self._on_command(chat_id, text)
        logger.log(CALL, f"Telegram: команда {text[:40]!r} від {chat_id} — оброблено")

    async def _process_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        command = callback.get("data", "")
        await self._api("answerCallbackQuery", callback_query_id=callback["id"])
        if chat_id is None or chat_id not in self.allowed_chat_ids:
            logger.warning(f"Відхилено натискання з чужого chat_id {chat_id}")
            return
        logger.log(CALL, f"Telegram: кнопка «{command}» від {chat_id}")
        if self._on_command is not None:
            await self._on_command(chat_id, f"/{command}")
