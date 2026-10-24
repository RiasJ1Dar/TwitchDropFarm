"""Регресії: delivery."""
from __future__ import annotations

import asyncio
import types
from datetime import timedelta
from time import monotonic

from core.channels import WatchReporter
from core.config import WATCH_ATTEMPTS
from core.events import StatusChanged, WatchUncounted
from core.miner import Miner
from core.model import Drop
from tests.core_cases.support import Bus, check, miner_stub


def delivery_checks() -> None:
    print("\n[11] Доставка перегляду")

    class FakeBackend:
        def __init__(self):
            self.gqls = 0
            self.gets: list[tuple[str, dict]] = []
            self.heads: list[tuple[str, dict]] = []
            self.playlist = "#EXTM3U\n#EXTINF:2,\na.ts\n#EXTINF:2,\nhttps://cdn.example/b.ts\n"
            self.master = "#EXTM3U\nhttps://cdn.example/high.m3u8\nhttps://cdn.example/low.m3u8\n"
            self.head_status = 200
            self.error = None
            self.payload = None

        async def graphql(self, payload):
            self.gqls += 1
            self.payload = payload
            return {"data": {"streamPlaybackAccessToken": {
                "signature": "a+b/c=", "value": '{"test": "a+b"}',
            }}}

        async def fetch_text(self, url, **kwargs):
            self.gets.append((url, kwargs))
            if self.error:
                raise self.error
            return self.master if 'usher.ttvnw.net' in url else self.playlist

        async def head(self, url, **kwargs):
            self.heads.append((url, kwargs))
            return self.head_status

    stream = types.SimpleNamespace(broadcast_id=99)
    channel = types.SimpleNamespace(login="ibeast", name="ibeast", stream=stream)
    backend = FakeBackend()
    reporter = WatchReporter(backend)
    check("HLS доставляється", asyncio.run(reporter.report(channel)))
    check("HEAD іде до кожного сегмента", len(backend.heads) == 2)
    check("обрано найнижчу якість", backend.gets[1][0].endswith('/low.m3u8'))
    check("відносні сегменти прив'язані до плейлиста",
          backend.heads[0][0] == "https://cdn.example/a.ts")
    check("абсолютні сегменти збережено",
          backend.heads[1][0] == "https://cdn.example/b.ts")
    check("CDN не чіпає індикатор загальної мережі",
          all(k.get("count_as_network") is False for _, k in backend.gets + backend.heads))
    check("доставка має обмежені повтори",
          all(k.get("attempts") == WATCH_ATTEMPTS for _, k in backend.gets + backend.heads))
    check("CDN-плейлист не тримає з'єднання",
          backend.gets[1][1].get("headers") == {"Connection": "close"})
    from urllib.parse import parse_qs, urlsplit
    signed = parse_qs(urlsplit(backend.gets[0][0]).query)
    check("підпис і токен закодовані без втрати символів",
          signed['sig'] == ['a+b/c='] and signed['token'] == ['{"test": "a+b"}'])
    check("playback запитується для обраного каналу",
          backend.payload['variables']['login'] == 'ibeast')
    asyncio.run(reporter.report(channel))
    check("адреса якості кешується між проходами", backend.gqls == 1)
    stream.broadcast_id = 100
    asyncio.run(reporter.report(channel))
    check("новий ефір отримує новий playback-токен", backend.gqls == 2)
    other = FakeBackend()
    asyncio.run(WatchReporter(other).report(channel))
    check("канали не ділять підписаний кеш", other.gqls == 1)
    backend.head_status = 404
    check("зниклий сегмент є відмовою доставки", not asyncio.run(reporter.report(channel)))
    backend.head_status = 200
    asyncio.run(reporter.report(channel))
    check("після відмови токен запитується заново", backend.gqls == 3)
    for label, body in (
        ("порожній плейлист", "#EXTM3U\n"),
        ("завершений ефір", "#EXTM3U\na.ts\n#EXT-X-ENDLIST\n"),
        ("JSON-помилка замість плейлиста", '{"error":"offline"}'),
    ):
        bad = FakeBackend()
        bad.playlist = body
        check(label + " не є переглядом", not asyncio.run(WatchReporter(bad).report(channel)))
        check(label + " не надсилає HEAD", not bad.heads)
    bad_master = FakeBackend()
    bad_master.master = "#EXTM3U\n"
    check("без доступної якості немає успіху",
          not asyncio.run(WatchReporter(bad_master).report(channel)))
    broken = FakeBackend()
    broken.error = OSError("network")
    check("мережевий збій не підміняється успішним spade",
          not asyncio.run(WatchReporter(broken).report(channel)))
    offline = types.SimpleNamespace(login="offline", stream=None)
    unused = FakeBackend()
    check("офлайн не надсилає запитів",
          not asyncio.run(WatchReporter(unused).report(offline)) and unused.gqls == 0)

    # Три швидкі проходи без відповіді Twitch не можуть стати трьома хвилинами.
    from unittest.mock import patch

    from core import watch_session as session_module
    from core.toolbox import TaskKeeper
    from core.watch_session import WatchHooks, WatchSession

    attempts: list[str] = []

    async def take_channel():
        if attempts.count("confirmed") == 3:
            raise asyncio.CancelledError
        return active

    async def failed_delivery():
        return False

    async def missing_confirmation(_channel):
        attempts.append("confirmed")
        return False

    async def no_wait(*_args):
        return None

    active = types.SimpleNamespace(live=True, report_watching=failed_delivery)
    progress = Drop.__new__(Drop)
    progress.counted_minutes = 7
    loop = WatchSession(WatchHooks(
        allowed=lambda: True, progress_mark=lambda: progress.minutes,
        confirm=lambda ch, valid: missing_confirmation(ch), completed=lambda ch: False,
        delivered=lambda ch: None, failed=lambda ch: attempts.append("failed"),
        check_stall=lambda before, ch: None, stop=lambda: None,
    ), TaskKeeper())
    loop.watching = types.SimpleNamespace(take=take_channel, peek=lambda _: active)

    async def run_loop():
        try:
            await loop.run()
        except asyncio.CancelledError:
            return

    with (
        patch.object(session_module, "WATCH_PERIOD", timedelta(0)),
        patch.object(session_module, "PROGRESS_GRACE", timedelta(0)),
        patch.object(session_module, "sleep_unless", no_wait),
    ):
        asyncio.run(run_loop())
    check("кожен прохід питає підтвердження Twitch", attempts.count("confirmed") == 3)
    check("невдала доставка лишається відмовою", attempts.count("failed") == 3)
    check("20-секундні проходи не вигадують хвилини", progress.minutes == 7)

    from core.watchdog import ChannelBench

    selector = types.SimpleNamespace(bench=ChannelBench())
    fake = miner_stub(
        _delivery_failures=0, events=Bus(), channel_selector=selector,
        _restart_watch=types.SimpleNamespace(set=lambda: None),
    )
    fake.bench = lambda channel: Miner.bench(fake, channel)
    ch = types.SimpleNamespace(name="канал")
    Miner._note_delivery_failed(fake, ch)
    Miner._note_delivery_failed(fake, ch)
    Miner._note_delivery_failed(fake, ch)
    uncounted = [e for e in fake.events.sent if isinstance(e, WatchUncounted)]
    statuses = [e.text for e in fake.events.sent if isinstance(e, StatusChanged)]
    check("друга відмова доставки б'є тривогу один раз",
          len(uncounted) == 1 and uncounted[0].consecutive == 2,
          f"подій={len(uncounted)}")
    check("вікно каже прямо, що перегляд не йде",
          "Перегляд не зараховується" in statuses)

    # ⚠️ Сказати про збій мало. Раніше майнер лише повідомляв «хвилина не
    # зарахувалась» і спокійно вертався на той самий канал наступним добором.
    check("канал, що підвів, відкладено", Miner.benched(fake, ch))
    check("відстійник знає, доки чекати",
          selector.bench.benched["канал"] > monotonic())
    selector.bench.benched["канал"] = monotonic() - 1.0
    check("через півгодини канал знову в грі", not Miner.benched(fake, ch))
    check("прострочений запис прибирається", "канал" not in selector.bench.benched)

    Miner._note_delivery_ok(fake, ch)
    check("успіх скидає лічильник відмов", fake._delivery_failures == 0)
    recovered = [e.text for e in fake.events.sent if isinstance(e, StatusChanged)]
    check("після успіху статус повертається",
          recovered[-1] == "Дивимось канал", str(recovered[-1]))
