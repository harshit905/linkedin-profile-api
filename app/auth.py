"""API-key auth and a fixed-window rate limiter for our own endpoints."""
from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status

from .settings import Settings, get_settings


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Reject callers without a valid key.

    If API_KEYS is unset the check is skipped, which keeps local development
    frictionless. Any real deployment must set it - the endpoint spends a
    LinkedIn session on every call, so leaving it open invites someone else to
    burn your rate limit.
    """
    settings = get_settings()
    valid = settings.api_key_set
    if not valid:
        return

    # Constant-time comparison against each key so timing can't leak a prefix.
    if x_api_key and any(hmac.compare_digest(x_api_key, k) for k in valid):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid X-API-Key header.",
        headers={"WWW-Authenticate": "ApiKey"},
    )


class RateLimiter:
    """Sliding-window limiter keyed by API key, falling back to client IP."""

    def __init__(self, per_minute: int):
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _key(self, request: Request) -> str:
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"key:{api_key[:12]}"
        # Behind a proxy the real client is the first XFF entry.
        forwarded = request.headers.get("X-Forwarded-For", "")
        client_ip = forwarded.split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        return f"ip:{client_ip}"

    def check(self, request: Request) -> None:
        if self._per_minute <= 0:
            return

        key = self._key(request)
        now = time.monotonic()
        window = self._hits[key]

        while window and window[0] <= now - 60:
            window.popleft()

        if len(window) >= self._per_minute:
            retry_after = max(1, int(60 - (now - window[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit of {self._per_minute}/min exceeded.",
                headers={"Retry-After": str(retry_after)},
            )

        window.append(now)


def build_rate_limiter(settings: Settings) -> RateLimiter:
    return RateLimiter(settings.rate_limit_per_minute)
