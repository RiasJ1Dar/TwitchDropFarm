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
