"""Ієрархія винятків майнера.

Поділ навмисно дрібний: на конкретні класи спирається логіка повторних спроб у
`core.twitch`, яка по-різному реагує на обрив мережі, помилку GQL і протухлий запит.
"""
from __future__ import annotations


class MinerException(Exception):
    """Базовий клас винятків застосунку."""

    def __init__(self, *args: object):
        super().__init__(*(args or ("Невідома помилка майнера",)))


class ExitRequest(MinerException):
    """Застосунок попросили завершитись ззовні головного циклу."""

    def __init__(self):
        super().__init__("Запитано завершення роботи")


class ReloadRequest(MinerException):
    """Застосунок попросили перезапустити ядро, не закриваючи вікно."""

    def __init__(self):
        super().__init__("Запитано повний перезапуск")


class RequestException(MinerException):
    """Веб-запит повернув не те, чого ми очікували."""

    def __init__(self, *args: object):
        super().__init__(*(args or ("Невідома помилка запиту",)))


class RequestInvalid(RequestException):
    """Запит став неактуальним усередині циклу повторів (напр. код пристрою протух)."""

    def __init__(self):
        super().__init__("Запит став неактуальним під час повторів")


class WebsocketClosed(RequestException):
    """З'єднання websocket закрито.

    `received` — True, якщо закриття ініціювала протилежна сторона.
    """

    def __init__(self, *args: object, received: bool = False):
        super().__init__(*(args or ("Websocket закрито",)))
        self.received: bool = received


class LoginException(RequestException):
    """Помилка на етапі авторизації."""

    def __init__(self, *args: object):
        super().__init__(*(args or ("Невідома помилка авторизації",)))


class CaptchaRequired(LoginException):
    def __init__(self):
        super().__init__("Потрібна CAPTCHA")


class GQLException(RequestException):
    """GQL-запит повернув відповідь з помилкою."""


class BrowserException(MinerException):
    """Не вдалося запустити системний браузер або поговорити з ним по CDP."""

    def __init__(self, *args: object):
        super().__init__(*(args or ("Помилка керування браузером",)))
