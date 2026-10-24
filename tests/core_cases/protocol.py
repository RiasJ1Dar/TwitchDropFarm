"""Регресії: protocol."""
from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta, timezone

from core import protocol
from core.api import ApiError, TwitchApi
from core.events import ProtocolStale
from core.exceptions import RequestInvalid
from core.miner import Miner
from tests.core_cases.support import Bus, check


def protocol_watch_checks() -> None:
    """Сторожа хешів Twitch.

    Головне тут — вона **ніколи** не питає мутації: перевірка «а чи живий цей
    хеш» для `ClaimDropRewards` означала б справді заклеймити дроп. І одна
    відмова не тривога: `PersistedQueryNotFound` приходить сплеском і минає.
    """
    print("\n[3д] Сторожа запитів Twitch")
    import core.miner as miner_module

    check("мутації позначені",
          protocol.CLAIM_DROP.mutation and protocol.CLAIM_POINTS.mutation
          and protocol.DROP_NOTIFICATION_DELETE.mutation)
    check("у переліку для перевірки лише читання",
          not any(q.mutation for q in protocol.READ_ONLY_QUERIES))

    class Probe:
        """Ядро з підробленим graphql: рахує, що саме питали."""

        def __init__(self, dead: set[str], *, heal_after: int | None = None):
            self.dead = dead
            self.heal_after = heal_after
            self.asked: list[str] = []
            self.events = Bus()
            self.campaigns = [types.SimpleNamespace(
                id="c1", available_to_me=True,
                game=types.SimpleNamespace(name="Гра", slug="hra"),
            )]
            self.channels = {}
            self.identity = types.SimpleNamespace(user_id=1)
            self.watching = types.SimpleNamespace(
                peek=lambda _d: types.SimpleNamespace(id=7, login="channel"),
            )
            self.settings = types.SimpleNamespace(check_updates=True)

        async def graphql(self, payload, *, retries=None):
            name = payload["operationName"]
            self.asked.append(name)
            if name in self.dead:
                if (self.heal_after is not None
                        and self.asked.count(name) > self.heal_after):
                    return {}
                raise ApiError("PersistedQueryNotFound")
            return {}

        # методи ядра, які сторожа використовує як є
        _probe_variables = Miner._probe_variables
        probe_protocol = Miner.probe_protocol
        _watch_protocol = Miner._watch_protocol

    real_pause = miner_module.PROTOCOL_PROBE_PAUSE
    miner_module.PROTOCOL_PROBE_PAUSE = timedelta(seconds=0)
    try:
        healthy = Probe(set())
        dead, skipped = asyncio.run(healthy.probe_protocol())
        check("живі запити — тиші", dead == [] and skipped == [], f"{dead} {skipped}")
        check("мутацій не питали жодного разу",
              not {"DropsPage_ClaimDropRewards", "ClaimCommunityPoints",
                   "OnsiteNotifications_DeleteNotification"} & set(healthy.asked),
              str(healthy.asked))

        # відмова, яка минула сама, — не новина
        blinked = Probe({"Inventory"}, heal_after=1)
        dead, _ = asyncio.run(blinked.probe_protocol())
        check("сплеск не вважається зміною хеша", dead == [], str(dead))

        # стійка відмова — тривога з назвою операції
        broken = Probe({"Inventory"})
        asyncio.run(broken._watch_protocol())
        stale = [e for e in broken.events.sent if isinstance(e, ProtocolStale)]
        check("стійка відмова — тривога",
              len(stale) == 1 and stale[0].operations == ("Inventory",), str(stale))
        check("одна операція — це не шторм", not stale[0].storm)

        # усі мертві разом — радше скид кешу в Twitch, ніж наші хеші
        everything = Probe({q.operation for q in protocol.READ_ONLY_QUERIES})
        asyncio.run(everything._watch_protocol())
        stale = [e for e in everything.events.sent if isinstance(e, ProtocolStale)]
        check("усі мертві — позначено як шторм",
              len(stale) == 1 and stale[0].storm, str(stale))

        # немає стану — не перевіряємо, але й не тривожимо
        empty = Probe(set())
        empty.campaigns = []
        empty.watching = types.SimpleNamespace(peek=lambda _d: None)
        dead, skipped = asyncio.run(empty.probe_protocol())
        check("без стану частину запитів пропущено",
              dead == [] and len(skipped) == 6
              and protocol.PLAYBACK_ACCESS.operation in skipped, f"{dead} {skipped}")
    finally:
        miner_module.PROTOCOL_PROBE_PAUSE = real_pause


def stale_request_checks() -> None:
    print("\n[6] Протухлий запит")
    api = TwitchApi(client=protocol.ANDROID, should_stop=lambda: False)

    async def run():
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        try:
            async with api.request("POST", "https://example.invalid",
                                   valid_until=past):
                pass
        finally:
            # Саме .close() тут не годиться: він заразом зберігає cookies у
            # робочий файл стану, а перевірка не сміє чіпати чужі дані.
            if api._session is not None:
                await api._session.close()

    try:
        asyncio.run(run())
    except RequestInvalid:
        check("протухле вікно → RequestInvalid, який ловить авторизація", True)
    except Exception as error:
        check("протухле вікно → RequestInvalid", False,
              f"натомість {type(error).__name__}: {error}")
    else:
        check("протухле вікно → RequestInvalid", False, "виняток не кинуто")


def request_limit_checks() -> None:
    print("\n[12] Стеля повторів")

    class FakeSession:
        closed = False
        timeout = types.SimpleNamespace(total=20)

        def __init__(self) -> None:
            self.calls = 0

        async def request(self, *args, **kwargs):
            self.calls += 1
            raise OSError("down")

    lost: list[tuple[str, int]] = []
    api = TwitchApi(
        client=protocol.ANDROID,
        should_stop=lambda: False,
        on_network_lost=lambda reason, attempt: lost.append((reason, attempt)),
    )
    session = FakeSession()
    api._session = session  # type: ignore[assignment]

    async def run() -> BaseException | None:
        try:
            async with api.request(
                "POST", "https://spade.example/process",
                attempts=2, count_as_network=False,
            ):
                pass
        except BaseException as error:
            return error
        return None

    error = asyncio.run(run())
    check("обмежені спроби піднімають помилку",
          isinstance(error, OSError), f"{type(error).__name__}: {error}")
    check("рівно стільки спроб, скільки просили",
          session.calls == 2, f"викликів={session.calls}")
    check("відмова доставки не малює «немає зв'язку»",
          not lost, f"втрат={lost}")
