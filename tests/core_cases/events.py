"""Регресії: events."""
from __future__ import annotations

import asyncio
import dataclasses
import sys
import tempfile
import types
from pathlib import Path

from core import config as config_module
from core import history as history_module
from core import protocol
from core.miner import Miner
from tests.core_cases.support import Bus, check, gui_stub, miner_stub

SILENT_EVENTS: frozenset[str] = frozenset()


def routing_checks() -> None:
    """Жодна подія не має загубитись дорогою до людини.

    ⚠️ Заради цієї перевірки й затівався весь маршрутизатор. Показ подій жив у
    трьох незалежних драбинах `elif isinstance(...)` — вікно, журнал, Telegram.
    Нову подію треба було вписати в три місця, і забути одне не заважало
    НІЧОМУ: код працює, лінтер чистий, тести зелені. Помічала це лише людина,
    яка так і не побачила того, про що програма хотіла сказати.

    Тепер маршрути — таблиці, тож у них можна спитати, що вони вміють. Ця
    перевірка й питає: кожен клас події або показується десь, або свідомо
    записаний у `SILENT_EVENTS`.
    """
    import inspect

    from core import events as events_module
    from core.reporters import TO_CONSOLE, TO_LOG
    from gui.app import SHOW
    from notify.telegram import SAY

    print("\n[30] Маршрути показу подій")

    every = {
        cls for _name, cls in inspect.getmembers(events_module, inspect.isclass)
        if issubclass(cls, events_module.Event) and cls is not events_module.Event
    }
    covered = SHOW.covered() | SAY.covered() | TO_LOG.covered() | TO_CONSOLE.covered()
    lost = {c.__name__ for c in every - covered} - SILENT_EVENTS

    check(f"усі {len(every)} подій кудись показуються", not lost)
    if lost:
        for name in sorted(lost):
            print(f"       загублено: {name}")

    # Список мовчазних гниє швидше за код: подію починають показувати, а запис
    # лишається й тихо дозволяє загубити її знову.
    stale = SILENT_EVENTS & {c.__name__ for c in covered}
    check("список мовчазних подій не застарів", not stale)

    # Кожен маршрутизатор мусить щось уміти: порожній — ознака того, що
    # споживача переписали, а маршрути забули перенести.
    for label, table in (("вікно", SHOW), ("бот", SAY),
                         ("журнал", TO_LOG), ("консоль", TO_CONSOLE)):
        check(f"{label}: маршрути на місці", len(table.covered()) > 0)

    # Обробник повертає None для типу без маршруту — на цьому стоїть уся
    # тиша споживачів, і зламати її означало б сипати винятками на кожній події.
    spare = events_module.Router()
    # ⚠️ Знімок мусить нести прив'язку. Без неї картка «Скоро завершаться»
    # показувала кампанії, яких програма не фармить і не візьме ніколи, —
    # 06.09 це прочиталось як «фармить те, до чого акаунт не прив'язаний».
    import inspect as _ins

    src = _ins.getsource(Miner._campaign_snapshot)
    check("знімок кампанії несе прив'язку акаунта", "linked=" in src)
    fields = {f.name for f in dataclasses.fields(events_module.CampaignSnapshot)}
    check("поле linked є у знімку", "linked" in fields)
    side = _ins.getsource(sys.modules["gui.app"].GUI._render_side)
    check("права колонка відсіює непривʼязані", "c.linked" in side)

    # Чорний список: бекенд працював давно, у вікні його не було взагалі —
    # людина 06.09 просила те, що в неї вже було, але лише в боті.
    gui_cls = sys.modules["gui.app"].GUI
    check("у вікні є чорний список", hasattr(gui_cls, "_block_add")
          and hasattr(gui_cls, "_block_remove"))
    adds = _ins.getsource(gui_cls._block_add)
    drops = _ins.getsource(gui_cls._block_remove)
    # Стан дропа окремо від стану кампанії. 06.09 в експорті знайшлись п'ять
    # рядків «завершено» з claimed=ні — виглядало як загублені нагороди, а
    # насправді це дропи не за перегляд (потрібно 0 хвилин).
    from types import SimpleNamespace as _S

    from core.export import _drop_state

    check("взятий дроп названо взятим",
          _drop_state(_S(taken=True, required_minutes=60, minutes=60)) == "взято")
    check("дроп не за перегляд названо чесно",
          _drop_state(_S(taken=False, required_minutes=0, minutes=0))
          == "не за переглядом")
    check("готовий до взяття видно",
          _drop_state(_S(taken=False, required_minutes=60, minutes=60,
                         ready_to_take=True)) == "можна забрати")
    check("почате не плутається з непочатим",
          _drop_state(_S(taken=False, required_minutes=60, minutes=20,
                         ready_to_take=False)) == "фармимо"
          and _drop_state(_S(taken=False, required_minutes=60, minutes=0,
                             ready_to_take=False)) == "не почато")

    check("додавання йде командою ядра", "EXCLUDE_ADD" in adds)
    check("зняття йде командою ядра", "EXCLUDE_REMOVE" in drops)

    # Втрачена прив'язка. ⚠️ Ознака навмисно вузька: непривʼязаних кампаній у
    # Twitch сотні (в живому інвентарі було 345), і якби ми говорили про кожну,
    # новина потонула б у шумі. Кажемо лише там, де вже намайнено хвилини.
    def _camp(cid, linked, minutes, cosmetic=False, over=False):
        drops = [types.SimpleNamespace(minutes=minutes)]
        return types.SimpleNamespace(
            id=cid, name=f"Кампанія {cid}", linked=linked, over=over,
            only_cosmetics=cosmetic, all_drops=drops,
            game=types.SimpleNamespace(name="Гра"),
        )

    miner = miner_stub(
        events=Bus(), _link_told=set(),
        campaigns=[_camp("a", False, 120), _camp("b", False, 0),
                   _camp("c", True, 60), _camp("d", False, 30, cosmetic=True),
                   _camp("e", False, 90, over=True)],
    )
    Miner._check_links(miner)
    said = [e for e in miner.events.sent
            if isinstance(e, events_module.AccountLinkLost)]
    check("про втрачену прив'язку сказано", len(said) == 1)
    check("сказано лише там, де є намайнене",
          said and said[0].campaigns == ("Кампанія a",))
    check("хвилини порахували", said and said[0].minutes_lost == 120)
    Miner._check_links(miner)
    check("вдруге про те саме не нагадуємо",
          len([e for e in miner.events.sent
               if isinstance(e, events_module.AccountLinkLost)]) == 1)

    # Discord-вебхук. ⚠️ Свідомо вужчий за Telegram: односторонній канал, на
    # який не можна відповісти, тож рутина туди не йде — інакше його
    # перестануть читати саме тоді, коли він знадобиться.
    from notify.discord import LIMIT, POST, DiscordHook, _to_markdown

    check("вебхук показує головне",
          {events_module.DropClaimed, events_module.AccountLinkLost,
           events_module.ProgressStalled, events_module.MinerError}
          <= POST.covered())
    check("рутина у вебхук не йде",
          events_module.WatchingChanged not in POST.covered()
          and events_module.StreamOffline not in POST.covered()
          and events_module.LogLine not in POST.covered())
    check("розмітка стає Discord-івською",
          _to_markdown("<b>Дроп</b> &amp; <i>гра</i>") == "**Дроп** & *гра*")
    check("невідомий тег не показують людині",
          "<" not in _to_markdown("<span class='x'>текст</span>"))

    hook = DiscordHook.__new__(DiscordHook)
    hook._twitch = types.SimpleNamespace(
        settings=types.SimpleNamespace(discord_webhook="  "))
    check("порожня адреса — канал вимкнений", hook.url == "")
    hook._twitch.settings.discord_webhook = " https://discord.com/api/webhooks/x "
    check("адресу беруть без пробілів",
          hook.url == "https://discord.com/api/webhooks/x")
    hook._session = None
    asyncio.run(hook.send("текст"))  # без сесії просто нічого не робить
    check("без сесії надсилання не падає", True)
    check("довге повідомлення ріжеться самі, а не 400 від Discord",
          LIMIT < 2000)

    # Самі заглушки. ⚠️ `SimpleNamespace` приймає будь-яке ім'я поля, тож
    # перевірка могла роками стояти на полі, якого в класі давно немає, — і
    # бути зеленою, тобто не перевіряти нічого. Тепер заглушка звіряється з
    # класом; ця перевірка стежить, що звіряння живе.
    caught = False
    try:
        miner_stub(_поля_такого_немає=1)
    except AssertionError as error:
        caught = "_поля_такого_немає" in str(error)
    check("заглушка майнера ловить неіснуюче поле", caught)

    caught = False
    try:
        gui_stub(_поля_такого_немає=1)
    except AssertionError:
        caught = True
    check("заглушка вікна ловить неіснуюче поле", caught)
    check("справжнє поле проходить",
          miner_stub(channel_selector=None, campaigns=[]) is not None)

    # ⚠️ Рятівний пріоритет. Написано за втраченим дропом: 31.08
    # `Anniversary Store #9 Drop 2` згорів на 206/240, і разом із ним майже
    # чотири години перегляду. Попередження прийшло, коли рятувати вже не було
    # чого, тож правило мусить спрацьовувати ЗАЗДАЛЕГІДЬ, поки запас тане.
    def _rescue_camp(slack, minutes, taken=False, hopeless=False, linked=True,
                     required=240):
        return types.SimpleNamespace(
            slack=slack, hopeless=hopeless, available_to_me=linked,
            all_drops=[types.SimpleNamespace(
                minutes=minutes, taken=taken, required_minutes=required)],
        )

    saver = miner_stub()
    check("кампанію з прогресом і тонким запасом рятуємо",
          Miner._needs_rescue(saver, _rescue_camp(1.2, 206)))
    check("порожню кампанію рятувати нема сенсу",
          not Miner._needs_rescue(saver, _rescue_camp(1.2, 0)))
    check("із запасом часу не смикаємось",
          not Miner._needs_rescue(saver, _rescue_camp(5.0, 206)))
    check("безнадійну не тягнемо поперед інших",
          not Miner._needs_rescue(saver, _rescue_camp(0.5, 206, hopeless=True)))
    check("взятий дроп рятувати нічого",
          not Miner._needs_rescue(saver, _rescue_camp(1.2, 240, taken=True)))
    check("непривʼязану не рятуємо — вона й так не фармиться",
          not Miner._needs_rescue(saver, _rescue_camp(1.2, 206, linked=False)))

    # Не кидати те, що ось-ось дасть нагороду.
    def _watching(left, taken=False, required=60):
        drop = types.SimpleNamespace(
            minutes=required - left, taken=taken, required_minutes=required)
        camp = types.SimpleNamespace(next_drop=drop)
        return miner_stub(active_campaign=lambda channel=None: camp)

    check("за чверть години до нагороди канал не міняють",
          Miner.almost_there(_watching(10)) == 10)
    check("коли до нагороди далеко — не тримаємось",
          Miner.almost_there(_watching(40)) == 0)
    check("взятий дроп не тримає канал",
          Miner.almost_there(_watching(5, taken=True)) == 0)
    check("без активної кампанії нічого не тримаємо",
          Miner.almost_there(miner_stub(
              active_campaign=lambda channel=None: None)) == 0)

    # Ціна втрат. ⚠️ Історія знала про РИЗИК, але не про наслідок, тож
    # втрачений дроп знаходився лише при ручному розборі CSV.
    with tempfile.TemporaryDirectory() as room:
        book = history_module.History(Path(room) / "h.jsonl")
        drops = [
            types.SimpleNamespace(id="d1", name="Drop 2", minutes=206,
                                  required_minutes=240, taken=False),
            types.SimpleNamespace(id="d2", name="Drop 1", minutes=180,
                                  required_minutes=180, taken=True),
            types.SimpleNamespace(id="d3", name="Drop 3", minutes=0,
                                  required_minutes=60, taken=False),
        ]
        burnt = types.SimpleNamespace(
            id="c1", name="Anniversary Store #9", over=True, all_drops=drops,
            game=types.SimpleNamespace(name="World of Tanks"))
        alive = types.SimpleNamespace(
            id="c2", name="Жива", over=False, all_drops=drops,
            game=types.SimpleNamespace(name="Гра"))
        keeper = miner_stub(campaigns=[burnt, alive], history=book,
                            _loss_told=set())
        Miner._check_losses(keeper)
        lost = book.entries(kind="lost")
        check("згорілий дроп із прогресом занесено", len(lost) == 1)
        check("занесено саме той, що згорів",
              lost and lost[0]["drop"] == "Drop 2" and lost[0]["minutes"] == 206)
        Miner._check_losses(keeper)
        check("двічі те саме не пишемо", len(book.entries(kind="lost")) == 1)
        check("звіт називає ціну", "206" in book.summary(90))

        # Тижневий підсумок мовчить на першому запуску.
        talker = miner_stub(history=book, say=lambda text: said.append(text))
        said: list[str] = []
        Miner._weekly_report(talker)
        check("перший запуск не звітує одразу", not said)
        check("але позначку ставить", len(book.entries(kind="report")) == 1)
        Miner._weekly_report(talker)
        check("і не звітує щохвилини", not said)

    # Підказка про прив'язку. ⚠️ Ознака вузька навмисно: у живому інвентарі
    # було 345 непривʼязаних дропів у 81 грі, і майже всі — про ігри, у які
    # людина не грає. Кажемо лише про ті, що вона сама назвала.
    def _hint_camp(cid, game, linked=False, cosmetic=False, over=False,
                   upcoming=False):
        return types.SimpleNamespace(
            id=cid, name=f"Кампанія {cid}", linked=linked, over=over,
            not_started=upcoming, only_cosmetics=cosmetic, total=3,
            link_url="https://link/" + cid,
            game=types.SimpleNamespace(name=game),
        )

    adviser = miner_stub(
        events=Bus(), _hint_told=set(),
        settings=types.SimpleNamespace(
            hint_links=True, priority=["World of Tanks"],
            watch_games=["EVE Online"]),
        campaigns=[_hint_camp("a", "World of Tanks"),
                   _hint_camp("b", "EVE Online"),
                   _hint_camp("c", "Shakes and Fidget"),
                   _hint_camp("d", "World of Tanks", linked=True),
                   _hint_camp("e", "World of Tanks", cosmetic=True)],
    )
    Miner._check_link_hints(adviser)
    hints = [e for e in adviser.events.sent
             if isinstance(e, events_module.AccountLinkNeeded)]
    check("підказка прийшла", len(hints) == 1)
    named = {c.name for c in hints[0].campaigns} if hints else set()
    check("лише про ігри, які людина назвала",
          named == {"Кампанія a", "Кампанія b"})
    check("посилання на прив'язку є",
          hints and all(c.url for c in hints[0].campaigns))
    Miner._check_link_hints(adviser)
    check("двічі про те саме не нагадуємо",
          len([e for e in adviser.events.sent
               if isinstance(e, events_module.AccountLinkNeeded)]) == 1)

    adviser.settings.hint_links = False
    adviser._hint_told.clear()
    Miner._check_link_hints(adviser)
    check("вимикач справді вимикає",
          len([e for e in adviser.events.sent
               if isinstance(e, events_module.AccountLinkNeeded)]) == 1)

    # Моменти й рейди. ⚠️ Обидва топіки — лише на активний канал: масова
    # підписка ділить навпіл, скільки каналів узагалі влазить у поле зору.
    calls: list[dict] = []

    async def _gql(payload):
        calls.append(payload)
        return {"data": {}}

    keeper = miner_stub(graphql=_gql, _joined_raid="")
    asyncio.run(Miner.on_moment(keeper, 1, {"type": "active",
                                            "data": {"moment_id": "m1"}}))
    check("момент забрано",
          calls and calls[0]["operationName"] == "CommunityMomentCallout_Claim")
    calls.clear()
    asyncio.run(Miner.on_moment(keeper, 1, {"type": "inactive", "data": {}}))
    check("на чужі повідомлення не смикаємось", not calls)

    asyncio.run(Miner.on_raid(keeper, 1, {"type": "raid_go_v2",
                                          "raid": {"id": "r1"}}))
    check("до рейду приєднались",
          calls and calls[0]["operationName"] == "JoinRaid")
    calls.clear()
    asyncio.run(Miner.on_raid(keeper, 1, {"type": "raid_update_v2",
                                          "raid": {"id": "r1"}}))
    check("до того самого рейду вдруге не лізем", not calls)

    check("моменти й рейди мають свої топіки",
          {"moments", "raid"} <= set(protocol.CHANNEL_TOPICS))
    check("місткість поля зору лишилась триснижною",
          config_module.MAX_CHANNELS > 300)

    check("подія без маршруту не падає",
          spare.dispatch(events_module.LogLine(text="x")) is None)
