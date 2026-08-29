"""Runs the profile sources and returns one combined profile.

The pipeline is deliberately dumb: it walks the sources in priority order,
collects whatever each returns, and merges. All the LinkedIn-specific
knowledge lives in the sources themselves.
"""
from __future__ import annotations

import logging

from ..cache import TTLCache
from ..settings import Settings
from ..response_models import Profile
from .source import Source
from .errors import ProfileSourceError, ProfileNotFound, SessionExpired, most_informative
from .merge import merge_profiles

logger = logging.getLogger(__name__)


class ProfileFetcher:
    def __init__(self, settings: Settings, sources: list[Source], cache: TTLCache):
        self.settings = settings
        self.sources = sources
        self.cache = cache

    async def ingest(self, public_id: str, refresh: bool = False) -> Profile:
        key = f"profile:{public_id.lower()}"

        if not refresh:
            cached: Profile | None = await self.cache.get(key)
            if cached is not None:
                return cached.model_copy(
                    update={
                        "meta": cached.meta.model_copy(
                            update={"cached": True, "source": "cache"}
                        )
                    }
                )

        collected: list[Profile] = []
        error: ProfileSourceError | None = None

        for source in self.sources:
            if not source.is_available():
                logger.debug("Skipping %s: no session configured", source.name)
                if error is None:
                    error = SessionExpired("No LinkedIn session is configured.")
                continue
            try:
                collected.append(await source.fetch(public_id))
                logger.info("%s supplied %s", source.name, public_id)
            except ProfileSourceError as exc:
                logger.info("%s failed for %s: %s", source.name, public_id, exc.code)
                error = exc if error is None else most_informative(error, exc)

            if self._is_complete(collected):
                break

        profile = merge_profiles(collected)
        if profile is None:
            raise error or ProfileNotFound("No source could read this profile.")

        await self.cache.set(key, profile)
        return profile

    @staticmethod
    def _is_complete(collected: list[Profile]) -> bool:
        """Stop early when a source returned a profile with nothing missing."""
        return bool(collected) and not collected[-1].meta.partial_sections
