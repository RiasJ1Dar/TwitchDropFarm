"""Майстер підключення Telegram-бота.

Досі налаштування жило в `settings.json`: людина мала сама здобути токен,
сама дізнатись свій `chat_id` через `getUpdates` і сама не помилитись у JSON.
Кожен із трьох кроків мовчазний — помилившись, ти дізнаєшся про це тим, що
бот просто не відповідає.

Тому майстер веде за руку й після кожного кроку показує доказ: ім'я бота від
`getMe`, знайдений чат від `getUpdates`, справжнє повідомлення в телефоні.
Зберігаємо тільки те, що перевірене — інакше в налаштуваннях знову осідає
рядок, який ніхто не пробував.

Мережа тут асинхронна, як і скрізь: Tk крутиться в тій самій петлі asyncio
(`GUI._poll`), тож кнопка запускає задачу й одразу віддає керування, а не
морозить вікно на час запиту.
"""
from __future__ import annotations

import asyncio
import tkinter as tk
import webbrowser
from collections.abc import Awaitable, Callable
from tkinter import ttk
from typing import TYPE_CHECKING, Any

from core.i18n import t
from notify.telegram import check_token, find_chats, send_greeting

if TYPE_CHECKING:
    from core.settings import Settings

BOTFATHER_URL = "https://t.me/BotFather"

class TelegramSetup(tk.Toplevel):
    """Вікно майстра. Живе поверх головного, налаштування чіпає лише наприкінці."""

    def __init__(self, master: tk.Tk, settings: Settings) -> None:
        super().__init__(master)
        self._settings = settings
        self._token = ""
        self._username = ""
        self._chats: list[tuple[int, str]] = []
        self._busy = False

        self.title(t("wiz_title"))
        self.resizable(False, False)
        self.transient(master)

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        # ---- крок 1: токен
        step1 = ttk.LabelFrame(frame, text=t("wiz_step1"), padding=8)
        step1.pack(fill="x")
        ttk.Label(step1, text=t("wiz_hint_token"), justify="left").pack(anchor="w")
        ttk.Button(
            step1, text=t("wiz_open_father"),
            command=lambda: webbrowser.open(BOTFATHER_URL),
        ).pack(anchor="w", pady=(6, 0))

        row = ttk.Frame(step1)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=t("wiz_token")).pack(side="left")
        self.token_var = tk.StringVar(value=settings.telegram["bot_token"])
        ttk.Entry(row, textvariable=self.token_var, width=38).pack(
            side="left", fill="x", expand=True, padx=(6, 6)
        )
        self.check_button = ttk.Button(row, text=t("wiz_check"), command=self._check)
        self.check_button.pack(side="left")

        self.token_status = ttk.Label(step1, text="", wraplength=430, justify="left")
        self.token_status.pack(anchor="w", pady=(6, 0))

        # ---- крок 2: чат
        self.step2 = ttk.LabelFrame(frame, text=t("wiz_step2"), padding=8)
        self.step2.pack(fill="x", pady=(10, 0))
        ttk.Label(self.step2, text=t("wiz_hint_chat"), justify="left").pack(anchor="w")

        row2 = ttk.Frame(self.step2)
        row2.pack(fill="x", pady=(6, 0))
        self.open_bot_button = ttk.Button(
            row2, text=t("wiz_open_bot"), command=self._open_bot
        )
        self.open_bot_button.pack(side="left")
        self.find_button = ttk.Button(row2, text=t("wiz_find"), command=self._find)
        self.find_button.pack(side="left", padx=(6, 0))

        self.chat_box = ttk.Combobox(self.step2, state="readonly", width=44)
        self.chat_box.pack(anchor="w", pady=(6, 0))
        self.chat_status = ttk.Label(self.step2, text="", wraplength=430, justify="left")
        self.chat_status.pack(anchor="w", pady=(6, 0))

        # ---- крок 3: перевірка
        self.step3 = ttk.LabelFrame(frame, text=t("wiz_step3"), padding=8)
        self.step3.pack(fill="x", pady=(10, 0))
        self.test_button = ttk.Button(
            self.step3, text=t("wiz_send_test"), command=self._test
        )
        self.test_button.pack(anchor="w")
        self.test_status = ttk.Label(self.step3, text="", wraplength=430, justify="left")
        self.test_status.pack(anchor="w", pady=(6, 0))

        # ---- підсумок
        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(12, 0))
        self.save_button = ttk.Button(
            bottom, text=t("wiz_save"), command=self._save
        )
        self.save_button.pack(side="right")
        ttk.Button(bottom, text=t("wiz_cancel"), command=self.destroy).pack(
            side="right", padx=(0, 6)
        )
        self.final_status = ttk.Label(frame, text="", wraplength=430, justify="left")
        self.final_status.pack(anchor="w", pady=(8, 0))

        self._refresh_buttons()
        # Токен із налаштувань перевіряємо самі: якщо він там уже є, людина
        # прийшла щось лагодити, і перший крок їй перегортати ні до чого.
        if self.token_var.get().strip():
            self._check()

    # ------------------------------------------------------------ службове

    def _refresh_buttons(self) -> None:
        """Крок стає доступним лише коли попередній дав доказ."""
        has_token = bool(self._username)
        has_chat = bool(self.chat_box.get())
        state = "disabled" if self._busy else "normal"
        self.check_button["state"] = state
        self.open_bot_button["state"] = state if has_token else "disabled"
        self.find_button["state"] = state if has_token else "disabled"
        self.test_button["state"] = state if has_token and has_chat else "disabled"
        self.save_button["state"] = state if has_token and has_chat else "disabled"

    def _run(self, coro: Awaitable[Any], done: Callable[[Any], None]) -> None:
        """Пускає мережевий крок, не морозячи вікно."""
        self._busy = True
        self._refresh_buttons()

        def finished(task: asyncio.Task[Any]) -> None:
            self._busy = False
            try:
                result = task.result()
            except Exception as exc:  # мережа може впасти будь-де
                self._refresh_buttons()
                self.final_status["text"] = t(
                    "wiz_fail", error=f"{type(exc).__name__}: {exc}")
                return
            done(result)
            self._refresh_buttons()

        asyncio.ensure_future(coro).add_done_callback(finished)

    def _chosen_chat_id(self) -> int | None:
        label = self.chat_box.get()
        for chat_id, name in self._chats:
            if self._chat_label(chat_id, name) == label:
                return chat_id
        return None

    @staticmethod
    def _chat_label(chat_id: int, name: str) -> str:
        return f"{name}  (id {chat_id})"

    # ------------------------------------------------------------ кроки

    def _check(self) -> None:
        token = self.token_var.get().strip()
        self.token_status["text"] = t("wiz_asking")

        def done(result: tuple[str, str]) -> None:
            username, error = result
            self._username = username
            self._token = token if username else ""
            if error:
                self.token_status["text"] = f"✖ {error}"
            else:
                self.token_status["text"] = t("wiz_bot_ok", username=username)

        self._run(check_token(token), done)

    def _open_bot(self) -> None:
        if self._username:
            webbrowser.open(f"https://t.me/{self._username}")

    def _find(self) -> None:
        self.chat_status["text"] = t("wiz_searching")

        def done(result: tuple[list[tuple[int, str]], str]) -> None:
            chats, error = result
            self._chats = chats
            if error:
                self.chat_status["text"] = f"✖ {error}"
                return
            if not chats:
                # Найчастіший глухий кут: людина натискає «знайти» до того, як
                # написала боту. Кажемо прямо, що робити, а не «нічого немає».
                self.chat_status["text"] = t("wiz_nobody")
                self.chat_box["values"] = []
                self.chat_box.set("")
                return
            labels = [self._chat_label(chat_id, name) for chat_id, name in chats]
            self.chat_box["values"] = labels
            self.chat_box.set(labels[0])
            self.chat_status["text"] = (
                t("wiz_found_many", n=len(chats))
                if len(chats) > 1 else t("wiz_found_one", label=labels[0])
            )

        self._run(find_chats(self._token or self.token_var.get().strip()), done)

    def _test(self) -> None:
        chat_id = self._chosen_chat_id()
        if chat_id is None:
            return
        self.test_status["text"] = t("wiz_sending")

        def done(error: str) -> None:
            self.test_status["text"] = (
                f"✖ {error}" if error else t("wiz_sent")
            )

        self._run(send_greeting(self._token, chat_id), done)

    def _save(self) -> None:
        chat_id = self._chosen_chat_id()
        if not self._token or chat_id is None:
            return
        telegram = self._settings.telegram
        telegram["bot_token"] = self._token
        telegram["chat_ids"] = [chat_id]
        telegram["enabled"] = True
        self._settings.touch()
        self._settings.save()
        self.final_status["text"] = t("wiz_saved")
        self.save_button["state"] = "disabled"
