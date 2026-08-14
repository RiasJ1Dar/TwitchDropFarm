"""Кеш зображень нагород і обкладинок ігор.

Картинки живуть на диску, а не в пам'яті: інвентар — це сотні кампаній, і
тримати їх усі розпакованими безглуздо, тоді як файл на 3 КБ читається миттєво
й переживає перезапуск.

Завантаження навмисно необов'язкове. Жодна помилка тут не має значення для
фарму: не вдалось узяти картинку — список просто лишиться текстовим.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from core.config import THUMBNAIL_SIZE

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from core.api import TwitchApi

log = logging.getLogger("TwitchDrops")

# Скільки картинок тягнемо одночасно. Twitch віддає їх зі свого CDN і не
# скаржиться, але сотня паралельних з'єднань — це вже неввічливо.
PARALLEL = 6
# Більше за це — уже не картинка нагороди, а щось стороннє
MAX_BYTES = 2 * 1024 * 1024
ALLOWED_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif")
# Сторона мініатюри на диску — з запасом на найбільший дозволений показ
THUMBNAIL = THUMBNAIL_SIZE


class ImageCache:
    """Тека з картинками, названими за адресою, з якої їх узято."""

    def __init__(self, folder: Path, api: TwitchApi):
        self.folder = folder
        self._api = api

    def path_for(self, url: str) -> Path | None:
        """Куди лягає картинка з цієї адреси.

        Ім'я — хеш адреси, а не назва нагороди: назви повторюються, містять
        двокрапки й скісні риски, та ще й змінюються між кампаніями.
        """
        if not url:
            return None
        suffix = ""
        for candidate in ALLOWED_SUFFIXES:
            if urlparse(url).path.lower().endswith(candidate):
                suffix = candidate
                break
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.folder / f"{digest}{suffix or '.img'}"

    def ready(self, url: str) -> Path | None:
        """Готовий файл, якщо він уже лежить у кеші."""
        path = self.path_for(url)
        if path is not None and path.exists() and path.stat().st_size > 0:
            return path
        return None

    async def fetch_all(self, urls: Iterable[str]) -> int:
        """Докачує те, чого ще немає. Повертає кількість нових файлів."""
        wanted = []
        seen: set[str] = set()
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            if self.ready(url) is None:
                wanted.append(url)
        if not wanted:
            return 0

        self.folder.mkdir(parents=True, exist_ok=True)
        gate = asyncio.Semaphore(PARALLEL)

        async def one(url: str) -> bool:
            async with gate:
                return await self._fetch(url)

        results = await asyncio.gather(*(one(url) for url in wanted),
                                       return_exceptions=True)
        done = sum(1 for r in results if r is True)
        log.log(logging.DEBUG, f"Картинок завантажено: {done} з {len(wanted)}")
        return done

    async def _fetch(self, url: str) -> bool:
        path = self.path_for(url)
        if path is None:
            return False
        try:
            async with self._api.request("GET", url) as response:
                if response.status != 200:
                    return False
                body = await response.read()
        except Exception as error:
            log.log(logging.DEBUG, f"Картинка не завантажилась: {error}")
            return False
        if not body or len(body) > MAX_BYTES:
            return False
        try:
            # Тимчасовий файл і перейменування: інакше обрив лишив би в кеші
            # напівфайл, який виглядає готовим і більше ніколи не оновиться.
            staging = path.with_suffix(path.suffix + ".part")
            staging.write_bytes(_shrink(body))
            staging.replace(path)
        except OSError as error:
            log.log(logging.DEBUG, f"Картинку не збережено: {error}")
            return False
        return True


def _shrink(body: bytes) -> bytes:
    """Зменшує картинку до розміру, у якому її справді показують.

    Twitch віддає нагороди по 30–60 КБ кожна, а в списку вони займають кілька
    десятків пікселів. Без цього кеш на 460 картинок важив 24 МБ — у десятки
    разів більше за все інше, що програма тримає на диску, і все заради
    пікселів, яких ніхто не побачить.

    Не вдалося розпізнати формат — зберігаємо як прийшло: краще зайві кілобайти,
    ніж порожній рядок у списку.
    """
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(body)) as picture:
            # вдвічі більше за показ — щоб не було мила на екранах із масштабом
            picture.thumbnail((THUMBNAIL, THUMBNAIL))
            out = BytesIO()
            picture.convert("RGBA").save(out, format="PNG", optimize=True)
            return out.getvalue()
    except Exception as error:
        log.log(logging.DEBUG, f"Картинку не зменшено: {error}")
        return body
