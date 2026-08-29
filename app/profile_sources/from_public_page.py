"""Source: the logged-out public profile page.

LinkedIn serves logged-out visitors a reduced page carrying a JSON-LD block:

    <script type="application/ld+json">
      {"@graph": [{"@type": "Person", "name": ..., "worksFor": [...]}]}
    </script>

Thinner than an authenticated read, but it needs no session and carries
fields the authenticated page omits for out-of-network members — the about
text, the photo and schools — so it is merged in even when other sources
succeed. Deliberately sends no cookies: that is what makes it logged-out.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import httpx

from ..response_models import Education, Experience, Image, Location, Profile, ResponseMeta
from .source import Source
from .errors import ProfileNotFound
from .browser_headers import document_headers, raise_if_challenged
from .profile_urls import canonical_url

LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)


def _clean(value) -> str | None:
    """Trim, and drop values LinkedIn has masked.

    The logged-out page redacts fields character by character rather than
    omitting them — a job title arrives as "********, *******". Passing that
    through is worse than returning nothing, since a consumer cannot tell it
    from real data.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or not text.replace("*", "").replace(",", "").strip():
        return None
    return text


def _person(payload) -> dict | None:
    """Find the Person node; @graph ordering is not guaranteed."""
    if isinstance(payload, dict):
        nodes = payload.get("@graph")
        nodes = nodes if isinstance(nodes, list) else [payload]
    elif isinstance(payload, list):
        nodes = payload
    else:
        return None
    return next(
        (n for n in nodes if isinstance(n, dict) and n.get("@type") == "Person"), None
    )


def _org_name(node) -> str | None:
    if isinstance(node, dict):
        return _clean(node.get("name"))
    return _clean(node)


def parse_ld_person(person: dict, public_id: str) -> Profile:
    name = _clean(person.get("name"))
    first, last = (name.split(" ", 1) + [None])[:2] if name and " " in name else (name, None)

    job_title = person.get("jobTitle")
    headline = _clean(
        ", ".join(t for t in job_title if isinstance(t, str))
        if isinstance(job_title, list)
        else job_title
    )

    address = person.get("address") if isinstance(person.get("address"), dict) else {}
    image = person.get("image")
    image_url = _clean(image.get("contentUrl") if isinstance(image, dict) else image)

    experience = [
        Experience(company=company, title=headline)
        for company in map(_org_name, person.get("worksFor") or [])
        if company
    ]
    education = [
        Education(school=school)
        for school in map(_org_name, person.get("alumniOf") or [])
        if school
    ]

    about = _clean(person.get("description"))

    # Report what this source genuinely could not supply for *this* profile,
    # since the headline and about text are sometimes present and sometimes
    # masked.
    missing = list(PublicPageSource.never_provides)
    if not about:
        missing.insert(0, "about")
    if not headline:
        missing.insert(0, "headline")
    if experience and not any(e.title for e in experience):
        missing.append("experience_titles")

    return Profile(
        public_id=public_id,
        profile_url=canonical_url(public_id),
        first_name=first,
        last_name=last,
        full_name=name,
        headline=headline,
        about=about,
        location=Location(
            text=_clean(address.get("addressLocality")),
            country=_clean(address.get("addressCountry")),
        ),
        profile_picture=Image(url=image_url) if image_url else None,
        experience=experience,
        education=education,
        meta=ResponseMeta(
            source=PublicPageSource.name,
            fetched_at=datetime.now(timezone.utc),
            degraded_reason="Read from the logged-out public page.",
            partial_sections=missing,
        ),
    )


class PublicPageSource(Source):
    name = "public_page"
    requires_session = False
    never_provides = (
        "skills", "certifications", "languages", "projects",
        "publications", "honors", "volunteer", "dates",
    )

    async def fetch(self, public_id: str) -> Profile:
        async with httpx.AsyncClient(
            headers=document_headers(self.settings),
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(canonical_url(public_id))

        raise_if_challenged(str(response.url))
        if response.status_code != 200:
            raise ProfileNotFound(f"Public page returned HTTP {response.status_code}.")

        for block in LD_JSON.findall(response.text):
            try:
                payload = json.loads(block)
            except json.JSONDecodeError:
                continue
            if person := _person(payload):
                return parse_ld_person(person, public_id)

        raise ProfileNotFound("No JSON-LD Person block on the public page.")
