"""Завантаження інвентаря; кеш деталей належить лише цьому компоненту."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any

from core import protocol
from core.i18n import t
from core.toolbox import batched, parse_timestamp

# Описи змінюються рідко; перечитувати десятки деталей щогодини зайве.
CAMPAIGN_DETAILS_TTL = 5400.0


@dataclass(frozen=True)
class InventorySnapshot:
    campaigns: dict[str, dict[str, Any]]
    awarded: dict[str, datetime]


class InventoryLoader:
    def __init__(self, graphql: Callable[[Any], Awaitable[Any]],
                 user_id: Callable[[], str], status: Callable[[str], None],
                 *, clock: Callable[[], float] = monotonic):
        self._graphql = graphql
        self._user_id = user_id
        self._status = status
        self._clock = clock
        self._details: dict[str, tuple[float, dict[str, Any]]] = {}

    def invalidate(self, campaign_id: str | None = None) -> None:
        if campaign_id is None:
            self._details.clear()
        else:
            self._details.pop(campaign_id, None)

    async def load(self) -> InventorySnapshot:
        # INVENTORY завжди свіжий: TTL стосується лише CAMPAIGN_DETAILS.
        answer = await self._graphql(protocol.INVENTORY())
        block = answer["data"]["currentUser"]["inventory"]
        in_progress = block["dropCampaignsInProgress"] or []
        awarded = {
            item["id"]: parse_timestamp(item["lastAwardedAt"])
            for item in block["gameEventDrops"] or []
        }
        merged: dict[str, dict[str, Any]] = {c["id"]: c for c in in_progress}
        answer = await self._graphql(protocol.CAMPAIGN_LIST())
        listed = answer["data"]["currentUser"]["dropCampaigns"] or []
        interesting = {c["id"]: c for c in listed if c["status"] in ("ACTIVE", "UPCOMING")}
        self._status(t("status_details"))
        details = await self.load_details(list(interesting.items()))
        for campaign_id, extra in details.items():
            merged[campaign_id] = blend(merged.get(campaign_id, {}), extra)
        return InventorySnapshot(merged, awarded)

    async def load_details(self, wanted: list[tuple[str, dict[str, Any]]]
                           ) -> dict[str, dict[str, Any]]:
        now = self._clock()
        collected: dict[str, dict[str, Any]] = {}
        missing: list[tuple[str, dict[str, Any]]] = []
        for cid, body in wanted:
            entry = self._details.get(cid)
            if entry is not None and (now - entry[0]) < CAMPAIGN_DETAILS_TTL:
                collected[cid] = entry[1]
            else:
                missing.append((cid, body))
        if missing:
            login = self._user_id()
            jobs = [asyncio.ensure_future(self._graphql([
                protocol.CAMPAIGN_DETAILS(channelLogin=login, dropID=cid) for cid, _ in portion
            ])) for portion in batched(missing, 20)]
            base = dict(missing)
            try:
                for finished in asyncio.as_completed(jobs):
                    for reply in await finished:
                        body = reply["data"]["user"]["dropCampaign"]
                        if body:
                            blended = blend(base.get(body["id"], {}), body)
                            self._details[body["id"]] = (now, blended)
                            collected[body["id"]] = blended
            except BaseException:
                # Дочікуємось скасування й при CancelledError, щоб запити старого
                # завантаження не записали кеш уже після RELOAD або shutdown.
                for job in jobs:
                    job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
                raise
        current_ids = {cid for cid, _ in wanted}
        for cid in list(self._details):
            if cid not in current_ids:
                self._details.pop(cid, None)
        return collected


def blend(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """За розбіжності виграє інвентар, зокрема його свіжий self."""
    merged = dict(extra)
    for key, value in base.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = blend(value, current)
        else:
            merged[key] = value
    return merged
