"""Source: LinkedIn's internal JSON API.

LinkedIn retired these endpoints — `profileView` returns 410 — but the source
is kept and tried first so the service picks them up again automatically if
they ever come back.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from ..response_models import Profile
from .source import Source
from .errors import ProfileSourceError, SessionChallenged
from .browser_headers import api_headers, is_self_redirect, raise_for_status
from .parse_api_json import parse_profile

logger = logging.getLogger(__name__)

API_BASE = "https://www.linkedin.com/voyager/api"
PROFILE_VIEW = "/identity/profiles/{public_id}/profileView"
SKILLS = "/identity/profiles/{public_id}/skills"
NETWORK_INFO = "/identity/profiles/{public_id}/networkinfo"


class LinkedInApiSource(Source):
    name = "linkedin_api"
    requires_session = True

    def __init__(self, settings):
        super().__init__(settings)
        self._client: httpx.AsyncClient | None = None
        # However many callers our API serves, LinkedIn sees one at a time.
        self._lock = asyncio.Semaphore(max(1, settings.max_upstream_concurrency))

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=API_BASE,
                timeout=self.settings.request_timeout_seconds,
                follow_redirects=False,
                headers=api_headers(self.settings),
                cookies=dict(self.settings.cookies),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, public_id: str) -> Profile:
        bundle: dict[str, Any] = {"partial_sections": []}
        headers = {"referer": f"https://www.linkedin.com/in/{public_id}/"}

        bundle["profile_view"] = await self._get(
            PROFILE_VIEW.format(public_id=public_id), headers=headers
        )

        # Supplementary calls degrade individually: a section gated by network
        # distance should not fail the whole request.
        extras = (
            ("skills", SKILLS.format(public_id=public_id), {"count": 100, "start": 0}),
            ("network_info", NETWORK_INFO.format(public_id=public_id), None),
        )
        for key, path, params in extras:
            await self._pause()
            try:
                bundle[key] = await self._get(path, params, headers)
            except ProfileSourceError as exc:
                if exc.code in ("linkedin_session_expired", "linkedin_blocked"):
                    raise
                bundle["partial_sections"].append(key)

        return parse_profile(bundle, public_id)

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        await self.start()
        assert self._client is not None

        last: Exception | None = None
        async with self._lock:
            for attempt in range(self.settings.max_retries + 1):
                try:
                    response = await self._client.get(
                        path, params=params, headers=headers
                    )
                except httpx.HTTPError as exc:
                    last = ProfileSourceError(f"Upstream transport error: {exc}")
                else:
                    # A self-redirect re-issues cookies; httpx has stored them,
                    # so one retry is legitimate. A second means soft-blocking.
                    if is_self_redirect(response):
                        if attempt < self.settings.max_retries:
                            await asyncio.sleep(1.0 + random.uniform(0, 0.5))
                            continue
                        raise SessionChallenged(
                            "LinkedIn kept redirecting the request to itself "
                            "after re-issuing cookies."
                        )
                    raise_for_status(response)
                    return response.json()

                if attempt < self.settings.max_retries:
                    await asyncio.sleep(2**attempt + random.uniform(0, 0.4))

        raise last or ProfileSourceError("Upstream request failed")

    async def _pause(self) -> None:
        delay = self.settings.inter_request_delay_seconds
        if delay > 0:
            await asyncio.sleep(delay + random.uniform(0, delay / 2))
