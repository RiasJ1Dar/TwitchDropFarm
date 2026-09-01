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
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from core.i18n import t
from notify.telegram import check_token, find_chats, send_greeting

if TYPE_CHECKING:
    from core.settings import Settings

BOTFATHER_URL = "https://t.me/BotFather"

class TelegramSetup(ctk.CTkToplevel):
    """Вікно майстра. Живе поверх головного, налаштування чіпає лише наприкінці.

    Палітру отримує ззовні: імпортувати її з `gui.app` не можна — той сам
    імпортує майстра, і вийшло б коло.
    """

    def __init__(self, master: tk.Tk, settings: Settings,
                 palette: dict[str, str], cards: dict[str, str]) -> None:
        super().__init__(master, fg_color=cards["page"])
        self._settings = settings
        self._palette = palette
        self._cards = cards
        self._token = ""
        self._username = ""
        self._chats: list[tuple[int, str]] = []
        self._busy = False

        self.title(t("wiz_title"))
        self.resizable(False, False)
        self.transient(master)

        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=14, pady=14)

        # ---- крок 1: токен
        step1 = self._step(frame, t("wiz_step1"))
        self._note(step1, t("wiz_hint_token")).pack(anchor="w", fill="x")
        self._act(step1, t("wiz_open_father"),
                  lambda: webbrowser.open(BOTFATHER_URL)).pack(
            anchor="w", pady=(8, 0))

        row = ctk.CTkFrame(step1, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(row, text=t("wiz_token"),
                     text_color=palette["fg"]).pack(side="left")
        self.token_var = tk.StringVar(value=settings.telegram["bot_token"])
        ctk.CTkEntry(row, textvariable=self.token_var, corner_radius=8,
                     fg_color=cards["card"], border_color=cards["line"],
                     text_color=palette["fg"]).pack(
            side="left", fill="x", expand=True, padx=(8, 8))
        self.check_button = self._act(row, t("wiz_check"), self._check)
        self.check_button.pack(side="left")

        self.token_status = self._note(step1, "")
        self.token_status.pack(anchor="w", fill="x", pady=(8, 0))

        # ---- крок 2: чат
        self.step2 = self._step(frame, t("wiz_step2"), top=12)
        self._note(self.step2, t("wiz_hint_chat")).pack(anchor="w", fill="x")

        row2 = ctk.CTkFrame(self.step2, fg_color="transparent")
        row2.pack(fill="x", pady=(8, 0))
        self.open_bot_button = self._act(row2, t("wiz_open_bot"), self._open_bot)
        self.open_bot_button.pack(side="left")
        self.find_button = self._act(row2, t("wiz_find"), self._find)
        self.find_button.pack(side="left", padx=(8, 0))

        # ⚠️ `CTkOptionMenu` не має порожнього стану: із порожнім `values`
        # він показує голу плашку. Тому поки чатів немає, кладемо туди
        # підказку, а справжній список живе в `self._chats`.
        self.chat_box = ctk.CTkOptionMenu(
            self.step2, values=[t("wiz_no_chats")], corner_radius=8,
            fg_color=cards["card"], button_color=palette["accent"],
            button_hover_color=palette["accent"], text_color=palette["fg"],
        )
        self.chat_box.pack(anchor="w", fill="x", pady=(8, 0))
        self.chat_status = self._note(self.step2, "")
        self.chat_status.pack(anchor="w", fill="x", pady=(8, 0))

        # ---- крок 3: перевірка
        self.step3 = self._step(frame, t("wiz_step3"), top=12)
        self.test_button = self._act(self.step3, t("wiz_send_test"), self._test)
        self.test_button.pack(anchor="w")
        self.test_status = self._note(self.step3, "")
        self.test_status.pack(anchor="w", fill="x", pady=(8, 0))

        # ---- підсумок
        bottom = ctk.CTkFrame(frame, fg_color="transparent")
        bottom.pack(fill="x", pady=(14, 0))
        self.save_button = self._act(bottom, t("wiz_save"), self._save, accent=True)
        self.save_button.pack(side="right")
        self._act(bottom, t("wiz_cancel"), self.destroy).pack(
            side="right", padx=(0, 8))
        self.final_status = self._note(frame, "")
        self.final_status.pack(anchor="w", fill="x", pady=(10, 0))

        self._refresh_buttons()
        # Токен із налаштувань перевіряємо самі: якщо він там уже є, людина
        # прийшла щось лагодити, і перший крок їй перегортати ні до чого.
        if self.token_var.get().strip():
            self._check()

    # ------------------------------------------------------------ вигляд

    def _step(self, parent: tk.Misc, title: str, *, top: int = 0) -> ctk.CTkFrame:
        """Крок майстра — заокруглена картка з підписом над нею."""
        ctk.CTkLabel(parent, text=title, anchor="w",
                     text_color=self._palette["muted"]).pack(
            anchor="w", padx=4, pady=(top, 4))
        card = ctk.CTkFrame(parent, corner_radius=12, fg_color=self._cards["card"],
                            border_width=1, border_color=self._cards["line"])
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=12)
        return inner

    def _note(self, parent: tk.Misc, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text=text, anchor="w", justify="left",
                            wraplength=430, text_color=self._palette["muted"])

    def _act(self, parent: tk.Misc, text: str, command: Any,
             *, accent: bool = False) -> ctk.CTkButton:
        p, c = self._palette, self._cards
        return ctk.CTkButton(
            parent, text=text, command=command, corner_radius=8, height=32,
            font=("Segoe UI", 12, "bold" if accent else "normal"),
            fg_color=p["accent"] if accent else c["card"],
            hover_color=p["accent"] if accent else c["hover"],
            text_color="#ffffff" if accent else p["fg"],
            border_width=0 if accent else 1, border_color=c["line"],
        )

    # ------------------------------------------------------------ службове

    def _refresh_buttons(self) -> None:
        """Крок стає доступним лише коли попередній дав доказ."""
        has_token = bool(self._username)
        # підказка в списку — це не обраний чат
        has_chat = self.chat_box.get() not in ("", t("wiz_no_chats"))
        state = "disabled" if self._busy else "normal"
        self.check_button.configure(state=state)
        self.open_bot_button.configure(state=state if has_token else "disabled")
        self.find_button.configure(state=state if has_token else "disabled")
        self.test_button.configure(state=state if has_token and has_chat else "disabled")
        self.save_button.configure(state=state if has_token and has_chat else "disabled")

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
                self.final_status.configure(text=t(
                    "wiz_fail", error=f"{type(exc).__name__}: {exc}"))
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
        self.token_status.configure(text=t("wiz_asking"))

        def done(result: tuple[str, str]) -> None:
            username, error = result
            self._username = username
            self._token = token if username else ""
            if error:
                self.token_status.configure(text=f"✖ {error}")
            else:
                self.token_status.configure(text=t("wiz_bot_ok", username=username))

        self._run(check_token(token), done)

    def _open_bot(self) -> None:
        if self._username:
            webbrowser.open(f"https://t.me/{self._username}")

    def _find(self) -> None:
        self.chat_status.configure(text=t("wiz_searching"))

        def done(result: tuple[list[tuple[int, str]], str]) -> None:
            chats, error = result
            self._chats = chats
            if error:
                self.chat_status.configure(text=f"✖ {error}")
                return
            if not chats:
                # Найчастіший глухий кут: людина натискає «знайти» до того, як
                # написала боту. Кажемо прямо, що робити, а не «нічого немає».
                self.chat_status.configure(text=t("wiz_nobody"))
                # не порожній список, а підказка: `CTkOptionMenu` із порожнім
                # `values` показує голу плашку, з якої не зрозуміло нічого
                self.chat_box.configure(values=[t("wiz_no_chats")])
                self.chat_box.set(t("wiz_no_chats"))
                return
            labels = [self._chat_label(chat_id, name) for chat_id, name in chats]
            self.chat_box.configure(values=labels)
            self.chat_box.set(labels[0])
            self.chat_status.configure(text=(
                t("wiz_found_many", n=len(chats))
                if len(chats) > 1 else t("wiz_found_one", label=labels[0])
            ))

        self._run(find_chats(self._token or self.token_var.get().strip()), done)

    def _test(self) -> None:
        chat_id = self._chosen_chat_id()
        if chat_id is None:
            return
        self.test_status.configure(text=t("wiz_sending"))

        def done(error: str) -> None:
            self.test_status.configure(text=(
                f"✖ {error}" if error else t("wiz_sent")
            ))

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
        self.final_status.configure(text=t("wiz_saved"))
        self.save_button.configure(state="disabled")
