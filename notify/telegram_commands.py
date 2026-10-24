"""Розбір тексту команд без транспорту, ядра й перевірки доступу."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    argument: str


def parse_command(text: str, buttons: Mapping[str, str]) -> ParsedCommand | None:
    if (mapped := buttons.get(text.strip())) is not None:
        text = f"/{mapped}"
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    name = parts[0].lstrip("/").split("@", 1)[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    return ParsedCommand(name, argument)
