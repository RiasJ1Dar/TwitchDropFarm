"""Майнер: що дивитись, коли перемикатись і як зарахувати хвилину.

Тут лишилась тільки логіка рішень. Мережа живе в `api`, авторизація в `identity`,
підписки в `pubsub`, модель у `model` — майнер їх складає докупи й нічого не знає
ні про заголовки, ні про інтерфейс.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any

from core import protocol, pubsub
from core.api import Aborted, ApiError, TwitchApi
from core.channels import Channel
from core.config import (
    CHANNEL_REFRESH_DELAY,
    HISTORY_FILE,
    MAX_CHANNELS,
    PROGRESS_GRACE,
    RESTART_PAUSE,
    STALL_LIMIT,
    TRACE,
    WATCH_PERIOD,
    FarmMode,
    Stage,
)
from core.events import (
    CampaignFinished,
    CampaignSnapshot,
    ChannelSnapshot,
    ChannelsUpdated,
    Command,
    CommandType,
    ConnectionLost,
    ConnectionRestored,
    ControlBus,
    DeadlineRisk,
    DropClaimed,
    DropProgress,
    DropSnapshot,
    EventBus,
    InventoryUpdated,
    LoggedIn,
    MinerError,
    MinerStopped,
    ProgressStalled,
    RiskSnapshot,
    StreamOffline,
    WatchingChanged,
    WindowVisibility,
)
from core.history import History
from core.identity import Identity
from core.model import Campaign, Drop
from core.settings import Settings
from core.toolbox import (
    Game,
    Slot,
    TaskKeeper,
    batched,
    describe_exception,
    guard_task,
    parse_timestamp,
    race,
    sleep_unless,
)

log = logging.getLogger("TwitchDrops")


class Restart(Exception):
    """Прохання підняти ядро наново, не закриваючи вікно."""


class Quit(Exception):
    """Прохання завершити роботу."""


class Miner:
    """Ядро застосунку."""

    def __init__(self, settings: Settings, events: EventBus | None = None):
        self.settings = settings
        self.events = events or EventBus()
        self.control = ControlBus()

        self.api = TwitchApi(
            client=protocol.ANDROID,
            proxy=settings.proxy,
            on_network_lost=self._network_lost,
            on_network_back=self._network_back,
            should_stop=lambda: self._quitting.is_set(),
        )
        self.identity = Identity(self.api, obtain_token=self._sign_in)
        self.api._auth_headers = self.identity.graphql_headers

        self.topics = pubsub.SocketPool(
            events=self.events,
            session_factory=self.api.session,
            token_source=self.identity.access_token,
            login_gate=self.identity.wait_ready,
            proxy=settings.proxy or None,
        )

        # стан
        self._stage = Stage.IDLE
        self._stage_changed = asyncio.Event()
        self._quitting = asyncio.Event()
        self._paused = False
        self.reboot_requested = False

        self.campaigns: list[Campaign] = []
        self._by_id: dict[str, Campaign] = {}
        self._drops: dict[str, Drop] = {}
        self.wanted: list[Game] = []
        self.channels: OrderedDict[int, Channel] = OrderedDict()
        self.watching: Slot[Channel] = Slot()

        self._tasks = TaskKeeper()
        self._watch_task: asyncio.Task[None] | None = None
        self._upkeep_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._restart_watch = asyncio.Event()
        self._upcoming: deque[datetime] = deque()

        self._full_sweep = False
        self._stall_count = 0
        self._last_progress: tuple[str, int] | None = None
        self._shown_progress: dict[str, tuple[int, int]] = {}

        # Ядро історію лише читає. Підписку на запис робить `main`, і не з
        # педантизму: `Miner` створюють і тести, а вони не сміють дописувати
        # вигадані нагороди у справжній файл користувача. Одного разу вже
        # дописали.
        self.history = History(HISTORY_FILE)
        # Кампанії, про безнадійність яких уже сказали. Не в самій кампанії:
        # `_rebuild` створює об'єкти заново на кожне читання інвентаря, тож
        # позначка всередині них не пережила б жодного оновлення. І не лише в
        # пам'яті: набір піднімається з історії, інакше після кожного
        # перезапуску та сама кампанія скаржилась би вдруге.
        self._risk_reported: set[str] = self.history.campaigns_warned()

    # ================================================================ послуги

    async def graphql(self, payload: Any) -> Any:
        return await self.api.graphql(payload)

    async def fetch_text(self, url: str) -> str:
        return await self.api.fetch_text(url)

    async def post_form(self, url: str, body: dict[str, Any]) -> int:
        return await self.api.post_form(url, body)

    def campaign_by_id(self, campaign_id: str) -> Campaign | None:
        return self._by_id.get(campaign_id)

    @property
    def cosmetics_wanted(self) -> bool:
        return bool(self.settings.farm_cosmetics)

    @property
    def verify_drops_enabled(self) -> bool:
        return bool(self.settings.verify_channel_drops)

    @property
    def user_id(self) -> int:
        return self.identity.user_id

    @property
    def stopping(self) -> bool:
        return self._quitting.is_set()

    # ================================================================ події

    def say(self, text: str) -> None:
        self.events.log(text)

    def request_stop(self) -> None:
        self._quitting.set()
        self.go(Stage.QUIT)

    def go(self, stage: Stage) -> None:
        if self._stage is not Stage.QUIT:
            self._stage = stage
        self._stage_changed.set()

    def _network_lost(self, reason: str, attempt: int) -> None:
        self.events.emit(ConnectionLost(reason=reason, attempt=attempt))

    def _network_back(self, downtime: float, attempts: int) -> None:
        self.events.emit(ConnectionRestored(downtime_seconds=downtime, attempts=attempts))
        # за час простою кампанії могли завершитись, а стріми піти офлайн
        self.go(Stage.LOAD_INVENTORY)

    # ---- зворотні виклики моделі

    def drop_changed(self, drop: Drop) -> None:
        self._emit_progress(drop, drop.minutes)

    def show_drop(self, drop: Drop, *, minus_one: bool = False) -> None:
        self._emit_progress(drop, max(0, drop.minutes - 1) if minus_one else drop.minutes)

    def _emit_progress(self, drop: Drop, minutes: int) -> None:
        """Єдина точка розсилки прогресу, із захистом від повторів.

        Один приріст приходить сюди двічі: від самого дропа й від кампанії, яка
        наприкінці ще раз показує поточний. Пам'ятаємо стан кожного дропа окремо —
        спільного «останнього значення» бракувало, бо послідовність виходить A-B-A.
        """
        state = (minutes, drop.required_minutes)
        if self._shown_progress.get(drop.id) == state:
            return
        self._shown_progress[drop.id] = state
        self.events.emit(DropProgress(
            drop_name=drop.name,
            game=drop.campaign.game.name,
            current_minutes=minutes,
            required_minutes=drop.required_minutes,
        ))

    def drop_claimed(self, drop: Drop) -> None:
        campaign = drop.campaign
        self.events.emit(DropClaimed(
            drop_name=drop.name,
            game=campaign.game.name,
            rewards=drop.rewards_text(),
        ))
        self.say(
            f"Отримано: {drop.rewards_text()} — {campaign.game.name} "
            f"({campaign.taken_count}/{campaign.total})"
        )
        if campaign.everything_taken and campaign.mark_reported_done():
            self.events.emit(
                CampaignFinished(campaign_name=campaign.name, game=campaign.game.name)
            )

    def campaign_exhausted(self, campaign: Campaign) -> None:
        self.go(Stage.PICK_CHANNEL)

    # ---- зворотні виклики каналів

    def channel_display_changed(self, channel: Channel) -> None:
        self._schedule_channel_refresh()

    def _schedule_channel_refresh(self) -> None:
        """Зливає сплеск оновлень в одну розсилку.

        Лічильник глядачів приходить по кожному з ~200 каналів окремо, і без
        злиття кожен тік перебудовував би весь список і змушував інтерфейс
        перемальовувати таблицю цілком.
        """
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = self._tasks.launch(self._flush_channels())

    async def _flush_channels(self) -> None:
        await asyncio.sleep(CHANNEL_REFRESH_DELAY)
        self._publish_channels()

    def channel_state_changed(self, channel: Channel, was_live: bool) -> None:
        now_live = channel.live
        current = self.watching.peek()

        if not was_live and now_live:
            if self.should_switch_to(channel):
                self.say(f"{channel.name} вийшов у етер")
                self.watch(channel)
            else:
                log.info(f"{channel.name} в етері")
        elif current is not None and current == channel:
            if not self.can_farm(channel):
                if not now_live:
                    self.say(f"{channel.name} пішов офлайн")
                    self.events.emit(StreamOffline(channel_name=channel.name))
                else:
                    log.info(f"{channel.name} більше не підходить — перемикаюсь")
                self.go(Stage.PICK_CHANNEL)
        elif not now_live:
            log.info(f"{channel.name} офлайн")
        elif self.should_switch_to(channel):
            self.watch(channel)
        channel.announce()

    # ---- знімки для інтерфейсів

    def _snapshot(self, channel: Channel) -> ChannelSnapshot:
        return ChannelSnapshot(
            id=channel.id,
            name=channel.name,
            game=channel.game.name if channel.game else None,
            viewers=channel.viewers,
            online=channel.live,
            drops_enabled=channel.drops_on,
            acl_based=channel.from_allowlist_flag,
        )

    def _publish_channels(self) -> None:
        self.events.emit(ChannelsUpdated(
            channels=tuple(self._snapshot(c) for c in self.channels.values())
        ))

    def _publish_inventory(self) -> None:
        self.events.emit(InventoryUpdated(campaigns=tuple(
            CampaignSnapshot(
                id=c.id, name=c.name, game=c.game.name,
                active=c.running, upcoming=c.not_started, expired=c.over,
                ends_at=c.closes_at,
                claimed_drops=c.taken_count, total_drops=c.total,
                drops=tuple(
                    DropSnapshot(
                        id=d.id, name=d.name,
                        current_minutes=d.minutes,
                        required_minutes=d.required_minutes,
                        claimed=d.taken, can_claim=d.ready_to_take,
                    )
                    for d in c.all_drops
                ),
            )
            for c in self.campaigns
        )))

    # ================================================================ вибір

    def priority_of(self, channel: Channel) -> int:
        """Менше — важливіше. Дуже велике — байдуже."""
        game = channel.game
        if game is None or game not in self.wanted:
            return 1 << 30
        return self.wanted.index(game)

    def can_farm(self, channel: Channel) -> bool:
        if not channel.live:
            return False
        return any(
            campaign.farmable(channel)
            and (
                (channel.game is not None and channel.drops_on
                 and channel.game in self.wanted)
                or campaign.game.any_channel
            )
            for campaign in self.campaigns
        )

    def should_switch_to(self, channel: Channel) -> bool:
        if not self.can_farm(channel):
            return False
        current = self.watching.peek()
        if current is None or not self.can_farm(current):
            return True
        here, there = self.priority_of(channel), self.priority_of(current)
        if here != there:
            return here < there
        # за рівних умов канал зі списку кампанії надійніший
        return channel.from_allowlist_flag and not current.from_allowlist_flag

    def watch(self, channel: Channel, *, announce: bool = True) -> None:
        self.watching.put(channel)
        self._stall_count = 0
        self._last_progress = None
        self.events.emit(WatchingChanged(channel=self._snapshot(channel)))
        if announce:
            self.events.status(f"Дивимось {channel.name}")
        # показуємо дроп одразу: інакше в інтерфейсі понад хвилину висіло б
        # «дроп не визначено», хоча активна кампанія відома вже зараз
        if (campaign := self.active_campaign(channel)) is not None:
            if (drop := campaign.next_drop) is not None:
                drop.show()

    def stop_watching(self) -> None:
        self.watching.clear()
        self.events.emit(WatchingChanged(channel=None))

    def active_campaign(self, channel: Channel | None = None) -> Campaign | None:
        target = self.watching.peek(channel)
        if target is None or not self.wanted:
            return None
        options = [c for c in self.campaigns if c.farmable(target)]
        return min(options, key=lambda c: c.minutes_left, default=None)

    # ================================================================ інвентар

    async def load_inventory(self) -> None:
        self.events.status("Читаю інвентар")
        answer = await self.graphql(protocol.INVENTORY())
        block = answer["data"]["currentUser"]["inventory"]
        in_progress = block["dropCampaignsInProgress"] or []
        awarded = {
            item["id"]: parse_timestamp(item["lastAwardedAt"])
            for item in block["gameEventDrops"] or []
        }
        merged: dict[str, dict[str, Any]] = {c["id"]: c for c in in_progress}

        answer = await self.graphql(protocol.CAMPAIGN_LIST())
        listed = answer["data"]["currentUser"]["dropCampaigns"] or []
        interesting = {
            c["id"]: c for c in listed if c["status"] in ("ACTIVE", "UPCOMING")
        }

        self.events.status("Читаю деталі кампаній")
        details = await self._load_details(list(interesting.items()))
        for campaign_id, extra in details.items():
            merged[campaign_id] = _blend(merged.get(campaign_id, {}), extra)

        self._rebuild(merged, awarded)

    async def _load_details(
        self, wanted: list[tuple[str, dict[str, Any]]]
    ) -> dict[str, dict[str, Any]]:
        """Дотягує подробиці кампаній пакетами, паралельно."""
        login = str(self.identity.user_id)
        jobs = [
            asyncio.ensure_future(self.graphql([
                protocol.CAMPAIGN_DETAILS(channelLogin=login, dropID=campaign_id)
                for campaign_id, _ in portion
            ]))
            for portion in batched(wanted, 20)
        ]
        collected: dict[str, dict[str, Any]] = {}
        base = dict(wanted)
        try:
            for finished in asyncio.as_completed(jobs):
                for reply in await finished:
                    body = reply["data"]["user"]["dropCampaign"]
                    if body:
                        collected[body["id"]] = _blend(base.get(body["id"], {}), body)
        except Exception:
            for job in jobs:
                job.cancel()
            raise
        return collected

    def _rebuild(self, raw: dict[str, dict[str, Any]],
                 awarded: dict[str, datetime]) -> None:
        campaigns = [
            Campaign(self, body, awarded)
            for body in raw.values()
            if body.get("game")
        ]
        # найважливіший критерій сортуємо останнім
        campaigns.sort(key=lambda c: c.running, reverse=True)
        campaigns.sort(key=lambda c: c.opens_at if c.not_started else c.closes_at)
        campaigns.sort(key=lambda c: c.available_to_me, reverse=True)

        self.campaigns = campaigns
        self._by_id = {c.id: c for c in campaigns}
        self._drops = {d.id: d for c in campaigns for d in c.all_drops}
        self._shown_progress.clear()

        soon = datetime.now(timezone.utc) + timedelta(hours=1)
        moments: set[datetime] = set()
        for campaign in campaigns:
            if campaign.farmable_before(soon):
                moments.update(campaign.moments)
        now = datetime.now(timezone.utc)
        self._upcoming = deque(sorted(m for m in moments if m > now))

        if self._upkeep_task is not None and not self._upkeep_task.done():
            self._upkeep_task.cancel()
        self._upkeep_task = self._tasks.launch(self._upkeep())
        self._publish_inventory()
        self._check_deadlines()

    def _check_deadlines(self) -> None:
        """Попереджає про кампанії, які вже не встигнути закрити.

        Про кожну кажемо один раз: інвентар перечитується часто, а повторювати
        погану новину щогодини — це лише привчити її не читати.
        """
        now = datetime.now(timezone.utc)
        risky: list[RiskSnapshot] = []
        for campaign in self.campaigns:
            if campaign.id in self._risk_reported:
                continue
            # безнадійною може бути лише та кампанія, яку ми взагалі беремося
            # фармити: чужі й завершені сюди потрапляти не повинні
            if not campaign.farmable() or campaign.slack >= 1:
                continue
            self._risk_reported.add(campaign.id)
            risky.append(RiskSnapshot(
                id=campaign.id,
                name=campaign.name,
                game=campaign.game.name,
                minutes_needed=campaign.minutes_left,
                minutes_available=max(
                    0, int((campaign.closes_at - now).total_seconds() // 60)
                ),
            ))
        if not risky:
            return
        for item in risky:
            log.warning(
                f"Не встигаємо закрити «{item.name}» ({item.game}): треба "
                f"{item.minutes_needed} хв перегляду, лишилось "
                f"{item.minutes_available} хв часу"
            )
        self.events.emit(DeadlineRisk(campaigns=tuple(risky)))

    async def find_streams(self, game: Game, *, limit: int = 20) -> list[Channel]:
        try:
            answer = await self.graphql(protocol.GAME_DIRECTORY(
                slug=game.slug, limit=limit,
                options={"systemFilters": ["DROPS_ENABLED"]},
            ))
        except ApiError as error:
            log.warning(f"Каталог {game.slug} недоступний: {error}")
            return []
        node = answer["data"].get("game")
        if not node:
            return []
        return [
            Channel.from_directory(self, edge["node"], drops_on=True)
            for edge in node["streams"]["edges"]
            if edge["node"]["broadcaster"] is not None
        ]

    async def check_many(self, channels: list[Channel]) -> None:
        """Пакетна перевірка статусу — замість запиту на кожен канал окремо."""
        if not channels:
            return
        by_id = {c.id: c for c in channels}
        jobs = [
            asyncio.ensure_future(self.graphql(
                [protocol.STREAM_INFO(channel=c.login) for c in portion]
            ))
            for portion in batched(channels, 20)
        ]
        found: dict[int, dict[str, Any]] = {}
        try:
            for finished in asyncio.as_completed(jobs):
                for reply in await finished:
                    body = reply["data"]["user"]
                    if body:
                        found[int(body["id"])] = body
        except Exception:
            for job in jobs:
                job.cancel()
            raise

        drops_map: dict[int, list[dict[str, Any]]] = {}
        if self.verify_drops_enabled:
            live_ids = [cid for cid, body in found.items() if body["stream"]]
            drop_jobs = [
                asyncio.ensure_future(self.graphql(
                    [protocol.CHANNEL_DROPS(channelID=str(cid)) for cid in portion]
                ))
                for portion in batched(live_ids, 20)
            ]
            try:
                for finished in asyncio.as_completed(drop_jobs):
                    for reply in await finished:
                        node = reply["data"]["channel"]
                        drops_map[int(node["id"])] = node["viewerDropCampaigns"] or []
            except Exception:
                for job in drop_jobs:
                    job.cancel()
                raise

        for channel_id, body in found.items():
            if (channel := by_id.get(channel_id)) is not None:
                channel.apply_bulk_update(body, drops_map.get(channel_id, []))

    # ================================================================ PubSub

    @guard_task
    async def on_stream_state(self, channel_id: int, message: dict[str, Any]) -> None:
        channel = self.channels.get(channel_id)
        if channel is None:
            return
        kind = message["type"]
        if kind == "viewcount":
            if channel.live:
                channel.viewers = message["viewers"]
                channel.announce()
            else:
                channel.expect_live()
        elif kind == "stream-down":
            watched = self.watching.peek() == channel
            channel.mark_dark()
            if watched:
                self.events.emit(StreamOffline(channel_name=channel.name))
        elif kind == "stream-up":
            channel.expect_live()

    @guard_task
    async def on_stream_settings(self, channel_id: int, message: dict[str, Any]) -> None:
        channel = self.channels.get(channel_id)
        if channel is None:
            return
        if message.get("old_game") != message.get("game"):
            log.log(TRACE, f"{channel.name}: гра {message['old_game']} → {message['game']}")
        # затримка всередині зливає кілька змін поспіль в одну перевірку
        channel.expect_live()

    @guard_task
    async def on_drop_event(self, _user_id: int, message: dict[str, Any]) -> None:
        kind = message["type"]
        if kind not in ("drop-progress", "drop-claim"):
            return
        drop = self._drops.get(message["data"]["drop_id"])
        watching = self.watching.peek()

        if kind == "drop-progress":
            if drop is not None and drop.farmable(watching):
                drop.set_counted(message["data"]["current_progress_min"])
            return

        if drop is None:
            log.error("Клейм для невідомого дропа")
            return
        drop.note_claim_id(message["data"]["drop_instance_id"])
        campaign = drop.campaign
        await drop.take()
        drop.show()

        # наступний дроп стартує за 4–20 с; чекаємо, поки Twitch перемкнеться
        await asyncio.sleep(4)
        if watching is not None:
            for _ in range(8):
                try:
                    context = await self.graphql(
                        protocol.CURRENT_DROP(channelID=str(watching.id))
                    )
                    session = context["data"]["currentUser"]["dropCurrentSession"]
                except ApiError:
                    break
                if session is None or session["dropID"] != drop.id:
                    break
                await asyncio.sleep(2)

        if campaign.farmable(watching):
            self._restart_watch.set()
        else:
            self.go(Stage.LOAD_INVENTORY)

    @guard_task
    async def on_notification(self, _user_id: int, message: dict[str, Any]) -> None:
        if message["type"] != "create-notification":
            return
        body = message["data"]["notification"]
        if body["type"] in (
            "user_drop_reward_reminder_notification",
            "quests_viewer_reward_campaign_earned_emote",
        ):
            self.go(Stage.LOAD_INVENTORY)
            await self.graphql(protocol.DROP_NOTIFICATION_DELETE(input={"id": body["id"]}))

    @guard_task
    async def on_points(self, _user_id: int, message: dict[str, Any]) -> None:
        """Забирає бонус channel points.

        Дістається майже безкоштовно: канал ми й так дивимось, підписку й так
        тримаємо — лишається обробити ще один топік.
        """
        if message["type"] != "claim-available":
            return
        claim = message["data"]["claim"]
        try:
            await self.graphql(protocol.CLAIM_POINTS(input={
                "claimID": claim["id"],
                "channelID": str(claim["channel_id"]),
            }))
            log.log(TRACE, "Забрано бонус channel points")
        except (ApiError, Exception) as error:
            log.log(TRACE, f"Бонус поінтів не забрався: {error}")

    # ================================================================ перегляд

    def _progress_mark(self) -> int | None:
        """Скільки хвилин Twitch підтвердив по всьому, що зараз фармиться.

        Саме по всьому, а не по одному «активному» дропу. Кампаній тієї самої
        гри буває кілька одночасно, Twitch зараховує перегляд котрійсь із них,
        а `active_campaign` обирає за найменшим залишком — і легко вказує на ту,
        що стоїть. Тоді детектор бачив нерухому позначку й бив тривогу, поки
        сусідня кампанія спокійно росла щохвилини. Спіймано на живому:
        одна йшла 2/60 → 9/60, друга стояла на 151/180, тривога — про другу.
        """
        channel = self.watching.peek(None)
        if channel is None or not self.wanted:
            return None
        counted = [
            drop.counted_minutes
            for campaign in self.campaigns if campaign.farmable(channel)
            for drop in campaign.all_drops if drop.farmable(channel)
        ]
        if not counted:
            return None
        # Лише підтверджені Twitch хвилини. Якщо брати `minutes`, туди входять
        # і домальовані наосліп — а їх додає щохвилини саме той шлях, яким
        # майнер іде, коли Twitch мовчить. Позначка щоразу мінялась би, і
        # застій маскував би сам себе: лічильник скидався в нуль, тривога
        # не спрацьовувала жодного разу.
        return sum(counted)

    @guard_task(vital=True)
    async def _watch_loop(self) -> None:
        period = WATCH_PERIOD.total_seconds()
        while True:
            channel = await self.watching.take()
            if not channel.live:
                self.stop_watching()
                continue

            before = self._progress_mark()
            sent_at = monotonic()
            if not await channel.report_watching():
                log.log(TRACE, f"Хвилина не зарахувалась: {channel.name}")

            # даємо Twitch час відзвітувати самому
            await asyncio.sleep(PROGRESS_GRACE.total_seconds())
            if not await self._confirm_progress(channel):
                self._estimate_progress(channel)
            self._check_stall(before, channel)

            self._restart_watch.clear()
            waited = monotonic() - sent_at
            await sleep_unless(self._restart_watch, max(0.0, period - waited))

    async def _confirm_progress(self, channel: Channel) -> bool:
        """Питає Twitch, скільки він нам нарахував. False — не вдалось.

        Кожен вихід із False лишає слід у журналі. Без цього чотири різні
        причини мовчання виглядають однаково, а від них залежить, чи вважати
        зупинку приросту застоєм: «Twitch не віддав сесію» і «Twitch рахує
        іншу гру» — стани зовсім різної ваги.
        """
        try:
            context = await self.graphql(protocol.CURRENT_DROP(channelID=str(channel.id)))
            session = context["data"]["currentUser"]["dropCurrentSession"]
        except (ApiError, Aborted) as error:
            log.log(TRACE, f"Підтвердження не вдалось: {type(error).__name__}")
            return False
        if session is None:
            log.log(TRACE, "Twitch не віддав поточну сесію дропа")
            return False
        drop = self._drops.get(session["dropID"])
        if drop is None:
            log.log(TRACE, f"Twitch звітує невідомий нам дроп {session['dropID']}")
            return False
        if drop.taken:
            # Кілька секунд після клейму Twitch віддає щойно забраний дроп як
            # поточний: його сесія перемикається на наступний із запізненням.
            # Це нормальний стан, а не чужий перегляд — просто чекаємо
            # наступного опитування, домалювавши хвилину наосліп.
            log.log(TRACE, f"Twitch ще звітує забраний дроп «{drop.name}»")
            return False
        if not drop.farmable(channel):
            # Twitch рахує зовсім іншу кампанію — майже напевно цим акаунтом
            # хтось дивиться Twitch вручну
            here = f"{channel.name} ({channel.game})" if channel.game else channel.name
            log.warning(
                f"Twitch зараховує «{drop.campaign.game.name}», "
                f"а ми дивимось {here} — схоже на паралельний перегляд"
            )
            return False
        drop.set_counted(session["currentMinutesWatched"])
        return True

    def _estimate_progress(self, channel: Channel) -> None:
        campaign = self.active_campaign(channel)
        if campaign is None:
            log.log(TRACE, "Активний дроп не визначено")
            return
        campaign.add_blind_minute(channel)

    def _check_stall(self, before: tuple[str, int] | None, channel: Channel) -> None:
        after = self._progress_mark()
        if after is None:
            return  # нема чого фармити — це не застій
        # before is None — фарм щойно почався, порівнювати ще нема з чим
        if before is None or after != before:
            self._stall_count = 0
            return
        self._stall_count += 1
        if self._stall_count == STALL_LIMIT:
            # у журнал це пише підписник подій у main; другий рядок звідси лише
            # дублював би той самий факт
            self.events.emit(ProgressStalled(
                minutes_without_progress=self._stall_count,
                channel_name=channel.name,
            ))

    @guard_task(vital=True)
    async def _upkeep(self) -> None:
        """Прокидається на межі активності кампаній і раз на годину."""
        horizon = datetime.now(timezone.utc) + timedelta(hours=1)
        while True:
            now = datetime.now(timezone.utc)
            if now >= horizon:
                break
            target = horizon
            while self._upcoming and self._upcoming[0] <= target:
                target = self._upcoming.popleft()
            await asyncio.sleep(max(0.0, (target - now).total_seconds()))
            if datetime.now(timezone.utc) >= horizon:
                break
            if target != horizon:
                self.go(Stage.DROP_CHANNELS)
        self.go(Stage.LOAD_INVENTORY)

    # ================================================================ вхід

    async def _sign_in(self) -> str:
        from auth.flow import device_login_with_browser
        return await device_login_with_browser(self)

    # сумісність із модулем входу, який писався для першої версії
    @property
    def _client_type(self):
        class _Shim:
            CLIENT_ID = protocol.ANDROID.client_id
            CLIENT_URL = protocol.TWITCH_HOME
            USER_AGENT = self.api.user_agent
        return _Shim()

    @property
    def _auth_state(self):
        return self.identity

    def print(self, text: str) -> None:
        self.say(text)

    def request(self, method: str, url: Any, **kwargs: Any):
        return self.api.request(method, str(url), **kwargs)

    # ================================================================ команди

    def _drain_commands(self) -> None:
        for command in self.control.drain_pending():
            self._apply(command)

    def _apply(self, command: Command) -> None:
        kind = command.type
        if kind is CommandType.PAUSE:
            self._paused = True
            self.stop_watching()
            self.events.status("Призупинено")
            self.go(Stage.IDLE)
        elif kind is CommandType.RESUME:
            self._paused = False
            self.go(Stage.LOAD_INVENTORY)
        elif kind is CommandType.RELOAD:
            self.go(Stage.LOAD_INVENTORY)
        elif kind is CommandType.SHUTDOWN:
            self.request_stop()
        elif kind is CommandType.REBOOT:
            self.reboot_requested = True
            self.say("Перезапускаю програму…")
            self.request_stop()
        elif kind is CommandType.SWITCH:
            self._switch_by_name(command.argument)
        elif kind in (CommandType.SHOW_WINDOW, CommandType.HIDE_WINDOW):
            # вікном розпоряджається інтерфейс, ядро лише переказує прохання
            self.events.emit(
                WindowVisibility(visible=kind is CommandType.SHOW_WINDOW)
            )
        elif kind in (CommandType.PRIORITY_ADD, CommandType.PRIORITY_REMOVE):
            self._edit_list("priority", command)
        elif kind in (CommandType.EXCLUDE_ADD, CommandType.EXCLUDE_REMOVE):
            self._edit_list("exclude", command)

    def _switch_by_name(self, wanted: str) -> None:
        target = wanted.strip().lower()
        for channel in self.channels.values():
            if target in (channel.login.lower(), channel.name.lower()):
                if self.can_farm(channel):
                    self.watch(channel)
                else:
                    self.say(f"{channel.name} зараз не підходить для фарму")
                return
        self.say(f"Канал «{wanted}» не в списку відстеження")

    def _edit_list(self, field: str, command: Command) -> None:
        current = list(getattr(self.settings, field))
        adding = command.type in (CommandType.PRIORITY_ADD, CommandType.EXCLUDE_ADD)
        if adding and command.argument not in current:
            current.append(command.argument)
        elif not adding and command.argument in current:
            current.remove(command.argument)
        setattr(self.settings, field, current)
        self.settings.save()
        self.go(Stage.LOAD_INVENTORY)

    # ================================================================ цикл

    async def run(self) -> None:
        """Головний цикл із самовідновленням.

        Twitch — рухома ціль: викидає хеші запитів із кешу, віддає 5xx, змінює
        поведінку. Будь-яка така несподіванка раніше вбивала застосунок, і майнер
        зникав серед ночі. Тепер ядро підіймається саме, а користувач дізнається
        про це подією, а не з тиші.
        """
        pause = RESTART_PAUSE.total_seconds()
        while True:
            try:
                await self._cycle()
                return
            except Restart:
                await self.shutdown()
            except (Quit, Aborted):
                return
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if self.stopping:
                    return
                log.exception("Ядро впало, перезапускаю")
                self.events.emit(MinerError(
                    message=f"{type(error).__name__}: {error}",
                    traceback=describe_exception(error),
                ))
                self.say(f"Помилка «{type(error).__name__}». Перезапуск через {pause:.0f}с.")
                await self.shutdown()
                if await sleep_unless(self._quitting, pause):
                    return

    async def _cycle(self) -> None:
        await self.identity.ensure()
        self.events.emit(LoggedIn(user_id=self.identity.user_id))
        await self.topics.start()

        if self._watch_task is not None:
            self._watch_task.cancel()
        self._watch_task = self._tasks.launch(self._watch_loop())

        user = self.identity.user_id
        self.topics.subscribe([
            pubsub.user_subscription("drops", user, self.on_drop_event),
            pubsub.user_subscription("notifications", user, self.on_notification),
            pubsub.user_subscription("points", user, self.on_points),
        ])

        self._full_sweep = False
        self.go(Stage.LOAD_INVENTORY)

        while True:
            self._drain_commands()

            if self._stage is Stage.QUIT:
                self.events.status("Завершення роботи")
                self.events.emit(MinerStopped(reason="Запит користувача"))
                return
            if self._stage is Stage.RESTART:
                raise Restart()

            if self._stage is Stage.IDLE:
                self.events.status("Призупинено" if self._paused else "Очікування")
                self.stop_watching()
                self._stage_changed.clear()
            elif self._paused and self._stage in (Stage.LOAD_INVENTORY, Stage.PICK_CHANNEL):
                self.go(Stage.IDLE)
                continue
            elif self._stage is Stage.LOAD_INVENTORY:
                await self.topics.start()
                await self.load_inventory()
                self.settings.save()
                self.go(Stage.PICK_GAMES)
            elif self._stage is Stage.PICK_GAMES:
                await self._pick_games()
            elif self._stage is Stage.DROP_CHANNELS:
                self._drop_stale_channels()
            elif self._stage is Stage.FIND_CHANNELS:
                await self._find_channels()
            elif self._stage is Stage.PICK_CHANNEL:
                self._pick_channel()

            # прокидаємось на зміну стану або на команду — саме на подію, а не
            # за таймером: опитування змушувало цикл переграти поточний стан
            # і сипати повтореннями статусу
            await race(self._stage_changed.wait(), self.control.wait())

    # ---- окремі кроки

    async def _pick_games(self) -> None:
        for campaign in self.campaigns:
            if campaign.not_started:
                continue
            for drop in campaign.all_drops:
                if drop.ready_to_take:
                    await drop.take()

        mode: FarmMode = self.settings.farm_mode
        priority: list[str] = list(self.settings.priority)
        excluded = set(self.settings.exclude)
        soon = datetime.now(timezone.utc) + timedelta(hours=1)

        ordered = list(self.campaigns)
        if mode is FarmMode.TIGHTEST_FIT:
            ordered.sort(key=lambda c: c.slack)
        elif mode is not FarmMode.PRIORITY_LIST:
            ordered.sort(key=lambda c: c.closes_at)
        if mode is not FarmMode.LINKED_ONLY:
            # LINKED_ONLY навмисно ігнорує список: його зміст у тому, щоб нічого
            # не налаштовувати руками
            ordered.sort(key=lambda c: (
                priority.index(c.game.name) if c.game.name in priority else 1 << 30
            ))

        chosen: list[Game] = []
        for campaign in ordered:
            game = campaign.game
            if game in chosen or game.name in excluded:
                continue
            if mode is FarmMode.PRIORITY_LIST and game.name not in priority:
                continue
            if mode is FarmMode.LINKED_ONLY and not (
                campaign.linked and campaign.has_real_item
            ):
                continue
            if campaign.farmable_before(soon):
                chosen.append(game)

        self.wanted = chosen
        self._full_sweep = True
        self._restart_watch.set()
        self.go(Stage.DROP_CHANNELS)

    def _drop_stale_channels(self) -> None:
        self.events.status("Прибирання каналів")
        if not self.wanted or self._full_sweep:
            doomed = list(self.channels.values())
        else:
            doomed = [
                c for c in self.channels.values()
                if not c.from_allowlist_flag
                and (c.dark or c.game is None or c.game not in self.wanted)
            ]
        self._full_sweep = False
        if doomed:
            self.topics.unsubscribe(pubsub.channel_topic_names(c.id for c in doomed))
            for channel in doomed:
                self.channels.pop(channel.id, None)
                channel.forget()
            self._publish_channels()
        if self.wanted:
            self.go(Stage.FIND_CHANNELS)
        else:
            self.say("Немає кампаній, які можна фармити зараз")
            self.go(Stage.IDLE)

    async def _find_channels(self) -> None:
        self.events.status("Шукаю канали")
        pool: set[Channel] = set(self.channels.values())
        self.channels.clear()

        soon = datetime.now(timezone.utc) + timedelta(hours=1)
        listed: set[Channel] = set()
        open_games: set[Game] = set()
        for campaign in self.campaigns:
            if campaign.game in self.wanted and campaign.farmable_before(soon):
                if campaign.channels:
                    listed.update(campaign.channels)
                else:
                    open_games.add(campaign.game)
        listed -= pool

        # Перевірка списків і пошук у каталозі — незалежні мережеві запити.
        # Послідовно це давало кілька секунд на старті: по колу чекали відповіді
        # для кожної гри окремо.
        directory = [
            asyncio.ensure_future(self.find_streams(game)) for game in open_games
        ]
        try:
            await self.check_many(list(listed))
            pool |= listed
            for found in await asyncio.gather(*directory):
                pool.update(found)
        except Exception:
            for job in directory:
                job.cancel()
            raise

        ranked = sorted(pool, key=lambda c: c.viewers, reverse=True)
        ranked.sort(key=lambda c: c.from_allowlist_flag, reverse=True)
        ranked.sort(key=self.priority_of)
        keep, spare = ranked[:MAX_CHANNELS], ranked[MAX_CHANNELS:]
        if spare:
            self.topics.unsubscribe(pubsub.channel_topic_names(c.id for c in spare))
        for channel in keep:
            self.channels[channel.id] = channel

        self.topics.subscribe([
            sub
            for channel_id in self.channels
            for sub in (
                pubsub.channel_subscription("state", channel_id, self.on_stream_state),
                pubsub.channel_subscription("settings", channel_id, self.on_stream_settings),
            )
        ])
        self._publish_channels()

        # канал, який дивились, міг не пережити переоблік
        current = self.watching.peek()
        if current is not None:
            replacement = self.channels.get(current.id)
            if replacement is not None and self.can_farm(replacement):
                self.watch(replacement, announce=False)
            else:
                self.stop_watching()
        self.go(Stage.PICK_CHANNEL)

    def _pick_channel(self) -> None:
        self.events.status("Обираю канал")
        for channel in sorted(self.channels.values(), key=self.priority_of):
            if self.should_switch_to(channel):
                self.watch(channel)
                self._stage_changed.clear()
                return
        current = self.watching.peek()
        if current is not None and self.can_farm(current):
            self.events.status(f"Дивимось {current.name}")
            self._stage_changed.clear()
            return
        self.say("Немає підходящого каналу для фарму")
        self.go(Stage.IDLE)

    # ================================================================ зупинка

    async def shutdown(self) -> None:
        self.stop_watching()
        for task in (self._watch_task, self._upkeep_task, self._refresh_task):
            if task is not None:
                task.cancel()
        self._watch_task = self._upkeep_task = self._refresh_task = None
        self._tasks.cancel_all()
        await self.topics.stop(forget_topics=True)
        await self.api.close()
        self.campaigns.clear()
        self._by_id.clear()
        self._drops.clear()
        self.channels.clear()
        self.wanted.clear()
        self._upcoming.clear()
        self._shown_progress.clear()
        self.identity.clear()
        await self.events.drain()


def _blend(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Зливає два описи однієї кампанії; за розбіжності виграє перший."""
    merged = dict(extra)
    for key, value in base.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _blend(value, current)
        else:
            merged[key] = value
    return merged
