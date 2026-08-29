"""End-to-end tests against the FastAPI app with the upstream client stubbed."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api
from app.profile_sources.source import Source
from app.profile_sources.errors import ProfileNotFound, SessionExpired
from app.profile_sources.parse_api_json import parse_profile
from app.response_models import Profile

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def bundle():
    return {
        "profile_view": json.loads((FIXTURES / "profile_view.json").read_text()),
        "skills": {"elements": [{"name": "Mathematics"}]},
        "network_info": {"followersCount": 12000, "connectionsCount": 500},
        "partial_sections": [],
    }


class _StubSource(Source):
    """A source whose behaviour is driven by the requested public id.

    Every source in the pipeline must be stubbed, not just the first: the
    fetcher walks all of them, so leaving one live would let the suite make
    real requests to linkedin.com and turn an expected 404 into a 200.
    """

    name = "linkedin_api"
    requires_session = True

    def __init__(self, settings, bundle, calls):
        super().__init__(settings)
        self._bundle = bundle
        self.calls = calls

    def is_available(self) -> bool:
        return True

    async def fetch(self, public_id: str) -> Profile:
        self.calls.append(public_id)
        if public_id == "missing":
            raise ProfileNotFound("No such profile.")
        if public_id == "deadsession":
            raise SessionExpired("Cookie rejected.")
        return parse_profile(self._bundle, public_id)


@pytest.fixture
def client(monkeypatch, bundle):
    calls: list[str] = []
    stub = _StubSource(api.settings, bundle, calls)
    monkeypatch.setattr(api.fetcher, "sources", [stub])
    # Start from an empty cache so tests don't leak into each other.
    api.cache._store.clear()

    with TestClient(api.app) as c:
        c.upstream_calls = calls
        yield c


def test_health(client, monkeypatch):
    configured = api.settings.model_copy(
        update={"linkedin_cookie": "li_at=test-cookie; JSESSIONID=ajax:1"}
    )
    monkeypatch.setattr(api, "settings", configured)

    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["linkedin_session_configured"] is True
    assert body["sources"] == ["linkedin_api"]
    # The health probe must never echo the cookie itself.
    assert "test-cookie" not in json.dumps(body)


def test_get_profile(client):
    r = client.get("/api/v1/profile", params={"url": "https://linkedin.com/in/adalovelace"})
    assert r.status_code == 200
    body = r.json()
    assert body["full_name"] == "Ada Lovelace"
    assert body["experience"][0]["title"] == "Analyst"
    assert body["meta"]["cached"] is False


def test_post_profile(client):
    r = client.post("/api/v1/profile", json={"url": "adalovelace"})
    assert r.status_code == 200
    assert r.json()["public_id"] == "adalovelace"


def test_second_request_is_served_from_cache(client):
    params = {"url": "https://linkedin.com/in/adalovelace"}
    first = client.get("/api/v1/profile", params=params).json()
    second = client.get("/api/v1/profile", params=params).json()

    assert first["meta"]["cached"] is False
    assert second["meta"]["cached"] is True
    assert second["meta"]["source"] == "cache"
    # Only the first request reached LinkedIn.
    assert client.upstream_calls == ["adalovelace"]


def test_refresh_bypasses_cache(client):
    params = {"url": "adalovelace"}
    client.get("/api/v1/profile", params=params)
    r = client.get("/api/v1/profile", params={**params, "refresh": "true"})
    assert r.json()["meta"]["cached"] is False
    assert client.upstream_calls == ["adalovelace", "adalovelace"]


def test_invalid_url_returns_422(client):
    r = client.get("/api/v1/profile", params={"url": "https://example.com/in/nope"})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_profile_url"


def test_missing_profile_returns_404(client):
    r = client.get("/api/v1/profile", params={"url": "missing"})
    assert r.status_code == 404
    assert r.json()["error"] == "profile_not_found"


def test_expired_session_returns_503_with_hint(client):
    r = client.get("/api/v1/profile", params={"url": "deadsession"})
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "linkedin_session_expired"
    assert "LINKEDIN_COOKIE" in body["hint"]


def test_no_api_key_configured_leaves_endpoint_open(client):
    """Local-dev default: with API_KEYS empty, no header is required."""
    assert client.get("/api/v1/profile", params={"url": "adalovelace"}).status_code == 200


def test_api_key_is_enforced_when_configured(client, monkeypatch):
    from app import auth

    # require_api_key reads settings at call time, so patching the getter it
    # resolves is enough to flip auth on for this test only.
    configured = api.settings.model_copy(update={"api_keys": "secret-key,other-key"})
    monkeypatch.setattr(auth, "get_settings", lambda: configured)

    params = {"url": "adalovelace"}
    assert client.get("/api/v1/profile", params=params).status_code == 401
    assert client.get(
        "/api/v1/profile", params=params, headers={"X-API-Key": "wrong"}
    ).status_code == 401
    # Any key in the configured list is accepted.
    for key in ("secret-key", "other-key"):
        assert client.get(
            "/api/v1/profile", params=params, headers={"X-API-Key": key}
        ).status_code == 200


def test_rate_limiter_returns_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(api.rate_limiter, "_per_minute", 2)
    api.rate_limiter._hits.clear()

    params = {"url": "adalovelace"}
    assert client.get("/api/v1/profile", params=params).status_code == 200
    assert client.get("/api/v1/profile", params=params).status_code == 200

    limited = client.get("/api/v1/profile", params=params)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
