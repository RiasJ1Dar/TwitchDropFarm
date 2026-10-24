"""Регресії: campaigns."""
from __future__ import annotations

import asyncio
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import FarmMode
from core.events import CampaignAppeared, DeadlineRisk
from core.miner import Miner
from core.model import Campaign
from core.seen import SeenCampaigns
from core.toolbox import Game
from tests.core_cases.support import Bus, check, miner, miner_stub


def selection_checks() -> None:
    """Опорна поведінка вибору перед винесенням політик із Miner."""
    print("\n[вибір] Режими й пріоритети кампаній")
    twitch = miner()
    now = datetime.now(timezone.utc)

    def campaign(name, days, slack, *, game=None, linked=True, real=True, hopeless=False):
        return types.SimpleNamespace(
            id=name, name=name,
            game=Game({"id": {"A": "1", "B": "2", "C": "3"}[game or name],
                       "name": game or name}),
            not_started=False, all_drops=[], closes_at=now + timedelta(days=days),
            slack=slack, linked=linked, has_real_item=real, hopeless=hopeless,
            farmable_before=lambda _time: True,
        )

    a, b, c = campaign("A", 2, 1), campaign("B", 1, 4), campaign("C", 3, 2)

    def select(mode, *, priority=(), exclude=(), skip=False, rescue=(), campaigns=None):
        twitch.settings = types.SimpleNamespace(
            farm_mode=mode, priority=list(priority), exclude=list(exclude), skip_hopeless=skip,
        )
        twitch.campaigns = campaigns if campaigns is not None else [a, b, c]
        twitch._needs_rescue = lambda item: item.id in rescue
        asyncio.run(twitch._pick_games())
        return [game.name for game in twitch.wanted]

    check("найближчий дедлайн задає порядок",
          select(FarmMode.SOONEST_END) == ["B", "A", "C"])
    check("tightest fit задає порядок за запасом часу",
          select(FarmMode.TIGHTEST_FIT) == ["A", "C", "B"])
    check("список пріоритету відкидає не названі ігри",
          select(FarmMode.PRIORITY_LIST, priority=("C", "A")) == ["C", "A"])
    check("порожній ручний список означає порожній вибір",
          select(FarmMode.PRIORITY_LIST) == [])
    check("пріоритети враховуються й поверх дедлайну",
          select(FarmMode.SOONEST_END, priority=("C",)) == ["C", "B", "A"])
    b.linked = False
    c.has_real_item = False
    check("linked-only лишає прив'язані кампанії зі справжнім предметом",
          select(FarmMode.LINKED_ONLY, priority=("B",)) == ["A"])
    b.linked = True
    c.has_real_item = True
    check("чорний список ігор діє при виборі",
          select(FarmMode.SOONEST_END, exclude=("B",)) == ["A", "C"])
    a.hopeless = True
    check("вимкнений пропуск дозволяє безнадійну кампанію",
          select(FarmMode.SOONEST_END, skip=False) == ["B", "A", "C"])
    check("увімкнений пропуск відкидає безнадійну кампанію",
          select(FarmMode.SOONEST_END, skip=True) == ["B", "C"])
    a.hopeless = False
    check("рятівний пріоритет випереджає звичайний порядок",
          select(FarmMode.SOONEST_END, rescue=("C",)) == ["C", "B", "A"])
    duplicate = campaign("earlier-A", 0.5, 0.5, game="A")
    check("дві кампанії гри не дублюють гру у виборі",
          select(FarmMode.SOONEST_END, campaigns=[a, b, c, duplicate]) == ["A", "B", "C"])
    check("порожній інвентар дає порожній вибір",
          select(FarmMode.SOONEST_END, campaigns=[]) == [])


def deadline_checks() -> None:
    print("\n[3] Прогноз «встигну / не встигну»")
    now = datetime.now(timezone.utc)

    def campaign(name, *, needs, hours, farmable=True):
        return types.SimpleNamespace(
            id=name, name=name,
            game=types.SimpleNamespace(name="гра"),
            minutes_left=needs,
            closes_at=now + timedelta(hours=hours),
            slack=(hours * 60 / needs) if needs else float("inf"),
            farmable=lambda: farmable,
        )

    fake = miner_stub(events=Bus(), _risk_reported=set(), campaigns=[
        campaign("встигаємо", needs=60, hours=10),
        campaign("не встигаємо", needs=600, hours=2),
        campaign("чужа", needs=600, hours=1, farmable=False),
    ])
    Miner._check_deadlines(fake)
    risky = [c.name for e in fake.events.sent
             if isinstance(e, DeadlineRisk) for c in e.campaigns]
    check("безнадійну помічено", risky == ["не встигаємо"], str(risky))

    before = len(fake.events.sent)
    Miner._check_deadlines(fake)
    check("повторно не повідомляє", len(fake.events.sent) == before)


def watchlist_checks() -> None:
    """Нова кампанія для гри зі списку — новина, а не перемикання.

    Найважливіше тут — перший запуск мовчить. Інакше свіжопоставлена програма
    вистрілила б вісьмома десятками повідомлень про кампанії, що існували й до
    неї, і людина вимкнула б сповіщення назавжди.
    """
    print("\n[3г] Список спостереження")

    folder = Path(tempfile.mkdtemp())

    def campaign(cid, game, name, over=False):
        return types.SimpleNamespace(
            id=cid, name=name, game=types.SimpleNamespace(name=game), over=over,
            running=True, not_started=False, closes_at=datetime.now(timezone.utc),
            taken_count=0, total=2, image="", all_drops=(),
        )

    def box(seen_path, games, campaigns):
        return types.SimpleNamespace(
            settings=types.SimpleNamespace(watch_games=games),
            campaigns=campaigns,
            seen=SeenCampaigns(seen_path),
            events=Bus(),
            _campaign_snapshot=lambda c: types.SimpleNamespace(
                id=c.id, name=c.name, game=c.game.name, total_drops=c.total,
            ),
        )

    # перший запуск: набору немає, тож нічого нового
    path = folder / "seen.json"
    first = box(path, ["Rocket League"], [campaign("a", "Rocket League", "RL x EWC")])
    Miner._check_watchlist(first)
    appeared = [e for e in first.events.sent if isinstance(e, CampaignAppeared)]
    check("перший запуск мовчить", not appeared, str(appeared))
    check("але побачене запам'ятав", path.is_file())

    # друга кампанія тієї ж гри — уже новина
    second = box(path, ["Rocket League"], [
        campaign("a", "Rocket League", "RL x EWC"),
        campaign("b", "Rocket League", "RLCS 2026"),
    ])
    Miner._check_watchlist(second)
    appeared = [e for e in second.events.sent if isinstance(e, CampaignAppeared)]
    check("нова кампанія — подія",
          len(appeared) == 1 and appeared[0].campaigns[0].name == "RLCS 2026",
          str(appeared))

    # ще один прохід без змін — тиша, інакше новина повторювалась би щогодини
    third = box(path, ["Rocket League"], [
        campaign("a", "Rocket League", "RL x EWC"),
        campaign("b", "Rocket League", "RLCS 2026"),
    ])
    Miner._check_watchlist(third)
    check("повторно не сповіщає",
          not [e for e in third.events.sent if isinstance(e, CampaignAppeared)])

    # чужа гра лишається поза увагою
    fourth = box(path, ["Rocket League"], [
        campaign("c", "EVE Online", "Foundation Day"),
    ])
    Miner._check_watchlist(fourth)
    check("гра не зі списку — тиша",
          not [e for e in fourth.events.sent if isinstance(e, CampaignAppeared)])

    # порожній список: сповіщень немає, але побачене все одно запам'ятовуємо —
    # інакше в день, коли людина додасть гру, усе старе стане «новим»
    quiet_path = folder / "quiet.json"
    quiet = box(quiet_path, [], [campaign("d", "EVE Online", "Foundation Day")])
    Miner._check_watchlist(quiet)
    check("порожній список — тиша",
          not [e for e in quiet.events.sent if isinstance(e, CampaignAppeared)])
    check("і все одно запам'ятовує", SeenCampaigns(quiet_path).fresh({"d"}) == set())

    # завершена кампанія — не новина
    over_path = folder / "over.json"
    SeenCampaigns(over_path).remember({"старе"})
    done = box(over_path, ["Rocket League"], [
        campaign("e", "Rocket League", "Вчорашня", over=True),
    ])
    Miner._check_watchlist(done)
    check("завершена кампанія не рахується новиною",
          not [e for e in done.events.sent if isinstance(e, CampaignAppeared)])


def model_cache_checks() -> None:
    print("\n[15] Кеш моделі та пріоритети")

    def payload(*kinds: str, cid: str = "c1") -> dict:
        return {
            "id": cid, "name": "Кампанія",
            "game": {"id": "1", "displayName": "Гра"},
            "self": {"isAccountConnected": True},
            "startAt": "2020-01-01T00:00:00Z",
            "endAt": "2099-01-01T00:00:00Z",
            "status": "ACTIVE",
            "timeBasedDrops": [
                {
                    "id": f"d{n}", "name": f"Дроп {n}",
                    "benefitEdges": [{"benefit": {
                        "id": f"b{n}", "name": "нагорода", "distributionType": kind,
                    }}],
                    "startAt": "2020-01-01T00:00:00Z",
                    "endAt": "2099-01-01T00:00:00Z",
                    "preconditionDrops": None,
                    "requiredMinutesWatched": 60,
                    "self": {"isClaimed": False, "currentMinutesWatched": 0},
                }
                for n, kind in enumerate(kinds)
            ],
        }

    owner = types.SimpleNamespace(cosmetics_wanted=False)
    only_badges = Campaign(owner, payload("BADGE", "EMOTE"), {})
    with_item = Campaign(owner, payload("BADGE", "DIRECT_ENTITLEMENT"), {})

    check("сама косметика — видно", only_badges.only_cosmetics)
    check("є справжній предмет — не косметика", not with_item.only_cosmetics)
    check("справжній предмет знайдено", with_item.has_real_item)
    check("серед значків предмета немає", not only_badges.has_real_item)

    chain = payload("DIRECT_ENTITLEMENT", "DIRECT_ENTITLEMENT")
    chain["timeBasedDrops"][1]["preconditionDrops"] = [{"id": "d0"}]
    dependent = Campaign(owner, chain, {})
    check("залежний дроп чекає видачі попереднього", not dependent.drops["d1"].farmable())
    dependent.drops["d0"].taken = True
    check("виданий попередній дроп відкриває залежний", dependent.drops["d1"].farmable())

    # Обидві властивості кешовані. Кеш живе рівно стільки, скільки об'єкт
    # кампанії, а той створюється наново на кожне читання інвентаря — саме
    # тому кешувати безпечно. Перевіряємо, що нова кампанія рахує заново.
    fresh = Campaign(owner, payload("DIRECT_ENTITLEMENT"), {})
    check("нова кампанія рахує наново, а не з чужого кеша",
          fresh.has_real_item and not fresh.only_cosmetics)

    # ⚠️ «Не братися за безнадійне». Критерій навмисно не `slack < 1`:
    # `Campaign.slack` — це мінімум по дропах, тобто кампанія «не
    # встигається» вже тоді, коли не закривається лише найдовший дроп. Для
    # попередження це правильно, для пропуску фарму — згубно.
    soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    def timed(*needs: int, ends: str = soon) -> dict:
        body = payload(*["DIRECT_ENTITLEMENT"] * len(needs))
        body["endAt"] = ends
        for drop, minutes in zip(body["timeBasedDrops"], needs, strict=True):
            drop["requiredMinutesWatched"] = minutes
            drop["endAt"] = ends
        return body

    # до кінця пів години: 20 хв встигнути, 600 — ні
    doomed = Campaign(owner, timed(600, 900), {})
    partly = Campaign(owner, timed(600, 20), {})
    check("жоден дроп не встигнути — безнадійна", doomed.hopeless)
    check("є досяжна нагорода — не безнадійна", not partly.hopeless,
          f"slack={partly.slack:.2f}")
    check("а ось `slack` таку кампанію вже засуджує",
          partly.slack < 1, str(partly.slack))

    roomy = Campaign(owner, timed(600, 900,
                                  ends="2099-01-01T00:00:00Z"), {})
    check("часу вдосталь — не безнадійна", not roomy.hopeless)
    check("порожня кампанія не вважається безнадійною",
          not Campaign(owner, payload(), {}).hopeless)

    # ⚠️ Головне через кеш: `available_to_me` мусить лишитись живою. Вона
    # питає налаштування, і якби кеш заліз і сюди, галочка «фармити косметику»
    # перестала б діяти до перезапуску.
    check("косметику не беремо, поки не дозволили", not only_badges.available_to_me)
    owner.cosmetics_wanted = True
    check("дозволили косметику — беремо", only_badges.available_to_me)

    # Пріоритети: індекс будується сеттером разом зі списком.
    fake = miner_stub()
    Miner.wanted.fset(fake, [Game({"id": "10", "name": "перша"}),
                            Game({"id": "20", "name": "друга"})])
    first = types.SimpleNamespace(game=Game({"id": "10", "name": "перша"}))
    second = types.SimpleNamespace(game=Game({"id": "20", "name": "друга"}))
    stranger = types.SimpleNamespace(game=Game({"id": "99", "name": "чужа"}))
    nameless = types.SimpleNamespace(game=None)
    check("порядок пріоритетів збережено",
          Miner.priority_of(fake, first) == 0 and Miner.priority_of(fake, second) == 1)
    check("гра поза списком — у кінець", Miner.priority_of(fake, stranger) == 1 << 30)
    check("канал без гри не падає", Miner.priority_of(fake, nameless) == 1 << 30)

    Miner.wanted.fset(fake, [Game({"id": "20", "name": "друга"})])
    check("новий список — новий індекс, без залишків старого",
          Miner.priority_of(fake, second) == 0
          and Miner.priority_of(fake, first) == 1 << 30)
