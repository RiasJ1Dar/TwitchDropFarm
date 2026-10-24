"""Вибір ігор із готового знімка кампаній: без мережі, подій і читання годинника."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from core.config import FarmMode

if TYPE_CHECKING:
    from core.toolbox import Game

SELECTION_LOOKAHEAD = timedelta(hours=1)


@dataclass(frozen=True)
class CampaignCandidate:
    """Значення після claim; придатність модель обчислює на спільний горизонт."""

    id: str
    name: str
    game: Game
    closes_at: datetime
    slack: float
    linked: bool
    has_real_item: bool
    hopeless: bool
    eligible: bool
    rescue: bool


@dataclass(frozen=True)
class SelectionPolicy:
    mode: FarmMode
    priority: tuple[str, ...] = ()
    excluded: frozenset[str] = frozenset()
    skip_hopeless: bool = False


class SkipReason(Enum):
    DUPLICATE_GAME = "duplicate_game"
    EXCLUDED_GAME = "excluded_game"
    OUTSIDE_PRIORITY = "outside_priority"
    NOT_LINKED_ITEM = "not_linked_item"
    HOPELESS = "hopeless"
    NOT_ELIGIBLE = "not_eligible"


@dataclass(frozen=True)
class SkippedCampaign:
    campaign: CampaignCandidate
    reason: SkipReason


@dataclass(frozen=True)
class SelectionResult:
    games: tuple[Game, ...]
    skipped: tuple[SkippedCampaign, ...]
    evaluated_at: datetime


def select_games(candidates: Sequence[CampaignCandidate], policy: SelectionPolicy,
                 *, now: datetime) -> SelectionResult:
    """Стабільний порядок і причини пропуску; вхідні знімки не змінюються."""
    ordered = list(candidates)
    if policy.mode is FarmMode.TIGHTEST_FIT:
        ordered.sort(key=lambda c: c.slack)
    elif policy.mode is not FarmMode.PRIORITY_LIST:
        ordered.sort(key=lambda c: c.closes_at)
    if policy.mode is not FarmMode.LINKED_ONLY:
        # Як і раніше, повтор назви у priority не змінює її першого місця.
        ordered.sort(key=lambda c: (
            policy.priority.index(c.game.name) if c.game.name in policy.priority else 1 << 30
        ))

    # Рятуємо вкладені хвилини, зберігаючи звичайний порядок усередині груп.
    # 31.08 без цього згорів Anniversary Store #9 Drop 2 на 206/240.
    ordered.sort(key=lambda c: not c.rescue)

    chosen: list[Game] = []
    skipped: list[SkippedCampaign] = []
    for campaign in ordered:
        game = campaign.game
        reason: SkipReason | None = None
        if game in chosen:
            reason = SkipReason.DUPLICATE_GAME
        elif game.name in policy.excluded:
            reason = SkipReason.EXCLUDED_GAME
        elif policy.mode is FarmMode.PRIORITY_LIST and game.name not in policy.priority:
            reason = SkipReason.OUTSIDE_PRIORITY
        elif policy.mode is FarmMode.LINKED_ONLY and not (
            campaign.linked and campaign.has_real_item
        ):
            reason = SkipReason.NOT_LINKED_ITEM
        elif policy.skip_hopeless and campaign.hopeless:
            reason = SkipReason.HOPELESS
        elif not campaign.eligible:
            reason = SkipReason.NOT_ELIGIBLE
        if reason is None:
            chosen.append(game)
        else:
            skipped.append(SkippedCampaign(campaign, reason))
    return SelectionResult(tuple(chosen), tuple(skipped), evaluated_at=now)
