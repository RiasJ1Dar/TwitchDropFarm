"""Справжній HTTP-транспорт updater: великі заголовки, redirect і криптоперевірки."""
from __future__ import annotations

import asyncio
import hashlib
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import update
from tests.core_cases.support import check


def update_http_checks() -> None:
    async def run():
        body = b"verified executable" * 20000
        digest = hashlib.sha256(body).hexdigest()
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption()).hex()
        public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                              serialization.PublicFormat.Raw)
        payload = update.sign_manifest({"version": "1.3.2", "files": [{
            "path": "TwitchDropFarm.exe", "sha256": digest, "size": len(body),
        }]}, private)
        csp = {"Content-Security-Policy": "default-src 'none'; " + "a" * 20000}

        async def manifest(_request):
            return web.json_response(payload, headers=csp)

        async def redirect(_request):
            return web.Response(status=302, headers={**csp, "Location": "/manifest.json"})

        async def asset(_request):
            return web.Response(body=body, headers=csp)

        async def asset_redirect(_request):
            return web.Response(status=302, headers={**csp, "Location": "/asset"})

        async def tampered(_request):
            return web.json_response({**payload, "version": "1.3.3"}, headers=csp)

        async def oversized(_request):
            return web.json_response(payload, headers={"X-Too-Large": "a" * (65 * 1024)})

        async def corrupted(_request):
            return web.Response(body=b"corrupted", headers=csp)

        app = web.Application()
        for path, handler in (("/manifest.json", manifest), ("/redirect", redirect),
                              ("/asset", asset), ("/asset-redirect", asset_redirect),
                              ("/tampered", tampered), ("/oversized", oversized),
                              ("/corrupted", corrupted)):
            app.router.add_get(path, handler)
        runner = web.AppRunner(app)
        await runner.setup()
        sock = socket.socket()
        try:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            sock.setblocking(False)
            port = sock.getsockname()[1]
            await web.SockSite(runner, sock).start()
            base = f"http://127.0.0.1:{port}"
            with tempfile.TemporaryDirectory() as folder, patch.object(
                update, "MANIFEST_PUBLIC_KEY", public,
            ):
                root = Path(folder)
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    try:
                        async with session.get(base + "/manifest.json"):
                            pass
                    except aiohttp.ClientResponseError:
                        check("старий ліміт відтворює збій на CSP понад 8190 байтів", True)
                    else:
                        check("старий ліміт відтворює збій на CSP понад 8190 байтів", False)
                    direct = await update.fetch_manifest(session, base + "/manifest.json")
                    check("великий CSP дозволяє прочитати підписаний манифест",
                          direct.version == "1.3.2" and direct.files[0].digest == digest)
                    redirected = await update.fetch_manifest(session, base + "/redirect")
                    check("ліміт працює також на redirect перед манифестом",
                          redirected == direct)
                    spec = update.FileSpec("TwitchDropFarm.exe", digest, len(body))
                    item = update.FetchItem(spec, root / spec.path, base + "/asset-redirect")
                    await update.download_item(session, item)
                    check("exe з великими заголовками й redirect завантажено повністю",
                          item.dest.read_bytes() == body)
                    check("успішне завантаження не лишає part",
                          not item.dest.with_suffix(".exe.part").exists())
                    try:
                        await update.fetch_manifest(session, base + "/tampered")
                    except ValueError as error:
                        check("більший HTTP-ліміт не вимикає Ed25519",
                              "підпис" in str(error))
                    else:
                        check("більший HTTP-ліміт не вимикає Ed25519", False)
                    try:
                        await update.fetch_manifest(session, base + "/oversized")
                    except aiohttp.ClientResponseError:
                        check("заголовки понад 64 KiB далі відхиляються", True)
                    else:
                        check("заголовки понад 64 KiB далі відхиляються", False)
                    bad = update.FetchItem(spec, root / "bad.exe", base + "/corrupted")
                    try:
                        await update.download_item(session, bad)
                    except ValueError as error:
                        check("пошкоджений exe відхиляється за SHA256", "хеш" in str(error))
                    else:
                        check("пошкоджений exe відхиляється за SHA256", False)
                    check("після невдалого SHA256 немає exe чи part",
                          not bad.dest.exists() and not bad.dest.with_suffix(".exe.part").exists())
        finally:
            await runner.cleanup()
            sock.close()

    asyncio.run(run())
