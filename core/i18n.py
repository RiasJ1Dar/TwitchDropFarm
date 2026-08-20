"""Мови інтерфейсу. Набір як README на GitHub, без російської.

Типово — українська. «auto» — мова Windows, якщо вона в наборі; інакше знову
українська. Китайська ніколи не підставляється «бо так випало»: лише коли її
обрали або Windows саме zh. Бот відповідає тією ж мовою, що й вікно.

Тексти — `core/locales/{код}.json`, по файлу на мову. Тут лише вибір і `t()`.
"""
from __future__ import annotations

import json
import locale
import sys
from pathlib import Path

LANGS = ("uk", "en", "es", "pt", "de", "fr", "pl", "tr", "zh")

NAMES = {
    "uk": "Українська",
    "en": "English",
    "es": "Español",
    "pt": "Português",
    "de": "Deutsch",
    "fr": "Français",
    "pl": "Polski",
    "tr": "Türkçe",
    "zh": "简体中文",
}

_current = "uk"
_loaded: dict[str, dict[str, str]] = {}


def _locales_dir() -> Path:
    here = Path(__file__).resolve().parent / "locales"
    if here.is_dir():
        return here
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "core" / "locales"
    return here


def _table(code: str) -> dict[str, str]:
    if code not in _loaded:
        path = _locales_dir() / f"{code}.json"
        _loaded[code] = json.loads(path.read_text(encoding="utf-8"))
    return _loaded[code]


def detect_os() -> str:
    for candidate in (locale.getlocale()[0], locale.getdefaultlocale()[0]):
        if not candidate:
            continue
        code = candidate.replace("-", "_").split("_")[0].lower()
        if code in LANGS:
            return code
    return "uk"


def resolve(stored: str) -> str:
    """Типово українська. «auto» — мова Windows, якщо вона в наборі."""
    code = (stored or "").strip().lower()
    if code == "auto":
        return detect_os()
    if not code:
        return "uk"
    return code if code in LANGS else "uk"


def set_language(code: str) -> str:
    global _current
    _current = resolve(code)
    _table("uk")
    if _current != "uk":
        _table(_current)
    return _current


def language() -> str:
    return _current


def t(key: str, **kwargs: object) -> str:
    table = _table(_current)
    text = table.get(key) or ""
    if not text and _current != "en":
        text = _table("en").get(key) or ""
    if not text:
        text = _table("uk").get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


class _Catalog:
    """Зворотна сумісність: тести й дамп можуть читати як словник мов."""

    def get(self, code: str, default: dict[str, str] | None = None) -> dict[str, str] | None:
        if code not in LANGS:
            return default
        return _table(code)

    def __getitem__(self, code: str) -> dict[str, str]:
        table = self.get(code)
        if table is None:
            raise KeyError(code)
        return table


CATALOG = _Catalog()
