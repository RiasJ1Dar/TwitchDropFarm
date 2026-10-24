"""Регресії ядра без мережі; --group запускає одну незалежну групу."""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.toolbox import force_utf8_console
from tests.core_cases import support

GROUPS = {
    "progress": [
        "stall_checks",
        "farm_indicator_checks",
        "growing_checks",
        "parallel_watch_checks",
    ],
    "completion": ["claim_checks", "completed_drop_checks", "claim_retry_checks"],
    "campaigns": ["deadline_checks", "watchlist_checks", "model_cache_checks", "selection_checks"],
    "selection": ["pure_selection_checks"],
    "inventory": ["inventory_checks"],
    "channels": ["channel_checks"],
    "session": ["session_checks"],
    "client": ["client_checks"],
    "protocol": ["protocol_watch_checks", "stale_request_checks", "request_limit_checks"],
    "delivery": ["delivery_checks"],
    "updates": ["update_checks"],
    "storage": [
        "lock_and_wording_checks",
        "log_rotation_checks",
        "history_checks",
        "autostart_checks",
        "seen_checks",
        "identity_checks",
        "settings_method_checks",
        "identity_storage_checks",
    ],
    "gui": [
        "i18n_checks",
        "window_checks",
        "telegram_setup_checks",
        "tray_checks",
        "image_cache_checks",
        "celebrate_checks",
        "theme_checks",
    ],
    "events": ["routing_checks"],
}


def run_groups(selected: list[str]) -> int:
    if not selected:
        raise ValueError("Не вибрано жодної групи перевірок")
    support.ok = support.fail = 0
    for name in selected:
        module = importlib.import_module(f"tests.core_cases.{name}")
        before = support.ok + support.fail
        for check_name in GROUPS[name]:
            getattr(module, check_name)()
        if support.ok + support.fail == before:
            raise RuntimeError(f"Група {name} не виконала жодної перевірки")
    print("\n" + "=" * 50)
    print(f"Пройдено: {support.ok}   Провалено: {support.fail}")
    return 1 if support.fail else 0


def main() -> int:
    force_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=GROUPS, help="Запустити одну групу в окремому процесі")
    args = parser.parse_args()
    logging.getLogger("TwitchDrops").setLevel(logging.CRITICAL)
    return run_groups([args.group] if args.group else list(GROUPS))


if __name__ == "__main__":
    sys.exit(main())
