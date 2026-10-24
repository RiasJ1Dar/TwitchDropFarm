"""Команди користувача переривають сесію на кожному мережевому очікуванні."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from core import watch_session as session_module
from core.config import Stage
from core.events import Command, CommandType
from core.toolbox import Game
from tests.core_cases.channels import Candidate
from tests.core_cases.support import check, miner


def session_checks() -> None:
    async def scenario(phase, action):
        twitch = miner()
        game = Game({"id": "1", "name": "Game"})
        old, new = Candidate(1, game), Candidate(2, game)
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        queries, counted = [], []
        head_calls = []
        drop = SimpleNamespace(taken=False, farmable=lambda _: True,
                               set_counted=counted.append, name="Drop")
        twitch._drops = {"d": drop}
        twitch._stage = Stage.PICK_CHANNEL

        async def reporting():
            head_calls.append(1)
            try:
                if action == "same" and len(head_calls) > 1:
                    await asyncio.Future()
                if phase == "HLS":
                    started.set()
                    await release.wait()
                elif phase == "grace":
                    started.set()
                return True
            finally:
                finished.set()

        async def new_reporting():
            await asyncio.Future()

        async def graphql(payload):
            queries.append(payload["variables"]["channelID"])
            if phase == "GraphQL":
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    # Імітуємо відповідь, яка вже готова одночасно зі скасуванням.
                    # Вона все одно повинна пройти перевірку покоління.
                    pass
            return {"data": {"currentUser": {"dropCurrentSession": {
                "dropID": "d", "currentMinutesWatched": 99,
            }}}}

        old.report_watching, new.report_watching = reporting, new_reporting
        twitch.graphql = graphql
        grace = timedelta(seconds=60) if phase == "grace" else timedelta(0)
        with patch.object(session_module, "PROGRESS_GRACE", grace):
            twitch.watch(old, announce=False)
            twitch.watch_session.start()
            await asyncio.wait_for(started.wait(), 1)
            if action in ("switch", "same"):
                twitch.watch(old if action == "same" else new, announce=False)
            elif action == "restart":
                twitch.go(Stage.RESTART)
            else:
                twitch._apply(Command({
                    "pause": CommandType.PAUSE, "reload": CommandType.RELOAD,
                    "quit": CommandType.SHUTDOWN,
                }[action]))
            release.set()
            for _ in range(8):
                await asyncio.sleep(0)
            await twitch.watch_session.shutdown()
        label = f"{phase}/{action}"
        check(label + ": старий прохід завершився", finished.is_set())
        check(label + ": стара відповідь не змінює прогрес", not counted)
        if phase != "GraphQL":
            check(label + ": старий канал не надсилає підтвердження", "1" not in queries)

    async def run():
        for phase in ("HLS", "grace", "GraphQL"):
            for action in ("pause", "reload", "quit", "switch", "same", "restart"):
                await scenario(phase, action)

        twitch = miner()
        channel = Candidate(1, Game({"id": "1", "name": "Game"}))
        entered, ended = asyncio.Event(), asyncio.Event()
        starts = []

        async def blocked():
            starts.append(1)
            entered.set()
            try:
                await asyncio.Future()
            finally:
                ended.set()

        channel.report_watching = blocked
        twitch._stage = Stage.PICK_CHANNEL
        twitch.watch(channel, announce=False)
        twitch.watch_session.start()
        twitch.watch_session.start()
        await asyncio.wait_for(entered.wait(), 1)
        check("повторний start не запускає другий перегляд", starts == [1])
        await twitch.watch_session.shutdown()
        check("shutdown дочікується HLS-задачі", ended.is_set())
        check("shutdown звільняє активний канал", not twitch.watching.filled)

        twitch.watch_session.activate(channel)
        twitch._stage = Stage.PICK_CHANNEL
        twitch._paused = False
        def valid():
            return twitch.watch_session.matches(channel)
        twitch.watch_session.deactivate()
        check("stop робить токен поточної сесії недійсним", not valid())
        twitch._apply(Command(CommandType.RESUME))
        check("resume повертає координатор до інвентаря", twitch._stage is Stage.LOAD_INVENTORY)

    asyncio.run(run())
