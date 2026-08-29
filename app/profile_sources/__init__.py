"""Every way of reading a LinkedIn profile, and the code that combines them."""
from __future__ import annotations

from ..cache import TTLCache
from ..settings import Settings
from .source import Source
from .errors import ProfileSourceError
from .fetch_profile import ProfileFetcher
from .from_sample_file import SampleFileSource
from .from_public_page import PublicPageSource
from .from_logged_in_page import LoggedInPageSource
from .from_linkedin_api import LinkedInApiSource
from .profile_urls import canonical_url, extract_public_id

__all__ = [
    "ProfileSourceError",
    "ProfileFetcher",
    "Source",
    "build_profile_fetcher",
    "canonical_url",
    "extract_public_id",
]


def build_profile_fetcher(settings: Settings, cache: TTLCache) -> ProfileFetcher:
    """Assemble the pipeline in priority order.

    Demo mode short-circuits to the fixture. Otherwise the authenticated
    sources come first, then the logged-out page — which is kept in the list
    even when the others succeed, because it carries fields they omit for
    out-of-network members.
    """
    if settings.demo_mode:
        sources: list[Source] = [SampleFileSource(settings)]
    else:
        sources = [LinkedInApiSource(settings), LoggedInPageSource(settings)]
        if settings.enable_public_fallback:
            sources.append(PublicPageSource(settings))

    return ProfileFetcher(settings, sources, cache)
