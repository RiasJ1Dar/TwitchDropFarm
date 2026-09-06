"""Запуск разом із Windows.

Через реєстр (`HKCU\\...\\Run`), а не ярликом у теці автозавантаження: запис
під поточним користувачем не потребує прав адміністратора, легко читається й
знімається, і його видно там, де його шукають — у диспетчері завдань.

Свідомо не чіпаємо `HKLM`: це вимагало б адміністратора й запускало б програму
для всіх користувачів комп'ютера, чого ніхто не просив.
"""
from __future__ import annotations

import logging
import sys

from core.config import FROZEN

log = logging.getLogger("TwitchDrops")

KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "TwitchDropFarm"


def _command() -> str:
    """Що саме запускати. У лапках — інакше пробіл у шляху розірве команду.

    Запускаємо одразу згорнутим: автозапуск, який розгортає вікно на весь
    екран під час входу в систему, дратує більше, ніж допомагає.

    ⚠️ `--log` тут обов'язковий. Без нього програма, піднята Windows, працює
    мовчки: `log.txt` не оновлюється тижнями, і на скаргу «прогрес стоїть»
    дивитись нема по чому — саме так 21.08 довелось діагностувати застій
    запитами до Twitch замість двох рядків журналу. Ротація в нас є
    (5 МБ × 3 копії), а темп — 0,1 МБ на добу, тож ціна нульова.
    """
    if FROZEN:
        return f'"{sys.executable}" --tray --log'
    # з вихідників — інтерпретатор і скрипт окремо
    return f'"{sys.executable}" "{sys.argv[0]}" --tray --log'


def is_enabled() -> bool:
    """Чи стоїть запис і чи вказує він на цю саму програму.

    Порівнюємо з поточною командою: якщо `.exe` переїхав, запис лишається, але
    веде в нікуди — і галочка в налаштуваннях брехала б, що все гаразд.
    """
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY_PATH) as key:
            stored, _kind = winreg.QueryValueEx(key, VALUE_NAME)
    except (FileNotFoundError, OSError):
        return False
    return str(stored).strip().lower() == _command().strip().lower()


def enable() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
    except (ImportError, OSError) as error:
        log.warning(f"Автозапуск не увімкнувся: {error}")
        return False
    log.info("Автозапуск разом із Windows увімкнено")
    return True


def disable() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return True  # його там і не було — стан той самий, якого хотіли
    except (ImportError, OSError) as error:
        log.warning(f"Автозапуск не вимкнувся: {error}")
        return False
    log.info("Автозапуск разом із Windows вимкнено")
    return True


def restore(wanted: bool) -> bool | None:
    """Повертає запис, якщо людина його ставила, а він зник.

    ⚠️ Написано за скаргою 06.09: «автозапуск не проходить після оновлення,
    треба вручну вмикати». Запис у `Run` не наш до скону — після заміни
    бінарника його може прибрати антивірус, чистильник чи політика. Програма
    ж досі знала лише те, що каже реєстр: немає запису — значить людина не
    хотіла. Тому втрата була тихою й повторюваною.

    Повертає `True`, якщо запис довелось відновити, `False` — якщо все на
    місці, `None` — якщо автозапуску не просили.

    Свідомо не чіпаємо випадок, коли запис Є, але вимкнений у диспетчері
    завдань: Windows тримає це окремим прапорцем у `StartupApproved`, сам
    запис лишається на місці, і нав'язувати себе поперед людини не можна.
    """
    if not wanted:
        return None
    if is_enabled():
        return False
    log.warning("Запис автозапуску зник — відновлюю")
    return enable()


def apply(wanted: bool) -> bool:
    """Приводить реєстр до бажаного стану. Повертає, що вийшло насправді."""
    if wanted == is_enabled():
        return wanted
    ok = enable() if wanted else disable()
    return wanted if ok else is_enabled()
