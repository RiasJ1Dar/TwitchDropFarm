"""Регресії: gui."""
from __future__ import annotations

import asyncio
import json
import tempfile
import types
from pathlib import Path
from time import monotonic

from core.config import (
    DEFAULT_IMAGE_SIZE,
    MAX_IMAGE_SIZE,
    MIN_IMAGE_SIZE,
    clamp_image_size,
    documents_dir,
    log_path,
)
from core.events import (
    CampaignFinished,
    Command,
    CommandType,
    ConnectionLost,
    ConnectionRestored,
    ControlBus,
    DropClaimed,
    LoginRequired,
    MinerError,
    MinerStarted,
    MinerStopped,
    ProgressStalled,
    StatusChanged,
    WatchingChanged,
    WatchUncounted,
    WindowVisibility,
)
from core.i18n import LANGS, resolve, set_language, t
from core.images import ImageCache
from core.miner import Miner
from gui.app import DARK, GUI, rounded_points
from gui.theme import COLOUR, PRESETS, blended, preset, read_overrides
from gui.theme import export as export_theme
from gui.tray import Tray
from tests.core_cases.support import Bus, check, miner_stub


def i18n_checks() -> None:
    print("\n[3ж] Мови")
    from notify.telegram import help_text

    set_language("uk")
    check("українська", t("pause") == "Призупинити")
    check("бот українською", "Команди" in help_text() and "Commands" not in help_text())
    set_language("en")
    check("англійська", t("pause") == "Pause")
    check("бот англійською", "Commands" in help_text() and "Команди" not in help_text())
    check("бот /status англійською", t("tg_status_title") == "📊 <b>Miner status</b>")
    set_language("zh")
    check("китайська лише коли обрали", t("pause") == "暂停")
    check("бот китайською", "命令" in help_text())
    check("невідомий код → українська", resolve("ru") == "uk")
    check("порожнє → українська, не авто", resolve("") == "uk")
    missing = []
    for code in LANGS:
        set_language(code)
        if t("tg_paused") in ("tg_paused", "") or t("tg_unknown") in ("tg_unknown", ""):
            missing.append(code)
    check("усі мови мають відповіді бота", not missing, ",".join(missing))
    source = Path("notify/telegram.py").read_text(encoding="utf-8")
    leftover = [s for s in ("Фарм призупинено", "Невідома команда", "Стан майнера")
                if s in source]
    check("у боті немає жорсткої української", not leftover, ", ".join(leftover))
    gui = Path("gui/app.py").read_text(encoding="utf-8")
    check("вікно без «Перечитати інвентар»", "Перечитати інвентар" not in gui)
    check("вікно без «Режим пріоритету»", "Режим пріоритету" not in gui)
    wiz = Path("gui/telegram_setup.py").read_text(encoding="utf-8")
    check("майстер без жорсткого заголовка", "Підключення Telegram-бота" not in wiz)
    locales = Path("core/locales")
    check("кожна мова — окремий JSON",
          all((locales / f"{code}.json").is_file() for code in LANGS))
    set_language("en")
    check("вікно англійською", t("reload_inventory") == "Reload inventory")
    set_language("uk")


def window_checks() -> None:
    print("\n[4] Керування вікном")
    for kind, visible in ((CommandType.HIDE_WINDOW, False),
                          (CommandType.SHOW_WINDOW, True)):
        box = miner_stub(events=Bus())
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
    check("застій фарбує трей як помилку",
          states[6] == "error", str(states))
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

    # Заокруглена підкладка плитки. Малюється руками, бо Canvas заокруглень не
    # вміє, а помилка тут на око не видна: крива на пару пікселів кривіша —
    # і ніхто не помітить, поки картка не почне вилазити за власне полотно.
    print("      заокруглення плитки:")
    points = rounded_points(0, 0, 100, 60, 12)
    check("  парна кількість координат", len(points) % 2 == 0, str(len(points)))
    xs, ys = points[0::2], points[1::2]
    check("  не виходить за межі",
          min(xs) == 0 and max(xs) == 100 and min(ys) == 0 and max(ys) == 60,
          f"x {min(xs)}..{max(xs)}, y {min(ys)}..{max(ys)}")
    check("  кути зрізані на радіус", 12 in xs and 88 in xs and 12 in ys)
    # Радіус більший за півсторони зрізав би кути один в одного: вийшла б
    # не картка, а пісочний годинник.
    narrow = rounded_points(0, 0, 20, 200, 50)
    check("  радіус обмежений півшириною", max(narrow[0::2]) == 20
          and sorted(set(narrow[0::2])) == [0, 10, 20],
          str(sorted(set(narrow[0::2]))))
    flat = rounded_points(0, 0, 40, 40, 0)
    check("  нульовий радіус — прямокутник",
          sorted(set(flat[0::2])) == [0, 40], str(sorted(set(flat[0::2]))))


def celebrate_checks() -> None:
    """Конфеті мусить не лише з'явитись, а й ЗНИКНУТИ.

    ⚠️ Цей блок написано за дефектом, який прожив два релізи. `Canvas.lift`
    у tkinter — не «підняти віджет над сусідами», а псевдонім `tag_raise`, і
    без аргументу він кидає TclError. Виняток летів після `place()`, але до
    першого `after()`: накладка з частинками вже лежала поверх картки, а
    анімація не стартувала жодного разу. Конфеті застигало назавжди й
    перекривало назву каналу. Ззовні це виглядало як «артефакти малювання».

    Тому перевіряємо саме те, чого бракувало: що анімація СТАРТУВАЛА
    (`_job` не порожній), і що після неї нічого не лишилось.
    """
    import time

    print("\n[29] Святкування дропа")
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as error:  # немає дисплея — не привід валити весь набір
        print(f"  --   Tk недоступний, пропускаю: {error}")
        return
    try:
        root.withdraw()
        from gui.celebrate import LIFETIME_MS, Confetti

        frame = tk.Frame(root, width=400, height=120)
        frame.pack()
        spark = Confetti(frame, background="#1e1d24",
                         colours=("#ff0000", "#00ff00", "#0000ff"))
        root.update()

        spark.burst()
        check("святкування почалось — частинки намальовані",
              len(spark.find_all()) > 0)
        check("анімація справді запустилась", spark._job is not None)

        deadline = monotonic() + LIFETIME_MS / 1000 + 3
        while spark._job is not None and monotonic() < deadline:
            root.update()
            time.sleep(0.005)

        check("частинки прибрано після свята", spark.find_all() == ())
        check("накладку знято з картки", not spark.winfo_ismapped())
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def theme_checks() -> None:
    print("\n[12] Своя тема оформлення")

    allowed = frozenset(DARK) | frozenset({"page", "card", "line", "hover"})
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "theme.json"
        check("теми немає — і не треба", read_overrides(path, allowed) == {})

        # Журнал: типово в «Документах», щоб людина знайшла його сама, не
        # питаючи, де ховається %LOCALAPPDATA%.
        check("порожня тека — типове місце",
              log_path().name == "log.txt"
              and log_path().parent == documents_dir())
        check("своя тека береться як є",
              log_path(str(Path(folder))) == Path(folder) / "log.txt")
        check("пробіли навколо шляху не ламають",
              log_path(f"  {folder}  ") == Path(folder) / "log.txt")
        check("типова тека завжди існує або відступає до стану",
              documents_dir().is_absolute())

        path.write_text(json.dumps({"accent": "#FF8800", "card": "#101014"}),
                        encoding="utf-8")
        good = read_overrides(path, allowed)
        check("свої кольори прийнято",
              good == {"accent": "#ff8800", "card": "#101014"}, str(good))

        # Одруківка в назві не має виглядати як «тема не працює» — такий
        # ключ відкидається окремо й гучно, а решта файлу лишається чинною.
        path.write_text(json.dumps({"акцент": "#ff8800", "ok": "#00ff00"}),
                        encoding="utf-8")
        check("невідомий ключ пропускається, решта діє",
              read_overrides(path, allowed) == {"ok": "#00ff00"})

        path.write_text(json.dumps({"accent": "червоний", "ok": "#fff",
                                    "err": 16711680, "warn": "#ffb020"}),
                        encoding="utf-8")
        check("кольори не виду #rrggbb відкинуто",
              read_overrides(path, allowed) == {"warn": "#ffb020"})

        path.write_text("{зламаний", encoding="utf-8")
        check("побитий файл не валить вікно", read_overrides(path, allowed) == {})
        path.write_text(json.dumps(["#ff8800"]), encoding="utf-8")
        check("список замість об'єкта — теж не валить",
              read_overrides(path, allowed) == {})

    mixed = blended(DARK, {"accent": "#ff8800", "невідоме": "#000000"})
    check("накладається лише відоме",
          mixed["accent"] == "#ff8800" and "невідоме" not in mixed)
    check("решта кольорів лишається вбудованою", mixed["ok"] == DARK["ok"])
    check("вбудована палітра не псується", DARK["accent"] == "#9147ff")

    # Набори: обирають зі списку ті, хто не хоче писати JSON руками.
    check("порожня назва — вбудована тема", preset("") == {})
    check("невідомий набір не валить вікно", preset("бла-бла") == {})
    check("набір дає кольори", preset("ocean").get("accent") == "#4d7cff")
    for name, colours in PRESETS.items():
        # окремою змінною: вкладені лапки в f-рядку з'явились аж у 3.12, а
        # проєкт обіцяє працювати з 3.10
        label = name or "вбудований"
        wrong = [k for k, v in colours.items() if not COLOUR.match(v)]
        check(f"набір «{label}» має лише коректні кольори", not wrong, str(wrong))
        unknown = set(colours) - (set(DARK) | {"page", "card", "line", "hover"})
        check(f"набір «{label}» не вигадує ключів", not unknown, str(unknown))

    # ⚠️ Порядок накладання: вбудована → набір → theme.json. Файл останній,
    # бо це ручна правка: хто його написав, має бачити свій колір.
    mixed = blended(DARK, {**preset("ocean"), **{"accent": "#000000"}})
    check("файл перебиває набір", mixed["accent"] == "#000000")
    check("решта кольорів набору лишається",
          blended(DARK, preset("forest"))["accent"] == "#3fae6a")

    # Експорт: щоб своя тема починалась не з порожнього аркуша.
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "theme.json"
        check("тему збережено", export_theme(out, DARK, {"card": "#101014"}))
        saved = json.loads(out.read_text(encoding="utf-8"))
        check("у файлі всі кольори палітри",
              set(DARK) <= set(saved) and saved["card"] == "#101014")
        check("збережене читається назад як тема",
              read_overrides(out, frozenset(saved)) == {
                  k: v.lower() for k, v in saved.items()})
