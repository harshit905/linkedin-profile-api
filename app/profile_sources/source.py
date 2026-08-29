"""What every profile source must provide.

A source is one way of reading a profile: the legacy JSON API, the
server-rendered page, the logged-out page, or a static fixture. They differ
enormously inside, but the pipeline only needs three things from each — a
name, whether it needs a session, and a coroutine that returns a Profile.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..settings import Settings
from ..response_models import Profile


class Source(ABC):
    #: Value written to `meta.source` when this source answers.
    name: ClassVar[str]

    #: Sources needing a LinkedIn session are skipped when none is configured.
    requires_session: ClassVar[bool] = False

    #: Sections this source can never supply, whatever the profile contains.
    never_provides: ClassVar[tuple[str, ...]] = ()

    def __init__(self, settings: Settings):
        self.settings = settings

    def is_available(self) -> bool:
        return self.settings.has_session if self.requires_session else True

    @abstractmethod
    async def fetch(self, public_id: str) -> Profile:
        """Read the profile, or raise an ProfileSourceError."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
