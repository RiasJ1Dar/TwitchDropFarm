"""Зібрати Windows-установник Inno Setup з перевіреного portable exe."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import VERSION  # noqa: E402
from core.toolbox import force_utf8_console  # noqa: E402


def find_compiler(explicit: str | None) -> Path:
    """Явний шлях має пріоритет; невдалий вибір не підміняємо іншим."""
    configured = explicit or os.environ.get("ISCC_PATH")
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise FileNotFoundError(f"ISCC.exe не знайдено: {path}")
        return path
    if found := shutil.which("ISCC.exe"):
        return Path(found)
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = Path(os.environ.get(variable, ""))
        for folder in (
            "Inno Setup 7", "Inno Setup 6", "Programs/Inno Setup 7", "Programs/Inno Setup 6",
        ):
            path = root / folder / "ISCC.exe"
            if path.is_file():
                return path
    raise FileNotFoundError("Встановіть Inno Setup або передайте --iscc <шлях до ISCC.exe>")


def main() -> int:
    force_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", help="Шлях до компілятора Inno Setup")
    parser.add_argument("--exe", type=Path, default=ROOT / "dist/TwitchDropFarm.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        compiler = find_compiler(args.iscc)
        executable = args.exe.resolve(strict=True)
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            str(compiler), f"/DAppVersion={VERSION}", f"/DAppExe={executable}",
            f"/O{output}", str(ROOT / "installer/TwitchDropFarm.iss"),
        ], check=True)
        target = output / f"TwitchDropFarm-Setup-{VERSION}.exe"
        if not target.is_file():
            raise FileNotFoundError(f"Компілятор не створив очікуваний установник: {target}")
        print(target)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Не вдалося зібрати установник: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
