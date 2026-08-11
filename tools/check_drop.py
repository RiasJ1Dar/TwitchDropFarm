"""Незалежна перевірка: що Twitch каже про наші дропи прямо зараз.

Окремий процес, який не бере lock-файл і не заважає працюючому майнеру. Питає Twitch
напряму збереженим токеном — тому це доказ із боку сервера, а не з нашого лічильника.

    python tools/check_drop.py            усі кампанії, доступні акаунту
    python tools/check_drop.py HEAT       лише ті, де в назві гри є «HEAT»
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from core.constants import (
    AUTH_PATH,
    GQL_QUERIES,
    GQL_URL,
    ClientType,
)

from core.toolbox import load_json as json_load


async def fetch(session: aiohttp.ClientSession, headers: dict, op) -> dict:
    async with session.post(str(GQL_URL), json=op, headers=headers) as response:
        return await response.json()


async def main(needle: str = "") -> int:
    auth = json_load(AUTH_PATH, {"access_token": "", "user_id": 0})
    if not auth["access_token"]:
        print("Немає збереженого токена — спершу авторизуйся.")
        return 1

    client = ClientType.ANDROID_APP
    headers = {
        "Accept": "*/*",
        "Client-Id": client.CLIENT_ID,
        "User-Agent": client.USER_AGENT,
        "Authorization": f"OAuth {auth['access_token']}",
        "Origin": str(client.CLIENT_URL),
        "Referer": str(client.CLIENT_URL),
    }

    async with aiohttp.ClientSession() as session:
        inventory = await fetch(session, headers, GQL_QUERIES["Inventory"])
        data = inventory["data"]["currentUser"]["inventory"]
        in_progress = data["dropCampaignsInProgress"] or []
        awarded = data["gameEventDrops"] or []

    def game_name(node: dict) -> str:
        # різні запити Twitch звуть це поле по-різному
        game = node.get("game") or {}
        return game.get("displayName") or game.get("name") or "—"

    print(f"Кампанії в процесі: {len(in_progress)}")
    for campaign in in_progress:
        game = game_name(campaign)
        if needle and needle.lower() not in game.lower():
            continue
        print(f"\n  {game} — {campaign['name']}")
        for drop in campaign["timeBasedDrops"]:
            got = drop.get("self") or {}
            minutes = got.get("currentMinutesWatched", 0)
            required = drop["requiredMinutesWatched"]
            claimed = got.get("isClaimed", False)
            ready = got.get("dropInstanceID")
            if claimed:
                mark = "ОТРИМАНО"
            elif ready:
                mark = "готовий до забору"
            else:
                mark = f"{minutes}/{required} хв"
            print(f"     {drop['name']:<44} {mark}")

    print(f"\nВидані нагороди на акаунті: {len(awarded)}")
    # найсвіжіші — саме там зʼявиться щойно отриманий предмет
    for entry in sorted(awarded, key=lambda b: b["lastAwardedAt"], reverse=True)[:8]:
        benefit = entry.get("benefit") or {}
        print(
            f"  {entry['lastAwardedAt']}  "
            f"{benefit.get('name', '—')}  ({game_name(benefit)})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "")))
