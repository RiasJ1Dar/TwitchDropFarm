"""Які кампанії ми вже бачили — щоб «нова кампанія» означало справді нову.

Окремо від `history.jsonl` навмисно: там журнал нагород, який людина читає й
експортує, а тут службовий набір на кілька десятків рядків. Дописати туди 84
записи «побачив» означало б витіснити з читання справжні нагороди — історія
береться останніми записами.

Найважливіше правило тут — **перший запуск нічого не сповіщає**. Інакше людина,
яка щойно поставила програму, отримала б вісім десятків повідомлень про
кампанії, що існували й до неї.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("TwitchDrops")


class SeenCampaigns:
    """Набір id кампаній, які вже потрапляли нам на очі."""

    def __init__(self, path: Path):
        self.path = path
        self._ids: set[str] = set()
        self._loaded = False

    @property
    def known(self) -> bool:
        """Чи є з чим порівнювати. False — перший запуск, сповіщати нічого."""
        self._load()
        return bool(self._ids)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(body, list):
            self._ids = {item for item in body if isinstance(item, str)}

    def fresh(self, ids: set[str]) -> set[str]:
        """Ті з переданих, яких ми ще не бачили."""
        self._load()
        return ids - self._ids

    def remember(self, ids: set[str]) -> None:
        """Додає до набору й одразу зберігає.

        Пишемо через тимчасовий файл: обрив живлення посеред запису інакше
        лишив би обрізаний JSON, і наступний запуск вважав би всі кампанії
        новими — тобто вистрілив би десятками сповіщень.
        """
        self._load()
        before = len(self._ids)
        self._ids |= ids
        if len(self._ids) == before:
            return
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(sorted(self._ids), ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except OSError as error:
            log.log(logging.DEBUG, f"Набір побачених кампаній не збережено: {error}")
