"""Регресії: storage."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import autostart, export, protocol, update
from core import history as history_module
from core.events import CampaignFinished, DeadlineRisk, DropClaimed, RiskSnapshot
from core.history import History
from core.identity import Identity
from core.seen import RETENTION_DAYS, SeenCampaigns
from core.settings import Settings
from core.toolbox import claim_single_instance, human_size, plural, rotating_log_handler
from tests.core_cases.support import Bus, check


def lock_and_wording_checks() -> None:
    """Замок і людські формулювання.

    18.08 друга копія програми впала вікном з `PermissionError`: файл замка
    відкривався на запис і в нього писали ДО спроби блокування, а перший
    процес тримає саме перший байт. Замість «Програма вже запущена» людина
    бачила трасування.
    """
    print("\n[14] Замок і формулювання")

    folder = Path(tempfile.mkdtemp())
    path = folder / "lock.file"
    ok, first = claim_single_instance(path)
    check("перший захоплює замок", ok and first is not None)

    # Недосяжний шлях — це «не змогли», а не привід падати
    ok2, handle2 = claim_single_instance(folder / "немає-теки" / "lock.file")
    check("недосяжний шлях не кидає виняток", ok2 is False and handle2 is None)

    # Читати можна лише після звільнення: поки замок тримається, Windows не
    # дає навіть прочитати заблокований байт — на цьому тест і спіймав себе.
    if first is not None:
        first.close()
    check("вміст записано", path.read_text(encoding="utf8").strip() == "lock")

    check("1 файл", plural(1, "файл", "файли", "файлів") == "файл")
    check("2 файли", plural(2, "файл", "файли", "файлів") == "файли")
    check("5 файлів", plural(5, "файл", "файли", "файлів") == "файлів")
    check("11 файлів", plural(11, "файл", "файли", "файлів") == "файлів")
    check("21 файл", plural(21, "файл", "файли", "файлів") == "файл")
    check("22,7 МБ замість 23213 КБ", human_size(23771050) == "22,7 МБ",
          human_size(23771050))
    check("дрібне в КБ", human_size(23771) == "23 КБ", human_size(23771))

    # Скрипт підміни мусить чекати ВСІ процеси: PyInstaller onefile тримає два,
    # і 18.08 заміна впала зі «Sharing violation», бо чекали лише один PID.
    body = update.write_apply_script().read_bytes()
    check("чекає не лише PID, а й ім'я процесу", b"IMAGENAME eq %NAME%" in body)
    check("повторює копіювання, а не здається одразу", b"TRY% GEQ 5" in body)
    check("повертає стару збірку, якщо не вийшло",
          b"starting old build back" in body)
    check("піднімає з тими самими аргументами", b'"%EXE%" %ARGS%' in body)
    # 18.08 скрипт завис назавжди: програма перезапускала себе сама, ім'я
    # процесу з'являлось знову, і цикл очікування не мав виходу.
    check("очікування процесу обмежене", b"WAIT% GTR 30" in body)


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


def history_checks() -> None:
    print("\n[8] Історія фарму")
    with tempfile.TemporaryDirectory() as folder:
        history = History(Path(folder) / "history.jsonl")

        # порожня історія не має падати — це стан першого запуску
        check("порожня історія читається", history.entries() == [])
        check("порожній звіт зрозумілий", "жодної нагороди" in history.summary())
        check("типовий звіт за 3 місяці", "90" in history.summary(),
              history.summary())

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

    # ⚠️ `MAX_ENTRIES` досі обмежував лише читання: файл дописувався довічно,
    # тож «останні 5000» з кожним роком коштували дедалі дорожче — читати
    # однаково доводилось усе.
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "history.jsonl"
        history = History(path)
        keep, every = history_module.MAX_ENTRIES, history_module.TRIM_EVERY
        history_module.MAX_ENTRIES, history_module.TRIM_EVERY = 5, 3
        try:
            for number in range(12):
                history.record("drop", game="гра", drop=f"дроп {number}")
            lines = path.read_text(encoding="utf-8").splitlines()
            check("історія обрізається до межі", len(lines) <= 5, f"рядків={len(lines)}")
            check("лишились саме останні записи", "дроп 11" in lines[-1], lines[-1])
            check("обрізана історія лишається читабельною",
                  len(history.entries()) == len(lines))

            short = History(Path(folder) / "short.jsonl")
            short.record("drop", game="гра", drop="єдиний")
            check("коротку історію не чіпаємо", len(short.entries()) == 1)
        finally:
            history_module.MAX_ENTRIES, history_module.TRIM_EVERY = keep, every


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

        # Відновлення зниклого запису. ⚠️ За скаргою 06.09: «автозапуск не
        # проходить після оновлення». Запис у Run може прибрати антивірус
        # після заміни бінарника, а програма знала лише те, що каже реєстр.
        check("без наміру нічого не робимо", autostart.restore(False) is None)
        check("запису немає — повертаємо", autostart.restore(True) is True)
        check("і він справді на місці", autostart.is_enabled() is True)
        check("запис цілий — не чіпаємо", autostart.restore(True) is False)
        autostart.disable()

        # запис від іншої збірки: команда чужа, отже автозапуск не наш
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, probe, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_SZ,
                              r'"C:\інша\копія.exe" --tray')
        check("чужий запис не вважається нашим", autostart.is_enabled() is False)
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, probe)
        autostart.KEY_PATH = real


def seen_checks() -> None:
    print("\n[13] Побачені кампанії")

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "seen.json"
        book = SeenCampaigns(path)
        check("порожньо — перший запуск, сповіщати нічого", not book.known)
        check("усе нове, поки нічого не бачили", book.fresh({"a", "b"}) == {"a", "b"})
        book.remember({"a", "b"})
        check("після запису вже є з чим порівнювати", book.known)
        check("бачене більше не нове", book.fresh({"a", "c"}) == {"c"})

        # Старий формат — простий список без дат. Оновлення програми не має
        # змітати набір, інакше людина отримає десятки «нових» кампаній.
        path.write_text(json.dumps(["x", "y"]), encoding="utf-8")
        old = SeenCampaigns(path)
        check("старий формат читається", old.fresh({"x", "z"}) == {"z"})

        # ⚠️ Головне заради чого все: набір мусить забувати давнє, інакше
        # росте довічно. Але не раніше строку.
        long_ago = (datetime.now(timezone.utc).date()
                    - timedelta(days=RETENTION_DAYS + 1)).isoformat()
        recent = (datetime.now(timezone.utc).date()
                  - timedelta(days=RETENTION_DAYS - 1)).isoformat()
        path.write_text(json.dumps({"старе": long_ago, "свіже": recent}),
                        encoding="utf-8")
        aged = SeenCampaigns(path)
        aged.remember({"нове"})
        check("давно не бачене забуто", "старе" in aged.fresh({"старе"}))
        check("те, що в межах строку, лишається",
              aged.fresh({"свіже"}) == set())
        saved = json.loads(path.read_text(encoding="utf-8"))
        check("забуте зникло і з файлу",
              set(saved) == {"свіже", "нове"}, str(sorted(saved)))

        # Файл не має переписуватись, коли нічого не змінилось: `remember`
        # кличеться на кожне читання інвентаря, тобто щогодини.
        before = path.stat().st_mtime_ns
        aged.remember({"нове"})
        check("той самий склад того ж дня — файл не чіпаємо",
              path.stat().st_mtime_ns == before)


def identity_checks() -> None:
    print("\n[14] Готовність особи")

    person = Identity(types.SimpleNamespace())
    person.token = "oauth"
    person.user_id = 42
    check("токен і користувач — уже готові", person.known)

    # ⚠️ `ensure()` кличеться перед КОЖНИМ запитом до Twitch. Поки він брав
    # замок беззастережно, паралельні пакети шикувались у чергу заради
    # перевірки прапорця. Тримаємо замок зайнятим і переконуємось, що
    # готова особа крізь нього проходить.
    async def through_busy_lock() -> bool:
        await person._lock.acquire()
        try:
            await asyncio.wait_for(person.ensure(), timeout=1.0)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            person._lock.release()

    check("готова особа не чекає на замок", asyncio.run(through_busy_lock()))
    check("готовність оголошено", person._ready.is_set())

    # А ось незавершена особа мусить дійти до замка, а не проскочити повз
    # нього: інакше двоє одночасно полізли б по токен.
    fresh = Identity(types.SimpleNamespace())

    async def blocked_when_unknown() -> bool:
        await fresh._lock.acquire()
        try:
            await asyncio.wait_for(fresh.ensure(), timeout=0.3)
            return False
        except asyncio.TimeoutError:
            return True
        finally:
            fresh._lock.release()

    check("незнайома особа таки чекає на замок",
          asyncio.run(blocked_when_unknown()))


def settings_method_checks() -> None:
    """alter() падав як «немає налаштування» — тести save() не кликали."""
    print("\n[налаштування] touch замість неіснуючого alter")
    settings = Settings()
    settings.touch()
    check("touch не падає", True)
    missing = []
    for rel in ("gui/app.py", "gui/telegram_setup.py", "notify/telegram.py"):
        text = Path(rel).read_text(encoding="utf-8")
        if ".alter()" in text:
            missing.append(rel)
    check("жоден виклик .alter()", not missing, ", ".join(missing))
    source = Path("tools/check_drop.py").read_text(encoding="utf-8")
    check("check_drop не імпортує core.constants",
          "core.constants" not in source)
    payload = protocol.INVENTORY()
    check("INVENTORY збирає payload",
          payload.get("operationName") == "Inventory", str(payload))


def identity_storage_checks() -> None:
    """DPAPI: старий відкритий файл читається, новий на диску без токена."""
    print("\n[сховище токена] DPAPI")
    import core.identity as ident
    old = ident.TOKEN_FILE
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "auth.json"
        ident.TOKEN_FILE = path
        try:
            path.write_text(
                '{"access_token": "plain-token", "user_id": 414015364}',
                encoding="utf-8",
            )
            token, user_id = Identity._load()
            check("старий відкритий auth.json читається",
                  token == "plain-token" and user_id == 414015364,
                  f"{token!r} {user_id}")
            holder = Identity.__new__(Identity)
            holder.token = "plain-token"
            holder.user_id = 414015364
            holder._persist()
            on_disk = path.read_text(encoding="utf-8")
            check("після запису токена немає відкритим текстом",
                  "plain-token" not in on_disk, on_disk[:120])
            check("файл позначено protected",
                  '"protected"' in on_disk, on_disk[:80])
            again, user_again = Identity._load()
            check("шифрований читається назад",
                  again == "plain-token" and user_again == 414015364,
                  f"{again!r} {user_again}")
        finally:
            ident.TOKEN_FILE = old
