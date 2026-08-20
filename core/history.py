"""Історія фарму: те, що варто пам'ятати довше за журнал.

`log.txt` розповідає, як минула остання доба, і ротується — тобто клейми
місячної давнини з нього зникають назовсім. Тут навпаки: лише події, що мають
значення через тиждень і через рік, по рядку JSON на кожну.

Формат навмисно найпростіший із можливих. JSONL читається очима, дописується
без блокувань і не псується від обриву на середині: зіпсованим буде щонайбільше
останній рядок, а не весь файл — на відміну від JSON-масиву, який довелося б
щоразу перечитувати й переписувати цілком.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from core.config import REPORT_DAYS
from core.events import CampaignFinished, DeadlineRisk, DropClaimed, Event
from core.i18n import t

if TYPE_CHECKING:
    from pathlib import Path

    from core.events import EventBus

log = logging.getLogger("TwitchDrops")

# Скільки останніх рядків тримати. Подій тут одиниці на добу, тож навіть за рік
# файл лишається дрібним; межа існує на випадок, якщо щось почне сипати.
MAX_ENTRIES = 5000


class History:
    """Дозапис подій у JSONL і кілька відповідей на питання про минуле."""

    def __init__(self, path: Path):
        self.path = path

    # ------------------------------------------------------------ запис

    def attach(self, events: EventBus) -> None:
        events.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        if isinstance(event, DropClaimed):
            self.record("drop", game=event.game, drop=event.drop_name,
                        rewards=event.rewards)
        elif isinstance(event, CampaignFinished):
            self.record("campaign", game=event.game, campaign=event.campaign_name)
        elif isinstance(event, DeadlineRisk):
            for item in event.campaigns:
                self.record("risk", id=item.id, game=item.game, campaign=item.name,
                            needed=item.minutes_needed,
                            available=item.minutes_available)

    def record(self, kind: str, **fields: Any) -> None:
        """Дописує подію. Збій запису не має зупиняти фарм — історія вторинна."""
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "kind": kind, **fields}
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as error:
            log.log(logging.DEBUG, f"Історію не записано: {error}")

    # ------------------------------------------------------------ читання

    def entries(self, *, since: datetime | None = None,
                kind: str | None = None) -> list[dict[str, Any]]:
        """Читає історію, мовчки пропускаючи зіпсовані рядки.

        Обрив живлення посеред запису псує рівно один рядок; втрачати через
        нього всю історію було б безглуздо.
        """
        try:
            raw = self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []
        found: list[dict[str, Any]] = []
        for line in raw[-MAX_ENTRIES:]:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if kind is not None and entry.get("kind") != kind:
                continue
            if since is not None:
                stamp = _parse(entry.get("at"))
                if stamp is None or stamp < since:
                    continue
            found.append(entry)
        return found

    def campaigns_warned(self) -> set[str]:
        """Кампанії, про безнадійність яких уже сказали.

        Саме заради цього в історію пишеться `id`: без неї набір жив у пам'яті
        процесу, і після кожного перезапуску та сама кампанія отримувала друге
        попередження.
        """
        return {
            entry["id"] for entry in self.entries(kind="risk")
            if isinstance(entry.get("id"), str)
        }

    def summary(self, days: int = REPORT_DAYS) -> str:
        """Звіт за період — коротко, для людини."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        entries = self.entries(since=since)
        drops = [e for e in entries if e.get("kind") == "drop"]
        campaigns = [e for e in entries if e.get("kind") == "campaign"]
        if not drops and not campaigns:
            return t("tg_report_empty", days=days)

        by_game: dict[str, int] = {}
        for entry in drops:
            game = str(entry.get("game", "—"))
            by_game[game] = by_game.get(game, 0) + 1

        lines = [
            t("tg_report_head", days=days, drops=len(drops),
              campaigns=len(campaigns)),
        ]
        for game, count in sorted(by_game.items(), key=lambda p: -p[1]):
            lines.append(f"  {game}: {count}")
        recent = drops[-3:]
        if recent:
            lines.append(t("tg_report_recent"))
            for entry in reversed(recent):
                when = str(entry.get("at", ""))[:16].replace("T", " ")
                lines.append(f"  {when} — {entry.get('rewards', '?')}")
        return "\n".join(lines)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
