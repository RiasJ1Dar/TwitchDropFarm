"""Авторизація через браузер: device code + автопідтвердження на twitch.tv/activate.

Принцип, покладений в основу: **автоматика — це найкраще зусилля, людина — запасний
варіант.** Twitch регулярно міняє розмітку, тому будь-який автоклік рано чи пізно
промахнеться. Замість того, щоб у цей момент падати, ми лишаємо вікно браузера
відкритим — користувач дотисне кнопку сам, а полінг токена цього навіть не помітить.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import TYPE_CHECKING

from auth.browser import Browser, find_browser
from auth.cdp import SET_REACT_INPUT, CDPSession
from auth.device import DeviceCode, poll_for_token, request_device_code
from core.events import LoginRequired
from core.exceptions import BrowserException, RequestInvalid

if TYPE_CHECKING:
    from core.miner import Miner as Twitch

logger = logging.getLogger("TwitchDrops")


# Розвідка сторінки. Повертає JSON, за яким Python вирішує, що робити далі.
# Свідомо не робимо один великий JS «зроби все» — так неможливо зрозуміти,
# на чому саме автоматика спіткнулась.
PROBE_PAGE = """
(function () {
  const norm = (s) => (s || '').trim().toLowerCase();
  const clickable = Array.from(
    document.querySelectorAll('button, [role="button"], input[type="submit"]')
  );
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const inputs = Array.from(document.querySelectorAll('input')).filter(visible);
  return JSON.stringify({
    url: location.href,
    needsLogin: inputs.some((i) => i.type === 'password'),
    codeInput: inputs.some((i) => i.type !== 'password' && i.type !== 'hidden'),
    codeFilled: inputs.some(
      (i) => i.type !== 'password' && i.type !== 'hidden' && i.value.trim().length >= 6
    ),
    buttons: clickable.filter(visible).map((b) => norm(b.textContent || b.value)),
    body: norm(document.body ? document.body.innerText.slice(0, 400) : '')
  });
})()
"""

# Слова кнопки підтвердження активації. Кілька мов — інтерфейс Twitch показується
# мовою акаунта, а не нашою.
#
# ВАЖЛИВО: сюди не можна додавати слова входу («увійти», «continue», «log in»,
# «продовжити»). Одного разу вони тут були, і автоматика натиснула «продовжити через
# google», а потім 18 разів «увійти» — тобто і обрала за користувача спосіб входу,
# і повелася рівно як бот. Клікаємо ТІЛЬКИ підтвердження активації коду.
CONFIRM_WORDS = (
    "activate", "authorize", "authorise", "confirm",
    "активувати", "авторизувати", "підтвердити",
    "активировать", "авторизовать", "подтвердить",
)

# Сторінка активації. Усе, що поза нею (форма входу Twitch, google, стороння 2FA),
# — територія людини, і автоматика туди не лізе.
ACTIVATE_HOSTS = ("twitch.tv",)
ACTIVATE_PATH = "/activate"
# запобіжник від довбання однієї кнопки, якщо клік не дає ефекту
MAX_CLICKS = 3

CLICK_CONFIRM = """
(function (words) {
  const norm = (s) => (s || '').trim().toLowerCase();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const clickable = Array.from(
    document.querySelectorAll('button, [role="button"], input[type="submit"]')
  ).filter(visible).filter((b) => !b.disabled);
  for (const word of words) {
    const hit = clickable.find((b) => norm(b.textContent || b.value).includes(word));
    if (hit) { hit.click(); return norm(hit.textContent || hit.value); }
  }
  return null;
})
"""


async def _probe(page: CDPSession) -> dict:
    raw = await page.evaluate(PROBE_PAGE)
    try:
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _on_activate_page(url: str) -> bool:
    """True, лише якщо ми справді на сторінці активації Twitch."""
    if not url:
        return False
    lowered = url.lower()
    if not any(f"://{host}" in lowered or f".{host}" in lowered
               for host in ACTIVATE_HOSTS):
        return False
    # відрізаємо схему й хост, лишаємо шлях
    tail = lowered.split("//", 1)[-1]
    path = tail.split("/", 1)[1] if "/" in tail else ""
    return path.split("?", 1)[0].strip("/").startswith(ACTIVATE_PATH.strip("/"))


async def _assist(twitch: Twitch, code: DeviceCode) -> None:
    """Веде користувача через сторінку активації. Живе, доки її не скасують.

    Межа відповідальності: автоматика працює **тільки** на сторінці активації і
    тільки з кодом. Вибір способу входу, пароль, 2FA — виключно людина.
    """
    executable = find_browser(twitch.settings.browser_path)
    async with Browser(executable) as browser:
        page = browser.page
        assert page is not None
        await page.navigate(str(code.verification_uri))
        await page.wait_ready()

        announced_login = False
        announced_manual = False
        clicks = 0

        while True:
            state = await _probe(page)
            url: str = state.get("url", "")

            # Не на сторінці активації або видно форму пароля — не чіпаємо нічого
            if not _on_activate_page(url) or state.get("needsLogin"):
                if not announced_login:
                    twitch.print(
                        "Увійди в акаунт Twitch у вікні браузера тим способом, яким "
                        "звик — далі я підставлю код сам."
                    )
                    announced_login = True
                clicks = 0
                await asyncio.sleep(2)
                continue

            announced_login = False

            if state.get("codeInput") and not state.get("codeFilled"):
                filled = await page.evaluate(
                    f"({SET_REACT_INPUT})('input:not([type=password]):not([type=hidden])',"
                    f" {json.dumps(code.user_code)})"
                )
                if filled:
                    logger.info(f"Код {code.user_code} підставлено у форму")
                    clicks = 0
                await asyncio.sleep(0.5)
                continue

            if clicks >= MAX_CLICKS:
                # Клікали, а стан не змінився — далі тільки заважатимемо
                if not announced_manual:
                    logger.info("Підтвердження не спрацювало, лишаю це користувачу")
                    twitch.print(f"Підтвердь код {code.user_code} у вікні браузера.")
                    announced_manual = True
                await asyncio.sleep(3)
                continue

            clicked = await page.evaluate(
                f"({CLICK_CONFIRM})({json.dumps(list(CONFIRM_WORDS))})"
            )
            if clicked:
                clicks += 1
                logger.info(f"Натиснуто кнопку «{clicked}» ({clicks}/{MAX_CLICKS})")
                await asyncio.sleep(2)
            else:
                if not announced_manual:
                    logger.info("Кнопку підтвердження не знайдено, чекаю на користувача")
                    twitch.print(f"Підтвердь код {code.user_code} у вікні браузера.")
                    announced_manual = True
                await asyncio.sleep(3)


async def device_login_with_browser(twitch: Twitch) -> str:
    """Повний цикл входу. Повертає access_token для Android-клієнта."""
    while True:
        code = await request_device_code(twitch)
        logger.info(f"Код пристрою: {code.user_code} → {code.verification_uri}")
        twitch.events.emit(
            LoginRequired(verification_uri=code.verification_uri, user_code=code.user_code)
        )

        assist_task = asyncio.create_task(_wrapped_assist(twitch, code))
        try:
            return await poll_for_token(twitch, code)
        except RequestInvalid:
            # код протух, поки користувач думав — беремо новий і починаємо спочатку
            logger.info("Код пристрою протух, запитую новий")
            continue
        finally:
            assist_task.cancel()
            with suppress(asyncio.CancelledError):
                await assist_task


async def _wrapped_assist(twitch: Twitch, code: DeviceCode) -> None:
    """Помилка браузера не має валити авторизацію — код завжди можна ввести вручну."""
    try:
        await _assist(twitch, code)
    except asyncio.CancelledError:
        raise
    except BrowserException as exc:
        logger.warning(f"Автоматика браузера не спрацювала: {exc}")
        twitch.print(
            f"Не вдалося відкрити браузер автоматично ({exc}). "
            f"Відкрий {code.verification_uri} вручну і введи код {code.user_code}."
        )
    except Exception:
        logger.exception("Несподівана помилка в помічнику браузера")
        twitch.print(
            f"Відкрий {code.verification_uri} вручну і введи код {code.user_code}."
        )
