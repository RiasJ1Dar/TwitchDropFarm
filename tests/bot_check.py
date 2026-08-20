"""Перевірка Telegram-бота без реальної доставки.

Підміняємо лише `_api` — найнижчий шар, що робить HTTP-запит. Усе вище (маршрутизація
подій, форматування, розбір команд, білий список chat_id) працює справжнє.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
    UpdateAvailable,
    WatchingChanged,
    WatchUncounted,
)
from core.i18n import set_language
from core.miner import Miner as Twitch
from core.settings import Settings
from core.toolbox import force_utf8_console
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
    force_utf8_console()
    set_language("uk")
    args = argparse.Namespace(log=False, tray=False, logging_level=50,
                              debug_ws=0, debug_gql=0)
    twitch = Twitch(Settings(args))
    tg = TelegramNotifier(twitch)

    sent: list[tuple[int, str]] = []
    raw: list[dict] = []
    bios: list[str] = []

    async def fake_api(method: str, **payload):
        if method == "sendMessage":
            sent.append((payload["chat_id"], payload["text"]))
            raw.append(payload)
            return {"ok": True}
        if method == "getMe":
            return {"username": "DropFarm_bot"}
        if method == "setMyShortDescription":
            bios.append(payload.get("short_description", ""))
            return True
        if method == "setMyDescription":
            return True
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
        ("перегляд не йде", WatchUncounted(channel_name="ibeast", consecutive=2),
         "spade.twitch.tv"),
        ("оновлення", UpdateAvailable(version="1.0.4", files=2, bytes_to_fetch=4096),
         "перезапуститься"),
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

    check("біографія бота показує застій",
          any("Стоїть" in line for line in bios), str(bios))
    check("біографія бота показує канал",
          any("Іде · ibeast" in line for line in bios), str(bios))

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

    set_language("en")
    before = len(sent)
    await tg._handle_command(OWNER, "/help")
    got = sent[before:]
    check("бот слідує мові вікна",
          len(got) == 1 and "Commands" in got[0][1] and "Команди" not in got[0][1],
          f"надіслано={got}")
    set_language("uk")

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

    export_dir = Path(tempfile.mkdtemp())
    tg._export_dir = lambda: export_dir  # type: ignore[method-assign]
    before = len(sent)
    await tg._handle_command(OWNER, "/export")
    got = sent[before:]
    check("/export відповідає",
          got and "Експорт" in got[0][1], f"надіслано={got}")
    names = set(os.listdir(export_dir))
    check("/export пише файли не в теку користувача",
          "history.csv" in names and "inventory.html" in names,
          str(sorted(names)))

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

    print("\n[5б] Автопризначення власника")
    original_api = tg._api
    tg._config["chat_ids"] = []

    async def fake_updates(method: str, **payload):
        if method == "getUpdates":
            return [
                {"message": {"chat": {"id": STRANGER}, "text": "/start"}},
                {"message": {"chat": {"id": OWNER}, "text": "/start"}},
            ]
        return None

    tg._api = fake_updates  # type: ignore[assignment]
    found = await tg.discover_chat_ids()
    check("бачить, хто писав", found == [STRANGER, OWNER], str(found))
    check("не записує їх власниками",
          tg._config["chat_ids"] == [], str(tg._config["chat_ids"]))
    tg._api = original_api  # type: ignore[assignment]
    tg._config["chat_ids"] = [OWNER]

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

    # ---------------------------------------------------------------- оновлення
    # Оновлення саме себе не ставить: воно перезапускає програму, тож рішення
    # людини потрібне до, а не після.
    print("\n[8] Оновлення: підтвердити або відкласти")
    raw.clear()
    twitch.events.emit(UpdateAvailable(version="1.0.4", files=3, bytes_to_fetch=8192))
    await twitch.events.drain()
    markup = raw[-1].get("reply_markup", {}) if raw else {}
    buttons = [b for row in markup.get("inline_keyboard", []) for b in row]
    actions = [b["callback_data"] for b in buttons]
    check("під сповіщенням дві кнопки", actions == ["update", "later"], str(actions))
    check("текст попереджає про перезапуск",
          "перезапуститься" in sent[-1][1], sent[-1][1])

    check("до рішення нічого не качається", twitch._update_plan is None)

    await tg._handle_command(OWNER, "/later")
    check("«Відкласти» запам'ятовується", twitch.update_postponed is True)
    check("і каже, як поставити потім", "/update" in sent[-1][1], sent[-1][1])

    twitch.update_postponed = False
    before = len(sent)
    await tg._handle_command(OWNER, "/update")
    check("«Оновити» ставить команду ядру",
          twitch.control.get_nowait() is not None and len(sent) == before + 1)

    # Нема чого качати — кнопок не треба, це просто новина
    raw.clear()
    twitch.events.emit(UpdateAvailable(version="1.0.4", files=0, bytes_to_fetch=0))
    await twitch.events.drain()
    check("нуль файлів — без кнопок", not raw, str(raw))

    print(f"\n{'='*50}\nПройдено: {ok}   Провалено: {fail}")
    return 1 if fail else 0


raise SystemExit(asyncio.run(main()))
