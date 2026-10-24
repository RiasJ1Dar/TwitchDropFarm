"""Регресії: completion."""
from __future__ import annotations

import asyncio
import logging
import types
from datetime import datetime, timedelta, timezone

from core.config import Stage
from core.model import Campaign
from tests.core_cases.support import check, miner


def claim_checks() -> None:
    print("\n[2] Щойно забраний дроп — не «паралельний перегляд»")
    twitch = miner()
    channel = types.SimpleNamespace(id=1, name="канал", game="гра")
    taken = types.SimpleNamespace(
        name="дроп", taken=True,
        campaign=types.SimpleNamespace(game=types.SimpleNamespace(name="гра")),
        farmable=lambda _c: False,
        set_counted=lambda _m: None,
    )
    twitch._drops = {"d1": taken}

    async def fake_graphql(_payload):
        return {"data": {"currentUser": {"dropCurrentSession": {
            "dropID": "d1", "currentMinutesWatched": 30,
        }}}}

    twitch.graphql = fake_graphql

    записи: list[logging.LogRecord] = []

    class Catcher(logging.Handler):
        def emit(self, record):
            записи.append(record)

    logger = logging.getLogger("TwitchDrops")
    catcher = Catcher()
    previous = logger.level
    logger.addHandler(catcher)
    logger.setLevel(logging.DEBUG)
    try:
        result = asyncio.run(twitch._confirm_progress(channel))
    finally:
        logger.removeHandler(catcher)
        logger.setLevel(previous)  # інакше наступні групи почнуть шуміти в консоль

    warned = [r for r in записи if r.levelno >= logging.WARNING]
    check("підтвердження не вдалось", result is False)
    check("про паралельний перегляд не кричить", not warned,
          "; ".join(r.getMessage() for r in warned))


def completed_drop_checks() -> None:
    print("\n[2а] Автоматична видача без PubSub drop-claim")
    twitch = miner()
    body = {
        "id": "auto-campaign", "name": "Кампанія",
        "game": {"id": "1", "displayName": "Гра"},
        "self": {"isAccountConnected": True}, "status": "ACTIVE",
        "startAt": "2020-01-01T00:00:00Z", "endAt": "2099-01-01T00:00:00Z",
        "timeBasedDrops": [{
            "id": "auto-drop", "name": "Нагорода",
            "benefitEdges": [{"benefit": {
                "id": "benefit", "name": "Нагорода", "distributionType": "DIRECT_ENTITLEMENT",
            }}],
            "startAt": "2020-01-01T00:00:00Z", "endAt": "2099-01-01T00:00:00Z",
            "preconditionDrops": None, "requiredMinutesWatched": 15,
        }],
    }
    campaign = Campaign(twitch, body, {})
    drop = campaign.drops["auto-drop"]
    channel = types.SimpleNamespace(id=1, name="канал", game=campaign.game, live=True)
    twitch.campaigns = [campaign]
    twitch._drops = {drop.id: drop}
    twitch.wanted = [campaign.game]
    twitch.watching.put(channel)
    twitch._stage = Stage.PICK_CHANNEL
    from core.inventory import InventoryLoader

    detail_requests = []

    async def details_query(payload):
        ids = [item["variables"]["dropID"] for item in payload]
        detail_requests.extend(ids)
        return [{"data": {"user": {"dropCampaign": {"id": cid}}}} for cid in ids]

    twitch.inventory = InventoryLoader(details_query, lambda: "1", lambda _: None)
    wanted_details = [(campaign.id, body), ("other", {"id": "other"})]
    asyncio.run(twitch.inventory.load_details(wanted_details))
    detail_requests.clear()

    async def progress(_payload):
        return {"data": {"currentUser": {"dropCurrentSession": {
            "dropID": drop.id, "currentMinutesWatched": 15,
        }}}}

    drop.counted_minutes = 14
    check("14/15 не викликає перечитування",
          not twitch._refresh_completed_drops(channel, now=100.0))
    twitch.graphql = progress
    asyncio.run(twitch._confirm_progress(channel))
    check("періодичний запит підтвердив 15/15 без події", drop.counted_minutes == 15)
    check("PubSub claim ID не потрібний для перевірки видачі", drop.claim_id is None)
    check("завершення запускає перечитування інвентаря",
          twitch._refresh_completed_drops(channel, now=100.0))
    check("стан переходить до LOAD_INVENTORY", twitch._stage is Stage.LOAD_INVENTORY)
    check("старий канал більше не отримує HLS", not twitch.watching.filled)
    asyncio.run(twitch.inventory.load_details(wanted_details))
    check("self завершеної кампанії не береться зі старого кешу",
          campaign.id in detail_requests)
    check("кеш інших кампаній збережено", "other" not in detail_requests)

    # Видача ще не відобразилась у self: не перечитуємо інвентар кожні 20 с.
    twitch.watching.put(channel)
    twitch._stage = Stage.PICK_CHANNEL
    check("відкладений self має паузу між перевірками",
          not twitch._refresh_completed_drops(channel, now=159.0))
    check("через хвилину можна перевірити знову",
          twitch._refresh_completed_drops(channel, now=160.0))
    twitch.watching.put(channel)
    twitch._stage = Stage.PICK_CHANNEL
    twitch._paused = True
    check("пауза користувача не скасовується перевіркою",
          not twitch._refresh_completed_drops(channel, now=220.0))
    twitch._paused = False
    other = types.SimpleNamespace(id=2, name="інший канал", game=campaign.game, live=True)
    check("відповідь старого каналу не зупиняє новий перегляд",
          not twitch._refresh_completed_drops(other, now=220.0))
    twitch._stage = Stage.QUIT
    check("завершення програми не скасовується перевіркою",
          not twitch._refresh_completed_drops(channel, now=220.0))

    async def delivered():
        return True

    async def run_completion_loop():
        twitch._completed_drop_refresh.clear()
        twitch._stage = Stage.PICK_CHANNEL
        twitch._stage_changed.clear()
        channel.report_watching = delivered
        task = asyncio.create_task(twitch._watch_loop())
        try:
            await asyncio.wait_for(twitch._stage_changed.wait(), timeout=1.0)
            return twitch._stage is Stage.LOAD_INVENTORY and not twitch.watching.filled
        except asyncio.TimeoutError:
            return False
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    from unittest.mock import patch

    from core import miner as miner_module

    with patch.object(miner_module, "PROGRESS_GRACE", timedelta(0)):
        check("робочий HLS-цикл сам виходить із завершеного дропа без PubSub",
              asyncio.run(run_completion_loop()))

    # Точна форма живого збою 24.10: self порожній, нагорода вже в gameEventDrops.
    fresh = Campaign(twitch, body, {"benefit": datetime.now(timezone.utc)})
    twitch.campaigns = [fresh]
    twitch._stage = Stage.PICK_CHANNEL
    twitch.watching.put(channel)
    check("інвентар розпізнає автоматично видану нагороду", fresh.everything_taken)
    check("забраний дроп не утворює цикл перечитувань",
          not twitch._refresh_completed_drops(channel, now=220.0))
