"""Parsing LinkedIn profile URLs down to a public identifier."""
import re
from urllib.parse import unquote, urlparse

from .errors import InvalidProfileURL

# /in/<public-id> is the canonical profile path. LinkedIn also serves
# localised hosts (uk.linkedin.com, www.linkedin.cn) and trailing segments
# such as /in/foo/detail/experience which we discard.
_PROFILE_PATH = re.compile(r"/in/(?P<public_id>[^/?#]+)", re.IGNORECASE)
_VALID_HOST = re.compile(r"(^|\.)linkedin\.(com|cn)$", re.IGNORECASE)
# LinkedIn public ids are slugs: letters, digits, hyphens, and (rarely)
# percent-encoded unicode which unquote() will have already expanded.
_VALID_PUBLIC_ID = re.compile(r"^[\w\-À-￿]{1,120}$", re.UNICODE)


def extract_public_id(url: str) -> str:
    """Return the public identifier from a LinkedIn profile URL.

    Accepts bare slugs too, so `johndoe` and
    `https://www.linkedin.com/in/johndoe/` are equivalent inputs.
    """
    if not url or not url.strip():
        raise InvalidProfileURL("URL is empty")

    raw = url.strip()

    # Bare slug with no scheme and no path separators.
    if "/" not in raw and "." not in raw:
        candidate = unquote(raw)
        if _VALID_PUBLIC_ID.match(candidate):
            return candidate
        raise InvalidProfileURL(f"Not a valid LinkedIn public identifier: {raw!r}")

    if not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if not _VALID_HOST.search(host):
        raise InvalidProfileURL(f"Not a LinkedIn host: {parsed.hostname!r}")

    match = _PROFILE_PATH.search(parsed.path)
    if not match:
        raise InvalidProfileURL(
            "URL is not a member profile. Expected a /in/<public-id> path; "
            "company and school pages are not supported."
        )

    public_id = unquote(match.group("public_id")).strip()
    if not _VALID_PUBLIC_ID.match(public_id):
        raise InvalidProfileURL(f"Malformed public identifier: {public_id!r}")

    return public_id


def canonical_url(public_id: str) -> str:
    return f"https://www.linkedin.com/in/{public_id}"
