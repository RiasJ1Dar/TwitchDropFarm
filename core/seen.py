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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("TwitchDrops")

# Скільки пам'ятати кампанію після того, як вона зникла зі списку Twitch.
# Набір ніколи нічого не забував, тож ріс довічно: id кампанії, яка скінчилась
# торік, лишався і в пам'яті, і у файлі. Три місяці — із запасом більше за
# будь-яку кампанію Twitch, тому повернення давно забутої (а отже й хибне
# «нова кампанія!») практично неможливе.
RETENTION_DAYS = 90


class SeenCampaigns:
    """Кампанії, які вже потрапляли нам на очі, з датою останньої появи."""

    def __init__(self, path: Path):
        self.path = path
        # id -> коли востаннє бачили. Дата, а не час: точності до дня досить,
        # а файл лишається читабельним очима.
        self._seen: dict[str, date] = {}
        self._loaded = False
        self._written_on: date | None = None

    @property
    def known(self) -> bool:
        """Чи є з чим порівнювати. False — перший запуск, сповіщати нічого."""
        self._load()
        return bool(self._seen)

    @staticmethod
    def _today() -> date:
        return datetime.now(timezone.utc).date()

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        today = self._today()
        if isinstance(body, list):
            # Старий формат — простий список без дат. Вважаємо, що всі вони
            # бачені сьогодні: інакше оновлення програми змело б їх усі разом,
            # і людина отримала б десятки сповіщень про «нові» кампанії.
            self._seen = {item: today for item in body if isinstance(item, str)}
            self._written_on = None  # формат змінився — перезаписати
            return
        if isinstance(body, dict):
            for key, value in body.items():
                if not isinstance(key, str):
                    continue
                try:
                    self._seen[key] = date.fromisoformat(str(value))
                except ValueError:
                    self._seen[key] = today
            self._written_on = today if self._seen else None

    def fresh(self, ids: set[str]) -> set[str]:
        """Ті з переданих, яких ми ще не бачили."""
        self._load()
        return ids - self._seen.keys()

    def remember(self, ids: set[str]) -> None:
        """Позначає передані як бачені сьогодні й забуває надто давні."""
        self._load()
        today = self._today()
        before = set(self._seen)
        for campaign_id in ids:
            self._seen[campaign_id] = today

        edge = today - timedelta(days=RETENTION_DAYS)
        forgotten = [key for key, seen_on in self._seen.items() if seen_on < edge]
        for key in forgotten:
            del self._seen[key]
        if forgotten:
            log.debug(f"Забуто кампаній, яких давно не видно: {len(forgotten)}")

        # Пишемо, коли змінився склад — або коли настав новий день: дати мусять
        # бути свіжими на диску, інакше після перезапуску відлік до забуття
        # почнеться від старої позначки.
        if set(self._seen) == before and self._written_on == today:
            return
        self._save()
        self._written_on = today

    def _save(self) -> None:
        """Пишемо через тимчасовий файл: обрив живлення посеред запису інакше
        лишив би обрізаний JSON, і наступний запуск вважав би всі кампанії
        новими — тобто вистрілив би десятками сповіщень.
        """
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(
                    {key: value.isoformat() for key, value in sorted(self._seen.items())},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        except OSError as error:
            log.log(logging.DEBUG, f"Набір побачених кампаній не збережено: {error}")
