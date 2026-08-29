"""Runtime configuration. Every secret is read from the environment - nothing
sensitive is ever committed to the repository."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Cookies worth forwarding. Sending only li_at + JSESSIONID is itself a
# fingerprint: a real browser carries consent, locale and routing cookies
# alongside the session, and their absence is trivially detectable.
FORWARDED_COOKIES = frozenset(
    {
        "li_at",       # session token
        "JSESSIONID",  # session id; doubles as the CSRF token
        "liap",        # auth-context flag set during session upgrade
        "bcookie",     # browser id, long lived
        "bscookie",    # secure browser id
        "lidc",        # datacentre routing hint; stale values cause redirects
        "li_gc",       # guest consent
        "lang",        # locale
        "timezone",
        "li_theme",
        "li_theme_set",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LinkedIn session -------------------------------------------------
    # Preferred: paste the entire Cookie header from a browser request
    # (DevTools -> Network -> any voyager call -> Copy as cURL -> the -b value).
    # This captures bcookie/bscookie/lidc automatically, which matters for
    # looking like a browser rather than a script.
    linkedin_cookie: str = Field(default="", alias="LINKEDIN_COOKIE")

    # Fallback: the two cookies supplied individually.
    linkedin_li_at: str = Field(default="", alias="LINKEDIN_LI_AT")
    linkedin_jsessionid: str = Field(default="", alias="LINKEDIN_JSESSIONID")

    # --- Modes ------------------------------------------------------------
    # Serve a committed fixture instead of calling LinkedIn. Lets a reviewer
    # exercise the schema with no credentials at all.
    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    # When a Voyager call fails, fall back to the logged-out public page,
    # which carries a thin JSON-LD summary. Degraded, but needs no session.
    enable_public_fallback: bool = Field(
        default=True, alias="ENABLE_PUBLIC_FALLBACK"
    )

    # --- API auth ---------------------------------------------------------
    # Comma-separated list. Empty disables auth (local dev only).
    api_keys: str = Field(default="", alias="API_KEYS")

    # --- Behaviour --------------------------------------------------------
    cache_ttl_seconds: int = Field(default=3600, alias="CACHE_TTL_SECONDS")
    cache_max_entries: int = Field(default=512, alias="CACHE_MAX_ENTRIES")
    rate_limit_per_minute: int = Field(default=20, alias="RATE_LIMIT_PER_MINUTE")
    request_timeout_seconds: float = Field(default=20.0, alias="REQUEST_TIMEOUT_SECONDS")
    max_retries: int = Field(default=2, alias="MAX_RETRIES")
    # Politeness delay between upstream calls for a single profile fetch.
    inter_request_delay_seconds: float = Field(
        default=0.6, alias="INTER_REQUEST_DELAY_SECONDS"
    )
    # Never allow more than this many concurrent calls to LinkedIn, however
    # many callers our own API is serving.
    max_upstream_concurrency: int = Field(default=1, alias="MAX_UPSTREAM_CONCURRENCY")

    # Chrome's own version string and the matching voyager-web build. These
    # travel together - a UA claiming Chrome 151 alongside a two-year-old
    # clientVersion is a contradiction worth avoiding.
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
        ),
        alias="USER_AGENT",
    )
    client_version: str = Field(default="1.13.46312", alias="LINKEDIN_CLIENT_VERSION")
    sec_ch_ua: str = Field(
        default='"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        alias="SEC_CH_UA",
    )
    sec_ch_ua_platform: str = Field(default='"macOS"', alias="SEC_CH_UA_PLATFORM")
    timezone_name: str = Field(default="Asia/Calcutta", alias="LINKEDIN_TIMEZONE")
    timezone_offset: float = Field(default=5.5, alias="LINKEDIN_TIMEZONE_OFFSET")

    # ------------------------------------------------------------------

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def cookies(self) -> dict[str, str]:
        """The cookie jar to send upstream.

        A pasted Cookie header wins; otherwise fall back to the two
        individually-configured values.
        """
        if self.linkedin_cookie.strip():
            # Parse pair by pair rather than with http.cookies: real LinkedIn
            # cookie headers contain unquoted commas and JSON-ish values that
            # SimpleCookie silently drops.
            parsed: dict[str, str] = {}
            for chunk in self.linkedin_cookie.split(";"):
                if "=" not in chunk:
                    continue
                name, _, value = chunk.partition("=")
                name = name.strip()
                if name in FORWARDED_COOKIES:
                    parsed[name] = value.strip()
            if parsed:
                return parsed

        jar_out: dict[str, str] = {}
        if self.linkedin_li_at.strip():
            jar_out["li_at"] = self.linkedin_li_at.strip()
        token = self.jsessionid
        if token:
            # LinkedIn expects the cookie to keep its quotes.
            jar_out["JSESSIONID"] = f'"{token}"'
        return jar_out

    @property
    def jsessionid(self) -> str:
        """The bare JSESSIONID value, unquoted, for the csrf-token header."""
        if self.linkedin_cookie.strip():
            for chunk in self.linkedin_cookie.split(";"):
                name, _, value = chunk.partition("=")
                if name.strip() == "JSESSIONID":
                    return value.strip().strip('"')
        return self.linkedin_jsessionid.strip().strip('"')

    @property
    def has_session(self) -> bool:
        return "li_at" in self.cookies

    @property
    def config_warnings(self) -> list[str]:
        """Misconfigurations that would otherwise fail silently.

        Pasting only the `li_at` token into LINKEDIN_COOKIE is an easy mistake
        and looks identical to "no session configured", so name it explicitly.
        """
        warnings: list[str] = []
        raw = self.linkedin_cookie.strip()
        if raw and not self.cookies:
            warnings.append(
                "LINKEDIN_COOKIE is set but no cookies could be parsed from it. "
                "It must be the whole Cookie header - 'li_at=...; JSESSIONID=...' "
                "- not just the li_at value on its own."
            )
        elif raw and "li_at" not in self.cookies:
            warnings.append(
                "LINKEDIN_COOKIE parsed, but contains no li_at cookie, which is "
                "the session token."
            )
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
