"""Своя тема оформлення: `theme.json` поруч із рештою стану.

Формат найпростіший із можливих — словник «ключ палітри → колір», і лише ті
ключі, які хочеться перевизначити:

    {"accent": "#ff8800", "card": "#101014"}

Решта береться з вбудованої теми. Файлу типово немає, і тоді нічого не
змінюється взагалі.

Чому так, а не повноцінні теми CustomTkinter. Ті описують кожен віджет окремо
й розсипаються при оновленні бібліотеки; тут же вісім кольорів, які й так
керують усім вікном. Людина міняє один рядок і бачить результат, не вивчаючи
чужий формат.

⚠️ Помилки тут **не ковтаються**. Крива тема — це файл, який людина щойно
писала руками, і мовчазне «нічого не сталось» залишило б її гадати, чи то
програма не бачить файл, чи то колір не такий. Кожна відхилена дрібниця
називається в журналі.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("TwitchDrops")

# `#rgb` не приймаємо навмисно: Tk його розуміє, але в шести знаках менше
# шансів помилитись, а різнобій у файлі читається гірше.
COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Готові набори. Головний бар'єр був не у форматі, а в тому, що людина мусила
# вигадати дванадцять кольорів із нуля; тут вона обирає зі списку, а хто хоче
# своє — тисне «зберегти у файл» і править готове.
#
# Кожен набір задає лише те, що робить його собою: решта береться з вбудованої
# теми, тож набори не ламаються, коли в палітру додається новий ключ.
PRESETS: dict[str, dict[str, str]] = {
    # порожній — вбудована тема, як була
    "": {},
    "ocean": {
        "accent": "#4d7cff", "hover": "#1b2b52", "page": "#0a0f1a",
        "card": "#141b2d", "line": "#1f2940", "bg": "#0d1220",
    },
    "forest": {
        "accent": "#3fae6a", "hover": "#1c3a28", "page": "#0b120e",
        "card": "#141d17", "line": "#1f2c24", "bg": "#101711",
    },
    "ember": {
        "accent": "#ff7a33", "hover": "#43230f", "page": "#161010",
        "card": "#211715", "line": "#30211d", "bg": "#1b1413",
    },
    "grape": {
        "accent": "#c661e0", "hover": "#3d1c47", "page": "#140f17",
        "card": "#1e1723", "line": "#2c2136", "bg": "#181320",
    },
    "mono": {
        "accent": "#8a8a94", "hover": "#2f2f36", "page": "#121214",
        "card": "#1b1b1f", "line": "#2a2a30", "bg": "#161618",
    },
}


def preset(name: str) -> dict[str, str]:
    """Кольори набору. Невідома назва — вбудована тема, без скарг у журнал:
    у файлі налаштувань може лежати що завгодно, і це не привід шуміти."""
    return dict(PRESETS.get(name, {}))


def read_overrides(path: Path, allowed: frozenset[str]) -> dict[str, str]:
    """Читає тему з файлу. Повертає лише зрозумілі й дійсні кольори.

    `allowed` — які ключі взагалі має сенс перевизначати; усе інше відкидаємо,
    щоб одруківка в назві не виглядала як «тема не працює».
    """
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as error:
        log.warning(f"Тему не прочитано ({type(error).__name__}: {error})")
        return {}
    except json.JSONDecodeError as error:
        log.warning(f"Тема {path.name} не є коректним JSON: {error}")
        return {}

    if not isinstance(body, dict):
        log.warning(f"Тема {path.name} має бути об'єктом виду "
                    f"{{\"accent\": \"#ff8800\"}}, а не {type(body).__name__}")
        return {}

    good: dict[str, str] = {}
    for key, value in body.items():
        if key not in allowed:
            log.warning(f"Тема: ключ «{key}» невідомий, пропускаю. "
                        f"Можна задавати: {', '.join(sorted(allowed))}")
            continue
        if not isinstance(value, str) or not COLOUR.match(value):
            log.warning(f"Тема: «{key}» — не колір виду #rrggbb, пропускаю: {value!r}")
            continue
        good[key] = value.lower()
    if good:
        log.info(f"Тему застосовано, змінено кольорів: {len(good)}")
    return good


def blended(base: dict[str, str], overrides: dict[str, str]) -> dict[str, str]:
    """Вбудована палітра, поверх якої лягли кольори з теми."""
    return {**base, **{k: v for k, v in overrides.items() if k in base}}


def export(path: Path, palette: dict[str, str], cards: dict[str, str]) -> bool:
    """Записує поточні кольори у файл, щоб було з чого починати свою тему.

    Головний бар'єр для «зроби свою тему» — не формат, а порожній аркуш:
    дванадцять ключів треба звідкись узяти. Тут людина отримує їх усі, вже з
    робочими значеннями, і править те, що хоче.
    """
    body = {**palette, **cards}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(sorted(body.items())), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        log.warning(f"Тему не збережено ({type(error).__name__}: {error})")
        return False
    log.info(f"Тему збережено: {path}")
    return True
