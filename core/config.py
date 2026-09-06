"""Шляхи, інтервали та межі. Усе, що можна крутити, зібрано тут.

Навмисно окремо від `protocol`: там факти про Twitch, тут наші рішення.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta
from enum import Enum, auto
from pathlib import Path

VERSION = "1.1.5"

# Публічне дзеркало оновлень: манифест + блоби, названі SHA-256.
# `1.0.4` — перший реліз із цим механізмом, але доїхати ним не виходило:
# скрипт підміни чекав один PID, а PyInstaller onefile тримає два процеси, і
# заміна падала зі «Sharing violation» — мовчки. `1.0.4.2` — перший, який
# справді ставиться сам.
GITHUB_REPO = "RiasJ1Dar/TwitchDropFarm"
UPDATE_MANIFEST_URL = (
    f"https://github.com/{GITHUB_REPO}/releases/latest/download/manifest.json"
)

# Запущено зі зібраного .exe, а не з вихідників
FROZEN: bool = hasattr(sys, "_MEIPASS")

# Рівень між INFO і DEBUG: трасування викликів API без потопу від бібліотек
TRACE = logging.INFO - 1
logging.addLevelName(TRACE, "TRACE")

APP_DIR = (
    Path(sys.executable).resolve().parent if FROZEN
    else Path(__file__).resolve().parent.parent
)


def _pick_state_dir() -> Path:
    """Одна тека стану на користувача — не поруч із виконуваним файлом.

    Інакше стан дублюється на кожну теку запуску (вихідники, збірка, будь-яка
    копія), і кожен новий шлях вимагає повторного входу. Файл `portable.txt`
    поруч із програмою повертає стан до неї — для флешки чи чужого комп'ютера.
    """
    if (APP_DIR / "portable.txt").exists():
        return APP_DIR
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME")
    return Path(base) / "TwitchDropFarm" if base else Path.home() / ".twitch_drop_farm"


STATE_DIR = _pick_state_dir()
STATE_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE = STATE_DIR / "auth.json"
COOKIE_FILE = STATE_DIR / "cookies.jar"
CONFIG_FILE = STATE_DIR / "settings.json"
LOG_FILE = STATE_DIR / "log.txt"

# Історія нагород. Окремо від журналу: той ротується, а нагороди мають
# лишатись назавжди — інакше сліду про них не буде взагалі.
HISTORY_FILE = STATE_DIR / "history.jsonl"
# Типовий звіт у Telegram (/report без аргумента). Сім днів було мало.
REPORT_DAYS = 90
# Службовий набір id кампаній, які вже потрапляли нам на очі. Окремо від
# історії нагород: там журнал для людини, а тут кілька десятків рядків, які
# витіснили б із читання справжні нагороди.
SEEN_CAMPAIGNS_FILE = STATE_DIR / "seen-campaigns.json"
# Своя тема оформлення. Файлу типово немає — тоді працює вбудована палітра.
# Формат навмисно найпростіший: {"accent": "#ff8800"}, лише ті ключі, які
# хочеться перевизначити.
THEME_FILE = STATE_DIR / "theme.json"

# Сторожа persisted-запитів Twitch. Раз на добу: хеші міняються рідко, а зайві
# запити тут ні до чого. Пауза між спробами — щоб відрізнити зміну хеша від
# `PersistedQueryNotFound`, який приходить сплеском і минає сам.
PROTOCOL_PROBE_EVERY = 24 * 60 * 60
PROTOCOL_PROBE_PAUSE = timedelta(seconds=30)
# Оновлення: на старті і ще раз кожні 12 годин, поки програма живе.
UPDATE_CHECK_EVERY = 12 * 60 * 60
# Картинки нагород і обкладинки ігор. Вимикається налаштуванням `drop_images`:
# трафік невеликий, але це все ж мережа заради краси, і вибір лишається за
# користувачем.
IMAGE_DIR = STATE_DIR / "images"
# На диску тримаємо один розмір — із запасом. Тоді зміна розміру показу не
# вимагає качати все наново, а збільшення не дає мила: зменшити картинку можна
# завжди, збільшити без втрати — ніколи.
THUMBNAIL_SIZE = 96
# Скільки пікселів займає картинка в списку. Смак у всіх різний, тож це лише
# типове значення: користувач міняє його налаштуванням `image_size`.
DEFAULT_IMAGE_SIZE = 48
MIN_IMAGE_SIZE, MAX_IMAGE_SIZE = 16, THUMBNAIL_SIZE
# Сторона картинки в плитковому вигляді. Тут місця вистачає, тож беремо все,
# що є на диску: саме заради цього вигляду картинки й завантажуються.
TILE_SIZE = THUMBNAIL_SIZE


def clamp_image_size(value: object) -> int:
    """Розмір картинки в дозволених межах.

    Значення приходить із файлу налаштувань, тобто там може лежати що завгодно:
    нуль, від'ємне, рядок, мільйон. Понад мініатюру на диску теж немає сенсу —
    збільшення дало б лише мило.
    """
    try:
        wanted = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_SIZE
    return max(MIN_IMAGE_SIZE, min(MAX_IMAGE_SIZE, wanted))
LOCK_FILE = STATE_DIR / "lock.file"
BROWSER_PROFILE = STATE_DIR / "browser_profile"
def documents_dir() -> Path:
    """Тека «Документи» користувача, якщо вона взагалі є.

    Типове місце для журналу: людина має знайти його сама, не питаючи, де
    ховається `%LOCALAPPDATA%`. ⚠️ Не завжди `~/Documents`: OneDrive часто
    перенаправляє цю теку до себе, а на не-Windows її може не бути зовсім —
    тому перевіряємо обидва варіанти й тихо відступаємо до теки стану.
    """
    for candidate in (
        Path.home() / "Documents",
        Path.home() / "OneDrive" / "Documents",
        Path.home() / "OneDrive" / "Документи",
    ):
        if candidate.is_dir():
            return candidate / "TwitchDropFarm"
    return STATE_DIR


def log_path(folder: str = "") -> Path:
    """Куди писати журнал. Порожня тека — типове місце в «Документах»."""
    if folder.strip():
        return Path(folder.strip()) / "log.txt"
    return documents_dir() / "log.txt"


# ---------------------------------------------------------------- ритм роботи

# Плеєр звітує про перегляд раз на хвилину; трохи частіше, щоб не запізнюватись
WATCH_PERIOD = timedelta(seconds=59)
# Скільки чекати відповіді Twitch про прогрес, перш ніж перевіряти самим
PROGRESS_GRACE = timedelta(seconds=20)
# Подія «стрім піднявся» випереджає реальний старт трансляції
STREAM_UP_DELAY = timedelta(seconds=120)
PING_PERIOD = timedelta(minutes=3)
PONG_DEADLINE = timedelta(seconds=10)
# Пауза перед підняттям ядра після несподіваної помилки Twitch
RESTART_PAUSE = timedelta(seconds=30)

# Стільки хвилин без підтвердженого приросту вважаємо застоєм.
# Рахуємо за годинником, не за кількістю проходів циклу: коли мережа
# сипле помилками, одна ітерація розтягується на хвилини, і лічильник
# проходів мовчить саме тоді, коли треба кричати.
STALL_LIMIT = 5
# Стільки спроб на доставку хвилини (spade або сторінка каналу). Нескінченне
# наростання тут блокує цикл перегляду й глушить усі перевірки за ним —
# швидка відмова корисніша за впертість.
SPADE_ATTEMPTS = 2
# Стільки хвилин дозволено домалювати наосліп, поки Twitch мовчить
BLIND_MINUTES_LIMIT = 15
# Транзиторні помилки GQL приходять сплеском на всі паралельні запити одразу,
# тому однієї повторної спроби замало
GQL_RETRIES = 4
# Оновлення лічильника глядачів приходять по кожному каналу окремо; зливаємо їх,
# щоб не перебудовувати весь список двісті разів поспіль
CHANNEL_REFRESH_DELAY = 1.0


# ---------------------------------------------------------------- межі PubSub

WEBSOCKET_LIMIT = 8
TOPICS_PER_SOCKET = 50
# ⚠️ Один, а не два. На кожен канал є два топіки — `video-playback-by-id`
# (стрім піднявся/впав) і `broadcast-settings-update` (стрімер змінив гру), —
# але обидва обробники закінчуються тим самим `expect_live()`. Другий корисний
# рівно тоді, коли гру міняють НЕ перериваючи трансляції, і важливо це лише на
# каналі, який ми дивимось: саме там ідуть хвилини. Тому масово підписуємо
# тільки перший, а другий — точково на активний канал.
# Ціна помилки тут висока: підписка понад ліміт Twitch кладе весь клієнт, а не
# окремий топік. Тому запас на активний канал закладено нижче явно.
TOPICS_PER_CHANNEL = 1
# Топіки рівня користувача: дропи, сповіщення, поінти.
# МУСИТЬ збігатися з тим, скільки їх насправді підписує ядро — інакше пул
# порахує на канал більше, ніж влазить, і впаде на переповненні.
# Три топіки користувача (дропи, сповіщення, поінти) плюс один запасний під
# `broadcast-settings-update` активного каналу.
USER_TOPIC_COUNT = 3 + 1

MAX_CHANNELS = (
    WEBSOCKET_LIMIT * TOPICS_PER_SOCKET - USER_TOPIC_COUNT
) // TOPICS_PER_CHANNEL


class Stage(Enum):
    """Крок робочого циклу майнера."""

    IDLE = auto()
    LOAD_INVENTORY = auto()
    PICK_GAMES = auto()
    DROP_CHANNELS = auto()
    FIND_CHANNELS = auto()
    PICK_CHANNEL = auto()
    RESTART = auto()
    QUIT = auto()


class FarmMode(Enum):
    """Як обирати, що фармити.

    LINKED_ONLY стоїть окремо: інші режими зважають на список пріоритету й
    вважають кампанію придатною навіть без прив'язки акаунта, якщо вона роздає
    значки чи емоції. LINKED_ONLY бере рівно те, до чого акаунт підключений
    і де дають справжній предмет.
    """

    PRIORITY_LIST = 0
    SOONEST_END = 1
    TIGHTEST_FIT = 2
    LINKED_ONLY = 3


# ---------------------------------------------------------------- журнал

VERBOSITY = {
    0: logging.ERROR,
    1: logging.WARNING,
    2: logging.INFO,
    3: TRACE,
    4: logging.DEBUG,
}
# Журнал росте помірно — близько 0,1 МБ на добу при -vvv, — але темп не сталий:
# шторм помилок або -vvvv дають на порядок більше, а програма розрахована на те,
# щоб місяцями працювати без нагляду. Тримаємо стелю: 5 МБ на файл, три копії.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3

FILE_LOG_FORMAT = logging.Formatter(
    "{asctime}.{msecs:03.0f}:\t{levelname:>7}:\t{message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M:%S",
)
CONSOLE_LOG_FORMAT = logging.Formatter("{levelname}: {message}", style="{")

# Де шукати браузер для входу, у порядку переваги
BROWSER_LOCATIONS: tuple[str, ...] = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)
