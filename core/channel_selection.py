"""Пошук, ранжування та відстійник каналів; Stage і підписки координує Miner."""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from core import protocol
from core.api import ApiError
from core.channels import Backend, Channel
from core.config import MAX_CHANNELS, TRACE
from core.model import Campaign
from core.toolbox import Game, batched
from core.watchdog import ChannelBench

log = logging.getLogger("TwitchDrops")


def can_farm(campaigns: Sequence[Campaign], wanted: Sequence[Game],
             channel: Channel | None) -> bool:
    if channel is None or not channel.live:
        return False
    return any(
        campaign.farmable(channel)
        and ((channel.game is not None and channel.drops_on and channel.game in wanted)
             or campaign.game.any_channel)
        for campaign in campaigns
    )


def should_switch(channel: Channel, current: Channel | None, *, remaining: int,
                  farmable: Callable[[Channel | None], bool],
                  priority: Callable[[Channel], int]) -> bool:
    # Не кидаємо вкладені години перед самою нагородою.
    if remaining and farmable(current):
        log.log(TRACE, f"Не перемикаюсь: до нагороди {remaining} хв")
        return False
    if not farmable(channel):
        return False
    if current is None or not farmable(current):
        return True
    here, there = priority(channel), priority(current)
    if here != there:
        return here < there
    return channel.from_allowlist_flag and not current.from_allowlist_flag


class ChannelSelector:
    def __init__(self, backend: Backend):
        self.backend = backend
        self.channels: OrderedDict[int, Channel] = OrderedDict()
        self.bench = ChannelBench()

    def remove_stale(self, wanted: Sequence[Game], *, full_sweep: bool) -> list[Channel]:
        doomed = list(self.channels.values()) if not wanted or full_sweep else [
            c for c in self.channels.values()
            if not c.from_allowlist_flag and (c.dark or c.game is None or c.game not in wanted)
        ]
        for channel in doomed:
            self.channels.pop(channel.id, None)
        return doomed

    async def discover(self, campaigns: Sequence[Campaign], wanted: Sequence[Game],
                       priority: Callable[[Channel], int], *, now: datetime) -> list[Channel]:
        pool = set(self.channels.values())
        self.channels.clear()
        soon = now + timedelta(hours=1)
        listed: set[Channel] = set()
        open_games: set[Game] = set()
        for campaign in campaigns:
            if campaign.game in wanted and campaign.farmable_before(soon):
                if campaign.channels:
                    listed.update(campaign.channels)
                else:
                    open_games.add(campaign.game)
        listed -= pool
        directory = [asyncio.ensure_future(self.find_streams(game)) for game in open_games]
        try:
            await self.check_many(list(listed))
            pool |= listed
            for found in await asyncio.gather(*directory):
                pool.update(found)
        except BaseException:
            for job in directory:
                job.cancel()
            await asyncio.gather(*directory, return_exceptions=True)
            raise
        ranked = sorted(pool, key=lambda c: c.viewers, reverse=True)
        ranked.sort(key=lambda c: c.from_allowlist_flag, reverse=True)
        ranked.sort(key=priority)
        keep, spare = ranked[:MAX_CHANNELS], ranked[MAX_CHANNELS:]
        for channel in keep:
            self.channels[channel.id] = channel
        return spare

    async def find_streams(self, game: Game, *, limit: int = 20) -> list[Channel]:
        try:
            answer = await self.backend.graphql(protocol.GAME_DIRECTORY(
                slug=game.slug, limit=limit,
                options={"systemFilters": ["DROPS_ENABLED"]},
            ))
        except ApiError as error:
            log.warning(f"Каталог {game.slug} недоступний: {error}")
            return []
        node = answer["data"].get("game")
        if not node:
            return []
        return [
            Channel.from_directory(self.backend, edge["node"], drops_on=True)
            for edge in node["streams"]["edges"]
            if edge["node"]["broadcaster"] is not None
        ]

    async def check_many(self, channels: list[Channel]) -> None:
        """Пакетна перевірка статусу — замість запиту на кожен канал окремо."""
        if not channels:
            return
        by_id = {c.id: c for c in channels}
        jobs = [
            asyncio.ensure_future(self.backend.graphql(
                [protocol.STREAM_INFO(channel=c.login) for c in portion]
            ))
            for portion in batched(channels, 20)
        ]
        found: dict[int, dict[str, Any]] = {}
        try:
            for finished in asyncio.as_completed(jobs):
                for reply in await finished:
                    body = reply["data"]["user"]
                    if body:
                        found[int(body["id"])] = body
        except BaseException:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            raise

        drops_map: dict[int, list[dict[str, Any]]] = {}
        if self.backend.verify_drops_enabled:
            live_ids = [cid for cid, body in found.items() if body["stream"]]
            drop_jobs = [
                asyncio.ensure_future(self.backend.graphql(
                    [protocol.CHANNEL_DROPS(channelID=str(cid)) for cid in portion]
                ))
                for portion in batched(live_ids, 20)
            ]
            try:
                for finished in asyncio.as_completed(drop_jobs):
                    for reply in await finished:
                        node = reply["data"]["channel"]
                        drops_map[int(node["id"])] = node["viewerDropCampaigns"] or []
            except BaseException:
                for job in drop_jobs:
                    job.cancel()
                await asyncio.gather(*drop_jobs, return_exceptions=True)
                raise

        for channel_id, body in found.items():
            if (channel := by_id.get(channel_id)) is not None:
                channel.apply_bulk_update(body, drops_map.get(channel_id, []))
