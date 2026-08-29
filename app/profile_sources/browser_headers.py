"""Shared HTTP concerns: browser-like headers and response classification.

Sending a minimal header set is itself a fingerprint. Everything here mirrors
what Chrome actually sends, so the three sources don't each reinvent it.
"""
from __future__ import annotations

import httpx

from ..settings import Settings
from .errors import (
    Blocked,
    EndpointRetired,
    ProfileSourceError,
    ProfileNotFound,
    RateLimited,
    SessionExpired,
)

CHALLENGE_MARKERS = ("authwall", "/uas/login", "checkpoint", "challenge")


def browser_headers(settings: Settings) -> dict[str, str]:
    """Client hints and fetch metadata common to every request."""
    return {
        "accept-language": "en-US,en;q=0.9",
        "user-agent": settings.user_agent,
        "sec-ch-ua": settings.sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": settings.sec_ch_ua_platform,
    }


def document_headers(settings: Settings) -> dict[str, str]:
    """Headers for fetching an HTML page as a browser navigation would."""
    return {
        **browser_headers(settings),
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "upgrade-insecure-requests": "1",
    }


def api_headers(settings: Settings) -> dict[str, str]:
    """Headers for an XHR against the internal API.

    `csrf-token` must carry the JSESSIONID value *without* quotes while the
    cookie keeps them; a mismatch is the usual cause of a puzzling 403.
    """
    track = (
        '{"clientVersion":"%s","mpVersion":"%s","osName":"web",'
        '"timezoneOffset":%s,"timezone":"%s","deviceFormFactor":"DESKTOP",'
        '"mpName":"voyager-web","displayDensity":2,"displayWidth":2940,'
        '"displayHeight":1912}'
    ) % (
        settings.client_version,
        settings.client_version,
        settings.timezone_offset,
        settings.timezone_name,
    )
    return {
        **browser_headers(settings),
        "accept": "application/vnd.linkedin.normalized+json+2.1",
        "csrf-token": settings.jsessionid,
        "x-restli-protocol-version": "2.0.0",
        "x-li-lang": "en_US",
        "x-li-track": track,
        "sec-ch-prefers-color-scheme": "light",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "priority": "u=1, i",
    }


def raise_for_status(response: httpx.Response) -> None:
    """Translate an API response into a domain error."""
    status = response.status_code
    if status == 200:
        return
    if status in (401, 403):
        raise SessionExpired(f"LinkedIn rejected the session cookie (HTTP {status}).")
    if status == 404:
        raise ProfileNotFound("No visible profile at that identifier.")
    if status == 410:
        raise EndpointRetired(
            f"HTTP 410 for {response.request.url.path}; the endpoint is retired."
        )
    if status == 429:
        raise RateLimited("LinkedIn returned HTTP 429.")
    if status == 999:
        # LinkedIn's non-standard "request denied" code.
        raise Blocked("LinkedIn returned HTTP 999 (automated-traffic challenge).")
    if 300 <= status < 400:
        location = response.headers.get("location", "")
        if any(m in location for m in CHALLENGE_MARKERS):
            raise Blocked(f"Redirected to a challenge: {location}")
        raise SessionExpired(f"Unexpected redirect to {location!r}.")
    raise ProfileSourceError(f"LinkedIn returned HTTP {status}.")


def raise_if_challenged(final_url: str) -> None:
    """Guard for page fetches that follow redirects."""
    lowered = final_url.lower()
    if "authwall" in lowered or "/uas/login" in lowered:
        raise SessionExpired("Redirected to the auth wall; the session is not valid.")
    if "checkpoint" in lowered or "challenge" in lowered:
        raise Blocked(f"LinkedIn served a challenge: {final_url[:120]}")


def is_self_redirect(response: httpx.Response) -> bool:
    """A 302 pointing at the request URL means cookies are being re-issued."""
    if not (300 <= response.status_code < 400):
        return False
    location = response.headers.get("location", "")
    if not location:
        return False
    return location.split("?")[0] == str(response.request.url).split("?")[0]
