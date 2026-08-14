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
    """
    if FROZEN:
        return f'"{sys.executable}" --tray'
    # з вихідників — інтерпретатор і скрипт окремо
    return f'"{sys.executable}" "{sys.argv[0]}" --tray'


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


def apply(wanted: bool) -> bool:
    """Приводить реєстр до бажаного стану. Повертає, що вийшло насправді."""
    if wanted == is_enabled():
        return wanted
    ok = enable() if wanted else disable()
    return wanted if ok else is_enabled()
