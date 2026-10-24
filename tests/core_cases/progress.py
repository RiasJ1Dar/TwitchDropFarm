"""Регресії: progress."""
from __future__ import annotations

import asyncio
import types

from core.config import STALL_LIMIT
from core.events import ProgressStalled, StatusChanged
from core.miner import Miner
from gui.app import DARK, GUI
from gui.icon import profile_photo_jpeg
from gui.pulse import rainbow
from tests.core_cases.support import Bus, check, gui_stub, miner_stub


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
    statuses = [e.text for e in fake.events.sent if isinstance(e, StatusChanged)]
    check("застій видно в рядку статусу",
          any(text.startswith("Прогрес стоїть") for text in statuses),
          str(statuses))

    # Позначка мусить брати лише підтверджені Twitch хвилини. Якщо туди
    # повернуться домальовані наосліп, застій знову маскуватиме сам себе —
    # саме так він і не спрацьовував жодного разу.
    #
    # І рахувати мусить по ВСІХ придатних кампаніях. Кампаній однієї гри буває
    # кілька; коли одна росте, а друга стоїть, вибір «активної» за найменшим
    # залишком вказував на нерухому — і тривога била під час здорового фарму.
    def drop(counted, blind=0, fits=True):
        return types.SimpleNamespace(
            counted_minutes=counted,
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
    box = miner_stub(
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


def farm_indicator_checks() -> None:
    """Шапка мусить казати «Стоїть», а не лишати «Дивимось», коли хвилин немає."""
    print("\n[3е] Індикатор іде / стоїть")

    class FakeLabel:
        def __init__(self) -> None:
            self.text = ""
            self.fg = ""

        def configure(self, **kwargs: object) -> None:
            if "text" in kwargs:
                self.text = str(kwargs["text"])
            if "fg" in kwargs:
                self.fg = str(kwargs["fg"])

    box = gui_stub(
        palette=DARK, farm_label=FakeLabel(), _farm_state="",
    )
    box._set_farm_state = lambda state: GUI._set_farm_state(box, state)
    GUI._set_farm_state(box, "going")
    # Кружечок більше не в тексті: його малює PulseDot поруч із підписом.
    check("іде", box.farm_label.text == "Іде" and box.farm_label.fg == DARK["ok"],
          box.farm_label.text)
    GUI._set_farm_state(box, "stalled")
    check("стоїть червоним",
          "Стоїть" in box.farm_label.text and box.farm_label.fg == DARK["err"],
          box.farm_label.text)
    GUI._farm_from_status(box, "Шукаю канали")
    check("пошук каналів не зтирає «стоїть»", box._farm_state == "stalled")
    GUI._farm_from_status(box, "Дивимось ibeast")
    check("хвилини пішли — знову іде", box._farm_state == "going")
    GUI._farm_from_status(box, "Призупинено")
    check("пауза", box._farm_state == "paused")
    GUI._set_farm_state(box, "idle")
    check("чекає", "Чекає" in box.farm_label.text)
    jpeg = profile_photo_jpeg()
    check("аватар бота — JPEG", jpeg[:2] == b"\xff\xd8" and len(jpeg) > 2000,
          f"байт={len(jpeg)}")


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

    class FakeBar:
        """CTkProgressBar рахує 0..1. Тест дивиться соті, як раніше ttk."""

        def __init__(self) -> None:
            self.value = 0.0

        def set(self, fraction: float) -> None:
            self.value = fraction * 100

        def __getitem__(self, key: str) -> float:
            if key == "value":
                return self.value
            raise KeyError(key)

    box = gui_stub(
        _growing={}, _watching_name="berbatow",
        drop_var=FakeVar(), progress=FakeBar(),
        GROWING_WINDOW=GUI.GROWING_WINDOW, GROWING_LINES=GUI.GROWING_LINES,
    )
    box._set_progress = lambda percent: GUI._set_progress(box, percent)
    now = 1000.0
    box._growing = {
        "EWC Platinum": (now, "EWC 2026 · Special Events", 298, 360),
        "Inferno Collection #2": (now, "RL x EWC 2026 · Rocket League", 272, 360),
    }
    GUI._render_growing(box, now=now)
    text = box.drop_var.value
    check("видно обидві кампанії",
          "Special Events" in text and "Rocket League" in text, text)
    # «Special Events» — категорія, а не відповідь на «за що дроп». Назва
    # кампанії мусить бути в рядку.
    check("названо кампанію, а не лише категорію",
          "EWC 2026" in text and "RL x EWC 2026" in text, text)
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

    # Смуга їде до цілі плавно. Крок винесено окремою функцією саме заради
    # перевірки: сама анімація живе на `after`, якого в заглушці немає, тож
    # інакше вона лишилась би непокритою зовсім.
    step = GUI._advance(0.0, 1.0)
    check("крок іде в бік цілі", 0.0 < step < 1.0, str(step))
    check("крок не перестрибує ціль", GUI._advance(0.99, 1.0) <= 1.0)
    value = 0.0
    for _ in range(60):
        value = GUI._advance(value, 1.0)
    check("смуга доїжджає до цілі", value == 1.0, str(value))
    check("на цілі стоїть на місці", GUI._advance(1.0, 1.0) == 1.0)
    down = GUI._advance(1.0, 0.0)
    check("назад рухається так само", 0.0 < down < 1.0, str(down))

    # Колір смуги повторює мітку в шапці: око ловить його швидше, ніж читає
    # слово. Раніше вона була фіолетова завжди.
    tinted = types.SimpleNamespace(palette=DARK)
    shades = {}
    for state in ("going", "stalled", "uncounted", "paused", "idle"):
        tinted._farm_state = state
        shades[state] = GUI._progress_colour(tinted)
    check("фарм іде — смуга зелена", shades["going"] == DARK["ok"])
    check("застій і незарахований перегляд — червона",
          shades["stalled"] == DARK["err"] and shades["uncounted"] == DARK["err"])
    check("пауза — жовта", shades["paused"] == DARK["warn"])
    check("очікування — звичайний акцент", shades["idle"] == DARK["accent"])

    # Перелив — окремий стиль, який вмикається галочкою. Колір рахує чиста
    # функція, тож її можна перевірити без вікна.
    check("веселка дає правильний формат кольору",
          len(rainbow(0.0)) == 7 and rainbow(0.0).startswith("#"), rainbow(0.0))
    check("різні фази — різні кольори", rainbow(0.0) != rainbow(0.33))
    check("коло замикається", rainbow(0.0) == rainbow(1.0))
    check("фаза поза межами не ламає", rainbow(2.25) == rainbow(0.25))

    class FakeRoot:
        def __init__(self) -> None:
            self.cancelled: list[str] = []
            self.planned = 0

        def after(self, _ms: int, _fn: object) -> str:
            self.planned += 1
            return f"job{self.planned}"

        def after_cancel(self, job: str) -> None:
            self.cancelled.append(job)

    class FakeColourBar:
        def __init__(self) -> None:
            self.colour = ""

        def configure(self, **kwargs: str) -> None:
            self.colour = kwargs.get("progress_color", self.colour)

    def styled(style: str, job: str | None) -> types.SimpleNamespace:
        fake = gui_stub(
            palette=DARK, progress=FakeColourBar(), root=FakeRoot(),
            _farm_state="going", _rainbow_job=job,
            RAINBOW_FRAME_MS=GUI.RAINBOW_FRAME_MS,
            RAINBOW_PERIOD_MS=GUI.RAINBOW_PERIOD_MS,
            _rainbow_tick=lambda: None,
            _twitch=types.SimpleNamespace(
                settings=types.SimpleNamespace(progress_style=style)),
        )
        # Прив'язуємо справжній метод, а не додаємо чергове поле в заглушку:
        # інакше кожна нова дрібниця всередині коду вимагає латати цей об'єкт
        # ще раз — так уже сталось тричі поспіль, поки писались ці перевірки.
        fake._progress_colour = lambda: GUI._progress_colour(fake)
        return fake

    on = styled("rainbow", None)
    GUI._apply_progress_style(on)
    check("перелив увімкнувся", on._rainbow_job is not None)

    off = styled("state", "job1")
    GUI._apply_progress_style(off)
    check("перелив вимкнувся", off._rainbow_job is None)
    check("вимкнення повертає колір стану",
          off.progress.colour == DARK["ok"], off.progress.colour)
    check("зупинений цикл справді скасовано", off.root.cancelled == ["job1"])

    # ⚠️ Поки смуга переливається, стан її не перефарбовує: інакше два
    # правила фарбували б по черзі й вона мигтіла б.
    busy = styled("rainbow", "job1")
    busy.farm_label = types.SimpleNamespace(configure=lambda **_: None)
    busy._farm_state = ""
    GUI._set_farm_state(busy, "stalled")
    check("під час переливу стан кольором не втручається",
          busy.progress.colour == "", busy.progress.colour)


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

        fake = miner_stub(graphql=graphql, _drops={"d1": drop},
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

    # ⚠️ Сесія без дропа. Twitch зарахував хвилину, але ЖОДНОМУ дропу — майже
    # завжди це канал із вимкненими дропами. 18–21.10 ця гілка домальовувала
    # хвилину щохвилини: лічильник дійшов до 11, а на боці Twitch стояв нуль,
    # і 21.10 о 23:08 намальоване обвалилось на 1. Людина два дні бачила фарм,
    # якого не було.
    async def no_drop(_payload):
        return {"data": {"currentUser": {"dropCurrentSession": {
            "dropID": "", "currentMinutesWatched": 0,
        }}}}

    empty = miner_stub(graphql=no_drop, _drops={}, _counted_elsewhere="",
                       verify_drops_enabled=False, _no_drop_told=set())
    # Заглушка не успадковує методів, а перевіряємо ми саме зв'язку
    # «порожня сесія → попередження», тож підв'язуємо справжній.
    empty._warn_no_drop = lambda ch: Miner._warn_no_drop(empty, ch)
    spy, real = FakeLog(), miner_module.log
    miner_module.log = spy
    try:
        confirmed = asyncio.run(Miner._confirm_progress(empty, channel))
        # вдруге по тому самому каналу — журнал не заливаємо
        asyncio.run(Miner._confirm_progress(empty, channel))
    finally:
        miner_module.log = real
    check("сесія без дропа — наосліп не домальовуємо", confirmed is True)
    check("сесія без дропа — сказано один раз", len(spy.warnings) == 1,
          str(spy.warnings))
    check("підказано, що вмикати",
          "перевіряти дропи" in (spy.warnings[0] if spy.warnings else ""),
          str(spy.warnings))

    # Причина доїжджає до повідомлення замість здогаду про ручний перегляд
    fake = miner_stub(
        _progress_mark=lambda: 40, _stall_since=0.0, _stall_alerted=False,
        events=Bus(), _counted_elsewhere="Special Events",
    )
    Miner._check_stall(fake, 40, channel, now=STALL_LIMIT * 60)
    stalled = [e for e in fake.events.sent if isinstance(e, ProgressStalled)]
    check("тривога називає справжню причину",
          len(stalled) == 1 and stalled[0].counted_elsewhere == "Special Events",
          str(stalled))
