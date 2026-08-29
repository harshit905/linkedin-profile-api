"""Everything that can go wrong when reading a profile, and its HTTP status."""


class ProfileSourceError(Exception):
    """Base error. `status` is what the public API returns."""

    status = 502
    code = "upstream_error"
    hint: str | None = None

    def __init__(self, message: str | None = None):
        super().__init__(message or self.code)
        self.message = message or self.code


class InvalidProfileURL(ProfileSourceError):
    status = 422
    code = "invalid_profile_url"
    hint = "Expected a member profile URL such as https://www.linkedin.com/in/<public-id>."


class ProfileNotFound(ProfileSourceError):
    status = 404
    code = "profile_not_found"


class SessionExpired(ProfileSourceError):
    status = 503
    code = "linkedin_session_expired"
    hint = "Copy a fresh LINKEDIN_COOKIE from a logged-in browser."


class SessionChallenged(ProfileSourceError):
    """LinkedIn redirects the request back to itself instead of answering.

    The credential is intact but the account is being soft-blocked, so it
    needs a human to load linkedin.com and clear the prompt.
    """

    status = 503
    code = "linkedin_session_challenged"
    hint = (
        "Open linkedin.com in the browser that owns the cookie, clear the "
        "prompt, then copy a fresh LINKEDIN_COOKIE."
    )


class Blocked(ProfileSourceError):
    status = 503
    code = "linkedin_blocked"
    hint = "Slow the request rate and clear the challenge in a browser."


class RateLimited(ProfileSourceError):
    status = 429
    code = "linkedin_rate_limited"
    hint = "Back off and retry; lower RATE_LIMIT_PER_MINUTE."


class EndpointRetired(ProfileSourceError):
    """A LinkedIn endpoint we depend on now returns 410."""

    status = 502
    code = "linkedin_endpoint_retired"
    hint = "The endpoint is gone; the source needs updating."


# Failures that describe why the session cannot read anything. They outrank a
# later source's per-profile failure: if the cookie is dead, reporting
# "profile not found" would send the caller after the wrong bug.
SESSION_LEVEL_CODES = frozenset(
    {
        SessionExpired.code,
        SessionChallenged.code,
        Blocked.code,
        RateLimited.code,
        EndpointRetired.code,
    }
)


def most_informative(first: ProfileSourceError, second: ProfileSourceError) -> ProfileSourceError:
    """Pick whichever error better explains the failure to the caller."""
    if first.code in SESSION_LEVEL_CODES:
        return first
    if second.code in SESSION_LEVEL_CODES:
        return second
    return second
