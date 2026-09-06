"""Що показувати в журналі й у консолі — таблицями, а не драбинами.

Раніше обидва набори жили вкладеними функціями в `main.py`, кожен —
двадцятигілковим ланцюжком `elif isinstance(event, …)`. Разом із таким самим
ланцюжком у вікні й у Telegram виходило три незалежні драбини на ті самі
події: нову подію треба було вписати в три місця, і забути одне — тихо.

Окремий модуль, а не функції в `main.py`, з двох причин. По-перше, `main.py`
імпортує все ліниво, щоб програма швидше стартувала, — тож обробники там не
могли жити на рівні модуля й лишались невидимими ззовні. По-друге, саме
завдяки видимості перевірка може спитати «а всі події кудись показуються?»:
`tests/core_check.py` бере всі класи подій і звіряє з `covered()`.
"""
from __future__ import annotations

import logging

from core.config import TRACE as CALL
from core.events import (
    CampaignFinished,
    ConnectionLost,
    ConnectionRestored,
    DropClaimed,
    DropProgress,
    LoggedIn,
    LoginRequired,
    LogLine,
    MinerStopped,
    ProgressStalled,
    Router,
    StatusChanged,
    UpdateAvailable,
    UpdateFailed,
    WatchingChanged,
    WatchUncounted,
)

logger = logging.getLogger("TwitchDrops")

# ==================================================================== журнал

TO_LOG = Router()
"""Дублює важливі події в журнал.

Без цього `--log` розповідав лише половину історії: стан, канал і прогрес
ішли тільки в stdout, а він губиться при запуску без вікна. Саме через це
журнал обривався на «Websocket підключено», хоча майнер працював далі.
"""


@TO_LOG.on(StatusChanged)
def _log_status(event: StatusChanged) -> None:
    logger.log(CALL, f"Стан: {event.text}")


@TO_LOG.on(WatchingChanged)
def _log_watching(event: WatchingChanged) -> None:
    if event.channel is None:
        logger.info("Перестали дивитись")
    else:
        logger.info(f"Дивимось {event.channel.name} ({event.channel.game})")


@TO_LOG.on(DropProgress)
def _log_progress(event: DropProgress) -> None:
    logger.log(
        CALL,
        f"Дроп {event.drop_name} ({event.game}): "
        f"{event.current_minutes}/{event.required_minutes} хв"
    )


@TO_LOG.on(DropClaimed)
def _log_claimed(event: DropClaimed) -> None:
    logger.warning(f"ОТРИМАНО ДРОП: {event.rewards} — {event.game}")


@TO_LOG.on(CampaignFinished)
def _log_campaign_done(event: CampaignFinished) -> None:
    logger.warning(f"КАМПАНІЮ ЗАВЕРШЕНО: {event.campaign_name} ({event.game})")


@TO_LOG.on(ProgressStalled)
def _log_stalled(event: ProgressStalled) -> None:
    where = (
        f", зараховується «{event.counted_elsewhere}»"
        if event.counted_elsewhere else ""
    )
    logger.error(
        f"Прогрес стоїть {event.minutes_without_progress} хв "
        f"на {event.channel_name}{where}"
    )


@TO_LOG.on(WatchUncounted)
def _log_uncounted(event: WatchUncounted) -> None:
    logger.error(
        f"Перегляд не зараховується на {event.channel_name}: "
        f"{event.consecutive} хвилини поспіль не дійшли до Twitch"
    )


@TO_LOG.on(UpdateAvailable)
def _log_update(event: UpdateAvailable) -> None:
    logger.warning(
        f"Оновлення {event.version}: {event.files} файлів, "
        f"{event.bytes_to_fetch} байт"
    )


@TO_LOG.on(UpdateFailed)
def _log_update_failed(event: UpdateFailed) -> None:
    logger.error(f"Оновлення не встало: {event.reason}")


@TO_LOG.on(MinerStopped)
def _log_stopped(event: MinerStopped) -> None:
    logger.warning(f"Майнер зупинено: {event.reason}")


# =================================================================== консоль

TO_CONSOLE = Router()
"""Друкує події в консоль. Заміна вікна, коли його немає (`--console`)."""


@TO_CONSOLE.on(LogLine)
def _say_line(event: LogLine) -> None:
    print(event.text)


@TO_CONSOLE.on(StatusChanged)
def _say_status(event: StatusChanged) -> None:
    print(f"[стан] {event.text}")


@TO_CONSOLE.on(LoginRequired)
def _say_login(event: LoginRequired) -> None:
    print(f"\n>>> Потрібна авторизація. Код: {event.user_code}")
    print(f">>> Сторінка: {event.verification_uri}\n")


@TO_CONSOLE.on(LoggedIn)
def _say_logged_in(event: LoggedIn) -> None:
    print(f"[вхід] Успішно, user ID {event.user_id}")


@TO_CONSOLE.on(WatchingChanged)
def _say_watching(event: WatchingChanged) -> None:
    if event.channel is None:
        print("[канал] Нічого не дивимось")
    else:
        print(f"[канал] {event.channel.name} ({event.channel.game})")


@TO_CONSOLE.on(DropProgress)
def _say_progress(event: DropProgress) -> None:
    print(
        f"[дроп] {event.drop_name} ({event.game}): "
        f"{event.current_minutes}/{event.required_minutes} хв"
    )


@TO_CONSOLE.on(DropClaimed)
def _say_claimed(event: DropClaimed) -> None:
    print(f"[НАГОРОДА] {event.rewards} — {event.game}")


@TO_CONSOLE.on(CampaignFinished)
def _say_campaign_done(event: CampaignFinished) -> None:
    print(f"[ГОТОВО] Кампанію завершено: {event.campaign_name} ({event.game})")


@TO_CONSOLE.on(ProgressStalled)
def _say_stalled(event: ProgressStalled) -> None:
    why = (
        f"Twitch зараховує «{event.counted_elsewhere}» — інший дроп "
        f"цього ж каналу."
        if event.counted_elsewhere
        else "Можлива причина — цим акаунтом хтось дивиться Twitch вручну."
    )
    print(
        f"[!] Прогрес стоїть {event.minutes_without_progress} хв на "
        f"{event.channel_name}. {why}"
    )


@TO_CONSOLE.on(WatchUncounted)
def _say_uncounted(event: WatchUncounted) -> None:
    print(
        f"[!] Перегляд не зараховується на {event.channel_name}. "
        f"Хвилина не доходить до Twitch — перевірте, чи не блокується "
        f"spade.twitch.tv."
    )


@TO_CONSOLE.on(ConnectionLost)
def _say_conn_lost(event: ConnectionLost) -> None:
    print(f"[!] Втрачено зв'язок: {event.reason}. Перепідключаюсь…")


@TO_CONSOLE.on(ConnectionRestored)
def _say_conn_ok(event: ConnectionRestored) -> None:
    print(f"[+] Зв'язок відновлено за {round(event.downtime_seconds)}с")


@TO_CONSOLE.on(MinerStopped)
def _say_stopped(event: MinerStopped) -> None:
    print(f"[стоп] {event.reason}")
