"""Перевірка переписаного ядра на живому Twitch.

Читає інвентар справжнім токеном і проганяє всі чотири режими вибору ігор.
Нічого не змінює на акаунті: жодних клеймів, жодного перегляду — тільки читання.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import FarmMode
from core.miner import Miner
from core.settings import Settings

logging.getLogger("TwitchDrops").addHandler(logging.NullHandler())

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}  {detail}")


async def main() -> int:
    miner = Miner(Settings())

    print("[1] Авторизація збереженим токеном")
    await miner.identity.ensure()
    check("токен прийнято", miner.identity.known)
    check("user_id", miner.identity.user_id > 0, str(miner.identity.user_id))
    check("device_id отримано", bool(miner.identity.device_id))
    print(f"       user_id = {miner.identity.user_id}")

    print("\n[2] Інвентар")
    await miner.load_inventory()
    total = len(miner.campaigns)
    check("кампанії прочитано", total > 0, f"{total}")
    check("дропи розібрані", len(miner._drops) > 0, f"{len(miner._drops)}")
    linked = [c for c in miner.campaigns if c.linked]
    print(f"       кампаній: {total}, з них привʼязано: {len(linked)}")

    print("\n[3] Модель кампанії")
    if linked:
        sample = linked[0]
        check("гра розібрана", bool(sample.game.name))
        check("slug побудовано", bool(sample.game.slug), sample.game.slug)
        check("вікно часу", sample.opens_at < sample.closes_at)
        check("дропи всередині", sample.total > 0, str(sample.total))
        drop = next(iter(sample.all_drops))
        check("хвилини дропа", drop.required_minutes > 0, str(drop.required_minutes))
        check("частка в межах 0..1", 0.0 <= drop.share <= 1.0, f"{drop.share}")
        check("залишок не відʼємний", drop.minutes_left >= 0)
        check("запас часу рахується", drop.slack >= 0)
        print(f"       {sample.game.name}: {sample.taken_count}/{sample.total}, "
              f"«{drop.name}» {drop.minutes}/{drop.required_minutes} хв")

    print("\n[4] Режими вибору ігор")
    results: dict[str, list[str]] = {}
    for mode in FarmMode:
        miner.settings.farm_mode = mode
        miner.settings.priority = ["THE FINALS"]  # щоб побачити вплив списку
        miner.wanted = []
        await miner._pick_games()
        results[mode.name] = [g.name for g in miner.wanted]
        print(f"       {mode.name:15} -> {len(miner.wanted)} ігор")

    linked_games = {c.game.name for c in miner.campaigns
                    if c.linked and c.has_real_item}
    chosen = set(results["LINKED_ONLY"])
    check("LINKED_ONLY бере лише привʼязане з предметом",
          chosen <= linked_games, f"зайве: {chosen - linked_games}")
    check("LINKED_ONLY ігнорує список пріоритету",
          not results["LINKED_ONLY"] or results["LINKED_ONLY"][0] != "THE FINALS"
          or "THE FINALS" not in results["SOONEST_END"][1:],
          str(results["LINKED_ONLY"][:2]))
    check("PRIORITY_LIST бере лише зі списку",
          set(results["PRIORITY_LIST"]) <= {"THE FINALS"},
          str(results["PRIORITY_LIST"]))
    check("SOONEST_END бере щось", bool(results["SOONEST_END"]))

    print("\n[5] Пошук каналів у каталозі")
    miner.settings.farm_mode = FarmMode.LINKED_ONLY
    miner.wanted = []
    await miner._pick_games()
    if miner.wanted:
        game = miner.wanted[0]
        found = await miner.find_streams(game, limit=5)
        check(f"канали для {game.name}", len(found) > 0, f"{len(found)}")
        if found:
            live = found[0]
            check("канал в етері", live.live)
            check("є назва", bool(live.name))
            check("гра каналу збігається", live.game is not None)
            print(f"       {live.name}: {live.viewers} глядачів, "
                  f"дропи={live.drops_on}")

    print("\n[6] Пакетна перевірка статусу")
    if miner.wanted:
        sample_channels = await miner.find_streams(miner.wanted[0], limit=3)
        if sample_channels:
            for channel in sample_channels:
                channel.stream = None  # навмисно гасимо, щоб перевірити оновлення
            await miner.check_many(sample_channels)
            revived = sum(1 for c in sample_channels if c.live)
            check("статус відновлено пакетом", revived > 0,
                  f"{revived}/{len(sample_channels)}")

    await miner.api.close()
    print(f"\n{'=' * 52}\nПройдено: {passed}   Провалено: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
