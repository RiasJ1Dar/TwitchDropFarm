"""Налаштування користувача.

Аргументи командного рядка мають перевагу над файлом: разовий запуск можна
змінити, не чіпаючи збережену конфігурацію.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from core.config import CONFIG_FILE, DEFAULT_IMAGE_SIZE, FarmMode
from core.toolbox import load_json, register_enum, save_json

if TYPE_CHECKING:
    from argparse import Namespace

register_enum(FarmMode)


class TelegramConfig(TypedDict):
    enabled: bool
    bot_token: str
    # білий список; усе, що прийшло не звідси, ігнорується
    chat_ids: list[int]
    notify_critical: bool
    notify_rewards: bool
    notify_routine: bool
    report_every_hours: int
    allow_control: bool
    # VERSION, коли востаннє ставили аватар. Порожньо — ще не ставили.
    photo_version: str


class ConfigShape(TypedDict):
    farm_mode: FarmMode
    priority: list[str]
    exclude: list[str]
    # Ігри, про нові кампанії яких хочемо знати, навіть коли фармиться інше.
    # Порожньо — не сповіщати: програма мовчазна, поки її не попросили говорити.
    watch_games: list[str]
    dark_theme: bool
    start_in_tray: bool
    tray_notifications: bool
    farm_cosmetics: bool
    verify_channel_drops: bool
    drop_images: bool
    check_updates: bool
    image_size: int
    inventory_view: str
    browser_path: str
    proxy: str
    telegram: TelegramConfig


DEFAULT_TELEGRAM: TelegramConfig = {
    "enabled": False,
    "bot_token": "",
    "chat_ids": [],
    "notify_critical": True,
    "notify_rewards": True,
    # рутина вимкнена: інакше це десятки повідомлень на день
    "notify_routine": False,
    "report_every_hours": 6,
    "allow_control": True,
    "photo_version": "",
}

DEFAULTS: ConfigShape = {
    # Не PRIORITY_LIST: при порожньому списку той режим означає «не фармити
    # нічого», і свіжовстановлена програма мовчки простоює. Стандартна
    # поведінка має бути корисною.
    "farm_mode": FarmMode.SOONEST_END,
    "priority": [],
    "exclude": [],
    "watch_games": [],
    "dark_theme": True,
    "start_in_tray": False,
    "tray_notifications": True,
    "farm_cosmetics": False,
    "verify_channel_drops": False,
    # Типово вимкнено: програма мовчазна за задумом, і ходити в мережу заради
    # картинок має лише тоді, коли про це попросили.
    "drop_images": False,
    # Раз на запуск питати GitHub, чи є новіші файли (за SHA-256).
    "check_updates": True,
    # Розмір картинки в списку. Кеш на диску завжди більший, тож це число
    # можна міняти будь-коли — качати наново нічого не доведеться.
    "image_size": DEFAULT_IMAGE_SIZE,
    # "list" або "tiles". Список щільний і показує все відразу; плитки
    # показують саме нагороду — заради них картинки й завантажуються.
    "inventory_view": "list",
    "browser_path": "",
    "proxy": "",
    "telegram": DEFAULT_TELEGRAM,
}


class Settings:
    """Доступ до налаштувань як до атрибутів, зі збереженням на диск."""

    _INTERNAL = frozenset({"_stored", "_args", "_dirty"})

    def __init__(self, args: Namespace | None = None):
        object.__setattr__(self, "_stored", load_json(CONFIG_FILE, DEFAULTS))
        object.__setattr__(self, "_args", args)
        object.__setattr__(self, "_dirty", False)

    def __getattr__(self, name: str) -> Any:
        args = object.__getattribute__(self, "_args")
        if args is not None and hasattr(args, name):
            return getattr(args, name)
        stored = object.__getattribute__(self, "_stored")
        if name in stored:
            return stored[name]
        raise AttributeError(f"Немає налаштування «{name}»")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._INTERNAL:
            object.__setattr__(self, name, value)
            return
        stored = object.__getattribute__(self, "_stored")
        if name not in stored:
            raise AttributeError(f"Немає налаштування «{name}»")
        if stored[name] != value:
            stored[name] = value
            object.__setattr__(self, "_dirty", True)

    def touch(self) -> None:
        """Позначити зміненим — для вкладених структур на кшталт telegram."""
        object.__setattr__(self, "_dirty", True)

    def save(self, *, force: bool = False) -> None:
        if self._dirty or force:
            save_json(CONFIG_FILE, self._stored)
            object.__setattr__(self, "_dirty", False)

    @property
    def telegram_config(self) -> TelegramConfig:
        return self._stored["telegram"]
