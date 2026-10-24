"""Чиста політика вибору без створення Miner або мережевих клієнтів."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from core.config import FarmMode
from core.selection import CampaignCandidate, SelectionPolicy, SkipReason, select_games
from core.toolbox import Game
from tests.core_cases.support import check


def pure_selection_checks() -> None:
    print("\n[політика] Чистий вибір зі знімка")
    now = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)

    def candidate(name, days, slack):
        return CampaignCandidate(
            id=name, name=name, game=Game({"id": str(ord(name)), "name": name}),
            closes_at=now + timedelta(days=days), slack=slack, linked=True,
            has_real_item=True, hopeless=False, eligible=True, rescue=False,
        )

    a, b, c = candidate("A", 2, 1), candidate("B", 1, 4), candidate("C", 3, 2)
    original = [a, b, c]

    def choose(policy, candidates=original):
        return select_games(candidates, policy, now=now)

    def names(result):
        return [game.name for game in result.games]

    soonest = SelectionPolicy(FarmMode.SOONEST_END)
    check("чиста політика обирає за дедлайном", names(choose(soonest)) == ["B", "A", "C"])
    check("чиста політика обирає tightest fit",
          names(choose(SelectionPolicy(FarmMode.TIGHTEST_FIT))) == ["A", "C", "B"])
    check("ручний пріоритет визначає порядок і склад",
          names(choose(SelectionPolicy(FarmMode.PRIORITY_LIST, ("C", "A")))) == ["C", "A"])
    check("порожній ручний список не стає автоматичним вибором",
          not choose(SelectionPolicy(FarmMode.PRIORITY_LIST)).games)
    check("повтор назви зберігає її першу позицію",
          names(choose(SelectionPolicy(FarmMode.PRIORITY_LIST, ("A", "B", "A")))) == ["A", "B"])
    check("linked-only ігнорує ручний порядок",
          names(choose(SelectionPolicy(FarmMode.LINKED_ONLY, ("C",)))) == ["B", "A", "C"])
    tied = [replace(item, closes_at=now) for item in (c, a, b)]
    check("рівні ключі зберігають вхідний порядок", names(choose(soonest, tied)) == ["C", "A", "B"])
    rescued = [a, b, replace(c, rescue=True)]
    check("рятівний пріоритет зберігає порядок решти",
          names(choose(soonest, rescued)) == ["C", "B", "A"])
    check("вхідний список не пересортовано", original == [a, b, c])
    check("час рішення передано явно", choose(soonest).evaluated_at == now)
    check("порожній знімок дає порожнє рішення", not choose(soonest, []).games)

    for label, candidates, policy, expected in (
        ("дубль гри", [a, replace(a, id="another")], soonest, SkipReason.DUPLICATE_GAME),
        ("чорний список", [a], SelectionPolicy(FarmMode.SOONEST_END, excluded=frozenset({"A"})),
         SkipReason.EXCLUDED_GAME),
        ("не в пріоритеті", [a], SelectionPolicy(FarmMode.PRIORITY_LIST),
         SkipReason.OUTSIDE_PRIORITY),
        ("без прив'язки", [replace(a, linked=False)], SelectionPolicy(FarmMode.LINKED_ONLY),
         SkipReason.NOT_LINKED_ITEM),
        ("без предмета", [replace(a, has_real_item=False)], SelectionPolicy(FarmMode.LINKED_ONLY),
         SkipReason.NOT_LINKED_ITEM),
        ("безнадійна", [replace(a, hopeless=True)], SelectionPolicy(FarmMode.SOONEST_END,
         skip_hopeless=True), SkipReason.HOPELESS),
        ("непридатна", [replace(a, eligible=False)], soonest, SkipReason.NOT_ELIGIBLE),
    ):
        result = choose(policy, candidates)
        check("причина пропуску: " + label, len(result.skipped) == 1
              and result.skipped[0].reason is expected)
    check("безнадійні типово дозволені",
          names(choose(soonest, [replace(a, hopeless=True)])) == ["A"])
    first_unavailable = [replace(a, eligible=False), replace(a, id="second")]
    result = choose(soonest, first_unavailable)
    check("пропущена кампанія не займає місце гри", names(result) == ["A"]
          and result.skipped[0].reason is SkipReason.NOT_ELIGIBLE)
