"""Одна активна сесія перегляду та її задача; модель хвилин належить кампаніям."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from time import monotonic

from core.channels import Channel
from core.config import PROGRESS_GRACE, WATCH_PERIOD
from core.model import Campaign, Drop
from core.toolbox import Slot, TaskKeeper, sleep_unless

# Автовидача може випередити self/claim ID; не перепитуємо кожні 20 с.
COMPLETED_DROP_REFRESH = 60.0


@dataclass(frozen=True)
class WatchHooks:
    allowed: Callable[[], bool]
    progress_mark: Callable[[], int | None]
    confirm: Callable[[Channel, Callable[[], bool]], Awaitable[bool]]
    completed: Callable[[Channel], bool]
    delivered: Callable[[Channel], None]
    failed: Callable[[Channel], None]
    check_stall: Callable[[int | None, Channel], None]
    stop: Callable[[], None]


class WatchSession:
    def __init__(self, hooks: WatchHooks, tasks: TaskKeeper):
        self.hooks = hooks
        self._tasks = tasks
        self.watching: Slot[Channel] = Slot()
        self.restart = asyncio.Event()
        self._changed = asyncio.Event()
        self._generation = 0
        self._task: asyncio.Task | None = None
        self._completion_retry: dict[str, float] = {}

    def activate(self, channel: Channel) -> None:
        self.watching.put(channel)
        self.interrupt()

    def deactivate(self) -> None:
        self.watching.clear()
        self.interrupt()

    def interrupt(self) -> None:
        self._generation += 1
        self._changed.set()
        self.restart.set()

    def matches(self, channel: Channel, generation: int | None = None) -> bool:
        return (self.watching.peek(None) is channel and self.hooks.allowed()
                and (generation is None or generation == self._generation))

    def pending_completions(self, campaigns: Sequence[Campaign], channel: Channel,
                            counts_here: Callable[[Campaign, Channel], bool],
                            *, now: float | None = None) -> list[Drop]:
        if not self.matches(channel):
            return []
        clock = monotonic() if now is None else now
        completed = [
            drop for campaign in campaigns if counts_here(campaign, channel)
            for drop in campaign.all_drops
            if not drop.taken and drop.required_minutes > 0
            and drop.counted_minutes >= drop.required_minutes
            and clock >= self._completion_retry.get(drop.id, 0.0)
        ]
        for drop in completed:
            self._completion_retry[drop.id] = clock + COMPLETED_DROP_REFRESH
        return completed

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = self._tasks.launch(self.run())

    async def shutdown(self) -> None:
        self.deactivate()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        while True:
            channel = await self.watching.take()
            self._changed.clear()
            generation = self._generation
            if channel is None:
                continue
            if not self.hooks.allowed():
                await self._changed.wait()
                continue
            if not channel.live:
                self.hooks.stop()
                continue
            tick = self._tasks.launch(self._tick(channel, generation))
            changed = self._tasks.launch(self._changed.wait())
            if tick is None or changed is None:
                raise RuntimeError("Не вдалося запустити прохід сесії перегляду")
            try:
                done, _ = await asyncio.wait((tick, changed), return_when=asyncio.FIRST_COMPLETED)
                if tick in done:
                    await tick
            finally:
                tick.cancel()
                changed.cancel()
                await asyncio.gather(tick, changed, return_exceptions=True)

    async def _tick(self, channel: Channel, generation: int) -> None:
        def valid() -> bool:
            return self.matches(channel, generation)
        if not valid():
            return
        before = self.hooks.progress_mark()
        sent_at = monotonic()
        delivered = await channel.report_watching()
        if not valid():
            return
        if delivered:
            self.hooks.delivered(channel)
        else:
            self.hooks.failed(channel)
        await asyncio.sleep(max(0.0, PROGRESS_GRACE.total_seconds() - (monotonic() - sent_at)))
        if not valid():
            return
        # Показуємо лише підтверджені Twitch хвилини, без локального лічильника.
        await self.hooks.confirm(channel, valid)
        if not valid():
            return
        if self.hooks.completed(channel):
            return
        self.hooks.check_stall(before, channel)
        self.restart.clear()
        remaining = max(0.0, WATCH_PERIOD.total_seconds() - (monotonic() - sent_at))
        await sleep_unless(self.restart, remaining)
