"""Регресії: updates."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import types
from pathlib import Path

from core import update
from core.config import UPDATE_CHECK_EVERY
from core.events import UpdateAvailable, UpdateFailed
from core.miner import Miner
from core.update_manager import UpdateCoordinator
from tests.core_cases.support import Bus, check, miner_stub


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

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    old_pub = update.MANIFEST_PUBLIC_KEY
    update.MANIFEST_PUBLIC_KEY = public
    try:
        payload = {
            "version": "1.0.6",
            "files": [{"path": "a.bin", "sha256": "a" * 64, "size": 1}],
        }
        signed = update.sign_manifest(payload, private)
        update.verify_signature(signed)
        check("валідний підпис проходить", True)
        signed["files"][0]["size"] = 99
        try:
            update.verify_signature(signed)
            intact = False
        except ValueError:
            intact = True
        check("підміна розміру ламає підпис", intact)
        try:
            update.verify_signature(payload)
            need = False
        except ValueError:
            need = True
        check("без підпису відмова і з вихідників", need)

        # ⚠️ Ключ у секреті GitHub може розійтися з публічним у коді, і
        # тоді релізи виходять «підписані», а не встановлюються НІ В КОГО.
        # Саме так автооновлення мовчки не працювало два тижні, з 1.0.6.
        # Запобіжник живе у `tools/write_manifest.py`; тут перевіряємо те,
        # на чому він стоїть: чужий ключ мусить давати відмову.
        stranger = Ed25519PrivateKey.generate().private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
        foreign = update.sign_manifest(dict(payload), stranger)
        try:
            update.verify_signature(foreign)
            caught = False
        except ValueError:
            caught = True
        check("підпис чужим ключем не проходить", caught)
    finally:
        update.MANIFEST_PUBLIC_KEY = old_pub

    check("інтервал перевірки — 12 годин", UPDATE_CHECK_EVERY == 12 * 60 * 60)

    # Провал підміни мусить називати ПРИЧИНУ. 05.09 у програмі стояло
    # «не вдалося замінити файли», і далі йти було нікуди, хоч `xcopy` писав
    # причину в той самий журнал.
    # Скрипт підміни мусить ПЕРЕПИТАТИ запуск, а не вірити, що «start» = успіх.
    # 01–06.09 нова збірка тричі показала «Failed to load Python DLL» і щоразу
    # піднімалась із другої спроби — руками. Причина не знайдена, тож повтор
    # робить те саме, що робила людина. Обидва сценарії перевірені живим
    # прогоном скрипта; тут стежимо, щоб механізм не зник із тіла.
    script = update.write_apply_script().read_text(encoding="ascii")
    check("запуск чекає підтвердження від самої програми",
          ":waitflag" in script and "%FLAG%" in script)
    check("є повторна спроба запуску", ":retry" in script and ":launch" in script)
    check("зависле вікно помилки знімається перед повтором",
          "taskkill" in script)
    check("спроб рівно дві, не нескінченно", "if %TRY% GEQ 2" in script)
    check("маркер стирається перед запуском, а не лишається від старого",
          script.index("del /F /Q " + chr(34) + "%FLAG%") < script.index(":launch"))

    NL = chr(10)
    why = update._why_copy_failed
    check("зайнятий файл названо своїм імʼям",
          why("copying" + NL + "Sharing violation" + NL + "XCOPY FAILED")
          == "файл був зайнятий іншим процесом")
    check("брак прав названо своїм імʼям",
          why("copying" + NL + "Access is denied" + NL + "XCOPY FAILED")
          == "немає прав на теку програми")
    unknown = why("all processes gone, copying" + NL
                  + "0 File(s) copied" + NL + "XCOPY FAILED after 5 tries")
    check("незнайому причину віддано як є, а не проковтнуто",
          "0 File(s) copied" in unknown)
    check("порожній журнал не вигадує причини",
          why("waiting for pid 1" + NL + "XCOPY FAILED")
          == "не вдалося замінити файли")

    # Прибирання тек `_MEI*`: воно ВИДАЛЯЄ теки, тому помилка тут дорога.
    # Перевіряємо обидві обережності — чужу не чіпати, живу не чіпати.
    with tempfile.TemporaryDirectory() as fake_temp:
        room = Path(fake_temp)
        for name in ("_MEI111111", "_MEI333333"):
            (room / name / "core" / "locales").mkdir(parents=True)
            (room / name / "core" / "locales" / "uk.json").write_text(
                "{}", encoding="utf-8")
        stranger = room / "_MEI222222"
        (stranger / "core").mkdir(parents=True)
        (stranger / "core" / "other.txt").write_text("x", encoding="utf-8")
        orphan, current = room / "_MEI111111", room / "_MEI333333"

        was_frozen, was_temp = update.FROZEN, tempfile.gettempdir
        had_mei = getattr(sys, "_MEIPASS", None)
        update.FROZEN = True
        tempfile.gettempdir = lambda: str(room)
        sys._MEIPASS = str(current)
        try:
            gone = update.sweep_stale_bundles()
        finally:
            update.FROZEN = was_frozen
            tempfile.gettempdir = was_temp
            if had_mei is None:
                del sys._MEIPASS
            else:
                sys._MEIPASS = had_mei

        check("чужу теку не чіпає", stranger.exists())
        check("теку поточного запуску не чіпає", current.exists())
        check("осиротілу нашу теку прибрано", gone == 1 and not orphan.exists())

    # Логіка пропозиції переїхала з `Miner` в `UpdateCoordinator`, і перевірки
    # їдуть за нею. Звірена заглушка лишається: координатор говорить із
    # майнером, і поле, якого в справжньому класі немає, має валити перевірку.
    fake = miner_stub(update_postponed=False, events=Bus(), say=lambda text: None)
    updater = UpdateCoordinator(fake)
    blob = types.SimpleNamespace(spec=types.SimpleNamespace(size=100))
    ver = types.SimpleNamespace(version="1.0.7")
    updater.offer_update(ver, [blob])
    first = [e for e in fake.events.sent if isinstance(e, UpdateAvailable)]
    updater.offer_update(ver, [blob])
    again = [e for e in fake.events.sent if isinstance(e, UpdateAvailable)]
    check("перша поява оновлення — подія", len(first) == 1)
    check("та сама версія вдруге не нагадує", len(again) == 1)
    fake.update_postponed = True
    updater.offer_update(types.SimpleNamespace(version="1.0.8"), [blob])
    later = [e for e in fake.events.sent if isinstance(e, UpdateAvailable)]
    check("відкладене не нагадує до перезапуску", len(later) == 1)

    # ⚠️ Провал перевірки мусить бути ЧУТНИМ. Зламаний підпис манифесту два
    # тижні не давав оновитись, і про це не знав ніхто: у вікні порожньо, у
    # Telegram порожньо, а журнал тоді вівся лише з `--log`.
    quiet = UpdateCoordinator(miner_stub(events=Bus()))
    quiet._update_check_failed(ValueError("підпис манифесту недійсний"))
    said = [e for e in quiet.miner.events.sent if isinstance(e, UpdateFailed)]
    check("зламаний підпис — подія, а не тільки журнал", len(said) == 1)
    quiet._update_check_failed(ValueError("підпис манифесту недійсний"))
    check("та сама причина вдруге не повторюється",
          len([e for e in quiet.miner.events.sent if isinstance(e, UpdateFailed)]) == 1)
    quiet._update_check_failed(OSError("мережа впала"))
    check("обірвана мережа людину не смикає",
          len([e for e in quiet.miner.events.sent if isinstance(e, UpdateFailed)]) == 1)
    quiet._update_check_failed(ValueError("манифест 404 з https://…"))
    check("404 — це «релізу ще немає», не поломка",
          len([e for e in quiet.miner.events.sent if isinstance(e, UpdateFailed)]) == 1)

    # Стан не роздвоєний: майнер віддає те саме, що тримає координатор.
    check("майнер читає план у координатора", Miner._update_plan.fget is not None)
    live = miner_stub(update_postponed=False, events=Bus(), say=lambda text: None)
    live.updater = UpdateCoordinator(live)
    live.updater.offer_update(ver, [blob])
    check("план видно через старе ім'я",
          Miner._update_plan.fget(live) is live.updater.plan)
