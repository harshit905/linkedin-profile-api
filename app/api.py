"""FastAPI application exposing the LinkedIn profile endpoint."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .cache import TTLCache
from .settings import get_settings
from .profile_sources import ProfileSourceError, build_profile_fetcher, extract_public_id
from .profile_sources.from_linkedin_api import LinkedInApiSource
from .response_models import ErrorResponse, Profile, ProfileRequest
from .auth import build_rate_limiter, require_api_key

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("linkedin_api")

settings = get_settings()
cache = TTLCache(settings.cache_ttl_seconds, settings.cache_max_entries)
rate_limiter = build_rate_limiter(settings)
fetcher = build_profile_fetcher(settings, cache)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for source in fetcher.sources:
        if isinstance(source, LinkedInApiSource):
            await source.start()
    if not (settings.has_session or settings.demo_mode):
        logger.warning("No LinkedIn session configured; set LINKEDIN_COOKIE in .env.")
    yield
    for source in fetcher.sources:
        if isinstance(source, LinkedInApiSource):
            await source.close()


app = FastAPI(
    title="LinkedIn Profile API",
    version="1.0.0",
    description=(
        "Accepts a LinkedIn profile URL and returns the profile as structured "
        "JSON, read from whichever profile sources are available."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(ProfileSourceError)
async def handle_source_error(_: Request, exc: ProfileSourceError) -> JSONResponse:
    logger.info("Request failed: %s - %s", exc.code, exc.message)
    return JSONResponse(
        status_code=exc.status,
        content=ErrorResponse(
            error=exc.code, detail=exc.message, hint=exc.hint
        ).model_dump(),
    )


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": "linkedin-profile-api",
        "docs": "/docs",
        "health": "/health",
        "endpoint": "GET|POST /api/v1/profile",
    }


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness probe. Reports configuration without exposing secrets."""
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "linkedin_session_configured": settings.has_session,
        "cookies_forwarded": len(settings.cookies),
        "demo_mode": settings.demo_mode,
        "sources": [s.name for s in fetcher.sources],
        "auth_enforced": bool(settings.api_key_set),
        "cache": cache.stats(),
        # Names and sizes only - never values. Present so a misconfigured
        # deployment can be diagnosed without shell access to the container.
        "env": _env_report(),
        "warnings": settings.config_warnings,
    }


def _env_report() -> dict:
    """Which configuration variables the process can actually see."""
    watched = (
        "LINKEDIN_COOKIE", "LINKEDIN_LI_AT", "LINKEDIN_JSESSIONID",
        "API_KEYS", "DEMO_MODE", "ENABLE_PUBLIC_FALLBACK", "PORT",
    )
    seen = {name: len(os.environ[name]) for name in watched if name in os.environ}
    return {
        "set": seen,
        # Anything LinkedIn-ish the platform has, to catch a misspelled name.
        "other_linkedin_vars": sorted(
            k for k in os.environ
            if "LINKEDIN" in k.upper() and k not in watched
        ),
    }


_RESPONSES = {code: {"model": ErrorResponse} for code in (401, 404, 422, 429, 502, 503)}


@app.get(
    "/api/v1/profile",
    response_model=Profile,
    tags=["profile"],
    dependencies=[Depends(require_api_key)],
    responses=_RESPONSES,
    summary="Fetch a LinkedIn profile as structured JSON",
)
async def get_profile(
    request: Request,
    url: str = Query(
        ...,
        description="LinkedIn profile URL or bare public identifier.",
        examples=["https://www.linkedin.com/in/williamhgates/"],
    ),
    refresh: bool = Query(default=False, description="Bypass the cache."),
) -> Profile:
    rate_limiter.check(request)
    return await fetcher.ingest(extract_public_id(url), refresh)


@app.post(
    "/api/v1/profile",
    response_model=Profile,
    tags=["profile"],
    dependencies=[Depends(require_api_key)],
    responses=_RESPONSES,
    summary="Fetch a LinkedIn profile as structured JSON (JSON body)",
)
async def post_profile(request: Request, payload: ProfileRequest) -> Profile:
    rate_limiter.check(request)
    return await fetcher.ingest(extract_public_id(payload.url), payload.refresh)
