"""Явна межа ядра для GUI та сповіщувачів; без доступу до задач і кешів."""
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from core.channels import Channel
    from core.events import ControlBus, EventBus
    from core.history import History
    from core.images import ImageCache
    from core.model import Campaign
    from core.settings import Settings


class MinerClient(Protocol):
    @property
    def settings(self) -> Settings: ...
    @property
    def control(self) -> ControlBus: ...
    @property
    def events(self) -> EventBus: ...
    @property
    def history(self) -> History: ...
    @property
    def images(self) -> ImageCache: ...
    @property
    def campaigns(self) -> Sequence[Campaign]: ...
    @property
    def current_channel(self) -> Channel | None: ...
    @property
    def paused(self) -> bool: ...
    @property
    def offline(self) -> bool: ...
    update_postponed: bool

    def active_campaign(self, channel: Channel | None = None) -> Campaign | None: ...
    def request_stop(self) -> None: ...
