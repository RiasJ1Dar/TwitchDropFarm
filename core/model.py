"""Кампанії, дропи й нагороди.

Модель нічого не знає про інтерфейс: про зміни вона повідомляє власника, а той
уже вирішує, кому це цікаво.

Проти попередньої версії тут навмисно немає поділу на «базовий» і «часовий» дроп:
усі дропи Twitch вимірюються хвилинами перегляду, тож два класи описували одне й
те саме й лише розмазували логіку.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from core import protocol
from core.toolbox import Game, parse_timestamp

if TYPE_CHECKING:
    from core.channels import Channel

log = logging.getLogger("TwitchDrops")

# Twitch дозволяє забрати дроп зі сторінки інвентаря ще добу після кінця кампанії
CLAIM_GRACE = timedelta(hours=24)

_IMAGE_SIZE_SUFFIX = re.compile(r"-\d+x\d+(?=\.(?:jpg|png|gif)$)", re.IGNORECASE)


def _strip_image_size(url: str) -> str:
    """Прибирає суфікс розміру з адреси картинки (".../id-285x380.jpg")."""
    return _IMAGE_SIZE_SUFFIX.sub("", url)


class Owner(Protocol):
    """Те, що модель очікує від власника — не більше."""

    def drop_changed(self, drop: Drop) -> None: ...
    def drop_claimed(self, drop: Drop) -> None: ...
    def show_drop(self, drop: Drop, *, minus_one: bool = False) -> None: ...
    def campaign_exhausted(self, campaign: Campaign) -> None: ...
    async def graphql(self, payload: Any) -> Any: ...
    @property
    def cosmetics_wanted(self) -> bool: ...


class RewardKind(Enum):
    ITEM = "DIRECT_ENTITLEMENT"
    BADGE = "BADGE"
    EMOTE = "EMOTE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, raw: str) -> RewardKind:
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN

    @property
    def cosmetic(self) -> bool:
        return self in (RewardKind.BADGE, RewardKind.EMOTE)


@dataclass(frozen=True, slots=True)
class Reward:
    """Що саме видають за дроп."""

    id: str
    name: str
    kind: RewardKind
    image: str

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Reward:
        body = payload["benefit"]
        return cls(
            id=body["id"],
            name=body["name"],
            kind=RewardKind.parse(body["distributionType"]),
            image=body.get("imageAssetURL", ""),
        )


class Drop:
    """Один дроп: стільки-то хвилин перегляду в обмін на нагороду."""

    __slots__ = (
        "_owner",
        "blind_minutes",
        "campaign",
        "claim_id",
        "closes_at",
        "counted_minutes",
        "id",
        "name",
        "needs",
        "opens_at",
        "required_minutes",
        "rewards",
        "taken",
    )

    def __init__(self, campaign: Campaign, payload: dict[str, Any],
                 awarded: dict[str, datetime]):
        self._owner = campaign._owner
        self.campaign = campaign
        self.id: str = payload["id"]
        self.name: str = payload["name"]
        self.rewards = tuple(Reward.parse(item) for item in payload["benefitEdges"] or ())
        self.opens_at = parse_timestamp(payload["startAt"])
        self.closes_at = parse_timestamp(payload["endAt"])
        # дропи, які треба забрати перед цим
        self.needs: tuple[str, ...] = tuple(
            item["id"] for item in payload["preconditionDrops"] or ()
        )
        self.required_minutes: int = payload["requiredMinutesWatched"]

        mine = payload.get("self") or {}
        self.claim_id: str | None = mine.get("dropInstanceID")
        self.taken: bool = bool(mine.get("isClaimed"))
        self.counted_minutes: int = mine.get("currentMinutesWatched") or 0
        # хвилини, домальовані нами, поки Twitch мовчить про прогрес
        self.blind_minutes: int = 0

        if not self.taken and not mine:
            self.taken = self._infer_taken(awarded)
        if self.taken:
            # забрані дропи звітують неконсистентні хвилини — вирівнюємо
            self.counted_minutes = self.required_minutes

    def _infer_taken(self, awarded: dict[str, datetime]) -> bool:
        """Дроп без self-ребра: судимо за тим, коли видали його нагороди.

        Якщо кожна нагорода дісталась акаунту в проміжок, поки дроп був активним,
        він майже напевно вже забраний.
        """
        stamps = [awarded[r.id] for r in self.rewards if r.id in awarded]
        return bool(stamps) and all(
            self.opens_at <= when < self.closes_at for when in stamps
        )

    # ------------------------------------------------------------ прогрес

    @property
    def minutes(self) -> int:
        return self.counted_minutes + self.blind_minutes

    @property
    def minutes_left(self) -> int:
        return max(0, self.required_minutes - self.minutes)

    @property
    def share(self) -> float:
        """Частка виконаного, від 0 до 1."""
        if self.required_minutes <= 0:
            return 0.0
        return min(1.0, max(0.0, self.minutes / self.required_minutes))

    @property
    def minutes_with_prerequisites(self) -> int:
        """Скільки треба разом із найдовшим ланцюжком передумов."""
        return self.required_minutes + max(
            (self.campaign.drops[i].minutes_with_prerequisites for i in self.needs),
            default=0,
        )

    @property
    def minutes_left_with_prerequisites(self) -> int:
        return self.minutes_left + max(
            (self.campaign.drops[i].minutes_left_with_prerequisites for i in self.needs),
            default=0,
        )

    @property
    def slack(self) -> float:
        """У скільки разів часу до кінця більше, ніж потрібно годин перегляду.

        Менше за 1 — не встигнути. Нескінченність — рахувати нема чого.
        """
        remaining = self.minutes_left_with_prerequisites
        now = datetime.now(timezone.utc)
        if remaining <= 0 or self.required_minutes <= 0 or now >= self.closes_at:
            return math.inf
        return ((self.closes_at - now).total_seconds() / 60) / remaining

    # ------------------------------------------------------------ придатність

    @property
    def prerequisites_done(self) -> bool:
        return all(self.campaign.drops[i].taken for i in self.needs)

    def _basic_fitness(self) -> bool:
        """Умови, не пов'язані з часом і каналом."""
        from core.config import BLIND_MINUTES_LIMIT
        return (
            not self.taken
            and self.required_minutes > 0
            and self.prerequisites_done
            # має власну нагороду або відкриває шлях іншому дропу
            and (bool(self.rewards) or self.id in self.campaign.prerequisite_ids)
            # домалювали стільки, що самим собі більше не віримо
            and self.blind_minutes < BLIND_MINUTES_LIMIT
        )

    @property
    def open_now(self) -> bool:
        return self.opens_at <= datetime.now(timezone.utc) < self.closes_at

    def farmable(self, channel: Channel | None = None,
                 ignore_channel_state: bool = False) -> bool:
        return (
            self._basic_fitness()
            and self.open_now
            and self.campaign.accepts(channel, ignore_channel_state)
        )

    def farmable_before(self, moment: datetime) -> bool:
        """Чи буде сенс у цьому дропі до вказаного моменту."""
        now = datetime.now(timezone.utc)
        return self._basic_fitness() and self.closes_at > now and self.opens_at < moment

    @property
    def ready_to_take(self) -> bool:
        return (
            self.claim_id is not None
            and not self.taken
            and datetime.now(timezone.utc) < self.campaign.closes_at + CLAIM_GRACE
        )

    # ------------------------------------------------------------ зміни стану

    def note_claim_id(self, claim_id: str) -> None:
        self.claim_id = claim_id

    def set_counted(self, minutes: int) -> None:
        """Приймає авторитетне значення від Twitch і підтягує під нього кампанію."""
        delta = minutes - self.counted_minutes
        if delta == 0:
            return
        delta = max(-self.counted_minutes, min(delta, self.required_minutes - self.counted_minutes))
        self.campaign.shift_counted(delta)

    def _apply_delta(self, delta: int) -> None:
        if delta == 0:
            return
        self.counted_minutes = max(
            0, min(self.counted_minutes + delta, self.required_minutes)
        )
        self.blind_minutes = 0
        self._owner.drop_changed(self)

    def add_blind_minute(self, channel: Channel | None) -> bool:
        """Домальовує хвилину, коли Twitch мовчить. True — ліміт довіри вичерпано."""
        from core.config import BLIND_MINUTES_LIMIT
        if not self.farmable(channel):
            return False
        self.blind_minutes += 1
        self._owner.drop_changed(self)
        return self.blind_minutes >= BLIND_MINUTES_LIMIT

    def show(self, *, minus_one: bool = False) -> None:
        self._owner.show_drop(self, minus_one=minus_one)

    def rewards_text(self, separator: str = ", ") -> str:
        return separator.join(r.name for r in self.rewards)

    async def take(self) -> bool:
        """Забирає дроп. True — Twitch підтвердив видачу."""
        if self.taken:
            return True
        if not self.ready_to_take:
            return False
        try:
            answer = await self._owner.graphql(
                protocol.CLAIM_DROP(input={"dropInstanceID": self.claim_id})
            )
            body = answer["data"]
            accepted = (
                not body.get("errors")
                and (result := body.get("claimDropRewards"))
                and result["status"] in ("ELIGIBLE_FOR_ALL", "DROP_INSTANCE_ALREADY_CLAIMED")
            )
        except Exception:
            log.exception(f"Не вдалося забрати дроп {self.id}")
            return False
        if not accepted:
            log.error(f"Twitch не підтвердив видачу дропа {self.id}")
            return False

        self.taken = True
        self.counted_minutes = self.required_minutes
        self.blind_minutes = 0
        self._owner.drop_claimed(self)
        self._owner.drop_changed(self)
        return True

    def __repr__(self) -> str:
        state = "взято" if self.taken else f"{self.minutes}/{self.required_minutes}"
        return f"Drop({self.name!r}, {state})"


class Campaign:
    """Кампанія: гра, вікно часу й набір дропів."""

    __slots__ = (
        "__dict__",
        "_alive",
        "_owner",
        "_reported_done",
        "channels",
        "closes_at",
        "drops",
        "game",
        "id",
        "image",
        "link_url",
        "linked",
        "name",
        "opens_at",
    )

    def __init__(self, owner: Owner, payload: dict[str, Any],
                 awarded: dict[str, datetime]):
        from core.channels import Channel

        self._owner = owner
        self.id: str = payload["id"]
        self.name: str = payload["name"]
        self.game = Game(payload["game"])
        self.linked: bool = payload["self"]["isAccountConnected"]
        self.link_url: str = payload.get("accountLinkURL", "")
        self.image = _strip_image_size(payload["game"].get("boxArtURL", ""))
        self.opens_at = parse_timestamp(payload["startAt"])
        self.closes_at = parse_timestamp(payload["endAt"])
        self._alive = payload["status"] != "EXPIRED"
        self._reported_done = False

        allow = payload.get("allow") or {}
        listed = allow.get("channels") or []
        self.channels: tuple[Channel, ...] = (
            tuple(Channel.from_allowlist(owner, item) for item in listed)
            if listed and allow.get("isEnabled", True) else ()
        )
        self.drops: dict[str, Drop] = {
            item["id"]: Drop(self, item, awarded)
            for item in payload["timeBasedDrops"]
        }

    # ------------------------------------------------------------ стан у часі

    @property
    def running(self) -> bool:
        return self._alive and self.opens_at <= datetime.now(timezone.utc) < self.closes_at

    @property
    def not_started(self) -> bool:
        return self._alive and datetime.now(timezone.utc) < self.opens_at

    @property
    def over(self) -> bool:
        return not self._alive or self.closes_at <= datetime.now(timezone.utc)

    @property
    def moments(self) -> set[datetime]:
        """Миті, коли стан кампанії може змінитися сам собою."""
        edges = {self.opens_at, self.closes_at}
        for drop in self.drops.values():
            edges.update((drop.opens_at, drop.closes_at))
        return edges

    # ------------------------------------------------------------ склад

    @property
    def all_drops(self) -> Iterable[Drop]:
        return self.drops.values()

    @property
    def total(self) -> int:
        return len(self.drops)

    @property
    def taken_count(self) -> int:
        return sum(drop.taken for drop in self.all_drops)

    @property
    def everything_taken(self) -> bool:
        return all(d.taken or d.required_minutes <= 0 for d in self.all_drops)

    @property
    def prerequisite_ids(self) -> frozenset[str]:
        """Дропи, які комусь потрібні як передумова й ще не взяті."""
        return frozenset(
            need for drop in self.all_drops if not drop.taken for need in drop.needs
        )

    @property
    def has_real_item(self) -> bool:
        """Чи дають тут щось, крім значків та емоцій."""
        return any(
            not reward.kind.cosmetic
            for drop in self.all_drops
            for reward in drop.rewards
        )

    @property
    def only_cosmetics(self) -> bool:
        rewards = [r for d in self.all_drops for r in d.rewards]
        return bool(rewards) and all(r.kind.cosmetic for r in rewards)

    @property
    def available_to_me(self) -> bool:
        """Чи взагалі можу я це фармити.

        Кампанія зі значками не потребує прив'язки акаунта — але й потрібна
        далеко не всім, тому вирішує налаштування.
        """
        if self.only_cosmetics:
            return self._owner.cosmetics_wanted
        return self.linked

    @property
    def minutes_left(self) -> int:
        return max(
            (d.minutes_left_with_prerequisites for d in self.all_drops), default=0
        )

    @property
    def slack(self) -> float:
        return min((d.slack for d in self.all_drops), default=math.inf)

    @property
    def share(self) -> float:
        return sum(d.share for d in self.all_drops) / self.total if self.total else 0.0

    @property
    def next_drop(self) -> Drop | None:
        """Дроп, який зараз фактично фармиться: найближчий до завершення."""
        candidates = [d for d in self.all_drops if d.farmable()]
        return min(candidates, key=lambda d: d.minutes_left, default=None)

    # ------------------------------------------------------------ придатність

    def accepts(self, channel: Channel | None = None,
                ignore_channel_state: bool = False) -> bool:
        """Чи зараховує ця кампанія перегляд вказаного каналу."""
        if not (self.available_to_me and self.running):
            return False
        if channel is None:
            return True
        if self.channels and channel not in self.channels:
            return False
        if ignore_channel_state or self.game.any_channel:
            return True
        return channel.game is not None and channel.game == self.game

    def farmable(self, channel: Channel | None = None,
                 ignore_channel_state: bool = False) -> bool:
        return self.accepts(channel, ignore_channel_state) and any(
            d._basic_fitness() and d.open_now for d in self.all_drops
        )

    def farmable_before(self, moment: datetime) -> bool:
        """Чи буде сенс у цій кампанії найближчим часом."""
        return (
            self.available_to_me
            and self._alive
            and self.closes_at > datetime.now(timezone.utc)
            and self.opens_at < moment
            and any(d.farmable_before(moment) for d in self.all_drops)
        )

    # ------------------------------------------------------------ зміни

    def shift_counted(self, delta: int) -> None:
        for drop in self.all_drops:
            drop._apply_delta(delta)
        if (current := self.next_drop) is not None:
            current.show()

    def add_blind_minute(self, channel: Channel) -> None:
        # список, а не генератор: хвилину мають отримати всі дропи, а не лише
        # ті, що трапились до першого True
        exhausted = [drop.add_blind_minute(channel) for drop in self.all_drops]
        if any(exhausted):
            log.warning(
                f"Кампанія «{self.name}» вичерпала ліміт домальованих хвилин"
            )
            self._owner.campaign_exhausted(self)
        if (current := self.next_drop) is not None:
            current.show()

    def mark_reported_done(self) -> bool:
        """True рівно один раз — щоб не повідомляти про завершення двічі."""
        if self._reported_done:
            return False
        self._reported_done = True
        return True

    def __repr__(self) -> str:
        return f"Campaign({self.game}, {self.name!r}, {self.taken_count}/{self.total})"
