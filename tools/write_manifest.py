"""Зібрати manifest.json для релізу: шлях + SHA-256 кожного файлу в теці.

Запуск (з кореня проєкту):

    env\\Scripts\\python.exe tools\\write_manifest.py dist\\TwitchDropFarm 1.0.4

У реліз кладуть manifest.json і кожен файл ще раз під іменем свого хеша —
тоді клієнт качає лише блоби, яких локально ще немає.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.update import build_manifest, file_sha256  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: write_manifest.py <тека-збірки> <версія>", file=sys.stderr)
        return 2
    folder = Path(sys.argv[1])
    version = sys.argv[2]
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    files = [path for path in files if path.name != "manifest.json"]
    payload = build_manifest(folder, version, files)
    (folder / "manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    blob_dir = folder / "blobs"
    blob_dir.mkdir(exist_ok=True)
    for path in files:
        digest = file_sha256(path)
        target = blob_dir / digest
        if not target.exists():
            target.write_bytes(path.read_bytes())
    print(f"записано {len(payload['files'])} файлів, версія {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
