"""Telegram: сповіщення про події майнера і повне керування з телефона.

Нових залежностей не додає — Bot API це звичайні HTTPS-запити через уже наявний aiohttp.

Безпека: команди приймаються **лише** від chat_id зі списку в налаштуваннях. Бот із
повним керуванням, доступний будь-кому, хто його знайде, — це чужий доступ до твого
акаунта Twitch, тому перевірка жорстка й без винятків.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import time
from typing import TYPE_CHECKING, Any

import aiohttp

from core.config import TRACE as CALL
from core.events import (
    CampaignAppeared,
    CampaignFinished,
    Command,
    CommandType,
    ConnectionLost,
    ConnectionRestored,
    DeadlineRisk,
    DropClaimed,
    Event,
    LoginRequired,
    MinerError,
    MinerStarted,
    MinerStopped,
    ProgressStalled,
    ProtocolStale,
    StatusChanged,
    StreamOffline,
    UpdateAvailable,
    UpdateFailed,
    WatchingChanged,
    WatchUncounted,
)
from core.protocol import TELEGRAM_ENDPOINT as TELEGRAM_API
from core.toolbox import plural

if TYPE_CHECKING:
    from core.miner import Miner as Twitch

logger = logging.getLogger("TwitchDrops")

# Єдине джерело правди для команд: з нього збирається і меню Telegram (setMyCommands),
# і текст довідки. Інакше вони неминуче розходяться з тим, що насправді робить код.
COMMANDS: tuple[tuple[str, str, str], ...] = (
    # (команда, опис для меню Telegram, підказка про аргументи для /help)
    ("status", "що зараз фармиться", ""),
    ("inventory", "прогрес по дропах", ""),
    ("campaigns", "список кампаній", ""),
    ("pause", "призупинити фарм", ""),
    ("resume", "продовжити фарм", ""),
    ("switch", "перемкнутись на канал", " &lt;канал&gt;"),
    ("priority", "керувати пріоритетом ігор", " add|remove &lt;гра&gt;"),
    ("watch", "слідкувати за новими кампаніями гри", " add|remove &lt;гра&gt;"),
    ("report", "звіт за тиждень", " [днів]"),
    ("export", "зберегти історію та інвентар", ""),
    ("update", "поставити оновлення (лише змінені файли)", ""),
    ("reload", "перечитати інвентар", ""),
    ("hide", "згорнути вікно в трей", ""),
    ("show", "розгорнути вікно", ""),
    ("reboot", "повністю перезапустити програму", ""),
    ("menu", "показати панель кнопок", ""),
    ("help", "довідка по командах", ""),
)

HELP_TEXT = "<b>Команди</b>\n" + "\n".join(
    f"/{name}{args} — {description}" for name, description, args in COMMANDS
)

# Панель керування. Рядки — як лягають кнопки в Telegram.
# Команди з аргументами (/switch, /priority) сюди не потрапляють: кнопка не має
# куди прийняти назву каналу чи гри, тож вони лишаються текстовими.
CONTROL_BUTTONS: tuple[tuple[tuple[str, str], ...], ...] = (
    (("📊 Стан", "status"), ("🎒 Дропи", "inventory")),
    (("📋 Кампанії", "campaigns"), ("📈 Звіт", "report")),
    (("⏸ Пауза", "pause"), ("▶️ Продовжити", "resume")),
    (("🙈 Сховати вікно", "hide"), ("🖥 Показати вікно", "show")),
    (("🔄 Оновити", "reload"), ("♻️ Перезапуск", "reboot")),
    (("💾 Експорт", "export"), ("❓ Довідка", "help")),
)

# Напис кнопки -> команда. Клавіатура під полем вводу шле саме текст напису,
# а не службові дані, тож переклад потрібен на вході.
BUTTON_COMMANDS: dict[str, str] = {
    label: command for row in CONTROL_BUTTONS for label, command in row
}


# Telegram відхиляє повідомлення понад 4096 символів помилкою, а не обрізає його.
# Довгий інвентар через це просто не доходив би, і мовчки.
MESSAGE_LIMIT = 3900

# Кнопки під повідомленням про оновлення. Саме інлайн, а не панель: питання
# разове й прив'язане до конкретної версії, а рішення про перезапуск програми
# людина має ухвалити свідомо — і мати право сказати «не зараз».
UPDATE_BUTTONS: dict = {
    "inline_keyboard": [[
        {"text": "⬆️ Оновити й перезапустити", "callback_data": "update"},
        {"text": "⏳ Відкласти", "callback_data": "later"},
    ]],
}


# ================================================================ майстер налаштування
#
# Окремо від `TelegramNotifier`: майстер працює з токеном, якого в налаштуваннях
# ще немає, і має відповісти «цей токен живий?» до того, як щось збережеться.
# Нотифаєр же піднімає довгу сесію й читає токен із конфігу — для перевірки
# чужого рядка він не годиться.

async def _probe(token: str, method: str, **payload: Any) -> tuple[Any, str]:
    """Один короткий виклик Bot API. Повертає (результат, помилка-для-людини)."""
    if not token.strip():
        return None, "Токен порожній."
    url = TELEGRAM_API.format(token=token.strip(), method=method)
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url, json=payload) as response,
        ):
            data = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return None, f"Немає зв'язку з Telegram: {type(exc).__name__}"
    except ValueError:
        return None, "Telegram відповів не по-людськи — схоже, токен зіпсований."
    if not data.get("ok"):
        # 401 при кривому токені — найчастіша помилка, і опис у неї невиразний
        description = data.get("description", "")
        if "unauthorized" in description.lower():
            return None, "Telegram не знає такого токена. Скопіюй його ще раз."
        return None, f"Telegram відмовив: {description or 'без пояснень'}"
    return data.get("result"), ""


async def check_token(token: str) -> tuple[str, str]:
    """Чи живий токен. Повертає (@ім'я бота, помилка)."""
    result, error = await _probe(token, "getMe")
    if error:
        return "", error
    return (result or {}).get("username", ""), ""


async def find_chats(token: str) -> tuple[list[tuple[int, str]], str]:
    """Хто вже писав боту: [(chat_id, підпис)]. Порожньо — ще ніхто."""
    result, error = await _probe(token, "getUpdates", timeout=0)
    if error:
        return [], error
    found: list[tuple[int, str]] = []
    seen: set[int] = set()
    for update in result or []:
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        chat = message["chat"]
        chat_id = chat["id"]
        if chat_id in seen:
            continue
        seen.add(chat_id)
        name = chat.get("username") or " ".join(
            bit for bit in (chat.get("first_name"), chat.get("last_name")) if bit
        ) or chat.get("title") or str(chat_id)
        found.append((chat_id, name))
    return found, ""


async def send_greeting(token: str, chat_id: int) -> str:
    """Вітальне повідомлення в чат. Повертає помилку або порожній рядок."""
    _, error = await _probe(
        token, "sendMessage", chat_id=chat_id,
        text="✅ Бот підключено. Майнер Twitch drops на зв'язку.",
        reply_markup=control_keyboard(),
    )
    return error


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


def control_keyboard() -> dict:
    """Клавіатура під полем вводу — постійна панель керування.

    Саме `keyboard`, а не `inline_keyboard`: інлайн-кнопки живуть усередині
    конкретного повідомлення й губляться, щойно чат прокрутили. Ця ж панель
    висить під полем вводу постійно, і кнопка завжди під рукою.
    """
    return {
        "keyboard": [
            [{"text": label} for label, _command in row]
            for row in CONTROL_BUTTONS
        ],
        "resize_keyboard": True,   # кнопки по висоті тексту, а не на пів екрана
        "is_persistent": True,     # не ховати після натискання
        "input_field_placeholder": "Або команда: /switch канал",
    }


def _file_bytes(path: Path) -> bytes:
    """Читання файлу винесено з async, щоб не блокувати цикл і не дратувати лінтер."""
    try:
        return path.read_bytes()
    except OSError:
        return b""


class TelegramNotifier:
    def __init__(self, twitch: Twitch):
        self._twitch = twitch
        self._settings = twitch.settings
        self._session: aiohttp.ClientSession | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._report_task: asyncio.Task[None] | None = None
        self._offset: int = 0
        self._unsubscribe: Any = None
        # троттлінг рутини: не частіше ніж раз на стільки секунд для того самого типу
        self._last_routine: dict[str, float] = {}
        self._bio = ""

    # ------------------------------------------------------------ життєвий цикл

    @property
    def _config(self) -> dict[str, Any]:
        return self._settings.telegram

    @property
    def _chat_ids(self) -> list[int]:
        return list(self._config["chat_ids"])

    async def start(self) -> None:
        if not self._config["bot_token"]:
            logger.warning("Telegram увімкнено, але bot_token порожній")
            return
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=70)  # long-polling до 60с + запас
        )
        self._unsubscribe = self._twitch.events.subscribe(self._on_event)
        if self._config["allow_control"]:
            await self.register_commands()
            if self._chat_ids:
                self._start_poll_task()
            else:
                # без білого списку раніше брали першого, хто написав боту —
                # то був чужий доступ до акаунта Twitch. Власника ставить лише майстер.
                logger.warning(
                    "Керування з Telegram вимкнено: не задано жодного chat_id. "
                    "Підключи бота в Налаштуваннях — майстер сам запише chat_id."
                )
        if self._config["report_every_hours"] > 0:
            self._report_task = asyncio.create_task(self._report_loop())
        logger.info("Telegram-сповіщення активні")
        await self._ensure_profile_photo()
        await self._set_bio("● Чекає")

    async def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        # знімаємо наглядача першим, інакше він перезапустить те, що ми зупиняємо
        if self._poll_task is not None:
            self._poll_task.remove_done_callback(self._poll_task_died)
        for task in (self._poll_task, self._report_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._poll_task = self._report_task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------ транспорт

    async def _api(self, method: str, **payload: Any) -> Any:
        """Виклик Bot API. `Any`, а не `dict`, — і це не лінь.

        `result` у Telegram різного типу залежно від методу: `getUpdates` віддає
        **список** оновлень, `getMe` — обʼєкт, `setMyCommands` — `true`. Тип
        `dict | None` тут був просто неправдою: перевірка типів через нього
        показувала «"str" has no attribute "get"» у місцях, де насправді все
        гаразд, і водночас не помітила б справжньої плутанини.
        """
        if self._session is None:
            return None
        url = TELEGRAM_API.format(token=self._config["bot_token"], method=method)
        try:
            async with self._session.post(url, json=payload) as response:
                data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # Telegram лежить — це не привід зупиняти майнінг, але й мовчати не можна.
            # Раніше тут був debug, і канал керування міг падати щосекунди абсолютно
            # нечутно: бот «не реагує», а в лозі порожньо.
            logger.warning(f"Telegram {method} не вдався: {type(exc).__name__}: {exc}")
            return None
        if not data.get("ok"):
            logger.warning(f"Telegram {method}: {data.get('description')}")
            return None
        return data.get("result")

    async def send(
        self, text: str, *, chat_id: int | None = None, keyboard: bool = False,
        markup: dict | None = None,
    ) -> None:
        targets = [chat_id] if chat_id is not None else self._chat_ids
        payload: dict[str, Any] = {
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        # `markup` — разова інлайн-клавіатура під конкретним повідомленням
        # (питання «оновити чи відкласти»), `keyboard` — постійна панель.
        if markup is not None:
            payload["reply_markup"] = markup
        elif keyboard:
            payload["reply_markup"] = control_keyboard()
        for target in targets:
            # Telegram обриває повідомлення понад 4096 символів помилкою, а не
            # усіченням, тож ріжемо самі — інакше довгий інвентар зникав би мовчки.
            for part in _split_message(text):
                chunk_payload = dict(payload, text=part, chat_id=target)
                if part is not text:
                    chunk_payload.pop("reply_markup", None)
                await self._api("sendMessage", **chunk_payload)

    async def register_commands(self) -> bool:
        """Показує команди в меню Telegram (кнопка «/» у полі вводу).

        Викликається на кожному старті: список у меню має відповідати тому, що код
        насправді вміє, а не тому, що колись вписали руками в BotFather.
        """
        result = await self._api(
            "setMyCommands",
            commands=[
                {"command": name, "description": description}
                for name, description, _args in COMMANDS
            ],
        )
        if result:
            logger.info(f"Меню Telegram оновлено: {len(COMMANDS)} команд")
            return True
        return False

    async def discover_chat_ids(self) -> list[int]:
        """Хто вже писав боту. Нічого не зберігає — власника ставить лише майстер."""
        updates = await self._api("getUpdates", timeout=0)
        if not updates:
            return []
        found: list[int] = []
        for update in updates:
            message = update.get("message") or update.get("edited_message")
            if message and (chat_id := message["chat"]["id"]) not in found:
                found.append(chat_id)
        return found

    async def send_test(self) -> None:
        chat_ids = self._chat_ids
        if not chat_ids:
            found = await self.discover_chat_ids()
            print(
                "chat_id не задано. Підключи бота через Налаштування → "
                "Підключити бота… — майстер запише власника після перевірки."
            )
            if found:
                print(f"Хто вже писав боту (ще не власники): {found}")
            return
        await self.send("✅ Перевірка звʼязку. Майнер Twitch drops на звʼязку.", )
        print(f"Тестове повідомлення надіслано у чати: {chat_ids}")

    # ------------------------------------------------------------ події → повідомлення

    def _on_event(self, event: Event) -> Any:
        bio = self._bio_text(event)
        text = self._format(event)
        if bio is None and text is None:
            return None

        async def deliver() -> None:
            if bio is not None:
                await self._set_bio(bio)
            if text is None:
                return
            logger.log(CALL, f"Telegram: надсилаю {type(event).__name__}")
            if isinstance(event, UpdateAvailable) and event.files:
                await self.send(text, markup=UPDATE_BUTTONS)
                return
            await self.send(text, keyboard=isinstance(event, MinerStarted))

        return deliver()

    def _bio_text(self, event: Event) -> str | None:
        """Рядок у профіль бота (short description, до 120 символів)."""
        if isinstance(event, WatchingChanged):
            if event.channel is None:
                return "● Чекає"
            return f"● Іде · {event.channel.name}"
        if isinstance(event, ProgressStalled):
            return (
                f"● Стоїть · {event.minutes_without_progress} хв · "
                f"{event.channel_name}"
            )
        if isinstance(event, WatchUncounted):
            return f"● Не зараховується · {event.channel_name}"
        if isinstance(event, StatusChanged):
            if event.text == "Призупинено":
                return "● Пауза"
            if event.text.startswith("Прогрес стоїть"):
                return f"● {event.text}"
            if event.text == "Перегляд не зараховується":
                return "● Не зараховується"
        if isinstance(event, ConnectionLost):
            return "● Немає зв'язку"
        if isinstance(event, MinerStopped):
            return "● Зупинено"
        if isinstance(event, MinerStarted):
            return "● Чекає"
        return None

    async def _set_bio(self, text: str) -> None:
        text = text[:120]
        if text == self._bio:
            return
        # Той самий вид («Іде · канал») не частіше ніж раз на 15 с — інакше
        # перемикання каналів молотить Bot API. Зміна виду (Іде → Стоїть) одразу.
        same_kind = self._bio[: self._bio.find("·") + 1] == text[: text.find("·") + 1]
        if same_kind and self._bio and not self._routine_allowed("bio", 15.0):
            return
        self._bio = text
        await self._api("setMyShortDescription", short_description=text)
        await self._api("setMyDescription", description=text)

    def _routine_allowed(self, key: str, min_interval: float = 60.0) -> bool:
        now = time()
        if now - self._last_routine.get(key, 0.0) < min_interval:
            return False
        self._last_routine[key] = now
        return True

    def _format(self, event: Event) -> str | None:
        cfg = self._config
        esc = html.escape

        if cfg["notify_critical"]:
            if isinstance(event, MinerStarted):
                where = "у треї" if event.tray else "у вікні"
                return f"🟢 <b>Майнер запущено</b> ({where}), версія {esc(event.version)}"
            if isinstance(event, LoginRequired):
                return (
                    "🔑 <b>Потрібна авторизація</b>\n"
                    f"Код: <code>{esc(event.user_code)}</code>\n"
                    f"{esc(str(event.verification_uri))}"
                )
            if isinstance(event, ProgressStalled):
                why = (
                    f"Twitch зараховує «{esc(event.counted_elsewhere)}» — інший "
                    f"дроп цього ж каналу."
                    if event.counted_elsewhere
                    else "Найімовірніша причина — цим же акаунтом хтось дивиться "
                         "Twitch вручну."
                )
                return (
                    f"⚠️ <b>Прогрес стоїть</b> {event.minutes_without_progress} хв\n"
                    f"Канал: {esc(event.channel_name)}\n{why}"
                )
            if isinstance(event, WatchUncounted):
                return (
                    f"⚠️ <b>Перегляд не зараховується</b>\n"
                    f"Канал: {esc(event.channel_name)}\n"
                    "Хвилина не доходить до Twitch. Якщо інтернет при цьому "
                    "живий — перевірте, чи не блокується <code>spade.twitch.tv</code> "
                    "(часто так роблять блокувальники реклами на роутері)."
                )
            if isinstance(event, UpdateAvailable) and event.files:
                return (
                    f"⬆️ <b>Оновлення {esc(event.version)}</b>\n"
                    f"Скачати {event.files} змінених файлів "
                    f"({event.bytes_to_fetch // 1024} КБ), решта лишиться як є.\n"
                    "Кожен файл звіряється SHA-256, після встановлення програма "
                    "перезапуститься сама.\n"
                    "Фарм на цей час зупиниться на хвилину-дві."
                )
            if isinstance(event, UpdateFailed):
                return f"❌ Оновлення не встало: {esc(event.reason)}"
            if isinstance(event, ProtocolStale):
                if event.storm:
                    return (
                        "⚠️ <b>Twitch не відповідає на жоден наш запит</b>\n"
                        "Схоже на скид кешу на його боці. Якщо за годину не "
                        "пройде — це вже зміна протоколу."
                    )
                names = ", ".join(esc(name) for name in event.operations)
                return (
                    f"🛠 <b>Twitch змінив запити</b>\n{names}\n"
                    "Фарм на цьому спиниться: хеші в <code>protocol.py</code> "
                    "треба оновити, автоматично це не лікується."
                )
            if isinstance(event, CampaignAppeared):
                # свої імена: нижче той самий блок для DeadlineRisk працює з
                # іншим типом знімка, і спільна змінна плутала і читача, і mypy
                news = [f"🆕 <b>Нова кампанія</b> ({len(event.campaigns)})"]
                for fresh in event.campaigns[:5]:
                    ends = fresh.ends_at.astimezone().strftime("%d.%m %H:%M")
                    news.append(
                        f"• {esc(fresh.name.strip())} ({esc(fresh.game)}) — "
                        f"{fresh.total_drops} "
                        f"{plural(fresh.total_drops, 'дроп', 'дропи', 'дропів')}, до {ends}"
                    )
                if len(event.campaigns) > 5:
                    news.append(f"…і ще {len(event.campaigns) - 5}")
                news.append("Фарм не переривався. Перемкнутись: /switch &lt;канал&gt;")
                return "\n".join(news)
            if isinstance(event, DeadlineRisk):
                lines = [f"⏳ <b>Не встигаємо закрити</b> ({len(event.campaigns)})"]
                for item in event.campaigns[:5]:
                    lines.append(
                        f"• {esc(item.name)} ({esc(item.game)}): треба "
                        f"{item.minutes_needed} хв, лишилось {item.minutes_available}"
                    )
                if len(event.campaigns) > 5:
                    lines.append(f"…і ще {len(event.campaigns) - 5}")
                return "\n".join(lines)
            if isinstance(event, ConnectionLost):
                return f"📡 <b>Втрачено зв'язок</b>: {esc(event.reason)}. Перепідключаюсь…"
            if isinstance(event, ConnectionRestored):
                return f"📡 Зв'язок відновлено за {round(event.downtime_seconds)}с"
            if isinstance(event, MinerError):
                return f"❌ <b>Помилка</b>\n<code>{esc(event.message)}</code>"
            if isinstance(event, MinerStopped):
                return f"🛑 <b>Майнер зупинено</b>: {esc(event.reason)}"

        if cfg["notify_rewards"]:
            if isinstance(event, DropClaimed):
                return (
                    f"🎁 <b>Отримано дроп</b>\n{esc(event.rewards)}\n"
                    f"Гра: {esc(event.game)}"
                )
            if isinstance(event, CampaignFinished):
                return (
                    f"🏁 <b>Кампанію завершено</b>\n{esc(event.campaign_name)}\n"
                    f"Гра: {esc(event.game)}"
                )

        if cfg["notify_routine"]:
            if isinstance(event, WatchingChanged) and event.channel is not None:
                if self._routine_allowed("watching"):
                    game = esc(event.channel.game or "—")
                    return f"📺 Перемкнувся на {esc(event.channel.name)} ({game})"
            if isinstance(event, StreamOffline):
                if self._routine_allowed("offline"):
                    return f"📴 {esc(event.channel_name)} пішов офлайн"
        return None

    # ------------------------------------------------------------ періодичний звіт

    async def _report_loop(self) -> None:
        interval = max(1, int(self._config["report_every_hours"])) * 3600
        while True:
            await asyncio.sleep(interval)
            try:
                await self.send(self._status_text())
            except Exception:
                logger.exception("Не вдалося надіслати періодичний звіт")

    # ------------------------------------------------------------ тексти станів

    def _status_text(self) -> str:
        twitch = self._twitch
        esc = html.escape
        lines = ["📊 <b>Стан майнера</b>"]
        channel = twitch.watching.peek()
        if twitch._paused:
            lines.append("Статус: <i>призупинено</i>")
        elif channel is None:
            lines.append("Статус: <i>нічого не фармиться</i>")
        else:
            game = channel.game.name if channel.game is not None else "—"
            lines.append(f"Канал: <b>{esc(channel.name)}</b> ({esc(game)})")
        campaign = twitch.active_campaign()
        if campaign is not None:
            drop = campaign.next_drop
            if drop is not None:
                lines.append(
                    f"Дроп: {esc(drop.name)} — "
                    f"{drop.minutes}/{drop.required_minutes} хв "
                    f"(лишилось {drop.minutes_left})"
                )
            lines.append(
                f"Кампанія: {esc(campaign.name)} "
                f"[{campaign.taken_count}/{campaign.total}]"
            )
        active = [c for c in twitch.campaigns
                  if c.running and c.available_to_me and not c.everything_taken]
        lines.append(f"Активних кампаній у черзі: {len(active)}")
        if twitch.api.offline:
            lines.append("⚠️ Зараз немає зв'язку з Twitch")
        return "\n".join(lines)

    def _inventory_text(self) -> str:
        esc = html.escape
        lines = ["🎒 <b>Прогрес по дропах</b>"]
        shown = 0
        for campaign in self._twitch.campaigns:
            if not campaign.available_to_me or campaign.everything_taken or not campaign.running:
                continue
            lines.append(f"\n<b>{esc(campaign.game.name)}</b> — {esc(campaign.name)}")
            for drop in campaign.all_drops:
                mark = "✅" if drop.taken else "▫️"
                lines.append(
                    f"{mark} {esc(drop.name)}: "
                    f"{drop.minutes}/{drop.required_minutes} хв"
                )
            shown += 1
            if shown >= 10:
                lines.append("\n<i>…показано перші 10 кампаній</i>")
                break
        if shown == 0:
            lines.append("Немає активних кампаній, доступних для фарму.")
        return "\n".join(lines)

    def _campaigns_text(self) -> str:
        esc = html.escape
        lines = ["📋 <b>Кампанії</b>"]
        now = datetime.now(timezone.utc)
        for campaign in self._twitch.campaigns[:25]:
            if campaign.over:
                state = "минула"
            elif campaign.not_started:
                state = "скоро"
            elif not campaign.available_to_me:
                state = "не привʼязано акаунт"
            elif campaign.everything_taken:
                state = "завершено"
            else:
                hours = max(0, int((campaign.closes_at - now).total_seconds() // 3600))
                state = f"активна, ще {hours} год"
            lines.append(
                f"• {esc(campaign.game.name)} [{campaign.taken_count}/"
                f"{campaign.total}] — {state}"
            )
        if not self._twitch.campaigns:
            lines.append("Інвентар ще не прочитано.")
        return "\n".join(lines)

    # ------------------------------------------------------------ команди

    def _start_poll_task(self) -> None:
        """Запускає цикл команд під наглядом.

        Наглядач тут не про красу. Одного разу таска опитування померла тихо: команди
        оброблялись, потім перестали, і в логу — жодного рядка. Мовчазна смерть циклу
        керування виглядає для користувача точно як «бот зламався», тому тепер вона
        і помітна, і самовиправна.
        """
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._poll_task.add_done_callback(self._poll_task_died)

    def _poll_task_died(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            logger.error("Цикл команд Telegram завершився без причини — перезапускаю")
        else:
            logger.error("Цикл команд Telegram упав — перезапускаю", exc_info=exc)
        # не перезапускаємось, якщо застосунок уже згортається
        if self._session is not None and not self._session.closed:
            asyncio.get_running_loop().call_later(5, self._restart_poll)

    def _restart_poll(self) -> None:
        if self._session is not None and not self._session.closed:
            self._start_poll_task()

    async def _poll_loop(self) -> None:
        """Long-polling getUpdates.

        Уся ітерація під `try`, а не лише HTTP-запит: одне криве оновлення не сміє
        зупинити обробку всіх наступних.
        """
        while True:
            try:
                updates = await self._api("getUpdates", offset=self._offset, timeout=60)
                if not updates:
                    await asyncio.sleep(1)
                    continue
                logger.log(CALL, f"Telegram: отримано оновлень — {len(updates)}")
                for update in updates:
                    # offset рухаємо одразу: інакше криве оновлення повертатиметься вічно
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
        if chat_id not in self._chat_ids:
            logger.warning(
                f"Відхилено команду з чужого chat_id {chat_id}: "
                f"{message['text'][:60]!r}"
            )
            return
        text = message["text"].strip()
        await self._handle_command(chat_id, text)
        # слід про успіх, а не лише про факт отримання: інакше при скарзі «бот не
        # відповів» неможливо сказати, на якому кроці обірвався ланцюг
        logger.log(CALL, f"Telegram: команда {text[:40]!r} від {chat_id} — оброблено")

    async def _process_callback(self, callback: dict[str, Any]) -> None:
        """Обробляє натискання інлайн-кнопки.

        Кнопки прив'язані до тих самих команд, що й текст, тож уся логіка одна.
        Білий список chat_id перевіряється так само жорстко: кнопка нікому не дає
        обхідного шляху.
        """
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        command = callback.get("data", "")
        # Telegram чекає на відповідь, інакше кнопка «крутиться» до таймауту
        await self._api("answerCallbackQuery", callback_query_id=callback["id"])
        if chat_id is None or chat_id not in self._chat_ids:
            logger.warning(f"Відхилено натискання з чужого chat_id {chat_id}")
            return
        logger.log(CALL, f"Telegram: кнопка «{command}» від {chat_id}")
        await self._handle_command(chat_id, f"/{command}")

    async def _handle_command(self, chat_id: int, text: str) -> None:
        # Кнопка панелі надсилає свій напис звичайним повідомленням — перекладаємо
        # його в команду, щоб далі був один спільний шлях обробки.
        if (mapped := BUTTON_COMMANDS.get(text.strip())) is not None:
            text = f"/{mapped}"
        if not text.startswith("/"):
            return
        parts = text.split(maxsplit=1)
        # у групах команди приходять як /cmd@botname
        command = parts[0].lstrip("/").split("@", 1)[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        control = self._twitch.control

        if command in ("start", "help", "menu"):
            await self.send(HELP_TEXT, chat_id=chat_id, keyboard=True)
        elif command == "status":
            await self.send(self._status_text(), chat_id=chat_id, keyboard=True)
        elif command == "inventory":
            await self.send(self._inventory_text(), chat_id=chat_id, keyboard=True)
        elif command == "campaigns":
            await self.send(self._campaigns_text(), chat_id=chat_id, keyboard=True)
        elif command == "pause":
            control.send(Command(CommandType.PAUSE))
            await self.send("⏸ Фарм призупинено.", chat_id=chat_id, keyboard=True)
        elif command == "resume":
            control.send(Command(CommandType.RESUME))
            await self.send("▶️ Фарм відновлено.", chat_id=chat_id, keyboard=True)
        elif command == "reload":
            control.send(Command(CommandType.RELOAD))
            await self.send("🔄 Перечитую інвентар.", chat_id=chat_id, keyboard=True)
        elif command == "reboot":
            # відповідаємо ДО перезапуску: після нього ця сесія бота вже мертва
            await self.send(
                "♻️ Перезапускаю програму. Про готовність повідомлю окремо.",
                chat_id=chat_id,
            )
            control.send(Command(CommandType.REBOOT))
        elif command == "report":
            days = 7
            if argument.strip().isdigit():
                days = max(1, min(365, int(argument.strip())))
            body = html.escape(self._twitch.history.summary(days))
            await self.send(f"📈 <b>Звіт</b>\n<pre>{body}</pre>",
                            chat_id=chat_id, keyboard=True)
        elif command == "export":
            await self._handle_export(chat_id)
        elif command == "update":
            control.send(Command(CommandType.APPLY_UPDATE))
            await self.send(
                "⬆️ Качаю змінені файли, звіряю хеші й перезапускаюсь.\n"
                "Про результат скажу після перезапуску.",
                chat_id=chat_id, keyboard=True,
            )
        elif command == "later":
            # Відкладаємо до наступного запуску: перевірка робиться раз на
            # старті, тож окремий таймер тут був би зайвою механікою.
            self._twitch.update_postponed = True
            await self.send(
                "⏳ Гаразд, не чіпаю. Нагадаю після наступного запуску — "
                "або постав будь-коли командою /update.",
                chat_id=chat_id, keyboard=True,
            )
        elif command == "hide":
            control.send(Command(CommandType.HIDE_WINDOW))
            await self.send("🙈 Вікно згорнуто в трей.", chat_id=chat_id, keyboard=True)
        elif command == "show":
            control.send(Command(CommandType.SHOW_WINDOW))
            await self.send("🖥 Вікно розгорнуто.", chat_id=chat_id, keyboard=True)
        elif command == "switch":
            if not argument:
                await self.send("Вкажи канал: <code>/switch назва</code>", chat_id=chat_id)
                return
            control.send(Command(CommandType.SWITCH, argument))
            await self.send(f"Перемикаюсь на {html.escape(argument)}…", chat_id=chat_id)
        elif command == "priority":
            await self._handle_priority(chat_id, argument)
        elif command == "watch":
            await self._handle_watch(chat_id, argument)
        else:
            await self.send("Невідома команда.", chat_id=chat_id, keyboard=True)

    def _export_dir(self) -> Path:
        from core.config import STATE_DIR
        return STATE_DIR

    async def _handle_export(self, chat_id: int) -> None:
        from core import export

        try:
            paths = export.write_all(
                self._export_dir(),
                entries=self._twitch.history.entries(),
                campaigns=self._twitch.campaigns,
            )
        except OSError as error:
            await self.send(
                f"Не вдалося зберегти експорт: {html.escape(str(error))}",
                chat_id=chat_id, keyboard=True,
            )
            return
        listing = "\n".join(str(path) for path in paths)
        await self.send(
            f"💾 <b>Експорт</b>\n<code>{html.escape(listing)}</code>",
            chat_id=chat_id, keyboard=True,
        )
        for path in paths:
            if path.suffix.lower() != ".csv":
                continue
            payload = _file_bytes(path)
            if payload:
                await self._send_document(
                    payload, filename=path.name, chat_id=chat_id,
                )

    async def _send_document(self, data: bytes, *, filename: str, chat_id: int) -> None:
        """Кладе файл у чат. Немає сесії (тести) — мовчки пропускаємо."""
        if self._session is None or not data:
            return
        url = TELEGRAM_API.format(
            token=self._config["bot_token"], method="sendDocument",
        )
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat_id))
        form.add_field(
            "document", data,
            filename=filename,
            content_type="text/csv",
        )
        try:
            async with self._session.post(url, data=form) as response:
                # не `data`: цим іменем уже названо байти файлу в аргументах, і
                # перевірка типів справедливо бачила в цьому плутанину
                answer = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            logger.warning(f"Telegram sendDocument не вдався: {type(exc).__name__}: {exc}")
            return
        if not answer.get("ok"):
            logger.warning(f"Telegram sendDocument: {answer.get('description')}")

    async def _ensure_profile_photo(self) -> None:
        """Ставить аватар бота з іконки програми, один раз на версію."""
        from core.config import VERSION
        from gui.icon import profile_photo_jpeg

        if self._config.get("photo_version") == VERSION:
            return
        if await self._set_profile_photo(profile_photo_jpeg()):
            self._config["photo_version"] = VERSION
            self._settings.touch()
            self._settings.save()
            logger.info("Аватар Telegram-бота оновлено")

    async def _set_profile_photo(self, jpeg: bytes) -> bool:
        if self._session is None or not jpeg:
            return False
        url = TELEGRAM_API.format(
            token=self._config["bot_token"], method="setMyProfilePhoto",
        )
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

    async def _handle_priority(self, chat_id: int, argument: str) -> None:
        bits = argument.split(maxsplit=1)
        if len(bits) < 2 or bits[0].lower() not in ("add", "remove"):
            current = self._settings.priority
            listing = "\n".join(f"{i + 1}. {html.escape(g)}"
                                for i, g in enumerate(current)) or "<i>порожньо</i>"
            await self.send(
                f"<b>Пріоритет ігор</b>\n{listing}\n\n"
                "Зміна: <code>/priority add Назва гри</code>",
                chat_id=chat_id,
            )
            return
        action, game = bits[0].lower(), bits[1].strip()
        kind = CommandType.PRIORITY_ADD if action == "add" else CommandType.PRIORITY_REMOVE
        self._twitch.control.send(Command(kind, game))
        verb = "додано до" if action == "add" else "прибрано з"
        await self.send(f"«{html.escape(game)}» {verb} пріоритету.", chat_id=chat_id)

    async def _handle_watch(self, chat_id: int, argument: str) -> None:
        """Список спостереження — про нові кампанії яких ігор повідомляти.

        Окремо від пріоритету навмисно: пріоритет міняє, що фармити зараз, а це
        лише новини. Людина може хотіти знати про нову кампанію Rocket League,
        не перериваючи фарм WoT.
        """
        bits = argument.split(maxsplit=1)
        if len(bits) < 2 or bits[0].lower() not in ("add", "remove"):
            current = self._settings.watch_games
            listing = "\n".join(f"{i + 1}. {html.escape(g)}"
                                for i, g in enumerate(current)) or "<i>порожньо</i>"
            await self.send(
                f"👀 <b>Спостереження за іграми</b>\n{listing}\n\n"
                "Повідомляю, коли з'явиться нова кампанія цієї гри. Фарм при "
                "цьому не переривається.\n"
                "Зміна: <code>/watch add Rocket League</code>",
                chat_id=chat_id, keyboard=True,
            )
            return
        action, game = bits[0].lower(), bits[1].strip()
        current = list(self._settings.watch_games)
        lowered = [item.lower() for item in current]
        if action == "add":
            if game.lower() in lowered:
                await self.send(f"«{html.escape(game)}» уже у списку.",
                                chat_id=chat_id, keyboard=True)
                return
            current.append(game)
        else:
            current = [item for item in current if item.lower() != game.lower()]
        self._settings.watch_games = current
        self._settings.touch()
        self._settings.save()
        verb = "додано до спостереження" if action == "add" else "прибрано зі спостереження"
        await self.send(f"«{html.escape(game)}» {verb}.",
                        chat_id=chat_id, keyboard=True)
