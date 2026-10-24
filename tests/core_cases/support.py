"""Спільні перевірені заглушки, шина та лічильники результатів."""
from __future__ import annotations

import argparse
import types

from core.events import StatusChanged
from core.miner import Miner
from core.settings import Settings

ok = 0


fail = 0


def class_attrs(cls: type) -> frozenset[str]:
    """Усе, що справжній клас заводить на собі.

    Збираємо з трьох джерел: клас (методи й властивості), анотації та
    присвоєння `self.X =` у `__init__`. Останнє — через розбір джерела, бо
    інакше атрибути екземпляра не видно, доки об'єкт не створено, а створювати
    справжній об'єкт у перевірках ядра ми не хочемо: він тягне мережу
    або вікно.
    """
    import inspect
    import re as _re

    names = set(dir(cls)) | set(getattr(cls, "__annotations__", {}))
    # Джерело всього класу, а не лише `__init__`: половина атрибутів
    # заводиться пізніше — у `_load_palette`, у побудові вкладок, у обробниках.
    source = inspect.getsource(cls)
    names.update(_re.findall(r"self\.([A-Za-z_]\w*)\s*[:=]", source))
    return frozenset(names)


_MINER_ATTRS = class_attrs(Miner)


def stub_of(cls: type, **fields: object) -> types.SimpleNamespace:
    """Звірена заглушка будь-якого класу.

    Дрібні заглушки на одне-два поля (`channel`, `drop`, `stream`) свідомо
    лишились простими: вони не ламаються від змін у майнері, а звіряти
    `name="канал"` — робота заради роботи. Але коли така заглушка виросте,
    ось готовий спосіб не лишити її сліпою.
    """
    return _checked_stub(cls, class_attrs(cls), fields)


def _checked_stub(cls: type, known: frozenset[str],
                  fields: dict[str, object]) -> types.SimpleNamespace:
    unknown = sorted(set(fields) - known)
    if unknown:
        raise AssertionError(
            f"Заглушка {cls.__name__} описує поля, яких у класі немає: "
            f"{', '.join(unknown)}. Або поле перейменували, або в перевірці "
            f"помилка."
        )
    return types.SimpleNamespace(**fields)


def gui_stub(**fields: object) -> types.SimpleNamespace:
    """Те саме для вікна: заглушка, звірена зі справжнім `GUI`."""
    from gui.app import GUI as _GUI

    return _checked_stub(_GUI, class_attrs(_GUI), fields)


def miner_stub(**fields: object) -> types.SimpleNamespace:
    """Заглушка майнера, звірена зі справжнім класом.

    ⚠️ Заглушки `SimpleNamespace` мовчазні за природою: вони приймають будь-яке
    ім'я поля. Через це перевірка могла роками стояти на полі, якого в `Miner`
    давно немає, і при цьому бути зеленою — тобто нічого не перевіряти.
    Зворотний бік теж болить: 09.09 нове поле `bench` поклало відразу три
    перевірки, бо заглушка про нього не знала.

    Тут ми хоча б ловимо перше: поле, якого в справжньому класі немає, валить
    перевірку одразу й називає його. Друге ловиться природно — заглушка без
    потрібного поля впаде на виклику.
    """
    return _checked_stub(Miner, _MINER_ATTRS, fields)


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
