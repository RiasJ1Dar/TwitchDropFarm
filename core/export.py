"""Експорт історії та інвентаря в CSV і HTML.

Історія вже є в JSONL — цей модуль лише розкладає її в таблицю, яку можна
відкрити в Excel або в браузері. Жодної мережі, жодного стану: на вході
рядки й кампанії, на виході файли.
"""
from __future__ import annotations

import csv
import html
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HISTORY_CSV = "history.csv"
HISTORY_HTML = "history.html"
INVENTORY_CSV = "inventory.csv"
INVENTORY_HTML = "inventory.html"

_HISTORY_FIELDS = ("at", "kind", "game", "drop", "rewards", "campaign",
                   "needed", "available", "id")
_INVENTORY_FIELDS = ("game", "campaign", "drop", "minutes", "required",
                     "claimed", "state", "ends_at")


def write_history(folder: Path, entries: Iterable[dict[str, Any]]) -> list[Path]:
    rows = list(entries)
    folder.mkdir(parents=True, exist_ok=True)
    csv_path = folder / HISTORY_CSV
    html_path = folder / HISTORY_HTML
    _write_csv(csv_path, _HISTORY_FIELDS, (
        {key: entry.get(key, "") for key in _HISTORY_FIELDS} for entry in rows
    ))
    _write_html(
        html_path,
        title="Історія фарму",
        headers=_HISTORY_FIELDS,
        rows=[[entry.get(key, "") for key in _HISTORY_FIELDS] for entry in rows],
        empty="Поки немає жодного запису.",
    )
    return [csv_path, html_path]


def write_inventory(folder: Path, campaigns: Iterable[Any]) -> list[Path]:
    now = datetime.now(timezone.utc)
    rows = list(_inventory_rows(campaigns, now))
    folder.mkdir(parents=True, exist_ok=True)
    csv_path = folder / INVENTORY_CSV
    html_path = folder / INVENTORY_HTML
    _write_csv(csv_path, _INVENTORY_FIELDS, rows)
    _write_html(
        html_path,
        title="Інвентар дропів",
        headers=_INVENTORY_FIELDS,
        rows=[[row.get(key, "") for key in _INVENTORY_FIELDS] for row in rows],
        empty="Кампаній немає — або інвентар ще не читали.",
    )
    return [csv_path, html_path]


def write_all(folder: Path, *, entries: Iterable[dict[str, Any]],
              campaigns: Iterable[Any]) -> list[Path]:
    return write_history(folder, entries) + write_inventory(folder, campaigns)


def _inventory_rows(campaigns: Iterable[Any], now: datetime) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for campaign in campaigns:
        game = getattr(campaign, "game", None)
        game_name = getattr(game, "name", None) or str(game or "")
        campaign_name = str(getattr(campaign, "name", ""))
        ends = getattr(campaign, "closes_at", None)
        ends_text = ends.isoformat(timespec="seconds") if isinstance(ends, datetime) else ""
        state = _campaign_state(campaign, now)
        drops = list(getattr(campaign, "all_drops", ()) or ())
        if not drops:
            rows.append({
                "game": game_name,
                "campaign": campaign_name,
                "drop": "",
                "minutes": "",
                "required": "",
                "claimed": "",
                "state": state,
                "ends_at": ends_text,
            })
            continue
        for drop in drops:
            rows.append({
                "game": game_name,
                "campaign": campaign_name,
                "drop": str(getattr(drop, "name", "")),
                "minutes": str(getattr(drop, "minutes", "")),
                "required": str(getattr(drop, "required_minutes", "")),
                "claimed": "так" if getattr(drop, "taken", False) else "ні",
                "state": state,
                "ends_at": ends_text,
            })
    return rows


def _campaign_state(campaign: Any, now: datetime) -> str:
    if getattr(campaign, "over", False):
        return "минула"
    if getattr(campaign, "not_started", False):
        return "скоро"
    if not getattr(campaign, "available_to_me", True):
        return "не привʼязано"
    if getattr(campaign, "everything_taken", False):
        return "завершено"
    ends = getattr(campaign, "closes_at", None)
    if isinstance(ends, datetime):
        hours = max(0, int((ends - now).total_seconds() // 3600))
        return f"активна, ще {hours} год"
    return "активна"


def _write_csv(path: Path, fields: tuple[str, ...],
               rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key, "")
                             for key in fields})


def _write_html(path: Path, *, title: str, headers: tuple[str, ...],
                rows: list[list[Any]], empty: str) -> None:
    cells = []
    if not rows:
        cells.append(f"<tr><td colspan=\"{len(headers)}\">{html.escape(empty)}</td></tr>")
    else:
        for row in rows:
            tds = "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
            cells.append(f"<tr>{tds}</tr>")
    table = (
        "<table>\n<thead><tr>"
        + "".join(f"<th>{html.escape(name)}</th>" for name in headers)
        + "</tr></thead>\n<tbody>\n"
        + "\n".join(cells)
        + "\n</tbody>\n</table>"
    )
    path.write_text(
        "<!DOCTYPE html>\n<html lang=\"uk\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:14px/1.4 sans-serif;margin:24px}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ccc;padding:6px 8px;text-align:left}"
        "th{background:#f3f3f3}</style></head><body>"
        f"<h1>{html.escape(title)}</h1>\n{table}\n</body></html>\n",
        encoding="utf-8",
    )
