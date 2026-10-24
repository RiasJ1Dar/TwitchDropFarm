"""Публічний стан ядра та чистий розбір Telegram-команд."""
from core.i18n import NAMES, set_language, t
from notify.telegram_commands import ParsedCommand, parse_command
from notify.telegram_views import button_commands, render_status
from tests.core_cases.support import check, miner


def client_checks() -> None:
    check("команда у групі відкидає botname і знижує регістр",
          parse_command("/STATUS@DropFarm_bot", {}) == ParsedCommand("status", ""))
    check("аргумент з пробілами збережено",
          parse_command("/priority   World of Tanks  ", {})
          == ParsedCommand("priority", "World of Tanks"))
    check("звичайний текст не стає командою", parse_command("status", {}) is None)
    check("невідома команда доходить до чинного повідомлення",
          parse_command("/unknown arg", {}) == ParsedCommand("unknown", "arg"))
    check("порожній аргумент не вигадується",
          parse_command("/switch   ", {}) == ParsedCommand("switch", ""))
    try:
        for language in NAMES:
            set_language(language)
            buttons = button_commands()
            check("кнопки всіх команд збережено: " + language,
                  bool(buttons) and all(parse_command("  " + label + "  ", buttons)
                                       == ParsedCommand(command, "")
                                       for label, command in buttons.items()))
    finally:
        set_language("uk")
    twitch = miner()
    check("публічна пауза читає канонічний стан", twitch.paused is False)
    twitch._paused = True
    check("публічна пауза не має незалежної копії", twitch.paused is True)
    check("публічний поточний канал читає власника сесії", twitch.current_channel is None)
    check("статус Telegram читає публічну паузу", t("tg_status_paused") in render_status(twitch))
