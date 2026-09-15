"""Координатор оновлень застосунку.

Перевіряє манифести релізів на GitHub, повідомляє користувача через події
(вікно і Telegram) та безпечно завантажує і застосовує оновлення.

⚠️ Стан оновлення живе **тільки тут**. `Miner._update_plan`, `._updating` і
`._update_problem` — властивості, які читають ці поля; своїх копій у майнера
немає. Це не педантизм: перша редакція цього модуля тримала стан удвох і
синхронізувала руками, а таке роздвоєння ламається тихо — одна половина
каже «оновлення вже ставиться», друга «ще ні», і кнопку можна натиснути
двічі. Саме від цього тут колись і з'явився прапорець `_updating`.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING, Any

from core.config import TRACE, UPDATE_CHECK_EVERY
from core.events import UpdateAvailable, UpdateFailed
from core.i18n import t
from core.toolbox import human_size, plural, sleep_unless

if TYPE_CHECKING:
    from core.miner import Miner

log = logging.getLogger("TwitchDrops")


class UpdateCoordinator:
    """Керує життєвим циклом перевірки та встановлення оновлень."""

    def __init__(self, miner: Miner) -> None:
        self.miner = miner
        # що саме пропонуємо поставити: (манифест, перелік файлів до завантаження)
        self.plan: tuple[Any, list[Any]] | None = None
        # оновлення ставиться просто зараз: вікно й Telegram — два входи
        # до однієї дії, і натиснути можна двічі
        self.updating = False
        # остання причина, чому перевірка не вдалась: щоб та сама не летіла
        # в Telegram двічі на добу
        self.problem: str | None = None

    async def check_updates(self) -> None:
        """На старті і далі двічі на добу, поки галочка не знята."""
        first = True
        while not self.miner.stopping:
            if not first:
                if await sleep_unless(self.miner._quitting, UPDATE_CHECK_EVERY):
                    return
            first = False
            if self.miner.settings.check_updates:
                await self.check_updates_once()

    async def check_updates_once(self) -> None:
        from core import update

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                found = await update.check_for_update(session)
        except Exception as error:
            self._update_check_failed(error)
            return
        self.problem = None
        if found is None:
            return
        self.offer_update(*found)

    def _update_check_failed(self, error: Exception) -> None:
        text = str(error).strip() or type(error).__name__
        if "404" in text:
            log.log(TRACE, "Манифест оновлень ще не опубліковано")
            return
        log.warning(f"Перевірка оновлення не вдалась: {text}")
        if not isinstance(error, ValueError):
            return
        if text == self.problem:
            return
        self.problem = text
        self.miner.events.emit(UpdateFailed(reason=text))

    def offer_update(self, manifest: Any, items: list[Any]) -> None:
        already = (
            self.plan is not None
            and self.plan[0].version == manifest.version
        )
        self.plan = (manifest, items)
        if self.miner.update_postponed:
            log.log(TRACE, f"Оновлення {manifest.version} відкладено людиною")
            return
        if already:
            return
        total = sum(item.spec.size for item in items)
        self.miner.events.emit(UpdateAvailable(
            version=manifest.version,
            files=len(items),
            bytes_to_fetch=total,
        ))
        if not items:
            self.miner.say(t("say_update_same", version=manifest.version))
            return
        self.miner.say(t(
            "say_update_ready",
            version=manifest.version,
            files=len(items),
            unit=plural(len(items), t("file_one"), t("file_few"), t("file_many")),
            size=human_size(total),
        ))

    async def apply_update(self) -> None:
        from core import update

        if not update.can_apply():
            self.miner.events.emit(UpdateFailed(reason="оновлення лише для зібраного .exe"))
            self.miner.say(t("say_update_source"))
            return
        if self.updating:
            self.miner.say(t("say_update_busy"))
            return
        if self.plan is None:
            self.miner.say(t("say_update_none"))
            return
        self.updating = True
        manifest, items = self.plan
        try:
            if items:
                import aiohttp
                for leftover in update.STAGE_DIR.glob("*.part"):
                    leftover.unlink(missing_ok=True)
                async with aiohttp.ClientSession() as session:
                    for item in items:
                        self.miner.events.status(t("status_download", path=item.spec.path))
                        await update.download_item(session, item)
                update.verify_staged(items)
            from core.config import APP_DIR
            script = update.write_apply_script()
            update.launch_apply(
                script,
                exe=APP_DIR / "TwitchDropFarm.exe",
                dest=APP_DIR,
                pid=os.getpid(),
                args=" ".join(sys.argv[1:]),
            )
        except Exception as error:
            self.updating = False
            self.miner.events.emit(UpdateFailed(reason=str(error)))
            self.miner.say(t("say_update_fail", error=error))
            return
        self.miner.say(t("say_update_exit", version=manifest.version))
        self.miner.request_stop()
