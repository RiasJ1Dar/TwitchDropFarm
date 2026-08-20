"""Зібрати manifest.json для релізу: шлях + SHA-256 кожного файлу в теці.

Запуск (з кореня проєкту):

    env\\Scripts\\python.exe tools\\write_manifest.py dist\\TwitchDropFarm 1.0.4

У реліз кладуть manifest.json і кожен файл ще раз під іменем свого хеша —
тоді клієнт качає лише блоби, яких локально ще немає.

Третім аргументом можна дати базову адресу:

    … write_manifest.py dist 1.0.4 https://github.com/O/R/releases/download/v1.0.4/

Тоді кожен файл отримує прямий `url`, а блоби не створюються. Для збірки з
одного `.exe` це вдвічі менший реліз: інакше той самий двадцятимегабайтний
файл лежав би там двічі — під своїм іменем і під іменем свого хеша.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.toolbox import force_utf8_console  # noqa: E402
from core.update import build_manifest, file_sha256, sign_manifest  # noqa: E402


def main() -> int:
    # Консоль CI не в UTF-8, і кириличний рядок падає з UnicodeEncodeError на
    # першому ж друку — збірка релізу зривалась саме тут, уже маючи готовий .exe.
    force_utf8_console()
    if len(sys.argv) < 3:
        print("usage: write_manifest.py <тека-збірки> <версія>", file=sys.stderr)
        return 2
    folder = Path(sys.argv[1])
    version = sys.argv[2]
    base = sys.argv[3].rstrip("/") + "/" if len(sys.argv) > 3 else ""
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    files = [path for path in files if path.name != "manifest.json"]
    payload = build_manifest(folder, version, files)
    if base:
        for entry in payload["files"]:
            entry["url"] = base + entry["path"]
    secret = os.environ.get("MANIFEST_SIGNING_KEY", "").strip()
    if secret:
        payload = sign_manifest(payload, secret)
        print("манифест підписано")
    elif os.environ.get("REQUIRE_MANIFEST_SIGNATURE", "").strip().lower() in (
        "1", "true", "yes",
    ):
        print("немає MANIFEST_SIGNING_KEY — реліз без підпису заборонено",
              file=sys.stderr)
        return 2
    (folder / "manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not base:
        blob_dir = folder / "blobs"
        blob_dir.mkdir(exist_ok=True)
        for path in files:
            digest = file_sha256(path)
            target = blob_dir / digest
            if not target.exists():
                target.write_bytes(path.read_bytes())
    where = "прямі посилання" if base else "блоби за хешем"
    print(f"записано {len(payload['files'])} файлів, версія {version} ({where})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
