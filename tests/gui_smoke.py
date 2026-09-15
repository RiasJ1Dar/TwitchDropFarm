"""Жива димова перевірка Tkinter-інтерфейсу (gui/app.py).

Перевіряє справжнє вікно Tk, створення всіх віджетів, перемикання тем,
обидва вигляди інвентаря та обробку всіх 21 типу подій шини через SHOW.

Відсутність графічного дисплея граційно пропускає набір (код 0).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

# Додаємо корінь проєкту до шляхів пошуку
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.events import (
    AccountLinkLost,
    AccountLinkNeeded,
    CampaignAppeared,
    CampaignSnapshot,
    ChannelSnapshot,
    ChannelsUpdated,
    ConnectionLost,
    ConnectionRestored,
    ControlBus,
    DropClaimed,
    DropProgress,
    DropSnapshot,
    EventBus,
    InventoryUpdated,
    LinkHint,
    LoggedIn,
    LoginRequired,
    LogLine,
    ProgressStalled,
    ProtocolStale,
    StatusChanged,
    UpdateAvailable,
    UpdateFailed,
    WatchingChanged,
    WatchUncounted,
    WebsocketStatus,
    WindowVisibility,
)
from core.i18n import set_language, t
from core.miner import Miner
from core.settings import Settings
from core.toolbox import force_utf8_console
from gui.app import GUI, SHOW
from gui.theme import PRESETS

ok = 0
fail = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global ok, fail
    if condition:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}  {detail}")


class FakeHistory:
    def entries(self) -> list[Any]:
        return []


class SmokeSettings(Settings):
    """Налаштування для димової перевірки: не перезаписують файл на диску."""

    def save(self, *, force: bool = False) -> None:
        pass


class FakeMiner:
    """Мінімальний майнер для GUI зі справжнім об'єктом Settings."""

    def __init__(self) -> None:
        self.settings = SmokeSettings()
        self.events = EventBus()
        self.control = ControlBus()
        self._paused = False
        self.active_campaign = None
        self.campaigns: list[Any] = []
        self.history = FakeHistory()
        self.images = None

    def request_stop(self) -> None:
        pass


def run_smoke_tests() -> int:
    global ok, fail
    force_utf8_console()
    set_language("uk")

    print("[1] Перевірка доступності Tkinter")
    try:
        import tkinter as tk
        probe = tk.Tk()
        probe.destroy()
    except Exception as error:
        print(f"  --   Tk недоступний у цьому оточенні, пропускаю: {error}")
        return 0

    fake = FakeMiner()
    gui = None

    try:
        print("\n[2] Побудова повного GUI")
        gui = GUI(cast(Miner, fake))
        gui.root.withdraw()
        gui.root.update()
        check("вікно GUI успішно створене", gui.root is not None)

        print("\n[3] Звірка обов'язкових полів GUI (__annotations__)")
        annotations = getattr(GUI, "__annotations__", {})
        check(
            "GUI має оголошені анотації",
            len(annotations) > 0,
            f"кількість: {len(annotations)}",
        )
        for name in annotations:
            has_attr = hasattr(gui, name) and getattr(gui, name) is not None
            check(f"поле {name} створено", has_attr, f"hasattr={hasattr(gui, name)}")

        print("\n[4] Відображення інвентаря (список і плитки)")
        now = datetime.now(timezone.utc)
        drop1 = DropSnapshot(
            id="d1",
            name="Alpha Drop",
            current_minutes=15,
            required_minutes=30,
            claimed=False,
            can_claim=False,
            image="",
        )
        drop2 = DropSnapshot(
            id="d2",
            name="Beta Reward",
            current_minutes=60,
            required_minutes=60,
            claimed=True,
            can_claim=False,
            image="",
        )
        camp1 = CampaignSnapshot(
            id="c1",
            name="Active Campaign",
            game="Awesome Game",
            active=True,
            upcoming=False,
            expired=False,
            ends_at=now + timedelta(hours=5),
            claimed_drops=1,
            total_drops=2,
            drops=(drop1, drop2),
            image="",
            linked=True,
        )
        camp_exp = CampaignSnapshot(
            id="c2",
            name="Expired Campaign",
            game="Old Game",
            active=False,
            upcoming=False,
            expired=True,
            ends_at=now - timedelta(hours=1),
            claimed_drops=0,
            total_drops=1,
            drops=(drop1,),
            image="",
            linked=False,
        )
        inv_event = InventoryUpdated(campaigns=(camp1, camp_exp))

        # Вигляд «список»
        gui.view_var.set("list")
        gui._view_changed()
        gui._render_inventory(inv_event)
        gui.root.update()
        tree_children = gui.inv_tree.get_children()
        check(
            "вигляд інвентаря 'список' показує кампанії",
            len(tree_children) > 0,
            f"елементів: {len(tree_children)}",
        )

        # Вигляд «плитки»
        gui.view_var.set("tiles")
        gui._view_changed()
        gui._render_inventory(inv_event)
        gui.root.update()
        tiles_count = len(gui.tiles_holder.winfo_children())
        check(
            "вигляд інвентаря 'плитки' показує картки",
            tiles_count > 0,
            f"карток: {tiles_count}",
        )

        print("\n[5] Перемикання тем і пресетів кольорів")
        # Перемикання світла / темна тема
        gui.dark_var.set(False)
        gui._misc_changed()
        gui.root.update()
        check("світла тема застосована без помилок", not gui._twitch.settings.dark_theme)

        gui.dark_var.set(True)
        gui._misc_changed()
        gui.root.update()
        check("темна тема застосована без помилок", gui._twitch.settings.dark_theme)

        # Перебір усіх кольорових пресетів
        for name in PRESETS:
            key = name or "builtin"
            label = t(f"theme_{key}")
            gui.preset_var.set(label)
            gui._preset_changed()
            gui.root.update()
            check(
                f"пресет оформлення '{key}' застосовано",
                gui._twitch.settings.theme_preset == (name or ""),
            )

        print("\n[6] Прогін усіх 21 подій шини через SHOW")
        ch_snap = ChannelSnapshot(
            id=101,
            name="test_streamer",
            game="Test Game",
            viewers=777,
            online=True,
            drops_enabled=True,
            acl_based=False,
            stream_title="Exciting Stream",
        )

        sample_events = {
            WindowVisibility: WindowVisibility(visible=False),
            StatusChanged: StatusChanged(text="Тестовий стан майнера"),
            LogLine: LogLine(text="Тестове повідомлення в журнал"),
            LoginRequired: LoginRequired(
                verification_uri="https://twitch.tv/activate", user_code="TEST-1234"
            ),
            LoggedIn: LoggedIn(user_id=987654321),
            WatchingChanged: WatchingChanged(channel=ch_snap),
            DropProgress: DropProgress(
                drop_name="Alpha Drop",
                game="Test Game",
                current_minutes=20,
                required_minutes=30,
                campaign="Active Campaign",
            ),
            DropClaimed: DropClaimed(
                drop_name="Alpha Drop", game="Test Game", rewards="Super Skin"
            ),
            ProtocolStale: ProtocolStale(operations=("TestQuery",), storm=False),
            CampaignAppeared: CampaignAppeared(campaigns=(camp1,)),
            UpdateAvailable: UpdateAvailable(version="2.0.0", files=2, bytes_to_fetch=65536),
            AccountLinkLost: AccountLinkLost(campaigns=("Active Campaign",), minutes_lost=20),
            AccountLinkNeeded: AccountLinkNeeded(
                campaigns=(
                    LinkHint(
                        name="Active Campaign",
                        game="Test Game",
                        url="https://twitch.tv",
                        drops=1,
                    ),
                )
            ),
            UpdateFailed: UpdateFailed(reason="Тестова помилка оновлення"),
            ProgressStalled: ProgressStalled(
                minutes_without_progress=5,
                channel_name="test_streamer",
                counted_elsewhere="",
            ),
            WatchUncounted: WatchUncounted(channel_name="test_streamer", consecutive=1),
            ConnectionLost: ConnectionLost(reason="Зв'язок втрачено", attempt=1),
            ConnectionRestored: ConnectionRestored(downtime_seconds=12.5, attempts=1),
            WebsocketStatus: WebsocketStatus(index=0, status="connected", topics=4),
            ChannelsUpdated: ChannelsUpdated(channels=(ch_snap,)),
            InventoryUpdated: InventoryUpdated(campaigns=(camp1,)),
        }

        # Звірка, що всі маршрути SHOW протестовано
        routes_in_show = set(SHOW._routes.keys())
        tested_routes = set(sample_events.keys())
        check(
            "усі маршрути SHOW покриті тестовими подіями",
            routes_in_show == tested_routes,
            f"різниця: {routes_in_show ^ tested_routes}",
        )

        for ev_cls, ev in sample_events.items():
            fake.events.emit(ev)
            gui.root.update()
            check(f"подію {ev_cls.__name__} оброблено у SHOW", True)

        # Окремо: WatchingChanged(channel=None)
        fake.events.emit(WatchingChanged(channel=None))
        gui.root.update()
        check("подію WatchingChanged(None) оброблено", True)

    finally:
        if gui is not None and getattr(gui, "root", None) is not None:
            try:
                gui.root.destroy()
            except Exception:
                pass

    print(f"\n{'='*50}\nПройдено: {ok}   Провалено: {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(run_smoke_tests())
