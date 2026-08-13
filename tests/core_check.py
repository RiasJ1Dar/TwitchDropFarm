"""Перевірка логіки ядра без мережі.

Кожна група тут стоїть не просто так: усе це вже було зламане, причому мовчки.
Код був написаний, лінтер чистий, збірка проходила — і не працювало. Ловиться
таке лише виконанням, тому набір ганяється за секунду й не питає інтернету.

Підміняємо якнайменше: справжні класи, справжні методи, фальшиві тільки дані
й найнижчий шар (graphql, іконка трея).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import protocol
from core.api import TwitchApi
from core.config import STALL_LIMIT
from core.events import (
    CampaignFinished,
    CommandType,
    ConnectionLost,
    ConnectionRestored,
    DeadlineRisk,
    DropClaimed,
    LoginRequired,
    MinerError,
    MinerStarted,
    MinerStopped,
    ProgressStalled,
    StatusChanged,
    WatchingChanged,
    WindowVisibility,
)
from core.exceptions import RequestInvalid
from core.miner import Miner
from core.settings import Settings
from gui.tray import Tray

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


class Bus:
    """Шина, яка нічого не робить, крім запамʼятовування."""

    def __init__(self) -> None:
        self.sent: list = []

    def emit(self, event) -> None:
        self.sent.append(event)

    def status(self, text: str) -> None:
        self.sent.append(StatusChanged(text=text))


def miner() -> Miner:
    args = argparse.Namespace(log=False, tray=False, logging_level=50,
                              debug_ws=0, debug_gql=0)
    return Miner(Settings(args))


# ------------------------------------------------------------------ застій

def stall_checks() -> None:
    print("\n[1] Детектор застою")

    def run(marks: list[tuple[str, int] | None]) -> tuple[int, int]:
        fake = types.SimpleNamespace(_stall_count=0, events=Bus())
        channel = types.SimpleNamespace(name="канал")
        before = None
        for mark in marks:
            fake._progress_mark = lambda m=mark: m
            Miner._check_stall(fake, before, channel)
            before = mark
        fired = [e for e in fake.events.sent if isinstance(e, ProgressStalled)]
        return len(fired), fake._stall_count

    frozen = [("drop", 40)] * (STALL_LIMIT + 3)
    fired, count = run(frozen)
    check("застій ловиться", fired == 1, f"тривог={fired}, лічильник={count}")

    growing = [("drop", 40 + n) for n in range(STALL_LIMIT + 3)]
    fired, _ = run(growing)
    check("здоровий фарм не тривожить", fired == 0, f"тривог={fired}")

    fired, count = run([None, ("drop", 5), ("drop", 6)])
    check("початок фарму не рахується застоєм", count == 0, f"лічильник={count}")

    # Найважливіше: позначка мусить брати лише підтверджені Twitch хвилини.
    # Якщо туди повернуться домальовані наосліп, застій знову маскуватиме сам
    # себе — саме так він і не спрацьовував жодного разу.
    drop = types.SimpleNamespace(id="d", counted_minutes=40, blind_minutes=7,
                                 minutes=47)
    box = types.SimpleNamespace(
        active_campaign=lambda: types.SimpleNamespace(next_drop=drop)
    )
    mark = Miner._progress_mark(box)
    check("позначка бере лише підтверджене Twitch", mark == ("d", 40), str(mark))


# ------------------------------------------------------------------ клейм

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


# ------------------------------------------------------------------ дедлайни

def deadline_checks() -> None:
    print("\n[3] Прогноз «встигну / не встигну»")
    now = datetime.now(timezone.utc)

    def campaign(name, *, needs, hours, farmable=True):
        return types.SimpleNamespace(
            id=name, name=name,
            game=types.SimpleNamespace(name="гра"),
            minutes_left=needs,
            closes_at=now + timedelta(hours=hours),
            slack=(hours * 60 / needs) if needs else float("inf"),
            farmable=lambda: farmable,
        )

    fake = types.SimpleNamespace(events=Bus(), _risk_reported=set(), campaigns=[
        campaign("встигаємо", needs=60, hours=10),
        campaign("не встигаємо", needs=600, hours=2),
        campaign("чужа", needs=600, hours=1, farmable=False),
    ])
    Miner._check_deadlines(fake)
    risky = [c.name for e in fake.events.sent
             if isinstance(e, DeadlineRisk) for c in e.campaigns]
    check("безнадійну помічено", risky == ["не встигаємо"], str(risky))

    before = len(fake.events.sent)
    Miner._check_deadlines(fake)
    check("повторно не повідомляє", len(fake.events.sent) == before)


# ------------------------------------------------------------------ вікно

def window_checks() -> None:
    print("\n[4] Керування вікном")
    for kind, visible in ((CommandType.HIDE_WINDOW, False),
                          (CommandType.SHOW_WINDOW, True)):
        box = types.SimpleNamespace(events=Bus())
        Miner._apply(box, types.SimpleNamespace(type=kind, argument=""))
        got = [e for e in box.events.sent if isinstance(e, WindowVisibility)]
        check(f"{kind.name} → подія", len(got) == 1 and got[0].visible is visible,
              str(box.events.sent))


# ------------------------------------------------------------------ трей

def tray_checks() -> None:
    print("\n[5] Трей: колір і сповіщення")

    class FakeIcon:
        def __init__(self):
            object.__setattr__(self, "notices", [])
            object.__setattr__(self, "redraws", 0)

        def notify(self, message, title):
            self.notices.append((title, message))

        def __setattr__(self, name, value):
            if name == "icon":
                self.__dict__["redraws"] += 1
                return
            object.__setattr__(self, name, value)

    def build(notifications=True):
        twitch = types.SimpleNamespace(
            settings=types.SimpleNamespace(tray_notifications=notifications)
        )
        tray = Tray(twitch, gui=None)
        tray._icon = FakeIcon()
        return tray

    channel = types.SimpleNamespace(name="канал")
    sequence = [
        MinerStarted(version="0", tray=True),
        StatusChanged(text="рутина"),
        WatchingChanged(channel=channel),
        WatchingChanged(channel=channel),
        DropClaimed(drop_name="д", game="гра", rewards="нагорода"),
        CampaignFinished(campaign_name="к", game="гра"),
        ProgressStalled(minutes_without_progress=5, channel_name="канал"),
        ConnectionLost(reason="таймаут", attempt=1),
        ConnectionRestored(downtime_seconds=1.0, attempts=1),
        LoginRequired(verification_uri="https://twitch.tv", user_code="КОД"),
        MinerError(message="халепа"),
        MinerStopped(reason="користувач"),
    ]

    tray = build()
    states = []
    for event in sequence:
        tray._on_event(event)
        states.append(tray._state)
    check("колір слідує за станом",
          states[2] == "active" and states[7] == "error" and states[-1] == "idle",
          str(states))
    check("та сама подія не перемальовує іконку", tray._icon.redraws == 5,
          f"перемальовувань={tray._icon.redraws}")
    check("сповіщення надіслані", len(tray._icon.notices) == 7,
          str(len(tray._icon.notices)))
    check("рутина мовчить",
          all("рутина" not in m for _t, m in tray._icon.notices))

    quiet = build(notifications=False)
    for event in sequence:
        quiet._on_event(event)
    check("вимкнені сповіщення — тиша", not quiet._icon.notices)


# ------------------------------------------------------------------ протухле

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


def main() -> int:
    logging.getLogger("TwitchDrops").setLevel(logging.CRITICAL)
    stall_checks()
    claim_checks()
    deadline_checks()
    window_checks()
    tray_checks()
    stale_request_checks()
    print("\n" + "=" * 50)
    print(f"Пройдено: {ok}   Провалено: {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
