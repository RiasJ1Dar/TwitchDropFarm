"""Придатність, стабільний добір та межі мережевих задач каналів."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from core.channel_selection import can_farm, should_switch
from core.config import MAX_CHANNELS, Stage
from core.events import StreamOffline
from core.toolbox import Game
from tests.core_cases.support import Bus, check, miner


class Candidate:
    def __init__(self, cid, game, *, viewers=10, listed=False, live=True):
        self.id, self.game = cid, game
        self.name = self.login = str(cid)
        self.viewers = viewers
        self.from_allowlist_flag = listed
        self.live = live
        self.drops_on = True
        self.dark = not live
        self.stream = None
        self.announced = 0
        self.forgotten = False

    def announce(self):
        self.announced += 1

    def forget(self):
        self.forgotten = True


def channel_checks() -> None:
    twitch = miner()
    game = Game({"id": "1", "name": "A"})
    other_game = Game({"id": "2", "name": "B"})
    campaign = SimpleNamespace(game=game, farmable=lambda c: c is not None and c.game == game)
    current, other = Candidate(1, game), Candidate(2, game, listed=True)
    check("live-канал потрібної гри придатний", can_farm([campaign], [game], current))
    check("відсутній канал непридатний", not can_farm([campaign], [game], None))
    current.live = False
    check("offline канал непридатний", not can_farm([campaign], [game], current))
    current.live = True
    current.drops_on = False
    check("вимкнені дропи не дають придатного каналу", not can_farm([campaign], [game], current))
    current.drops_on = True
    current.game = other_game
    check("зміна гри робить канал непридатним", not can_farm([campaign], [game], current))
    current.game = game

    def decision(target=other, *, remaining=0, watched=current, ranks=None, fits=lambda _: True):
        ranks = ranks or {1: 0, 2: 0}
        return should_switch(target, watched, remaining=remaining, farmable=fits,
                             priority=lambda c: ranks[c.id])

    check("allowlist перемагає за рівного пріоритету", decision())
    check("поточний allowlist не витісняє звичайний канал",
          not decision(current, watched=other))
    check("вищий пріоритет перемагає allowlist",
          decision(current, watched=other, ranks={1: 0, 2: 1}))
    check("нижчий пріоритет не витісняє поточний", not decision(ranks={1: 0, 2: 1}))
    check("майже готова нагорода захищає поточний канал", not decision(remaining=2))
    check("непридатний поточний канал звільняє місце",
          decision(fits=lambda c: c is other))
    check("непридатний кандидат не обирається", not decision(fits=lambda c: c is current))
    check("без поточного каналу береться придатний", decision(watched=None))
    twitch.channel_selector.bench.bench(other.name)
    check("відстійник блокує перемикання до оцінки решти стану", not twitch.should_switch_to(other))

    selector = twitch.channel_selector
    stale, wrong, listed = Candidate(3, game, live=False), Candidate(4, other_game), Candidate(
        5, other_game, listed=True, live=False)
    selector.channels.update({c.id: c for c in (current, stale, wrong, listed)})
    removed = selector.remove_stale([game], full_sweep=False)
    check("вибіркове очищення прибирає offline і чужу гру", {c.id for c in removed} == {3, 4})
    check("allowlist і придатний поточний зберігаються", set(selector.channels) == {1, 5})
    check("повне очищення прибирає весь список",
          len(selector.remove_stale([game], full_sweep=True)) == 2 and not selector.channels)

    async def discovery():
        candidates = [Candidate(cid, game, viewers=cid) for cid in range(10, MAX_CHANNELS + 12)]
        acl = Candidate(9, game, listed=True, viewers=0)
        checked = []

        async def find(_game):
            return candidates

        async def check_many(channels):
            checked.extend(channels)

        selector.find_streams, selector.check_many = find, check_many
        campaigns = [SimpleNamespace(game=game, channels=[], farmable_before=lambda _: True,
                                     farmable=lambda _: True),
                     SimpleNamespace(game=game, channels=[acl], farmable_before=lambda _: True,
                                     farmable=lambda _: True)]
        spare = await selector.discover(campaigns, [game], lambda _: 0,
                                        now=datetime.now(timezone.utc))
        check("пошук перевіряє allowlist окремо", checked == [acl])
        check("місткість каналів збережена", len(selector.channels) == MAX_CHANNELS)
        check("allowlist перший попри нуль глядачів", next(iter(selector.channels.values())) is acl)
        check("решта впорядкована за глядачами",
              list(selector.channels.values())[1].viewers == max(c.viewers for c in candidates))
        check("надлишок повертається для відписки",
              len(spare) == len(candidates) + 1 - MAX_CHANNELS)

        # Координатор лишає підписки й поточний перегляд при заміні об'єктів.
        subscriptions, unsubscribed, watches = [], [], []
        twitch.topics = SimpleNamespace(
            subscribe=subscriptions.extend, unsubscribe=unsubscribed.extend,
        )
        twitch.campaigns, twitch.wanted = campaigns, [game]
        replacement = next(iter(selector.channels.values()))
        twitch.watching.put(Candidate(replacement.id, game))

        def watch(channel, *, announce=True):
            watches.append((channel, announce))
            twitch.watching.put(channel)

        twitch.watch = watch
        await twitch._find_channels()
        check("переоблік відновлює придатний поточний канал без оголошення",
              watches == [(replacement, False)])
        check("масові підписки тільки на стан каналу",
              len(subscriptions) == MAX_CHANNELS
              and all("video-playback-by-id" in item.name for item in subscriptions))
        check("пошук передає керування вибору каналу", twitch._stage is Stage.PICK_CHANNEL)

        ended = asyncio.Event()
        started = asyncio.Event()

        async def pending_directory(_game):
            started.set()
            try:
                await asyncio.Future()
            finally:
                ended.set()

        async def pending_check(_channels):
            await started.wait()
            await asyncio.Future()

        selector.find_streams, selector.check_many = pending_directory, pending_check
        task = asyncio.create_task(selector.discover(campaigns, [game], lambda _: 0,
                                                    now=datetime.now(timezone.utc)))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        check("скасування пошуку дочікується запиту каталогу", ended.is_set())

    asyncio.run(discovery())
    twitch.events = Bus()
    twitch.say = lambda _: None
    twitch.watching.put(current)
    twitch.campaigns, twitch.wanted = [campaign], [game]
    current.live = False
    twitch.channel_state_changed(current, was_live=True)
    check("offline активного каналу запускає новий вибір", twitch._stage is Stage.PICK_CHANNEL)
    check("offline активного каналу повідомляється один раз",
          sum(isinstance(e, StreamOffline) for e in twitch.events.sent) == 1)
