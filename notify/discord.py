"""Сповіщення в Discord через вебхук.

Навіщо другий канал, коли є Telegram. По-перше, Telegram у частині країн
блокують, і людина лишається без єдиного способу дізнатись, що фарм став.
По-друге, це найчастіша фіча в усіх подібних програмах — вебхук є майже
скрізь, бо він нічого не вимагає: ні бота, ні реєстрації, ні токена, який
дає доступ до листування. Тільки адреса, яку Discord видає в налаштуваннях
каналу.

⚠️ Чому тут НЕ повний набір подій, як у Telegram. Вебхук — односторонній: на
нього не можна відповісти, з нього не можна керувати. Тому сюди йде лише те,
про що варто дізнатись негайно, а рутина (зміна каналу, стрім упав) лишається
в боті й у журналі. Інакше канал у Discord перетворюється на стрічку, яку
перестають читати — а тоді від нього немає користі й у важливий момент.

Тексти беруться з тих самих ключів локалі, що й у Telegram: інакше довелось
би вести дев'ять мов удвічі. Різниця лише в розмітці — Discord не розуміє
HTML, тож теги перекладаються в Markdown.
"""
from __future__ import annotations

import asyncio
import html as html_module
import logging
import re
from typing import TYPE_CHECKING, Any

import aiohttp

from core.events import (
    AccountLinkLost,
    CampaignAppeared,
    DeadlineRisk,
    DropClaimed,
    Event,
    LoginRequired,
    MinerError,
    MinerStopped,
    ProgressStalled,
    Router,
    UpdateAvailable,
    WatchUncounted,
)
from core.i18n import t
from core.toolbox import plural

if TYPE_CHECKING:
    from core.miner import Miner as Twitch

log = logging.getLogger("TwitchDrops")

# Скільки чекати на Discord. Вебхук — не частина фарму: якщо він мовчить,
# майнер має працювати далі, а не стояти в очікуванні відповіді.
TIMEOUT = aiohttp.ClientTimeout(total=10)
# Discord ріже повідомлення довші за 2000 знаків, і тоді відповідає 400 —
# тобто мовчазна втрата. Ріжемо самі й чесно ставимо трикрапку.
LIMIT = 1900

POST = Router()
"""Маршрути «подія → текст у Discord». Свідомо вужчі за телеграмні."""


def _to_markdown(text: str) -> str:
    """HTML-розмітка Telegram → розмітка Discord.

    Дешевша заміна повноцінному конвертеру: у наших текстах трапляються рівно
    три теги. Усе інше, що виглядає як тег, прибираємо — краще втратити
    оформлення, ніж показати людині сирий `<b>`.
    """
    text = re.sub(r"</?b>", "**", text)
    text = re.sub(r"</?i>", "*", text)
    text = re.sub(r"</?code>", "`", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html_module.unescape(text)


class DiscordHook:
    """Шле важливі події у вебхук. Односторонньо, без керування."""

    def __init__(self, twitch: Twitch) -> None:
        self._twitch = twitch
        self._session: aiohttp.ClientSession | None = None
        self._unsubscribe: Any = None
        self._failures = 0

    @property
    def url(self) -> str:
        return str(self._twitch.settings.discord_webhook or "").strip()

    async def start(self) -> None:
        if not self.url:
            return
        self._session = aiohttp.ClientSession(timeout=TIMEOUT)
        self._unsubscribe = self._twitch.events.subscribe(self._on_event)
        log.info("Сповіщення в Discord увімкнено")

    async def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------ надсилання

    def _on_event(self, event: Event) -> Any:
        text = POST.dispatch(event, owner=self)
        if not text:
            return None
        return self.send(text)

    async def send(self, text: str) -> None:
        """Надсилає повідомлення. Провал вебхука ніколи не чіпає фарм.

        ⚠️ Мовчазних відмов тут бути не повинно. Вебхук легко зіпсувати —
        видалили канал у Discord, змінили адресу, помилились при вставці, — і
        людина роками думатиме, що сповіщення просто не приходять. Тому кожен
        провал іде в журнал, а перший — ще й помітним рівнем.
        """
        if self._session is None:
            return
        body = text if len(text) <= LIMIT else text[:LIMIT] + "…"
        try:
            async with self._session.post(self.url, json={"content": body}) as answer:
                if answer.status >= 400:
                    detail = (await answer.text())[:200]
                    self._note_failure(f"{answer.status}: {detail}")
                    return
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            self._note_failure(str(error))
            return
        if self._failures:
            log.info("Discord знову приймає повідомлення")
            self._failures = 0

    def _note_failure(self, detail: str) -> None:
        self._failures += 1
        if self._failures == 1:
            log.warning(f"Discord не прийняв повідомлення: {detail}")
        else:
            log.info(f"Discord мовчить ({self._failures}): {detail}")

    # --------------------------------------------------------------- маршрути

    @POST.on(DropClaimed)
    def _post_claimed(self, event: DropClaimed) -> str:
        return _to_markdown(t("tg_claimed", rewards=event.rewards, game=event.game))

    @POST.on(AccountLinkLost)
    def _post_link_lost(self, event: AccountLinkLost) -> str:
        return _to_markdown(t("tg_link_lost", names=", ".join(event.campaigns),
                              minutes=event.minutes_lost))

    @POST.on(ProgressStalled)
    def _post_stalled(self, event: ProgressStalled) -> str:
        why = (
            t("tg_stalled_else", name=event.counted_elsewhere)
            if event.counted_elsewhere
            else t("tg_stalled_manual")
        )
        return _to_markdown(t("tg_stalled",
                              minutes=event.minutes_without_progress,
                              channel=event.channel_name, why=why))

    @POST.on(WatchUncounted)
    def _post_uncounted(self, event: WatchUncounted) -> str:
        return _to_markdown(t("tg_uncounted", channel=event.channel_name))

    @POST.on(MinerError)
    def _post_error(self, event: MinerError) -> str:
        return _to_markdown(t("tg_error", message=event.message))

    @POST.on(MinerStopped)
    def _post_stopped(self, event: MinerStopped) -> str:
        return _to_markdown(t("tg_stopped", reason=event.reason))

    @POST.on(LoginRequired)
    def _post_login(self, event: LoginRequired) -> str:
        return _to_markdown(t("tg_login", code=event.user_code,
                              uri=str(event.verification_uri)))

    @POST.on(UpdateAvailable)
    def _post_update(self, event: UpdateAvailable) -> str | None:
        if not event.files:
            return None
        return _to_markdown(t("tg_update", version=event.version,
                              files=event.files,
                              kb=event.bytes_to_fetch // 1024))

    @POST.on(DeadlineRisk)
    def _post_deadline(self, event: DeadlineRisk) -> str:
        lines = [t("tg_deadline_title", count=len(event.campaigns))]
        for item in event.campaigns[:5]:
            lines.append(t("tg_deadline_item", name=item.name, game=item.game,
                           needed=item.minutes_needed,
                           available=item.minutes_available))
        if len(event.campaigns) > 5:
            lines.append(t("tg_deadline_more", n=len(event.campaigns) - 5))
        return _to_markdown("\n".join(lines))

    @POST.on(CampaignAppeared)
    def _post_new_campaigns(self, event: CampaignAppeared) -> str:
        lines = [t("tg_new_campaign_title", count=len(event.campaigns))]
        for fresh in event.campaigns[:5]:
            lines.append(t(
                "tg_new_campaign_item",
                name=fresh.name.strip(), game=fresh.game,
                drops=fresh.total_drops,
                unit=plural(fresh.total_drops, t("tg_drop_one"),
                            t("tg_drop_few"), t("tg_drop_many")),
                ends=fresh.ends_at.astimezone().strftime("%d.%m %H:%M"),
            ))
        if len(event.campaigns) > 5:
            lines.append(t("tg_new_campaign_more", n=len(event.campaigns) - 5))
        return _to_markdown("\n".join(lines))
