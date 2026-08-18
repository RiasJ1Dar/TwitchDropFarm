"""Модульне оновлення: лише файли з іншим SHA-256, потім перевірка хешем.

Як у СЗІ, але без крипти — репозиторій публічний. Манифест перелічує
відносні шляхи й sha256. Блоб на дзеркалі зветься хешем: незмінений файл
має те саме ім'я і не качається. Підміна — після виходу процесу, бо
працюючий `.exe` на Windows не перезапишеш.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

from core.config import APP_DIR, FROZEN, STATE_DIR, UPDATE_MANIFEST_URL, VERSION

log = logging.getLogger("TwitchDrops")

STAGE_DIR = STATE_DIR / "update-stage"
APPLY_BAT = STATE_DIR / "apply-update.cmd"
# Журнал самої підміни: скрипт працює після виходу програми, і без цього файлу
# збій у ньому не лишав жодного слова ніде.
APPLY_LOG = STATE_DIR / "update-apply.log"
CHUNK = 256 * 1024
# 2.0.0 вийшла раніше за 1.0.x — порівнюємо лише всередині тієї ж мажорної лінії
USER_AGENT = f"TwitchDropFarm/{VERSION}"


@dataclass(frozen=True)
class FileSpec:
    path: str
    sha256: str
    size: int
    url: str | None = None

    @property
    def digest(self) -> str:
        return self.sha256.lower()


@dataclass(frozen=True)
class Manifest:
    version: str
    files: tuple[FileSpec, ...]
    base_url: str = ""


@dataclass(frozen=True)
class FetchItem:
    spec: FileSpec
    dest: Path
    url: str


def parse_version(text: str) -> tuple[int, ...]:
    body = text.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in body.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if digits == "" and parts:
            break
        if digits:
            parts.append(int(digits))
    return tuple(parts) or (0,)


def same_line(left: str, right: str) -> bool:
    a, b = parse_version(left), parse_version(right)
    return a[0] == b[0]


def is_newer(candidate: str, current: str) -> bool:
    """Чи кандидат новіший. Інша мажорна лінія (2.x при 1.x) — ні."""
    if not same_line(candidate, current):
        return False
    return parse_version(candidate) > parse_version(current)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def safe_rel(raw: str) -> Path:
    rel = Path(raw.replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts or rel.parts[:1] == ("",):
        raise ValueError(f"небезпечний шлях у манифесті: {raw}")
    return rel


def read_manifest(payload: Any, *, source: str = "") -> Manifest:
    if not isinstance(payload, dict):
        raise ValueError("манифест має бути об'єктом")
    version = str(payload.get("version") or "").strip()
    if not version:
        raise ValueError("у манифесті немає version")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("у манифесті немає files")
    files: list[FileSpec] = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("елемент files має бути об'єктом")
        path = str(item.get("path") or "").strip()
        digest = str(item.get("sha256") or "").strip().lower()
        if not path or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(f"поганий запис файлу: {item!r}")
        safe_rel(path)
        size = int(item.get("size") or 0)
        url = item.get("url")
        files.append(FileSpec(
            path=path, sha256=digest, size=size,
            url=str(url) if url else None,
        ))
    given = str(payload.get("base_url") or "").strip()
    if given:
        base = given if given.endswith("/") else given + "/"
    elif source.endswith("manifest.json"):
        base = source.rsplit("/", 1)[0] + "/"
    else:
        base = source if source.endswith("/") else (source + "/" if source else "")
    return Manifest(version=version, files=tuple(files), base_url=base)


def blob_url(spec: FileSpec, manifest: Manifest) -> str:
    if spec.url:
        return spec.url
    if not manifest.base_url:
        raise ValueError(f"немає URL для {spec.path}")
    return urljoin(manifest.base_url, spec.digest)


def plan_fetch(manifest: Manifest, root: Path) -> list[FetchItem]:
    """Що качати: лише файли, чий локальний SHA-256 інший або яких немає."""
    wanted: list[FetchItem] = []
    for spec in manifest.files:
        local = root / safe_rel(spec.path)
        if local.is_file() and file_sha256(local) == spec.digest:
            continue
        wanted.append(FetchItem(
            spec=spec,
            dest=STAGE_DIR / safe_rel(spec.path),
            url=blob_url(spec, manifest),
        ))
    return wanted


def verify_staged(items: Iterable[FetchItem]) -> None:
    for item in items:
        if not item.dest.is_file():
            raise ValueError(f"немає звантаженого {item.spec.path}")
        got = file_sha256(item.dest)
        if got != item.spec.digest:
            raise ValueError(
                f"хеш не зійшовся для {item.spec.path}: {got} ≠ {item.spec.digest}"
            )
        if item.spec.size and item.dest.stat().st_size != item.spec.size:
            raise ValueError(f"розмір не зійшовся для {item.spec.path}")


def _oem_encoding() -> str:
    """Кодування, яким `cmd.exe` читає `.cmd`. Не UTF-8 і не ANSI."""
    try:
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "cp866"


def write_apply_script() -> Path:
    """Скрипт підміни. Жодного шляху в тілі — усе приходить аргументами.

    Кирилиця в тілі `.cmd` не виживає: файл пишеться в одному кодуванні, а
    `cmd.exe` читає його в консольному, і `C:\\Users\\Гартунг\\…` стає мусором —
    `xcopy` каже «File not found», оновлення не встає. Спіймано 17.08 живою
    перевіркою: саме на цій машині воно й не встало б, бо ім'я користувача
    кириличне.

    Короткі 8.3 імена (`ГАРТУН~1`) цього не рятують: `GetShortPathName` віддає
    коротке ім'я лише для наявного шляху, а 8.3 на томі можуть бути й вимкнені.
    Тому шляхи передаються **аргументами процесу**: їх несе `CreateProcessW` у
    Unicode, вони не проходять через кодування файлу взагалі, а тіло скрипта
    лишається чистим ASCII назавжди.

    Свій журнал скрипт веде тому, що працює вже після виходу програми: при
    збої в самій підміні жодного слова не лишалось би ні у вікні, ні в
    `log.txt` — оновлення просто «не сталося», і зрозуміти чому було нічим.

    cmd, не PowerShell: ESET ріже мережу в powershell.exe. Мережа тут не
    потрібна, але тримати той самий канал безпечніше.
    """
    APPLY_BAT.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "@echo off\r\n"
        "setlocal\r\n"
        "set STAGE=%~1\r\n"
        "set DEST=%~2\r\n"
        "set PID=%~3\r\n"
        "set EXE=%~4\r\n"
        "set LOG=%~5\r\n"
        # Тільки ASCII і тільки дозапис: шапку зі шляхами вже написав Python у
        # UTF-8, а `echo` кирилицю псує — cp866 не має ні «і», ні «ї», ні «є».
        # Так файл лишається читабельним цілком.
        "set NAME=%~6\r\n"
        "set ARGS=%~7\r\n"
        "echo waiting for pid %PID% and image %NAME% >> \"%LOG%\"\r\n"
        ":wait\r\n"
        # `ping`, а не `timeout`: скрипт запускається відв'язаним, без консолі,
        # а `timeout` без неї падає з «Input redirection is not supported» —
        # цикл очікування зависав назавжди. Спіймано живою перевіркою 17.08.
        "ping -n 2 127.0.0.1 >nul\r\n"
        "tasklist /FI \"PID eq %PID%\" | find \"%PID%\" >nul && goto wait\r\n"
        # Чекати на один PID замало. PyInstaller onefile тримає ДВА процеси:
        # батьківський розпаковує себе у %TEMP% і запускає дочірній, а той і
        # тримає файл. 18.08 підміна через це впала зі «Sharing violation», і
        # оновлення мовчки не встало. Тому чекаємо, поки зникне саме ім'я.
        ":wait_image\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        "tasklist /FI \"IMAGENAME eq %NAME%\" | find /I \"%NAME%\" >nul "
        "&& goto wait_image\r\n"
        "echo all processes gone, copying >> \"%LOG%\"\r\n"
        # Кілька спроб: Windows звільняє файл не миттєво після виходу процесу —
        # антивірус чи індексатор можуть потримати його ще секунду-дві.
        "set TRY=0\r\n"
        ":copy\r\n"
        "set /a TRY+=1\r\n"
        "xcopy /E /Y /I \"%STAGE%\\*\" \"%DEST%\\\" >> \"%LOG%\" 2>&1\r\n"
        "if not errorlevel 1 goto copied\r\n"
        "echo copy attempt %TRY% failed >> \"%LOG%\"\r\n"
        "if %TRY% GEQ 5 (\r\n"
        "  echo XCOPY FAILED after %TRY% tries - files not replaced >> \"%LOG%\"\r\n"
        "  echo starting old build back >> \"%LOG%\"\r\n"
        "  start \"\" \"%EXE%\" %ARGS%\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        "goto copy\r\n"
        ":copied\r\n"
        "echo copied, removing stage >> \"%LOG%\"\r\n"
        "rmdir /S /Q \"%STAGE%\"\r\n"
        "echo starting app >> \"%LOG%\"\r\n"
        # З тими самими аргументами: інакше після оновлення програма підіймалась
        # без `--log`, і журнал мовчки переставав вестися.
        "start \"\" \"%EXE%\" %ARGS%\r\n"
        "echo done >> \"%LOG%\"\r\n"
    )
    APPLY_BAT.write_text(body, encoding="ascii")
    return APPLY_BAT


def launch_apply(script: Path, *, exe: Path, dest: Path, pid: int,
                 args: str = "") -> None:
    """Пускає скрипт так, щоб він пережив вихід програми й не блимнув вікном.

    `CREATE_NO_WINDOW`, а не `DETACHED_PROCESS`: відв'язаний процес лишається
    зовсім без консолі, і тоді в скрипті не працює ні `timeout`, ні конвеєр
    `tasklist | find` — цикл очікування зависав назавжди, файли не
    підмінювались, а в журналі лишався єдиний рядок «waiting for pid».
    Спіймано живою перевіркою 17.08, двома заходами.
    """
    from time import strftime

    APPLY_LOG.write_text(
        f"=== {strftime('%Y-%m-%d %H:%M:%S')} застосування оновлення\n"
        f"стейдж: {STAGE_DIR}\n"
        f"тека:   {dest}\n"
        f"запуск: {exe} {args}\n",
        encoding="utf-8",
    )
    detached = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | NEW_PROCESS_GROUP
    subprocess.Popen(
        [
            os.environ.get("COMSPEC", "cmd.exe"), "/c", str(script),
            str(STAGE_DIR), str(dest), str(pid), str(exe), str(APPLY_LOG),
            exe.name, args,
        ],
        creationflags=detached,
        close_fds=True,
    )


def apply_outcome() -> tuple[str, str]:
    """Чим скінчилась минула підміна: ("ok"|"failed"|"none", подробиці).

    Скрипт працює вже після виходу програми, тож про його провал ніхто не
    дізнавався: 18.08 підміна впала зі «Sharing violation», оновлення мовчки не
    встало, і людина побачила лише те, що версія не змінилась. Тепер новий
    запуск читає журнал і каже вголос.
    """
    try:
        body = APPLY_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "none", ""
    if "XCOPY FAILED" in body:
        reason = "не вдалося замінити файли"
        if "Sharing violation" in body:
            reason = "файл був зайнятий іншим процесом"
        elif "Access is denied" in body:
            reason = "немає прав на теку програми"
        return "failed", reason
    if "done" in body:
        return "ok", ""
    return "none", ""


def forget_outcome() -> None:
    """Прибирає журнал підміни, щоб та сама новина не повторювалась щостарту."""
    APPLY_LOG.unlink(missing_ok=True)


async def fetch_manifest(
    session: aiohttp.ClientSession,
    url: str = UPDATE_MANIFEST_URL,
) -> Manifest:
    async with session.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    ) as response:
        if response.status != 200:
            raise ValueError(f"манифест {response.status} з {url}")
        payload = json.loads(await response.text())
    return read_manifest(payload, source=str(response.url))


async def download_item(session: aiohttp.ClientSession, item: FetchItem) -> None:
    item.dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = item.dest.with_suffix(item.dest.suffix + ".part")
    async with session.get(item.url, headers={"User-Agent": USER_AGENT}) as response:
        if response.status != 200:
            raise ValueError(f"{response.status} для {item.spec.path}")
        with tmp.open("wb") as handle:
            async for chunk in response.content.iter_chunked(CHUNK):
                handle.write(chunk)
    digest = file_sha256(tmp)
    if digest != item.spec.digest:
        tmp.unlink(missing_ok=True)
        raise ValueError(
            f"хеш після завантаження {item.spec.path}: {digest} ≠ {item.spec.digest}"
        )
    tmp.replace(item.dest)


def build_manifest(root: Path, version: str, files: Iterable[Path]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in files:
        rel = path.relative_to(root)
        entries.append({
            "path": rel.as_posix(),
            "sha256": file_sha256(path),
            "size": path.stat().st_size,
        })
    entries.sort(key=lambda row: str(row["path"]))
    return {"version": version, "files": entries}


async def check_for_update(
    session: aiohttp.ClientSession,
    *,
    current: str = VERSION,
    root: Path | None = None,
    url: str = UPDATE_MANIFEST_URL,
) -> tuple[Manifest, list[FetchItem]] | None:
    manifest = await fetch_manifest(session, url)
    if not is_newer(manifest.version, current):
        log.info(f"Оновлення: {manifest.version} не новіша за {current}")
        return None
    items = plan_fetch(manifest, root or APP_DIR)
    return manifest, items


def can_apply() -> bool:
    return FROZEN
