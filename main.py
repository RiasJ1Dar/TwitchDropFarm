"""Точка входу майнера Twitch drops."""
from __future__ import annotations

from multiprocessing import freeze_support

if __name__ == "__main__":
    freeze_support()

    import argparse
    import asyncio
    import io
    import logging
    import os
    import sys
    import traceback

    # Консоль Windows за замовчуванням не в UTF-8, і весь український текст
    # перетворюється на кашу. Лагодимо до першого рядка логів.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if isinstance(stream, io.TextIOWrapper):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass

    from core.config import CONSOLE_LOG_FORMAT as OUTPUT_FORMATTER
    from core.config import FILE_LOG_FORMAT as FILE_FORMATTER
    from core.config import LOCK_FILE as LOCK_PATH
    from core.config import LOG_FILE as LOG_PATH
    from core.config import TRACE as CALL
    from core.config import VERBOSITY as LOGGING_LEVELS
    from core.config import VERSION as __version__
    from core.events import (
        CampaignFinished,
        Command,
        CommandType,
        ConnectionLost,
        ConnectionRestored,
        DropClaimed,
        DropProgress,
        Event,
        LoggedIn,
        LoginRequired,
        LogLine,
        MinerStarted,
        MinerStopped,
        ProgressStalled,
        StatusChanged,
        WatchingChanged,
    )
    from core.miner import Miner as Twitch
    from core.settings import Settings
    from core.toolbox import claim_single_instance as lock_file

    parser = argparse.ArgumentParser(
        "TwitchDropFarm",
        description="Фарм timed drops на Twitch з авторизацією через браузер.",
    )
    parser.add_argument("--version", action="version", version=f"v{__version__}")
    parser.add_argument("-v", dest="_verbose", action="count", default=2,
                        help="більше подробиць у логах (можна повторювати)")
    parser.add_argument("--log", action="store_true", help="писати log.txt")
    parser.add_argument("--tray", action="store_true", help="стартувати згорнутим у трей")
    parser.add_argument("--console", action="store_true",
                        help="без вікна: лише консоль (для сервера чи автозапуску)")
    parser.add_argument("--auth-only", action="store_true",
                        help="лише авторизуватись і вийти")
    parser.add_argument("--dump-inventory", action="store_true",
                        help="показати кампанії та дропи і вийти")
    parser.add_argument("--test-telegram", action="store_true",
                        help="надіслати тестове повідомлення в Telegram і вийти")
    # Прихований: сам себе перезапускає через N секунд. Потрібен, щоб перевіряти
    # шлях /reboot без участі людини — саме там ховалась проблема з теками _MEI.
    parser.add_argument("--debug-reboot", type=int, default=0,
                        help=argparse.SUPPRESS)
    parser.add_argument("--debug-ws", dest="_debug_ws", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--debug-gql", dest="_debug_gql", action="store_true",
                        help=argparse.SUPPRESS)

    class ParsedArgs(argparse.Namespace):
        _verbose: int
        _debug_ws: bool
        _debug_gql: bool

        @property
        def logging_level(self) -> int:
            return LOGGING_LEVELS[min(self._verbose, 4)]

        @property
        def debug_ws(self) -> int:
            if self._debug_ws:
                return logging.DEBUG
            return logging.INFO if self._verbose >= 4 else logging.NOTSET

        @property
        def debug_gql(self) -> int:
            if self._debug_gql:
                return logging.DEBUG
            return logging.INFO if self._verbose >= 4 else logging.NOTSET

    args = parser.parse_args(namespace=ParsedArgs())
    settings = Settings(args)

    logger = logging.getLogger("TwitchDrops")
    logger.setLevel(settings.logging_level)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(OUTPUT_FORMATTER)
    logger.addHandler(console)
    if settings.log:
        file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(FILE_FORMATTER)
        logger.addHandler(file_handler)
    logging.getLogger("TwitchDrops.gql").setLevel(settings.debug_gql)
    logging.getLogger("TwitchDrops.websocket").setLevel(settings.debug_ws)

    def log_reporter(event: Event) -> None:
        """Дублює важливі події в журнал.

        Без цього `--log` розповідав лише половину історії: стан, канал і прогрес
        ішли тільки в stdout, а він губиться при запуску без вікна. Саме через це
        журнал обривався на «Websocket підключено», хоча майнер працював далі.
        """
        if isinstance(event, StatusChanged):
            logger.log(CALL, f"Стан: {event.text}")
        elif isinstance(event, WatchingChanged):
            if event.channel is None:
                logger.info("Перестали дивитись")
            else:
                logger.info(f"Дивимось {event.channel.name} ({event.channel.game})")
        elif isinstance(event, DropProgress):
            logger.log(
                CALL,
                f"Дроп {event.drop_name} ({event.game}): "
                f"{event.current_minutes}/{event.required_minutes} хв"
            )
        elif isinstance(event, DropClaimed):
            logger.warning(f"ОТРИМАНО ДРОП: {event.rewards} — {event.game}")
        elif isinstance(event, CampaignFinished):
            logger.warning(f"КАМПАНІЮ ЗАВЕРШЕНО: {event.campaign_name} ({event.game})")
        elif isinstance(event, ProgressStalled):
            logger.error(
                f"Прогрес стоїть {event.minutes_without_progress} хв "
                f"на {event.channel_name}"
            )
        elif isinstance(event, MinerStopped):
            logger.warning(f"Майнер зупинено: {event.reason}")

    def console_reporter(event: Event) -> None:
        """Друкує події в консоль. Тимчасова заміна GUI, поки його немає."""
        if isinstance(event, LogLine):
            print(event.text)
        elif isinstance(event, StatusChanged):
            print(f"[стан] {event.text}")
        elif isinstance(event, LoginRequired):
            print(f"\n>>> Потрібна авторизація. Код: {event.user_code}")
            print(f">>> Сторінка: {event.verification_uri}\n")
        elif isinstance(event, LoggedIn):
            print(f"[вхід] Успішно, user ID {event.user_id}")
        elif isinstance(event, WatchingChanged):
            if event.channel is None:
                print("[канал] Нічого не дивимось")
            else:
                print(f"[канал] {event.channel.name} ({event.channel.game})")
        elif isinstance(event, DropProgress):
            print(
                f"[дроп] {event.drop_name} ({event.game}): "
                f"{event.current_minutes}/{event.required_minutes} хв"
            )
        elif isinstance(event, DropClaimed):
            print(f"[НАГОРОДА] {event.rewards} — {event.game}")
        elif isinstance(event, CampaignFinished):
            print(f"[ГОТОВО] Кампанію завершено: {event.campaign_name} ({event.game})")
        elif isinstance(event, ProgressStalled):
            print(
                f"[!] Прогрес стоїть {event.minutes_without_progress} хв на "
                f"{event.channel_name}. Можлива причина — цим акаунтом хтось "
                f"дивиться Twitch вручну."
            )
        elif isinstance(event, ConnectionLost):
            print(f"[!] Втрачено зв'язок: {event.reason}. Перепідключаюсь…")
        elif isinstance(event, ConnectionRestored):
            print(f"[+] Зв'язок відновлено за {round(event.downtime_seconds)}с")
        elif isinstance(event, MinerStopped):
            print(f"[стоп] {event.reason}")

    # разові режими завжди консольні — вікно там ні до чого
    one_shot = args.auth_only or args.dump_inventory or args.test_telegram
    use_gui = not (args.console or one_shot)

    async def main() -> int:
        client = Twitch(settings)
        client.events.subscribe(console_reporter)
        client.events.subscribe(log_reporter)

        gui = tray = None
        started_in_tray = False
        if use_gui:
            from gui.app import GUI
            from gui.tray import Tray
            gui = GUI(client)
            gui.start()
            tray = Tray(client, gui)
            tray.start()
            started_in_tray = bool(settings.start_in_tray or args.tray)
            if started_in_tray:
                gui.hide_to_tray()

        telegram = None
        if settings.telegram["enabled"] and settings.telegram["bot_token"]:
            from notify.telegram import TelegramNotifier
            telegram = TelegramNotifier(client)
            await telegram.start()

        # Після підписки Telegram, інакше повідомлення про старт нікуди не піде.
        # Для разових режимів не шлемо: --dump-inventory це не «запуск майнера».
        if not one_shot:
            client.events.emit(
                MinerStarted(version=__version__, tray=started_in_tray)
            )

        debug_reboot_task = None
        if args.debug_reboot:
            async def _debug_reboot() -> None:
                await asyncio.sleep(args.debug_reboot)
                logger.warning("debug-reboot: замовляю перезапуск")
                client.control.send(Command(CommandType.REBOOT))
            debug_reboot_task = asyncio.create_task(_debug_reboot())

        exit_status = 0
        try:
            if args.test_telegram:
                if telegram is None:
                    print("Telegram вимкнений або не налаштований у settings.json")
                    return 1
                await telegram.send_test()
                return 0

            if args.auth_only:
                await client.identity.ensure()
                auth_state = client.identity
                print(f"Авторизовано. user_id={auth_state.user_id}")
                return 0

            if args.dump_inventory:
                await client.identity.ensure()
                await client.load_inventory()
                for campaign in client.campaigns:
                    mark = "✔" if campaign.available_to_me else "✘"
                    print(
                        f"{mark} {campaign.game.name} — {campaign.name} "
                        f"[{campaign.taken_count}/{campaign.total}]"
                    )
                    for drop in campaign.all_drops:
                        print(
                            f"      {drop.name}: "
                            f"{drop.minutes}/{drop.required_minutes} хв"
                            f"{' (отримано)' if drop.taken else ''}"
                        )
                return 0

            await client.run()
        except KeyboardInterrupt:
            print("\nЗупинено користувачем")
        except Exception:
            exit_status = 1
            # Через логер, а не лише print: інакше з --log причина падіння нікуди
            # не потрапляє, і в журналі лишається обірваний хвіст без пояснення.
            logger.exception("Фатальна помилка")
            print("Фатальна помилка:\n")
            print(traceback.format_exc())
        finally:
            if telegram is not None:
                await telegram.stop()
            await client.shutdown()
            settings.save(force=True)
            if tray is not None:
                tray.stop()
            if debug_reboot_task is not None:
                debug_reboot_task.cancel()
            if gui is not None:
                gui.stop()
            reboot["wanted"] = client.reboot_requested
        return exit_status

    def relaunch() -> None:
        """Піднімає новий екземпляр програми з тими самими аргументами.

        Викликається ТІЛЬКИ після звільнення lock-файлу: інакше новий процес
        побачить чужий лок і одразу вийде з «Програма вже запущена».
        """
        import subprocess

        from core.config import FROZEN as IS_PACKAGED
        if IS_PACKAGED:
            command = [sys.executable, *sys.argv[1:]]
        else:
            command = [sys.executable, sys.argv[0], *sys.argv[1:]]

        # Найважливіший рядок цієї функції. У режимі одного файлу PyInstaller
        # розпаковує все (разом із бібліотеками Tk) у %TEMP%\_MEIxxxxx і передає
        # шлях через власні змінні середовища. Успадкувавши їх, новий процес
        # вважає розпаковку вже зробленою й бере теку СТАРОГО процесу — а той,
        # виходячи, її зносить. Результат: «DLL load failed while importing
        # _tkinter» рівно на /reboot. Чистимо, щоб новий екземпляр розпакувався сам.
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("_PYI") and key != "_MEIPASS2"
        }
        logger.info(f"Перезапуск: {' '.join(command)}")
        subprocess.Popen(command, close_fds=True, env=env)

    reboot = {"wanted": False}
    success, lock = lock_file(LOCK_PATH)
    if not success:
        print("Програма вже запущена.")
        sys.exit(3)
    try:
        status = asyncio.run(main())
    finally:
        lock.close()
    if reboot["wanted"]:
        relaunch()

    # Жорсткий вихід замість sys.exit. Трей (pystray) крутиться в недемонському
    # потоці виконавця, і звичайний вихід чекав би на нього — процес лишався б
    # живим. При /reboot це давало дві копії одночасно: нова піднімалась, стара
    # не вмирала. Усе своє ми вже прибрали вище, тож дочищати нема чого.
    logging.shutdown()
    os._exit(status)
