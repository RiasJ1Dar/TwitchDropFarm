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
from core.campaigns import (
    CampaignTracker,
    campaign_snapshot,
    check_deadlines,
    check_link_hints,
    check_links,
    check_losses,
    check_watchlist,
    needs_rescue,
    weekly_report,
)
from core.channels import Channel
from core.config import (
    ALMOST_THERE_MINUTES,
    CHANNEL_REFRESH_DELAY,
    HISTORY_FILE,
    IMAGE_DIR,
    MAX_CHANNELS,
    PROGRESS_GRACE,
    PROTOCOL_PROBE_EVERY,
    PROTOCOL_PROBE_PAUSE,
    RESTART_PAUSE,
    SEEN_CAMPAIGNS_FILE,
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
    DropClaimed,
    DropProgress,
    EventBus,
    InventoryUpdated,
    LoggedIn,
    MinerError,
    MinerStopped,
    ProtocolStale,
    StreamOffline,
    WatchingChanged,
    WindowVisibility,
)
from core.history import History
from core.i18n import t
from core.identity import Identity
from core.images import ImageCache
from core.model import Campaign, Drop
from core.seen import SeenCampaigns
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
from core.update_manager import UpdateCoordinator
from core.watchdog import (
    ChannelBench,
    StallDetector,
    build_probe_variables,
    note_delivery_failed,
    note_delivery_ok,
)
from core.watchdog import (
    probe_protocol as probe_queries,
)

log = logging.getLogger("TwitchDrops")

# Скільки тримати подробиці кампаній у кеші (90 хвилин). Описи, канали й
# нагороди рідко змінюються на ходу, тож повторний запис десятків однакових
# CAMPAIGN_DETAILS на кожному оновленні інвентаря лише марнує запити до GraphQL.
CAMPAIGN_DETAILS_TTL = 5400.0
# Twitch може видати нагороду автоматично, без PubSub drop-claim.
# Якщо self/claim ID ще не оновились, перепитуємо не частіше разу на хвилину.
COMPLETED_DROP_REFRESH = 60.0


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
        # показати/сховати вікно — повз чергу, інакше чекало б кінця стадії
        self.control.set_immediate_handler(self._apply)

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
        self.wanted = []  # сеттер нижче будує ще й індекс пріоритетів
        self.channels: OrderedDict[int, Channel] = OrderedDict()
        self.watching: Slot[Channel] = Slot()
        # точкові топіки активного каналу; тримаємо імена, щоб відписати
        self._live_topics: list[str] = []
        # рейд, до якого вже приєднались: Twitch шле подію кілька разів
        self._joined_raid: str = ""

        self._tasks = TaskKeeper()
        self._watch_task: asyncio.Task[None] | None = None
        self._upkeep_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._restart_watch = asyncio.Event()
        self._upcoming: deque[datetime] = deque()

        self._full_sweep = False
        self.stall_detector = StallDetector()
        self._stall_since: float | None = None
        self._stall_alerted = False
        # назва кампанії, якій Twitch зараховує наш перегляд замість нашої
        self._counted_elsewhere = ""
        self._delivery_failures = 0
        self._shown_progress: dict[str, tuple[int, int]] = {}
        # канали, які не зараховують хвилини: ім'я -> доки не чіпати
        self.bench_tracker = ChannelBench()
        self._benched: dict[str, float] = self.bench_tracker.benched
        # людина натиснула «Відкласти» — до наступного запуску не нагадуємо
        self.update_postponed = False
        # Стан оновлення тримає координатор, а не майнер. `_update_plan`,
        # `_updating` і `_update_problem` нижче — властивості до нього.
        self.updater = UpdateCoordinator(self)
        # коли востаннє питали Twitch, чи живі наші persisted-запити
        self._protocol_checked = 0.0
        # кеш подробиць кампаній (id -> (timestamp, data)) для зменшення навантаження GraphQL
        self._campaign_details_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._completed_drop_refresh: dict[str, float] = {}

        # Ядро історію лише читає. Підписку на запис робить `main`, і не з
        # педантизму: `Miner` створюють і тести, а вони не сміють дописувати
        # вигадані нагороди у справжній файл користувача. Одного разу вже
        # дописали.
        self.history = History(HISTORY_FILE)
        self.images = ImageCache(IMAGE_DIR, self.api)
        # Які кампанії вже бачили — щоб «нова» означало справді нову, а перший
        # запуск не сипав повідомленнями про все, що існувало й до нас.
        self.seen = SeenCampaigns(SEEN_CAMPAIGNS_FILE)
        # Відстеження стану кампаній, дедлайнів, зв'язків та втрат
        self.tracker = CampaignTracker(risk_reported=self.history.campaigns_warned())
        self._risk_reported: set[str] = self.tracker.risk_reported
        self._link_told: set[str] = self.tracker.link_told
        self._loss_told: set[str] = self.tracker.loss_told
        self._hint_told: set[str] = self.tracker.hint_told
        # канали, про які вже сказали «Twitch не нараховує тут жодному дропу»
        self._no_drop_told: set[str] = set()

    # ================================================================ послуги

    async def graphql(self, payload: Any, *, retries: int | None = None) -> Any:
        return await self.api.graphql(payload, retries=retries)

    async def fetch_text(self, url: str, **kwargs: Any) -> str:
        return await self.api.fetch_text(url, **kwargs)


    async def head(self, url: str, **kwargs: Any) -> int:
        return await self.api.head(url, **kwargs)

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

    # Стан оновлення переїхав у `UpdateCoordinator`, але читають його звідусіль:
    # вікно, Telegram і перевірки. Властивості лишають старі імена робочими,
    # не заводячи другої копії — синхронізувати дві копії руками вже пробували,
    # і саме там ховається «кнопку натиснули двічі».
    @property
    def _update_plan(self) -> tuple | None:
        return self.updater.plan

    @property
    def _updating(self) -> bool:
        return self.updater.updating

    @property
    def _update_problem(self) -> str | None:
        return self.updater.problem

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
            campaign=drop.campaign.name.strip(),
        ))

    def drop_claimed(self, drop: Drop) -> None:
        campaign = drop.campaign
        self.events.emit(DropClaimed(
            drop_name=drop.name,
            game=campaign.game.name,
            rewards=drop.rewards_text(),
        ))
        self.say(t("say_claimed", rewards=drop.rewards_text(),
                   game=campaign.game.name, taken=campaign.taken_count,
                   total=campaign.total))
        if campaign.everything_taken and campaign.mark_reported_done():
            self.events.emit(
                CampaignFinished(campaign_name=campaign.name, game=campaign.game.name)
            )


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
                self.say(t("say_live", name=channel.name))
                self.watch(channel)
            else:
                log.info(f"{channel.name} в етері")
        elif current is not None and current == channel:
            if not self.can_farm(channel):
                if not now_live:
                    self.say(t("say_offline", name=channel.name))
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
            stream_title=channel.stream.title if channel.stream else "",
        )

    def _publish_channels(self) -> None:
        self.events.emit(ChannelsUpdated(
            channels=tuple(self._snapshot(c) for c in self.channels.values())
        ))

    def _campaign_snapshot(self, c: Campaign) -> CampaignSnapshot:
        # linked=c.available_to_me
        return campaign_snapshot(c)

    def _needs_rescue(self, campaign: Any) -> bool:
        return needs_rescue(campaign)

    def _weekly_report(self) -> None:
        say_fn = getattr(self, "say", None)
        if say_fn is not None:
            weekly_report(self.history, say_fn)

    def _check_link_hints(self) -> None:
        tracker = getattr(self, "tracker", None)
        told = tracker.hint_told if tracker is not None else getattr(self, "_hint_told", set())
        check_link_hints(self.campaigns, self.settings, self.events, told)

    def _check_losses(self) -> None:
        tracker = getattr(self, "tracker", None)
        told = tracker.loss_told if tracker is not None else getattr(self, "_loss_told", set())
        check_losses(self.campaigns, self.history, told)

    def _check_links(self) -> None:
        tracker = getattr(self, "tracker", None)
        told = tracker.link_told if tracker is not None else getattr(self, "_link_told", set())
        check_links(self.campaigns, self.events, told)

    def _publish_inventory(self) -> None:
        self.events.emit(InventoryUpdated(campaigns=tuple(
            self._campaign_snapshot(c) for c in self.campaigns
        )))

    # ================================================================ вибір

    @property
    def wanted(self) -> list[Game]:
        """Ігри в порядку пріоритету."""
        return self._wanted

    @wanted.setter
    def wanted(self, games: list[Game]) -> None:
        self._wanted = games
        # Індекс будується разом зі списком і тільки тут: якби його оновлювали
        # окремим рядком, рано чи пізно хтось присвоїв би `wanted` і забув про
        # ранг — і сортування мовчки поїхало б.
        self._wanted_rank = {game: index for index, game in enumerate(games)}

    def priority_of(self, channel: Channel) -> int:
        """Менше — важливіше. Дуже велике — байдуже.

        Раніше тут стояв `list.index()` — лінійний пошук на кожен виклик, а
        викликається це як ключ сортування списків у сотні каналів.
        """
        game = channel.game
        if game is None:
            return 1 << 30
        return self._wanted_rank.get(game, 1 << 30)

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

    def benched(self, channel: Channel) -> bool:
        tracker = getattr(self, "bench_tracker", None)
        benched_map = getattr(self, "_benched", None)
        if benched_map is None and tracker is not None:
            benched_map = tracker.benched
        if benched_map is None:
            return False
        return ChannelBench(benched_map).is_benched(channel.name)

    def bench(self, channel: Channel) -> None:
        tracker = getattr(self, "bench_tracker", None)
        benched_map = getattr(self, "_benched", None)
        if benched_map is None and tracker is not None:
            benched_map = tracker.benched
        if benched_map is None:
            benched_map = {}
            self._benched = benched_map
        ChannelBench(benched_map).bench(channel.name)

    def almost_there(self) -> int:
        """Скільки хвилин лишилось до нагороди на каналі, який дивимось.

        Нуль означає «нема чого доводити до кінця»: або дроп щойно взято, або
        каналу немає, або до нагороди ще далеко.
        """
        campaign = self.active_campaign()
        if campaign is None:
            return 0
        drop = campaign.next_drop
        if drop is None or drop.taken or drop.required_minutes <= 0:
            return 0
        left = drop.required_minutes - drop.minutes
        if left <= 0 or left > ALMOST_THERE_MINUTES:
            return 0
        return left

    def should_switch_to(self, channel: Channel) -> bool:
        if self.benched(channel):
            return False
        # ⚠️ Доводимо до кінця те, що ось-ось дасть нагороду. Інакше добір міг
        # перемкнути канал за десять хвилин до дропа — намайнене лишалось
        # висіти, а хвилини починали копитись в іншому місці.
        left = self.almost_there()
        if left and self.can_farm(self.watching.peek()):
            log.log(TRACE, f"Не перемикаюсь: до нагороди {left} хв")
            return False
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
        self._follow_settings(channel)
        self.watching.put(channel)
        self._stall_since = None
        self._stall_alerted = False
        self._delivery_failures = 0
        self.events.emit(WatchingChanged(channel=self._snapshot(channel)))
        if announce:
            self.events.status(t("status_watching", name=channel.name))
        # показуємо дроп одразу: інакше в інтерфейсі понад хвилину висіло б
        # «дроп не визначено», хоча активна кампанія відома вже зараз
        if (campaign := self.active_campaign(channel)) is not None:
            if (drop := campaign.next_drop) is not None:
                drop.show()

    def _follow_settings(self, channel: Channel | None) -> None:
        """Слухати зміну гри — лише на каналі, який дивимось.

        Масово цей топік не підписуємо: він з'їдав половину місткості PubSub,
        роблячи по суті те саме, що й «стрім піднявся/впав». А от на активному
        каналі він потрібен: стрімер може перемкнути гру, не перериваючи
        трансляції, і тоді хвилини йдуть у порожнечу.
        """
        if self._live_topics:
            self.topics.unsubscribe(self._live_topics)
            self._live_topics = []
        if channel is None:
            return
        # Три точкові підписки на активний канал: зміна гри, моменти, рейди.
        # Масово їх тримати не можна — місткість PubSub жорстка.
        wanted = [
            pubsub.channel_subscription("settings", channel.id,
                                        self.on_stream_settings),
            pubsub.channel_subscription("moments", channel.id, self.on_moment),
            pubsub.channel_subscription("raid", channel.id, self.on_raid),
        ]
        self.topics.subscribe(wanted)
        self._live_topics = [item.name for item in wanted]

    def stop_watching(self) -> None:
        self._follow_settings(None)
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
        self.events.status(t("status_inventory"))
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

        self.events.status(t("status_details"))
        details = await self._load_details(list(interesting.items()))
        self._maybe_watch_protocol()
        for campaign_id, extra in details.items():
            merged[campaign_id] = _blend(merged.get(campaign_id, {}), extra)

        self._rebuild(merged, awarded)

    async def _load_details(
        self, wanted: list[tuple[str, dict[str, Any]]]
    ) -> dict[str, dict[str, Any]]:
        """Дотягує подробиці кампаній пакетами, паралельно з кешуванням TTL."""
        now = monotonic()
        collected: dict[str, dict[str, Any]] = {}
        missing: list[tuple[str, dict[str, Any]]] = []

        for cid, body in wanted:
            entry = self._campaign_details_cache.get(cid)
            if entry is not None and (now - entry[0]) < CAMPAIGN_DETAILS_TTL:
                collected[cid] = entry[1]
            else:
                missing.append((cid, body))

        if missing:
            login = str(self.identity.user_id)
            jobs = [
                asyncio.ensure_future(self.graphql([
                    protocol.CAMPAIGN_DETAILS(channelLogin=login, dropID=campaign_id)
                    for campaign_id, _ in portion
                ]))
                for portion in batched(missing, 20)
            ]
            base = dict(missing)
            try:
                for finished in asyncio.as_completed(jobs):
                    for reply in await finished:
                        body = reply["data"]["user"]["dropCampaign"]
                        if body:
                            blended = _blend(base.get(body["id"], {}), body)
                            self._campaign_details_cache[body["id"]] = (now, blended)
                            collected[body["id"]] = blended
            except Exception:
                for job in jobs:
                    job.cancel()
                raise

        current_ids = {cid for cid, _ in wanted}
        for cid in list(self._campaign_details_cache):
            if cid not in current_ids:
                self._campaign_details_cache.pop(cid, None)

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
        self._check_watchlist()
        self._check_links()
        self._check_losses()
        self._check_link_hints()
        self._weekly_report()
        if self.settings.drop_images:
            # У фоні: картинки — прикраса, і чекати на них перед фармом безглуздо
            self._tasks.launch(self._fetch_images())

    async def _fetch_images(self) -> None:
        urls: list[str] = []
        for campaign in self.campaigns:
            urls.append(campaign.image)
            for drop in campaign.all_drops:
                urls.extend(reward.image for reward in drop.rewards)
        added = await self.images.fetch_all(urls)
        if added:
            # список перемальовується вже з картинками
            self._publish_inventory()

    def _check_deadlines(self) -> None:
        tracker = getattr(self, "tracker", None)
        reported = (
            tracker.risk_reported
            if tracker is not None
            else getattr(self, "_risk_reported", set())
        )
        check_deadlines(self.campaigns, self.events, reported)

    def _check_watchlist(self) -> None:
        snapshot_fn = getattr(self, "_campaign_snapshot", campaign_snapshot)
        check_watchlist(
            self.campaigns,
            self.settings.watch_games,
            self.seen,
            self.events,
            snapshot_builder=snapshot_fn,
        )

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

    async def on_moment(self, _channel_id: int, message: dict[str, Any]) -> None:
        """Забирає community moment — разову нагороду за присутність в ефірі.

        Стрімер вмикає «момент» вручну, і той, хто дивиться саме тоді, отримує
        значок. Пропустити його легко: він живе хвилини й ніде більше не
        повторюється.
        """
        if message.get("type") != "active":
            return
        moment = str(message.get("data", {}).get("moment_id") or "")
        if not moment:
            return
        try:
            await self.graphql(protocol.CLAIM_MOMENT(input={"momentID": moment}))
            log.info("Забрано community moment")
        except Exception as error:
            log.log(TRACE, f"Момент не забрався: {error}")

    async def on_raid(self, _channel_id: int, message: dict[str, Any]) -> None:
        """Приєднується до рейду каналу, який дивимось.

        ⚠️ Це не забирає нас із фарму: рейд означає, що канал уже закінчив
        ефір, тобто дропи там усе одно припинились. Приєднання дає нагороду за
        участь і нічого не коштує.
        """
        if message.get("type") not in ("raid_go_v2", "raid_update_v2"):
            return
        raid = str(message.get("raid", {}).get("id") or "")
        if not raid or raid == self._joined_raid:
            return
        self._joined_raid = raid
        try:
            await self.graphql(protocol.JOIN_RAID(input={"raidID": raid}))
            log.info("Приєднались до рейду")
        except Exception as error:
            log.log(TRACE, f"До рейду не приєднались: {error}")

    # ================================================================ перегляд

    def _counts_here(self, campaign: Campaign, channel: Channel) -> bool:
        """Чи зараховує ця кампанія перегляд цього каналу.

        Ширше за `campaign.farmable(channel)`: турнірна трансляція стоїть у
        категорії гри, а зараховується в кампанію події («Special Events»), і
        за категорією та кампанія не проходить. Але канал є в її списку — а це
        і є дозвіл Twitch. Без цього детектор застою не бачив хвилин, які
        реально набігали: 15.08 EWC Platinum ріс щохвилини, а тривога била
        кожні десять.
        """
        return campaign.farmable(channel) or (
            channel in campaign.channels
            and campaign.farmable(channel, ignore_channel_state=True)
        )

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
            for campaign in self.campaigns if self._counts_here(campaign, channel)
            for drop in campaign.all_drops
            if drop.farmable(channel, ignore_channel_state=True)
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
            if await channel.report_watching():
                self._note_delivery_ok(channel)
            else:
                self._note_delivery_failed(channel)

            # даємо Twitch час відзвітувати самому
            await asyncio.sleep(max(
                0.0, PROGRESS_GRACE.total_seconds() - (monotonic() - sent_at),
            ))
            # Цикл HLS тепер іде кожні 20 с. Домальована «хвилина» на кожному
            # проході потроїла б прогрес; показуємо лише підтверджене Twitch.
            await self._confirm_progress(channel)
            if self._refresh_completed_drops(channel):
                continue
            self._check_stall(before, channel)

            self._restart_watch.clear()
            waited = monotonic() - sent_at
            await sleep_unless(self._restart_watch, max(0.0, period - waited))

    def _refresh_completed_drops(self, channel: Channel, *, now: float | None = None) -> bool:
        """Перечитати серверний стан завершеного дропа навіть без PubSub."""
        if self._paused or self.watching.peek(None) is not channel:
            return False
        if self._stage in (Stage.LOAD_INVENTORY, Stage.QUIT, Stage.RESTART):
            return False
        clock = monotonic() if now is None else now
        completed = [
            drop
            for campaign in self.campaigns if self._counts_here(campaign, channel)
            for drop in campaign.all_drops
            if not drop.taken and drop.required_minutes > 0
            and drop.counted_minutes >= drop.required_minutes
            and clock >= self._completed_drop_refresh.get(drop.id, 0.0)
        ]
        if not completed:
            return False
        for drop in completed:
            self._completed_drop_refresh[drop.id] = clock + COMPLETED_DROP_REFRESH
            # self у кеші деталей може ще казати «не забрано». Для цього
            # дропа потрібні свіжі self і gameEventDrops з інвентаря.
            self._campaign_details_cache.pop(drop.campaign.id, None)
        log.info(
            "Дроп досяг потрібних хвилин — перевіряю видачу в інвентарі: "
            + ", ".join(drop.name for drop in completed)
        )
        self.stop_watching()
        self.go(Stage.LOAD_INVENTORY)
        return True

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
        if not session["dropID"]:
            # ⚠️ Сесія є, дропа в ній немає — Twitch зарахував хвилину, але
            # ЖОДНОМУ дропу. Майже завжди це канал без увімкнених дропів:
            # стрімер грає потрібну гру, а нагород не роздає.
            #
            # Домальовувати тут не можна, і це не педантизм. 18–21.10 саме
            # ця гілка йшла в `_estimate_progress` щохвилини: лічильник ріс
            # 1, 2, 3 … 11, у вікні було видно рух, а на боці Twitch стояв
            # нуль. 21.10 о 23:08 Twitch уперше віддав справжній дроп, і
            # намальовані 11 хвилин обвалились на 1. Людина два дні бачила
            # фарм, якого не було, — і дізналась про це лише тому, що
            # прогрес стрибнув назад.
            self._warn_no_drop(channel)
            return True
        drop = self._drops.get(session["dropID"])
        if drop is None:
            log.log(TRACE, f"Twitch звітує невідомий нам дроп {session['dropID']}")
            return False
        if drop.taken:
            # Кілька секунд після клейму Twitch віддає щойно забраний дроп як
            # поточний: його сесія перемикається на наступний із запізненням.
            # Це нормальний стан, а не чужий перегляд — чекаємо наступного
            # підтвердження без непідтверджених хвилин.
            log.log(TRACE, f"Twitch ще звітує забраний дроп «{drop.name}»")
            return False
        if not drop.farmable(channel):
            if channel in drop.campaign.channels:
                # Той самий канал роздає кілька кампаній, а сесія дропа в Twitch
                # одна: після клейму він перемкнувся на сусідню й рахує тепер їй.
                # Наш дроп тут не росте — і домальовувати йому хвилини наосліп
                # не можна, бо це вигадка: 15.08 так намалювалось 25 хвилин, яких
                # на боці Twitch не існувало. Записуємо правду про ту кампанію,
                # яку він насправді рахує, і кажемо про це прямо.
                drop.set_counted(session["currentMinutesWatched"])
                self._counted_elsewhere = drop.campaign.game.name
                log.log(
                    TRACE,
                    f"{channel.name} зараховується в «{drop.campaign.game.name}», "
                    f"не в те, що ми фармимо"
                )
                return True
            # Twitch рахує кампанію, до якої наш канал не належить — майже
            # напевно цим акаунтом хтось дивиться Twitch вручну
            here = f"{channel.name} ({channel.game})" if channel.game else channel.name
            log.warning(
                f"Twitch зараховує «{drop.campaign.game.name}», "
                f"а ми дивимось {here} — схоже на паралельний перегляд"
            )
            return False
        self._counted_elsewhere = ""
        drop.set_counted(session["currentMinutesWatched"])
        return True

    def _warn_no_drop(self, channel: Channel) -> None:
        """Twitch рахує перегляд, але не нараховує його жодному дропу.

        Кажемо один раз на канал: повторювати щохвилини — значить залити
        журнал так, що в ньому не знайдеться нічого іншого. Але змовчати теж
        не можна: ззовні це виглядає як «програма фармить», а насправді
        години йдуть у нікуди.
        """
        told: set[str] = getattr(self, "_no_drop_told", set())
        if channel.name in told:
            return
        told.add(channel.name)
        self._no_drop_told = told
        hint = (
            ""
            if self.verify_drops_enabled
            else " Увімкніть «перевіряти дропи на каналі» в налаштуваннях —"
                 " інакше канали беруться за грою, без перевірки нагород."
        )
        log.warning(
            f"{channel.name}: Twitch зараховує перегляд, але не нараховує його "
            f"жодному дропу — схоже, на каналі дропи вимкнені.{hint}"
        )


    def _note_delivery_failed(self, channel: Channel) -> None:
        current = getattr(self, "_delivery_failures", 0)
        failures, should_bench = note_delivery_failed(channel.name, current, self.events)
        self._delivery_failures = failures
        if should_bench:
            self.bench(channel)
            self._restart_watch.set()

    def _note_delivery_ok(self, channel: Channel) -> None:
        current = getattr(self, "_delivery_failures", 0)
        self._delivery_failures = note_delivery_ok(channel.name, current, self.events)

    def _check_stall(self, before: int | None, channel: Channel,
                     *, now: float | None = None) -> None:
        after = self._progress_mark()
        detector = getattr(self, "stall_detector", None)
        if detector is None:
            detector = StallDetector(
                stall_since=getattr(self, "_stall_since", None),
                stall_alerted=getattr(self, "_stall_alerted", False),
            )
        else:
            detector.stall_since = getattr(self, "_stall_since", detector.stall_since)
            detector.stall_alerted = getattr(self, "_stall_alerted", detector.stall_alerted)

        detector.check(
            before=before,
            after=after,
            channel_name=channel.name,
            events=self.events,
            counted_elsewhere=getattr(self, "_counted_elsewhere", ""),
            now=now,
        )
        self._stall_since = detector.stall_since
        self._stall_alerted = detector.stall_alerted

    def _maybe_watch_protocol(self) -> None:
        """Раз на добу — і у фоні: перевірка не має затримувати читання інвентаря."""
        if not self.settings.check_updates:
            # та сама галочка, що й для оновлень: людина сказала «не ходи в
            # мережу без потреби» — сторожа теж додаткові запити
            return
        now = monotonic()
        if self._protocol_checked and now - self._protocol_checked < PROTOCOL_PROBE_EVERY:
            return
        self._protocol_checked = now
        self._tasks.launch(self._watch_protocol())

    def _probe_variables(self) -> dict[str, dict[str, Any]]:
        channels_map = getattr(self, "channels", None)
        fallback = next(iter(channels_map.values()), None) if channels_map else None
        watching = getattr(self, "watching", None)
        watching_ch = watching.peek(None) if watching is not None else None
        return build_probe_variables(
            identity=getattr(self, "identity", None),
            campaigns=getattr(self, "campaigns", ()),
            watching_channel=watching_ch,
            fallback_channel=fallback,
        )

    async def probe_protocol(self) -> tuple[list[str], list[str]]:
        return await probe_queries(
            self.graphql, self._probe_variables(), pause=PROTOCOL_PROBE_PAUSE,
        )


    async def _watch_protocol(self) -> None:
        """Раз на добу питає, чи живі наші запити.

        Хеші persisted-запитів Twitch колись зміняться, і без цієї перевірки це
        виглядало б як безкінечні самоперезапуски ядра з невиразною причиною —
        день на розбирання.
        """
        if not self.campaigns:
            return
        dead, skipped = await self.probe_protocol()
        if skipped:
            log.log(TRACE, f"Не перевірено (немає стану): {skipped}")
        if not dead:
            log.log(TRACE, "Запити Twitch на місці")
            return
        checked = len(protocol.READ_ONLY_QUERIES) - len(skipped)
        storm = checked > 1 and len(dead) == checked
        if storm:
            log.warning(
                "Twitch не знає жодного з наших запитів — схоже на скид кешу "
                "на його боці, а не на зміну хешів"
            )
        else:
            log.error(
                f"Twitch не знає запитів: {', '.join(dead)}. "
                f"Хеші в protocol.py треба оновити"
            )
        self.events.emit(ProtocolStale(operations=tuple(dead), storm=storm))

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
            self.events.status(t("status_paused"))
            self.go(Stage.IDLE)
        elif kind is CommandType.RESUME:
            self._paused = False
            self.go(Stage.LOAD_INVENTORY)
        elif kind is CommandType.RELOAD:
            self._campaign_details_cache.clear()
            self.go(Stage.LOAD_INVENTORY)
        elif kind is CommandType.SHUTDOWN:
            self.request_stop()
        elif kind is CommandType.REBOOT:
            self.reboot_requested = True
            self.say(t("say_reboot"))
            self.request_stop()
        elif kind is CommandType.APPLY_UPDATE:
            self._tasks.launch(self._apply_update())
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
                    self.say(t("say_switch_bad", name=channel.name))
                return
        self.say(t("say_switch_missing", wanted=wanted))

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
                # Людині — сам текст, без назви класу винятку: «ApiError:» на
                # початку рядка нічого їй не каже, а виглядає як поломка
                # програми. Тип нікуди не дівається — він лишається в
                # `traceback` і в журналі вище. Порожній текст буває у винятків
                # без повідомлення (той же KeyError), тоді тип — усе, що є.
                self.events.emit(MinerError(
                    message=str(error).strip() or type(error).__name__,
                    traceback=describe_exception(error),
                ))
                self.say(t("say_crash", error=type(error).__name__, pause=pause))
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

        self._tasks.launch(self._check_updates())

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
                self.events.status(t("status_stopping"))
                self.events.emit(MinerStopped(reason="Запит користувача"))
                return
            if self._stage is Stage.RESTART:
                raise Restart()

            if self._stage is Stage.IDLE:
                self.events.status(t("status_paused") if self._paused else t("status_waiting"))
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

        # ⚠️ РЯТІВНИЙ ПРІОРИТЕТ, поверх будь-якого режиму. Кампанія, у якій уже
        # намайнено години і в якої тане запас часу, має перебивати решту — бо
        # інакше ці години просто згорять. Саме так 31.08 пропав
        # `Anniversary Store #9 Drop 2` на 206/240: попередження про ризик
        # прийшло, коли рятувати було вже нічого.
        #
        # Стабільне сортування: усе інше впорядкування, вибудуване вище,
        # зберігається всередині обох груп.
        ordered.sort(key=lambda c: not self._needs_rescue(c))

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
            # «Не братися за безнадійне»: кампанія, де жоден дроп уже не
            # встигнути. Саме жоден — інакше ми викидали б і ті, де половина
            # нагород ще досяжна.
            if self.settings.skip_hopeless and campaign.hopeless:
                log.info(
                    f"Пропускаю «{campaign.name}» ({campaign.game.name}): "
                    f"жоден дроп не встигнути до кінця кампанії"
                )
                continue
            if campaign.farmable_before(soon):
                chosen.append(game)

        self.wanted = chosen
        self._full_sweep = True
        self._restart_watch.set()
        self.go(Stage.DROP_CHANNELS)

    async def _check_updates(self) -> None:
        """На старті і далі двічі на добу, поки галочка не знята."""
        await self.updater.check_updates()

    async def _check_updates_once(self) -> None:
        await self.updater.check_updates_once()

    def _update_check_failed(self, error: Exception) -> None:
        updater = getattr(self, "updater", None)
        if updater is None:
            updater = UpdateCoordinator(self)
            self.updater = updater
        updater._update_check_failed(error)

    def _offer_update(self, manifest: Any, items: list) -> None:
        updater = getattr(self, "updater", None)
        if updater is None:
            updater = UpdateCoordinator(self)
            self.updater = updater
        updater.offer_update(manifest, items)

    async def _apply_update(self) -> None:
        updater = getattr(self, "updater", None)
        if updater is None:
            updater = UpdateCoordinator(self)
            self.updater = updater
        await updater.apply_update()



    def _drop_stale_channels(self) -> None:
        self.events.status(t("status_cleanup"))
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
            self.say(t("say_no_campaigns"))
            self.go(Stage.IDLE)

    async def _find_channels(self) -> None:
        self.events.status(t("status_searching"))
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

        # Тільки «стрім піднявся/впав». Зміну гри слухаємо окремо й лише на
        # тому каналі, який дивимось, — це вдвічі збільшує, скільки каналів
        # узагалі влазить у поле зору.
        self.topics.subscribe([
            pubsub.channel_subscription("state", channel_id, self.on_stream_state)
            for channel_id in self.channels
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
        self.events.status(t("status_picking"))
        for channel in sorted(self.channels.values(), key=self.priority_of):
            if self.should_switch_to(channel):
                self.watch(channel)
                self._stage_changed.clear()
                return
        current = self.watching.peek()
        if current is not None and self.can_farm(current):
            self.events.status(t("status_watching", name=current.name))
            self._stage_changed.clear()
            return
        self.say(t("say_no_channel"))
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
