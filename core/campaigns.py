"""Аналітика, знімки та відстеження подій кампаній.

Модуль відповідає за:
- формування знімків кампаній для UI та сповіщень (`campaign_snapshot`)
- перевірку ризику дедлайнів (`check_deadlines`)
- сповіщення про нові кампанії зі списку спостереження (`check_watchlist`)
- виявлення втрати прив'язки акаунта (`check_links`)
- реєстрацію згорілих незавершених дропів в історії (`check_losses`)
- підказки щодо необхідності прив'язки для бажаних ігор (`check_link_hints`)
- тижневий звіт про результати фарму (`weekly_report`)
- клас CampaignTracker, що інкапсулює стан повідомлень
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from core.config import RESCUE_SLACK
from core.events import (
    AccountLinkLost,
    AccountLinkNeeded,
    CampaignAppeared,
    CampaignSnapshot,
    DeadlineRisk,
    DropSnapshot,
    EventBus,
    LinkHint,
    RiskSnapshot,
)
from core.history import History
from core.i18n import t
from core.model import Campaign
from core.seen import SeenCampaigns
from core.settings import Settings

log = logging.getLogger("TwitchDrops")


def campaign_snapshot(c: Campaign) -> CampaignSnapshot:
    """Створює незмінний знімок кампанії для передачі через шину подій."""
    return CampaignSnapshot(
        id=c.id,
        name=c.name,
        game=c.game.name,
        active=c.running,
        upcoming=c.not_started,
        expired=c.over,
        ends_at=c.closes_at,
        claimed_drops=c.taken_count,
        total_drops=c.total,
        image=c.image,
        linked=c.available_to_me,
        drops=tuple(
            DropSnapshot(
                id=d.id,
                name=d.name,
                current_minutes=d.minutes,
                required_minutes=d.required_minutes,
                claimed=d.taken,
                can_claim=d.ready_to_take,
                image=d.rewards[0].image if d.rewards else "",
            )
            for d in c.all_drops
        ),
    )


def needs_rescue(campaign: Any) -> bool:
    """Чи горить кампанія, у яку вже вкладено години.

    Три умови разом, і кожна потрібна:
    * є прогрес — рятувати порожню кампанію нема сенсу, вона нічого не втрачає;
    * запас тане (slack менший за поріг) — інакше під «рятунок» потрапило б усе
      підряд і звичайний порядок втратив би сенс;
    * ще можливо встигнути — кампанію, де жоден дроп уже не дотягнути, рятувати пізно.
    """
    if campaign.hopeless or not campaign.available_to_me:
        return False
    if campaign.slack >= RESCUE_SLACK:
        return False
    return any(
        d.minutes > 0 and not d.taken and d.required_minutes > 0
        for d in campaign.all_drops
    )


def check_deadlines(
    campaigns: Sequence[Any],
    events: EventBus | Any,
    reported: set[str],
    *,
    now: datetime | None = None,
) -> list[RiskSnapshot]:
    """Попереджає про кампанії, які вже не встигнути закрити.

    Про кожну кажемо один раз: інвентар перечитується часто, а повторювати
    погану новину щогодини — це лише привчити її не читати.
    """
    current_time = now or datetime.now(timezone.utc)
    risky: list[RiskSnapshot] = []
    for campaign in campaigns:
        if campaign.id in reported:
            continue
        # безнадійною може бути лише та кампанія, яку ми взагалі беремося
        # фармити: чужі й завершені сюди потрапляти не повинні
        if not campaign.farmable() or campaign.slack >= 1:
            continue
        reported.add(campaign.id)
        risky.append(RiskSnapshot(
            id=campaign.id,
            name=campaign.name,
            game=campaign.game.name,
            minutes_needed=campaign.minutes_left,
            minutes_available=max(
                0, int((campaign.closes_at - current_time).total_seconds() // 60)
            ),
        ))
    if not risky:
        return []
    for item in risky:
        log.warning(
            f"Не встигаємо закрити «{item.name}» ({item.game}): треба "
            f"{item.minutes_needed} хв перегляду, лишилось "
            f"{item.minutes_available} хв часу"
        )
    events.emit(DeadlineRisk(campaigns=tuple(risky)))
    return risky


def check_watchlist(
    campaigns: Sequence[Any],
    watch_games: Iterable[str],
    seen: SeenCampaigns | Any,
    events: EventBus | Any,
    snapshot_builder: Callable[[Any], Any] | None = None,
) -> list[Any]:
    """Нова кампанія для гри, за якою людина просила слідкувати.

    Поточний фарм не переривається: майнер тримається свого вибору, а новина
    потрібна, щоб людина могла вирішити сама.
    """
    wanted = {name.strip().lower() for name in watch_games if name.strip()}
    current = {c.id for c in campaigns}
    if not wanted:
        seen.remember(current)
        return []
    first_run = not seen.known
    fresh_ids = seen.fresh(current)
    seen.remember(current)
    if first_run or not fresh_ids:
        return []
    news = [
        c for c in campaigns
        if c.id in fresh_ids and c.game.name.strip().lower() in wanted
        and not c.over
    ]
    if not news:
        return []
    for campaign in news:
        log.warning(
            f"Нова кампанія: «{campaign.name.strip()}» ({campaign.game.name})"
        )
    build_snap = snapshot_builder or campaign_snapshot
    events.emit(CampaignAppeared(
        campaigns=tuple(build_snap(c) for c in news)
    ))
    return news


def check_links(
    campaigns: Sequence[Any],
    events: EventBus | Any,
    told: set[str],
) -> None:
    """Шукає кампанії, де прогрес є, а прив'язки вже немає."""
    lost = [
        c for c in campaigns
        if not c.linked and not c.over and not getattr(c, "only_cosmetics", False)
        and any(d.minutes > 0 for d in c.all_drops)
        and c.id not in told
    ]
    if not lost:
        return
    minutes = sum(d.minutes for c in lost for d in c.all_drops)
    for campaign in lost:
        told.add(campaign.id)
        log.warning(
            f"Прив'язку втрачено: «{campaign.name}» ({campaign.game.name}) — "
            f"намайнені хвилини не перетворяться на нагороду"
        )
    events.emit(AccountLinkLost(
        campaigns=tuple(c.name.strip() for c in lost),
        minutes_lost=minutes,
    ))


def check_losses(
    campaigns: Sequence[Any],
    history: History | Any,
    told: set[str],
) -> None:
    """Записує дропи, які згоріли з уже намайненими хвилинами."""
    for campaign in campaigns:
        if not campaign.over:
            continue
        for drop in campaign.all_drops:
            key = f"{campaign.id}:{drop.id}"
            if key in told:
                continue
            if drop.taken or drop.minutes <= 0 or drop.required_minutes <= 0:
                continue
            told.add(key)
            history.record(
                "lost",
                game=campaign.game.name,
                campaign=campaign.name.strip(),
                drop=drop.name,
                minutes=drop.minutes,
                required=drop.required_minutes,
            )
            log.warning(
                f"Згоріло без нагороди: «{drop.name}» "
                f"({campaign.game.name}) — {drop.minutes}/"
                f"{drop.required_minutes} хв"
            )


def check_link_hints(
    campaigns: Sequence[Any],
    settings: Settings | Any,
    events: EventBus | Any,
    told: set[str],
) -> list[LinkHint]:
    """Каже про дропи, до яких бракує лише прив'язки акаунта."""
    if not getattr(settings, "hint_links", True):
        return []
    priority = getattr(settings, "priority", ())
    watch_games = getattr(settings, "watch_games", ())
    mine = {
        name.strip().lower()
        for name in (*priority, *watch_games)
        if name.strip()
    }
    if not mine:
        return []
    found = [
        c for c in campaigns
        if not c.linked and not c.over and not getattr(c, "not_started", False)
        and not getattr(c, "only_cosmetics", False)
        and c.game.name.strip().lower() in mine
        and c.id not in told
    ]
    if not found:
        return []
    for campaign in found:
        told.add(campaign.id)
    hints = [
        LinkHint(
            name=c.name.strip(),
            game=c.game.name,
            url=getattr(c, "link_url", ""),
            drops=c.total,
        )
        for c in found
    ]
    events.emit(AccountLinkNeeded(campaigns=tuple(hints)))
    return hints


def weekly_report(
    history: History | Any,
    say_func: Callable[[str], None],
    *,
    now: datetime | None = None,
) -> None:
    """Раз на тиждень — короткий підсумок у чат/журнал."""
    marks = history.entries(kind="report")
    current_time = now or datetime.now(timezone.utc)
    if not marks:
        history.record("report")
        return
    raw = str(marks[-1].get("at") or "")
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        history.record("report")
        return
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if current_time - last < timedelta(days=7):
        return
    history.record("report")
    say_func(t("report_weekly") + chr(10) + history.summary(7))


class CampaignTracker:
    """Відстеження стану, дедлайнів, зв'язків та втрат кампаній."""

    def __init__(self, risk_reported: set[str] | None = None) -> None:
        self.risk_reported: set[str] = risk_reported if risk_reported is not None else set()
        self.link_told: set[str] = set()
        self.loss_told: set[str] = set()
        self.hint_told: set[str] = set()

    def check_deadlines(
        self,
        campaigns: Sequence[Any],
        events: EventBus | Any,
        *,
        now: datetime | None = None,
    ) -> list[RiskSnapshot]:
        return check_deadlines(campaigns, events, self.risk_reported, now=now)

    def check_watchlist(
        self,
        campaigns: Sequence[Any],
        watch_games: Iterable[str],
        seen: SeenCampaigns | Any,
        events: EventBus | Any,
        snapshot_builder: Callable[[Any], Any] | None = None,
    ) -> list[Any]:
        return check_watchlist(campaigns, watch_games, seen, events, snapshot_builder)

    def check_links(self, campaigns: Sequence[Any], events: EventBus | Any) -> None:
        check_links(campaigns, events, self.link_told)

    def check_losses(self, campaigns: Sequence[Any], history: History | Any) -> None:
        check_losses(campaigns, history, self.loss_told)

    def check_link_hints(
        self,
        campaigns: Sequence[Any],
        settings: Settings | Any,
        events: EventBus | Any,
    ) -> list[LinkHint]:
        return check_link_hints(campaigns, settings, events, self.hint_told)

    def weekly_report(
        self,
        history: History | Any,
        say_func: Callable[[str], None],
        *,
        now: datetime | None = None,
    ) -> None:
        weekly_report(history, say_func, now=now)

    @staticmethod
    def snapshot(c: Campaign) -> CampaignSnapshot:
        return campaign_snapshot(c)

    @staticmethod
    def needs_rescue(campaign: Any) -> bool:
        return needs_rescue(campaign)
