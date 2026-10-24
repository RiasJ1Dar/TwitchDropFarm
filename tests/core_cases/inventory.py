"""Контракт свіжого інвентаря, кешу деталей та автоматичної видачі."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from core.events import Command, CommandType
from core.inventory import CAMPAIGN_DETAILS_TTL, InventoryLoader, blend
from core.model import Campaign
from tests.core_cases.support import check, miner


def inventory_checks() -> None:
    async def run():
        calls = []
        clock = [100.0]
        version = [1]

        async def details(payload):
            calls.append(payload)
            return [{"data": {"user": {"dropCampaign": {
                "id": item["variables"]["dropID"], "version": version[0],
            }}}} for item in payload]

        loader = InventoryLoader(details, lambda: "42", lambda _: None, clock=lambda: clock[0])
        wanted = [(str(n), {"id": str(n), "name": "listed"}) for n in range(41)]
        result = await loader.load_details(wanted)
        check("деталі розбиті на пакети 20/20/1", sorted(map(len, calls)) == [1, 20, 20])
        check("паралельні пакети зібрано без втрат", len(result) == 41)
        check("деталі отримують чинний user_id",
              all(q["variables"]["channelLogin"] == "42" for batch in calls for q in batch))
        check("базові поля збережені при злитті", result["0"]["name"] == "listed")
        calls.clear()
        version[0] = 2
        clock[0] += CAMPAIGN_DETAILS_TTL - 1
        result = await loader.load_details(wanted)
        check("до TTL деталі беруться з кешу", not calls and result["0"]["version"] == 1)
        loader.invalidate("0")
        result = await loader.load_details(wanted)
        check("адресна інвалідація перечитує тільки одну кампанію",
              [q["variables"]["dropID"] for batch in calls for q in batch] == ["0"])
        check("адресна інвалідація зберігає решту кешу",
              result["0"]["version"] == 2 and result["1"]["version"] == 1)
        calls.clear()
        clock[0] += 1
        await loader.load_details(wanted)
        check("на межі TTL старі 40 записів перечитуються", sum(map(len, calls)) == 40)
        calls.clear()
        loader.invalidate()
        await loader.load_details(wanted)
        check("повне очищення перечитує всі деталі", sum(map(len, calls)) == 41)
        await loader.load_details(wanted[:1])
        calls.clear()
        await loader.load_details(wanted[:2])
        check("кампанії поза поточним списком прибираються з кешу",
              [q["variables"]["dropID"] for batch in calls for q in batch] == ["1"])
        base = {"self": {"currentMinutesWatched": 15}, "items": [1]}
        extra = {"self": {"currentMinutesWatched": 0, "isClaimed": False}, "items": [2]}
        before = deepcopy((base, extra))
        merged = blend(base, extra)
        check("свіжий self інвентаря виграє за розбіжності",
              merged["self"] == {"currentMinutesWatched": 15, "isClaimed": False})
        check("злиття не переписує вхідні відповіді", before == (base, extra))

        body = {
            "id": "auto", "name": "Auto", "status": "ACTIVE",
            "game": {"id": "1", "name": "Game"}, "self": {"isAccountConnected": True},
            "startAt": "2020-01-01T00:00:00Z", "endAt": "2099-01-01T00:00:00Z",
            "timeBasedDrops": [{
                "id": "drop", "name": "Drop", "startAt": "2020-01-01T00:00:00Z",
                "endAt": "2099-01-01T00:00:00Z", "requiredMinutesWatched": 15,
                "preconditionDrops": None, "self": None,
                "benefitEdges": [{"benefit": {"id": "reward", "name": "Reward",
                                             "distributionType": "DIRECT_ENTITLEMENT"}}],
            }],
        }
        awarded = [{"id": "reward", "lastAwardedAt": "2026-10-24T09:00:00Z"}]
        requests = []

        async def backend(payload):
            requests.append(payload)
            if isinstance(payload, list):
                return [{"data": {"user": {"dropCampaign": deepcopy(body)}}}]
            if payload["operationName"] == "Inventory":
                return {"data": {"currentUser": {"inventory": {
                    "dropCampaignsInProgress": [], "gameEventDrops": deepcopy(awarded),
                }}}}
            return {"data": {"currentUser": {"dropCampaigns": [deepcopy(body)]}}}

        status = []
        inventory = InventoryLoader(backend, lambda: "1", status.append)
        snapshot = await inventory.load()
        owner = SimpleNamespace(cosmetics_wanted=False)
        model = Campaign(owner, snapshot.campaigns["auto"], snapshot.awarded)
        check("gameEventDrops розпізнає видачу без кампанії in-progress і без self",
              model.everything_taken)
        check("час видачі розібраний як UTC", snapshot.awarded["reward"]
              == datetime(2026, 10, 24, 9, tzinfo=timezone.utc))
        awarded.clear()
        snapshot = await inventory.load()
        check("відсутність in-progress сама по собі не означає видачу",
              not Campaign(owner, snapshot.campaigns["auto"], snapshot.awarded).everything_taken)
        check("INVENTORY завжди свіжий навіть із теплим кешем деталей",
              sum(isinstance(q, dict) and q["operationName"] == "Inventory"
                  for q in requests) == 2 and sum(isinstance(q, list) for q in requests) == 1)
        check("кожне завантаження повідомляє стадію деталей", len(status) == 2)

        twitch = miner()
        twitch.inventory = inventory
        twitch._apply(Command(CommandType.RELOAD))
        requests.clear()
        await inventory.load()
        check("ручний RELOAD очищує кеш через власника",
              any(isinstance(q, list) for q in requests))

        failure = [True]
        batches = []

        async def partial(payload):
            ids = [q["variables"]["dropID"] for q in payload]
            batches.append(ids)
            if "20" in ids and failure[0]:
                await asyncio.sleep(0.01)
                raise RuntimeError("detail failure")
            return [{"data": {"user": {"dropCampaign": {"id": cid}}}} for cid in ids]

        recovering = InventoryLoader(partial, lambda: "1", lambda _: None)
        try:
            await recovering.load_details(wanted[:21])
        except RuntimeError as error:
            check("частковий збій не перетворюється на успішний інвентар",
                  str(error) == "detail failure")
        else:
            check("частковий збій не перетворюється на успішний інвентар", False)
        failure[0] = False
        batches.clear()
        await recovering.load_details(wanted[:21])
        check("після часткового збою успішні пакети збережені", batches == [["20"]])

        started, ended = set(), set()
        all_started = asyncio.Event()

        async def blocking(payload):
            key = payload[0]["variables"]["dropID"]
            started.add(key)
            if len(started) == 2:
                all_started.set()
            try:
                await asyncio.Future()
            finally:
                ended.add(key)

        pending = InventoryLoader(blocking, lambda: "1", lambda _: None)
        task = asyncio.create_task(pending.load_details(wanted[:21]))
        await asyncio.wait_for(all_started.wait(), 1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        check("скасування дочікується обох дочірніх запитів", started == ended and len(ended) == 2)

        old = twitch.campaigns = [model]

        async def broken(_payload):
            raise RuntimeError("inventory failure")

        twitch.inventory = InventoryLoader(broken, lambda: "1", lambda _: None)
        try:
            await twitch.load_inventory()
        except RuntimeError as error:
            check("збій свіжого інвентаря видимий координатору",
                  str(error) == "inventory failure")
        else:
            check("збій свіжого інвентаря видимий координатору", False)
        check("невдале завантаження не підміняє канонічну модель", twitch.campaigns is old)

    asyncio.run(run())
