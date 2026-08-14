"""Робить `icon.ico` для збірки з того самого малюнка, що й у програмі.

Запускати після зміни `gui/icon.py`:

    env\\Scripts\\python.exe tools\\make_icon.py

Окремий крок, а не частина збірки: PyInstaller читає `icon=` як готовий файл,
і генерувати його всередині `.spec` означало б робити збірку залежною від того,
чи імпортується Pillow у момент запуску збирача.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gui.icon import ico_sizes, make_icon  # noqa: E402

TARGET = ROOT / "icon.ico"


def main() -> int:
    sizes = ico_sizes()
    biggest = make_icon(max(sizes))
    biggest.save(TARGET, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"{TARGET.name}: {TARGET.stat().st_size:,} Б, розміри {sizes}")

    # Дрібні розміри найважливіші: саме їх видно в треї й на панелі завдань,
    # і саме там силует найлегше загубити. Показуємо, що вийшло.
    preview = ROOT / "icon-preview.png"
    strip = make_icon(16)
    print(f"перевірка: 16 px дає {strip.size}, режим {strip.mode}")
    preview.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
