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
import hashlib
import logging
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import autostart, export, protocol, update
from core.api import TwitchApi
from core.channels import WatchReporter
from core.config import (
    DEFAULT_IMAGE_SIZE,
    MAX_IMAGE_SIZE,
    MIN_IMAGE_SIZE,
    SPADE_ATTEMPTS,
    STALL_LIMIT,
    clamp_image_size,
)
from core.events import (
    CampaignFinished,
    Command,
    CommandType,
    ConnectionLost,
    ConnectionRestored,
    ControlBus,
    DeadlineRisk,
    DropClaimed,
    LoginRequired,
    MinerError,
    MinerStarted,
    MinerStopped,
    ProgressStalled,
    RiskSnapshot,
    StatusChanged,
    WatchingChanged,
    WatchUncounted,
    WindowVisibility,
)
from core.exceptions import RequestInvalid
from core.history import History
from core.images import ImageCache
from core.miner import Miner
from core.settings import Settings
from core.toolbox import force_utf8_console, rotating_log_handler
from gui.app import GUI
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

    def fresh():
        return types.SimpleNamespace(
            _stall_since=None, _stall_alerted=False, events=Bus(),
            _counted_elsewhere="",
        )

    def run(marks: list[int | None], *, step: float = 60.0) -> tuple[int, bool]:
        fake = fresh()
        channel = types.SimpleNamespace(name="канал")
        before = None
        clock = 0.0
        for mark in marks:
            fake._progress_mark = lambda m=mark: m
            Miner._check_stall(fake, before, channel, now=clock)
            before = mark
            clock += step
        fired = [e for e in fake.events.sent if isinstance(e, ProgressStalled)]
        return len(fired), fake._stall_alerted

    frozen = [40] * (STALL_LIMIT + 3)
    fired, alerted = run(frozen)
    check("застій ловиться", fired == 1 and alerted, f"тривог={fired}")

    growing = [40 + n for n in range(STALL_LIMIT + 3)]
    fired, _ = run(growing)
    check("здоровий фарм не тривожить", fired == 0, f"тривог={fired}")

    fired, alerted = run([None, 5, 6])
    check("початок фарму не рахується застоєм", not alerted, f"тривога={alerted}")

    # Спіймано 14.08: фарм стояв 11 хвилин, тривоги не було, бо STALL_LIMIT
    # рахував ітерації циклу, а не хвилини. При збоях мережі одна ітерація
    # розтягувалась на чотири хвилини — чим гірша мережа, тим пізніше скарга.
    fired, _ = run([40] * 20, step=1.0)
    check("багато швидких ітерацій без хвилин — не тривожать",
          fired == 0, f"тривог={fired}")

    fake = fresh()
    channel = types.SimpleNamespace(name="канал")
    fake._progress_mark = lambda: 40
    Miner._check_stall(fake, None, channel, now=0.0)
    Miner._check_stall(fake, 40, channel, now=4 * 60)
    Miner._check_stall(fake, 40, channel, now=8 * 60)
    fired = [e for e in fake.events.sent if isinstance(e, ProgressStalled)]
    check("дві довгі ітерації ловлять застій",
          len(fired) == 1 and fired[0].minutes_without_progress == 8,
          f"події={fired}")

    # Позначка мусить брати лише підтверджені Twitch хвилини. Якщо туди
    # повернуться домальовані наосліп, застій знову маскуватиме сам себе —
    # саме так він і не спрацьовував жодного разу.
    #
    # І рахувати мусить по ВСІХ придатних кампаніях. Кампаній однієї гри буває
    # кілька; коли одна росте, а друга стоїть, вибір «активної» за найменшим
    # залишком вказував на нерухому — і тривога била під час здорового фарму.
    def drop(counted, blind=0, fits=True):
        return types.SimpleNamespace(
            counted_minutes=counted, blind_minutes=blind,
            minutes=counted + blind,
            farmable=lambda _c, ignore_channel_state=False: fits,
        )

    def campaign(*drops, fits=True, channels=()):
        return types.SimpleNamespace(
            all_drops=drops, channels=channels,
            farmable=lambda _c, ignore_channel_state=False: (
                fits or ignore_channel_state
            ),
        )

    here = types.SimpleNamespace(name="канал")
    box = types.SimpleNamespace(
        watching=types.SimpleNamespace(peek=lambda _d: here),
        wanted=["гра"],
        campaigns=[campaign(drop(40, blind=7)), campaign(drop(151), drop(151))],
        _counts_here=lambda c, ch: Miner._counts_here(box, c, ch),
    )
    check("позначка бере лише підтверджене Twitch",
          Miner._progress_mark(box) == 40 + 151 + 151, str(Miner._progress_mark(box)))

    # Кампанія події зараховується на каналі, який стоїть у категорії гри:
    # за категорією вона не проходить, але канал є в її списку. 15.08 через це
    # EWC Platinum ріс щохвилини, а тривога била кожні десять.
    event_campaign = campaign(drop(260), fits=False, channels=[here])
    box.campaigns = [campaign(drop(40)), event_campaign]
    check("позначка бачить кампанію події",
          Miner._progress_mark(box) == 40 + 260, str(Miner._progress_mark(box)))

    stranger = campaign(drop(999), fits=False, channels=[])
    box.campaigns = [campaign(drop(40)), stranger]
    check("чужа кампанія в позначку не лізе",
          Miner._progress_mark(box) == 40, str(Miner._progress_mark(box)))

    # росте лише одна з двох кампаній — це не застій
    growing = drop(40)
    frozen = drop(151)
    box.campaigns = [campaign(growing), campaign(frozen)]
    fake = fresh()
    channel = types.SimpleNamespace(name="канал")
    before = None
    clock = 0.0
    for minute in range(STALL_LIMIT + 3):
        growing.counted_minutes = 40 + minute
        mark = Miner._progress_mark(box)
        fake._progress_mark = lambda m=mark: m
        Miner._check_stall(fake, before, channel, now=clock)
        before = mark
        clock += 60
    fired = [e for e in fake.events.sent if isinstance(e, ProgressStalled)]
    check("сусідня нерухома кампанія не дає хибної тривоги", not fired,
          f"тривог={len(fired)}")


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

    # Затримка розгортання вікна: команда чекала кінця стадії головного циклу,
    # а «Шукаю канали» тривала до хвилини.
    bus = ControlBus()
    seen: list = []
    bus.set_immediate_handler(seen.append)
    bus.send(Command(CommandType.SHOW_WINDOW))
    check("показати вікно — повз чергу", len(seen) == 1 and bus.get_nowait() is None)

    bus.send(Command(CommandType.RELOAD))
    check("решта команд — через чергу",
          len(seen) == 1 and len(bus.drain_pending()) == 1)

    plain = ControlBus()
    plain.send(Command(CommandType.SHOW_WINDOW))
    check("без обробника команда не губиться", len(plain.drain_pending()) == 1)

    # Сигнал гасився після вигрібання: команда, що надійшла посеред нього,
    # лишалась у черзі з погашеним сигналом — і цикл спав до зміни стану.
    race = ControlBus()
    race.send(Command(CommandType.RELOAD))
    real = race.get_nowait

    injected = False

    def get_then_send():
        nonlocal injected
        got = real()
        if got is not None and not injected:
            injected = True
            race.send(Command(CommandType.RESUME))
        return got

    race.get_nowait = get_then_send
    race.drain_pending()
    check("команда посеред вигрібання будить цикл", race._signal.is_set())


# ------------------------------------------------- «зараз фармимо» у вікні

def growing_checks() -> None:
    """Рядок «зараз фармимо» мусить показувати всі дропи, що просуваються.

    Спіймано зі скріншота 17.08: на трансляції EWC у вікні стояло «EWC Platinum
    — Special Events», хоч паралельно ріс дроп Rocket League. Людина: «не
    зрозуміло яка гра фармиться зараз» — і справді, відповіді там не було.
    """
    print("\n[3в] Зараз фармимо")

    class FakeVar:
        def __init__(self) -> None:
            self.value = ""

        def set(self, text: str) -> None:
            self.value = text

    box = types.SimpleNamespace(
        _growing={}, _watching_name="berbatow",
        drop_var=FakeVar(), progress={},
        GROWING_WINDOW=GUI.GROWING_WINDOW, GROWING_LINES=GUI.GROWING_LINES,
    )
    now = 1000.0
    box._growing = {
        "EWC Platinum": (now, "Special Events", 298, 360),
        "Inferno Collection #2": (now, "Rocket League", 272, 360),
    }
    GUI._render_growing(box, now=now)
    text = box.drop_var.value
    check("видно обидві кампанії",
          "Special Events" in text and "Rocket League" in text, text)
    check("найближчий до завершення — першим",
          text.splitlines()[0].startswith("EWC Platinum"), text)
    check("бар — по головному дропу", box.progress["value"] > 82, str(box.progress))

    # Дроп, який давно не оновлювався, — це вже не «зараз»
    box._growing["Старий"] = (now - GUI.GROWING_WINDOW - 60, "Гра", 10, 60)
    GUI._render_growing(box, now=now)
    check("протухлий рядок зникає", "Старий" not in box.drop_var.value,
          box.drop_var.value)
    check("і з пам'яті теж", "Старий" not in box._growing, str(box._growing))

    box._growing.clear()
    GUI._render_growing(box, now=now)
    check("нічого не росте — так і кажемо",
          box.drop_var.value == "Дроп не визначено", box.drop_var.value)


# ------------------------------------------------------ чужий перегляд

def parallel_watch_checks() -> None:
    """Коли «Twitch рахує іншу кампанію» — тривога, а коли норма.

    Спіймано 15.08: 248 попереджень за добу на трансляції RLCS. Канал роздавав
    дві кампанії одразу — категорія «Rocket League», а Twitch зараховував
    «Special Events». Фарм при цьому був здоровий.
    """
    print("\n[3б] Паралельний перегляд")
    import core.miner as miner_module

    channel = types.SimpleNamespace(id=1, name="alphakep", game="Rocket League")

    class FakeLog:
        def __init__(self) -> None:
            self.warnings: list[str] = []

        def warning(self, text: str) -> None:
            self.warnings.append(text)

        def log(self, _level, _text) -> None:
            pass

    def run(campaign_channels: list):
        counted: list[int] = []
        drop = types.SimpleNamespace(
            taken=False, name="чужий",
            farmable=lambda _c: False,
            set_counted=counted.append,
            campaign=types.SimpleNamespace(
                channels=campaign_channels,
                game=types.SimpleNamespace(name="Special Events"),
            ),
        )

        async def graphql(_payload):
            return {"data": {"currentUser": {"dropCurrentSession": {
                "dropID": "d1", "currentMinutesWatched": 5,
            }}}}

        fake = types.SimpleNamespace(graphql=graphql, _drops={"d1": drop},
                                     _counted_elsewhere="")
        spy, real = FakeLog(), miner_module.log
        miner_module.log = spy
        try:
            confirmed = asyncio.run(Miner._confirm_progress(fake, channel))
        finally:
            miner_module.log = real
        return confirmed, spy.warnings, counted, fake._counted_elsewhere

    confirmed, warnings, counted, elsewhere = run([channel])
    check("канал роздає дві кампанії — мовчимо", warnings == [], str(warnings))
    # Головне: True зупиняє домальовування наосліп. 15.08 так намалювалось
    # 25 хвилин, яких на боці Twitch не було — вікно показувало 266/360.
    check("наосліп не домальовуємо", confirmed is True)
    check("записано правду про сусідню кампанію", counted == [5], str(counted))
    check("причину запам'ятали", elsewhere == "Special Events", elsewhere)

    confirmed, warnings, counted, elsewhere = run([])
    check("справжній чужий перегляд — попередження",
          len(warnings) == 1 and "паралельний" in warnings[0], str(warnings))
    check("чужий перегляд — прогрес не підтверджено",
          confirmed is False and counted == [], str(counted))

    # Причина доїжджає до повідомлення замість здогаду про ручний перегляд
    fake = types.SimpleNamespace(
        _progress_mark=lambda: 40, _stall_since=0.0, _stall_alerted=False,
        events=Bus(), _counted_elsewhere="Special Events",
    )
    Miner._check_stall(fake, 40, channel, now=STALL_LIMIT * 60)
    stalled = [e for e in fake.events.sent if isinstance(e, ProgressStalled)]
    check("тривога називає справжню причину",
          len(stalled) == 1 and stalled[0].counted_elsewhere == "Special Events",
          str(stalled))


# ------------------------------------------------------------ майстер Telegram

def telegram_setup_checks() -> None:
    """Розбір відповідей Bot API. Мережу підміняємо, решта — справжня.

    Майстер веде людину по кроках і після кожного показує доказ; якщо доказ
    розібрано криво, весь сенс майстра зникає — він знову каже «щось не так».
    """
    print("\n[4б] Майстер Telegram")
    import notify.telegram as tg

    real_probe = tg._probe
    answer: tuple = (None, "")

    async def fake_probe(token, method, **payload):
        return answer

    tg._probe = fake_probe
    try:
        answer = ({"username": "RiasTwich_bot"}, "")
        name, error = asyncio.run(tg.check_token("123:ABC"))
        check("живий токен → ім'я бота", name == "RiasTwich_bot" and not error)

        answer = (None, "Telegram не знає такого токена. Скопіюй його ще раз.")
        name, error = asyncio.run(tg.check_token("сміття"))
        check("кривий токен → людська помилка", not name and "не знає" in error)

        # той самий чат у двох оновленнях — у списку має лишитись один
        answer = ([
            {"message": {"chat": {"id": 42, "first_name": "Віктор"}}},
            {"message": {"chat": {"id": 42, "first_name": "Віктор"}}},
            {"edited_message": {"chat": {"id": 7, "username": "kolega"}}},
            {"channel_post": {"chat": {"id": 99}}},
        ], "")
        chats, error = asyncio.run(tg.find_chats("123:ABC"))
        check("чати знайдено без повторів", chats == [(42, "Віктор"), (7, "kolega")],
              str(chats))

        answer = ([], "")
        chats, error = asyncio.run(tg.find_chats("123:ABC"))
        check("боту ще не писали → порожньо", chats == [] and not error)
    finally:
        tg._probe = real_probe

    # Справжня перевірка без мережі: порожній токен не має нікуди ходити.
    name, error = asyncio.run(tg.check_token("   "))
    check("порожній токен не йде в мережу", not name and "порожній" in error.lower())


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
        WatchUncounted(channel_name="канал", consecutive=2),
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
          states[2] == "active" and states[8] == "error" and states[-1] == "idle",
          str(states))
    check("та сама подія не перемальовує іконку", tray._icon.redraws == 5,
          f"перемальовувань={tray._icon.redraws}")
    check("сповіщення надіслані", len(tray._icon.notices) == 8,
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


# ------------------------------------------------------------------ журнал

def log_rotation_checks() -> None:
    print("\n[7] Ротація журналу")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "log.txt"
        handler = rotating_log_handler(
            path, max_bytes=2000, backups=2,
            formatter=logging.Formatter("{message}", style="{"),
        )
        probe = logging.getLogger("перевірка-ротації")
        probe.propagate = False
        probe.setLevel(logging.INFO)
        probe.addHandler(handler)
        try:
            for n in range(400):
                probe.info(f"рядок {n:04d} " + "х" * 60)
        finally:
            probe.removeHandler(handler)
            handler.close()

        files = sorted(p.name for p in Path(folder).iterdir())
        biggest = max(p.stat().st_size for p in Path(folder).iterdir())
        check("журнал розрізано на файли", len(files) == 3, str(files))
        check("старі копії не накопичуються без меж",
              files == ["log.txt", "log.txt.1", "log.txt.2"], str(files))
        check("жоден файл не переріс стелю", biggest <= 2000 * 1.1, f"{biggest} Б")


# ------------------------------------------------------------------ історія

def history_checks() -> None:
    print("\n[8] Історія фарму")
    with tempfile.TemporaryDirectory() as folder:
        history = History(Path(folder) / "history.jsonl")

        # порожня історія не має падати — це стан першого запуску
        check("порожня історія читається", history.entries() == [])
        check("порожній звіт зрозумілий", "жодної нагороди" in history.summary())

        bus = Bus()
        history.attach(types.SimpleNamespace(subscribe=lambda fn: None))
        for event in (
            DropClaimed(drop_name="Скін", game="EVE Online", rewards="Cyber Knight"),
            DropClaimed(drop_name="Бустер", game="World of Tanks", rewards="XP"),
            CampaignFinished(campaign_name="Foundation Day", game="EVE Online"),
            DeadlineRisk(campaigns=(
                RiskSnapshot(id="c-1", name="Пізно", game="THE FINALS",
                             minutes_needed=600, minutes_available=60),
            )),
        ):
            history._on_event(event)
            bus.emit(event)

        check("записано всі події", len(history.entries()) == 4,
              str(len(history.entries())))
        check("звіт рахує дропи", "2 дропів" in history.summary(),
              history.summary())
        check("звіт показує ігри", "EVE Online: 1" in history.summary(),
              history.summary())

        # головне заради чого все: пам'ять про попередження переживає перезапуск
        again = History(history.path)
        check("попереджені кампанії піднімаються з файлу",
              again.campaigns_warned() == {"c-1"}, str(again.campaigns_warned()))

        # зіпсований рядок не має забирати з собою решту історії
        with history.path.open("a", encoding="utf-8") as handle:
            handle.write("{це не json\n")
        check("зіпсований рядок пропускається", len(History(history.path).entries()) == 4,
              str(len(History(history.path).entries())))
        check("і не ламає пам'ять про попередження",
              History(history.path).campaigns_warned() == {"c-1"})

        written = export.write_history(Path(folder), history.entries())
        csv_text = (Path(folder) / "history.csv").read_text(encoding="utf-8-sig")
        html_text = (Path(folder) / "history.html").read_text(encoding="utf-8")
        check("історія лягає в CSV і HTML",
              len(written) == 2 and "Cyber Knight" in csv_text
              and "Cyber Knight" in html_text)

        ends = datetime.now(timezone.utc) + timedelta(hours=5)
        campaign = types.SimpleNamespace(
            name="Foundation Day",
            game=types.SimpleNamespace(name="EVE Online"),
            over=False, not_started=False, available_to_me=True,
            everything_taken=False, closes_at=ends,
            all_drops=[types.SimpleNamespace(
                name="Скін", minutes=12, required_minutes=60, taken=False,
            )],
        )
        export.write_inventory(Path(folder), [campaign])
        inv = (Path(folder) / "inventory.csv").read_text(encoding="utf-8-sig")
        check("інвентар містить гру і дроп",
              "EVE Online" in inv and "Скін" in inv and "12" in inv, inv)
        check("порожній експорт не падає",
              export.write_all(Path(folder) / "empty",
                               entries=[], campaigns=[])
              and "кампаній немає" in (Path(folder) / "empty" / "inventory.html"
                                       ).read_text(encoding="utf-8").lower())


# ------------------------------------------------------------------ картинки

def image_cache_checks() -> None:
    print("\n[9] Кеш зображень")
    with tempfile.TemporaryDirectory() as folder:
        cache = ImageCache(Path(folder) / "images", api=None)
        url = "https://static-cdn.twitch.tv/твоя/нагорода.png"

        first = cache.path_for(url)
        check("ім'я файлу стабільне", first == cache.path_for(url))
        check("розширення збережено", first.suffix == ".png", str(first))
        check("різні адреси — різні файли",
              cache.path_for(url) != cache.path_for(url + "?v=2"))
        check("порожня адреса не дає шляху", cache.path_for("") is None)
        check("невідоме розширення не ламає імені",
              cache.path_for("https://a/b").suffix == ".img")
        check("webp зберігає розширення",
              cache.path_for("https://a/b.webp").suffix == ".webp")

        check("порожнього кешу немає", cache.ready(url) is None)
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b"\x89PNG")
        check("готовий файл знайдено", cache.ready(url) == first)

        # порожній файл — не картинка: інакше збій завантаження назавжди
        # лишив би в кеші нуль байтів, який виглядає готовим
        first.write_bytes(b"")
        check("порожній файл не вважається готовим", cache.ready(url) is None)

        # api=None: якби воно спробувало піти в мережу, тут був би виняток
        first.write_bytes(b"\x89PNG")
        added = asyncio.run(cache.fetch_all([url, url, ""]))
        check("наявне не перезавантажується", added == 0, str(added))

    # Розмір показу приходить із файлу налаштувань, тобто там може лежати будь-що
    print("      межі розміру:")
    # Сітка плиток: Tk для ще не показаного віджета віддає ширину 1, і на ній
    # сітка згорталась в один стовпчик — картки йшли колонкою замість рядків.
    print("      колонки сітки:")
    for width, expect in ((1, 6), (0, 6), (100, 6), (1600, 12), (300, 2)):
        got = GUI._columns_for(width)
        check(f"  ширина {width} → {expect}", got == expect, str(got))

    print("      межі розміру:")
    for value, wanted in ((48, 48), (0, MIN_IMAGE_SIZE), (-10, MIN_IMAGE_SIZE),
                          (10_000, MAX_IMAGE_SIZE), ("сорок", DEFAULT_IMAGE_SIZE),
                          (None, DEFAULT_IMAGE_SIZE), ("64", 64)):
        got = clamp_image_size(value)
        check(f"  {value!r} → {wanted}", got == wanted, str(got))


# ------------------------------------------------------------------ автозапуск

def autostart_checks() -> None:
    print("\n[10] Автозапуск разом із Windows")
    if sys.platform != "win32":
        print("  — не Windows, пропускаємо")
        return

    # Пишемо у власний тимчасовий ключ: справжній HKCU\...\Run чіпати не можна,
    # інакше перевірка зробила б із машини те, чого ніхто не просив.
    import winreg
    probe = r"Software\TwitchDropFarm-перевірка"
    autostart.KEY_PATH, real = probe, autostart.KEY_PATH
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, probe)
    try:
        check("спочатку вимкнено", autostart.is_enabled() is False)
        check("вмикається", autostart.enable() is True)
        check("і це видно", autostart.is_enabled() is True)
        check("повторне вмикання не ламає", autostart.apply(True) is True)
        check("вимикається", autostart.disable() is True)
        check("і це теж видно", autostart.is_enabled() is False)
        check("зняття неіснуючого — не помилка", autostart.disable() is True)

        # запис від іншої збірки: команда чужа, отже автозапуск не наш
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, probe, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_SZ,
                              r'"C:\інша\копія.exe" --tray')
        check("чужий запис не вважається нашим", autostart.is_enabled() is False)
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, probe)
        autostart.KEY_PATH = real


# ------------------------------------------------------------------ оновлення

def update_checks() -> None:
    print("\n[13] Оновлення за хешем")
    check("2.0.0 не новіша за 1.0.3.1",
          not update.is_newer("2.0.0", "1.0.3.1"))
    check("1.0.4 новіша за 1.0.3.1",
          update.is_newer("1.0.4", "1.0.3.1"))
    check("та сама версія — не оновлення",
          not update.is_newer("1.0.3.1", "1.0.3.1"))
    check("v-префікс не заважає",
          update.is_newer("v1.0.4", "1.0.3.1"))

    folder = tempfile.mkdtemp()
    root = Path(folder)
    keep = root / "keep.bin"
    change = root / "change.bin"
    keep.write_bytes(b"same")
    change.write_bytes(b"old")
    keep_hash = update.file_sha256(keep)
    new_hash = hashlib.sha256(b"new").hexdigest()
    payload = {
        "version": "1.0.4",
        "files": [
            {"path": "keep.bin", "sha256": keep_hash, "size": 4},
            {"path": "change.bin", "sha256": new_hash, "size": 3,
             "url": "https://example/blob"},
        ],
    }
    manifest = update.read_manifest(payload, source="https://example/manifest.json")
    items = update.plan_fetch(manifest, root)
    check("незмінений файл не качається",
          len(items) == 1 and items[0].spec.path == "change.bin",
          str([i.spec.path for i in items]))
    try:
        update.safe_rel("../evil")
        bad = False
    except ValueError:
        bad = True
    check("шлях з .. відхиляється", bad)

    staged = Path(folder) / "stage"
    staged.mkdir()
    target = staged / "change.bin"
    target.write_bytes(b"new")
    item = update.FetchItem(
        spec=manifest.files[1], dest=target, url="https://example/blob",
    )
    update.verify_staged([item])
    target.write_bytes(b"nope")
    try:
        update.verify_staged([item])
        ok = False
    except ValueError:
        ok = True
    check("підміна після завантаження ловиться хешем", ok)

    # Пакетність — головна вимога людини: качаємо тільки те, що розійшлось.
    # Файл, якого локально немає, теж треба взяти — інакше нове ніколи не
    # приїде; а зайвий байт трафіку на незмінених робить механізм безглуздим.
    missing_hash = hashlib.sha256(b"brand new").hexdigest()
    payload_mixed = {
        "version": "1.0.4",
        "files": [
            {"path": "keep.bin", "sha256": keep_hash, "size": 4},
            {"path": "change.bin", "sha256": new_hash, "size": 3,
             "url": "https://example/blob"},
            {"path": "sub/added.bin", "sha256": missing_hash, "size": 9,
             "url": "https://example/added"},
        ],
    }
    mixed = update.read_manifest(payload_mixed, source="https://example/manifest.json")
    plan = update.plan_fetch(mixed, root)
    names = sorted(i.spec.path for i in plan)
    check("з трьох файлів беремо лише два змінені",
          names == ["change.bin", "sub/added.bin"], str(names))
    check("рахунок трафіку — тільки по тому, що качаємо",
          sum(i.spec.size for i in plan) == 12,
          str(sum(i.spec.size for i in plan)))

    # Повторний прохід після вдалого оновлення: усе зійшлось — качати нічого.
    change.write_bytes(b"new")
    check("після оновлення план порожній", update.plan_fetch(manifest, root) == [])

    # Розмір — друга сітка після хешу: зіпсований блоб того ж розміру ловить
    # хеш, а обрізаний до нуля міг би пройти, якби перевіряли лише наявність.
    target.write_bytes(b"new")
    short = update.FetchItem(
        spec=update.FileSpec(path="change.bin", sha256=new_hash, size=999),
        dest=target, url="https://example/blob",
    )
    try:
        update.verify_staged([short])
        caught = False
    except ValueError:
        caught = True
    check("розмір не зійшовся — теж відмова", caught)

    # Спіймано живою перевіркою 17.08: скрипт підміни писався в UTF-8, а cmd.exe
    # читає .cmd у консольному кодуванні. Шлях `C:\Users\Гартунг\…` ставав
    # мусором, xcopy казав «File not found», оновлення не вставало — і саме на
    # цій машині, бо ім'я користувача кириличне.
    body = update.write_apply_script().read_bytes()
    check("у тілі скрипта немає жодного шляху", body.isascii(),
          body.decode("ascii", errors="replace")[:200])
    check("шляхи приходять аргументами", b"set STAGE=%~1" in body)
    check("скрипт чекає виходу процесу", b"PID eq %PID%" in body)
    check("скрипт веде власний журнал", b"waiting for pid" in body)
    check("провал копіювання не тихий", b"XCOPY FAILED" in body)


# ------------------------------------------------------------------ доставка

def delivery_checks() -> None:
    print("\n[11] Доставка перегляду")

    class FakeBackend:
        def __init__(self, *, post_error=None, post_status=204, gql_ok=True):
            self.post_error = post_error
            self.post_status = post_status
            self.gql_ok = gql_ok
            self.posts = 0
            self.gqls = 0
            self.user_id = 1
            self.fetch_kwargs: dict = {}
            self.post_kwargs: dict = {}

        async def fetch_text(self, url, **kwargs):
            self.fetch_kwargs = kwargs
            return '"spade_url": "https://spade.twitch.tv/process"'

        async def post_form(self, url, body, **kwargs):
            self.posts += 1
            self.post_kwargs = kwargs
            if self.post_error is not None:
                raise self.post_error
            return self.post_status

        async def graphql(self, payload):
            self.gqls += 1
            if not self.gql_ok:
                raise RuntimeError("gql down")
            return {"data": {"sendSpadeEvents": {"statusCode": 204}}}

    stream = types.SimpleNamespace(
        broadcast_id=99, game=types.SimpleNamespace(id="1", name="гра"),
    )
    channel = types.SimpleNamespace(
        id=1, login="ibeast", name="ibeast",
        url="https://www.twitch.tv/ibeast", stream=stream,
    )

    def send(backend: FakeBackend) -> bool:
        return asyncio.run(WatchReporter(backend).report(channel))

    ok_spade = FakeBackend()
    check("живий spade — без GQL", send(ok_spade) and ok_spade.posts == 1
          and ok_spade.gqls == 0)
    check("spade не чіпає глобальний індикатор мережі",
          ok_spade.post_kwargs.get("count_as_network") is False
          and ok_spade.post_kwargs.get("attempts") == SPADE_ATTEMPTS,
          str(ok_spade.post_kwargs))

    blocked = FakeBackend(post_error=OSError("sinkhole"))
    check("заблокований spade падає на GQL",
          send(blocked) and blocked.posts == 1 and blocked.gqls == 1)

    http_fail = FakeBackend(post_status=403)
    check("чужий статус spade теж іде на GQL",
          send(http_fail) and http_fail.gqls == 1)

    both_down = FakeBackend(post_error=OSError("down"), gql_ok=False)
    check("обидва шляхи мертві — False", not send(both_down))

    # Якщо витяг сторінки впав, раніше метод одразу повертав False і GQL
    # навіть не пробували. Тепер це теж фолбек.
    class NoPage(FakeBackend):
        async def fetch_text(self, url, **kwargs):
            raise OSError("dns")

    no_page = NoPage()
    check("немає сторінки каналу — теж GQL",
          send(no_page) and no_page.gqls == 1 and no_page.posts == 0)

    fake = types.SimpleNamespace(
        _delivery_failures=0, events=Bus(),
    )
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

    Miner._note_delivery_ok(fake, ch)
    check("успіх скидає лічильник відмов", fake._delivery_failures == 0)
    recovered = [e.text for e in fake.events.sent if isinstance(e, StatusChanged)]
    check("після успіху статус повертається",
          recovered[-1] == "Дивимось канал", str(recovered[-1]))


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


def main() -> int:
    force_utf8_console()
    logging.getLogger("TwitchDrops").setLevel(logging.CRITICAL)
    stall_checks()
    claim_checks()
    deadline_checks()
    growing_checks()
    parallel_watch_checks()
    window_checks()
    telegram_setup_checks()
    tray_checks()
    stale_request_checks()
    log_rotation_checks()
    history_checks()
    image_cache_checks()
    autostart_checks()
    update_checks()
    delivery_checks()
    request_limit_checks()
    print("\n" + "=" * 50)
    print(f"Пройдено: {ok}   Провалено: {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
