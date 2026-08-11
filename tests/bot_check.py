"""Перевірка Telegram-бота без реальної доставки.

Підміняємо лише `_api` — найнижчий шар, що робить HTTP-запит. Усе вище (маршрутизація
подій, форматування, розбір команд, білий список chat_id) працює справжнє.
"""
from __future__ import annotations

import argparse
import asyncio

from core.events import (
    CampaignFinished,
    ChannelSnapshot,
    CommandType,
    ConnectionLost,
    ConnectionRestored,
    DropClaimed,
    LoginRequired,
    MinerError,
    MinerStopped,
    ProgressStalled,
    StreamOffline,
    WatchingChanged,
)
from core.miner import Miner as Twitch
from core.settings import Settings
from notify.telegram import TelegramNotifier

OWNER = 111111
STRANGER = 999999

ok = 0
fail = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global ok, fail
    if condition:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}  {detail}")


async def main() -> int:
    args = argparse.Namespace(log=False, tray=False, logging_level=50,
                              debug_ws=0, debug_gql=0)
    twitch = Twitch(Settings(args))
    tg = TelegramNotifier(twitch)

    sent: list[tuple[int, str]] = []

    async def fake_api(method: str, **payload):
        if method == "sendMessage":
            sent.append((payload["chat_id"], payload["text"]))
            return {"ok": True}
        if method == "getMe":
            return {"username": "DropFarm_bot"}
        return None

    tg._api = fake_api                      # type: ignore[assignment]
    tg._config["chat_ids"] = [OWNER]
    tg._config["notify_routine"] = True     # щоб перевірити і рутину
    twitch.events.subscribe(tg._on_event)

    # ---------------------------------------------------------------- події
    print("\n[1] Події → повідомлення")
    cases = [
        ("вхід потрібен", LoginRequired(verification_uri="https://twitch.tv/activate",
                                        user_code="ABCD1234"), "ABCD1234"),
        ("дроп отримано", DropClaimed(drop_name="Скін", game="EVE Online",
                                      rewards="Cyber Knight SKIN"), "Cyber Knight SKIN"),
        ("кампанію завершено", CampaignFinished(campaign_name="Foundation Day",
                                                game="EVE Online"), "Foundation Day"),
        ("застій прогресу", ProgressStalled(minutes_without_progress=5,
                                            channel_name="ibeast"), "вручну"),
        ("звʼязок втрачено", ConnectionLost(reason="TimeoutError", attempt=2), "Втрачено"),
        ("звʼязок відновлено", ConnectionRestored(downtime_seconds=42.0, attempts=3), "42"),
        ("помилка", MinerError(message="щось пішло не так"), "щось пішло не так"),
        ("зупинка", MinerStopped(reason="Запит користувача"), "зупинено"),
        ("стрім офлайн", StreamOffline(channel_name="ibeast"), "офлайн"),
    ]
    for name, event, expect in cases:
        before = len(sent)
        twitch.events.emit(event)
        await asyncio.sleep(0)
        await twitch.events.drain()
        got = sent[before:]
        check(name, len(got) == 1 and expect in got[0][1],
              f"надіслано={got}")

    # перемикання каналу — рутина
    before = len(sent)
    twitch.events.emit(WatchingChanged(channel=ChannelSnapshot(
        1, "ibeast", "EVE Online", 120, True, True, False)))
    await twitch.events.drain()
    check("зміна каналу", len(sent) == before + 1 and "ibeast" in sent[-1][1])

    # екранування HTML: назва з < > не має ламати розмітку
    before = len(sent)
    twitch.events.emit(DropClaimed(drop_name="x", game="<b>Гра</b>", rewards="a & b"))
    await twitch.events.drain()
    body = sent[-1][1]
    check("екранування HTML", "&lt;b&gt;" in body and "&amp;" in body, body)

    # ---------------------------------------------------------------- тексти
    print("\n[2] Тексти станів")
    check("статус", "Стан майнера" in tg._status_text())
    check("інвентар", "дропах" in tg._inventory_text())
    check("кампанії", "Кампанії" in tg._campaigns_text())

    # ---------------------------------------------------------------- команди
    print("\n[3] Команди")
    for cmd, expect in [
        ("/help", "Команди"),
        ("/start", "Команди"),
        ("/status", "Стан майнера"),
        ("/inventory", "дропах"),
        ("/campaigns", "Кампанії"),
        ("/priority", "Пріоритет ігор"),
        ("/абракадабра", "Невідома команда"),
    ]:
        before = len(sent)
        await tg._handle_command(OWNER, cmd)
        got = sent[before:]
        check(cmd, len(got) == 1 and expect in got[0][1], f"надіслано={got}")

    # команди, що змінюють стан
    print("\n[4] Команди керування → шина команд")
    for cmd, kind in [
        ("/pause", CommandType.PAUSE),
        ("/resume", CommandType.RESUME),
        ("/reload", CommandType.RELOAD),
        ("/switch ibeast", CommandType.SWITCH),
        ("/priority add EVE Online", CommandType.PRIORITY_ADD),
        ("/priority remove EVE Online", CommandType.PRIORITY_REMOVE),
    ]:
        await tg._handle_command(OWNER, cmd)
        pending = twitch.control.drain_pending()
        check(cmd, len(pending) == 1 and pending[0].type is kind,
              f"отримано={[p.type for p in pending]}")

    check("/switch передав аргумент", True)
    await tg._handle_command(OWNER, "/switch someone")
    cmds = twitch.control.drain_pending()
    check("аргумент каналу", cmds and cmds[0].argument == "someone", str(cmds))

    # команда в груповому вигляді /cmd@bot
    before = len(sent)
    await tg._handle_command(OWNER, "/status@DropFarm_bot")
    check("команда з @назвою бота", len(sent) == before + 1)

    # ---------------------------------------------------------------- безпека
    print("\n[5] Безпека: чужий chat_id")
    before_sent = len(sent)
    twitch.control.drain_pending()
    update = {"update_id": 1, "message": {"chat": {"id": STRANGER}, "text": "/pause"}}
    # проганяємо через той самий фільтр, що й у _poll_loop
    allowed = update["message"]["chat"]["id"] in tg._chat_ids
    if allowed:
        await tg._handle_command(STRANGER, "/pause")
    check("чужого не пущено", not allowed)
    check("чужий не надіслав команду", len(twitch.control.drain_pending()) == 0)
    check("чужому не відповіли", len(sent) == before_sent)

    # ---------------------------------------------------------------- вимкнені категорії
    print("\n[6] Категорії сповіщень вимикаються")
    tg._config["notify_rewards"] = False
    before = len(sent)
    twitch.events.emit(DropClaimed(drop_name="x", game="y", rewards="z"))
    await twitch.events.drain()
    check("rewards вимкнено — тиша", len(sent) == before)
    tg._config["notify_rewards"] = True

    tg._config["notify_routine"] = False
    tg._last_routine.clear()
    before = len(sent)
    twitch.events.emit(StreamOffline(channel_name="ibeast"))
    await twitch.events.drain()
    check("routine вимкнено — тиша", len(sent) == before)

    # ---------------------------------------------------------------- троттлінг
    print("\n[7] Троттлінг рутини")
    tg._config["notify_routine"] = True
    tg._last_routine.clear()
    before = len(sent)
    for _ in range(5):
        twitch.events.emit(StreamOffline(channel_name="ibeast"))
    await twitch.events.drain()
    check("5 однакових подій → 1 повідомлення", len(sent) == before + 1,
          f"надіслано {len(sent) - before}")

    print(f"\n{'='*50}\nПройдено: {ok}   Провалено: {fail}")
    return 1 if fail else 0


raise SystemExit(asyncio.run(main()))
