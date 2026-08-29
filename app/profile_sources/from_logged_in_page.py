"""Source: the authenticated, server-rendered profile page.

LinkedIn moved the profile to server-driven UI. A logged-in page load makes
no profile API call at all — the data is rendered server-side and shipped in
the HTML, inside `<script id="rehydrate-data">` as a React Server Components
flight stream:

    window.__como_rehydration__ = [ "0:[\"$\",\"$L1\",null,{...}]\n3c:[...]" ]

Rows are newline-separated `<hexId>:<json>` records. Row ids and CSS class
names regenerate every build, so the parser anchors only on things that hold:
`<title>` is always "<Name> | LinkedIn", the headline is the first `<span>`
after the name, and counts are matched by shape.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import httpx

from ..response_models import Experience, Location, Profile, ResponseMeta
from .source import Source
from .errors import ProfileNotFound
from .browser_headers import document_headers, raise_if_challenged
from .profile_urls import canonical_url

REHYDRATE = re.compile(r'<script[^>]*id="rehydrate-data"[^>]*>(.*?)</script>', re.S)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
FOLLOWERS = re.compile(r"^([\d,]+)\s+followers?$", re.I)
CONNECTIONS = re.compile(r"^([\d,+]+)\s+connections?$", re.I)
# "Seattle, Washington, United States" — strict, so headlines never match.
LOCATION = re.compile(r"^[A-Z][\w.'\- ]+(?:,\s*[\w.'\- ]+){1,3}$")

# Navigation and footer strings that appear in the text stream but are never
# profile data.
CHROME = frozenset(
    {
        "explore premium profiles", "send profile in a message", "save to pdf",
        "report / block", "about this member", "about", "accessibility",
        "talent solutions", "community guidelines", "careers", "talent",
        "marketing solutions", "privacy & terms", "ad choices", "advertising",
        "sales solutions", "mobile", "small business", "safety center", "sales",
        "questions?", "manage your account and privacy", "help", "language",
        "recommendation transparency", "settings & privacy", "sign out",
        "posts & activity", "job posting account", "more", "message",
        "follow", "connect",
    }
)


def _strip_tags(value: str) -> str:
    return " ".join(TAG.sub(" ", value).split())


def _flight_texts(script_body: str) -> list[str]:
    """Ordered text leaves from the flight stream, consecutive repeats dropped.

    The script is a JS assignment wrapping an array of chunks. Join the chunks
    before splitting into rows — a row can straddle a chunk boundary.
    """
    body = script_body.strip()
    if not body.startswith("["):
        _, _, body = body.partition("=")
        body = body.strip().rstrip(";").strip()
    try:
        chunks = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    if not (isinstance(chunks, list) and all(isinstance(c, str) for c in chunks)):
        return []

    found: list[str] = []

    def visit(node) -> None:
        if isinstance(node, dict):
            props = node.get("textProps")
            if isinstance(props, dict):
                children = props.get("children")
                for item in children if isinstance(children, list) else [children]:
                    if isinstance(item, str) and item.strip():
                        found.append(item.strip())
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    for row in "".join(chunks).split("\n"):
        _, _, payload = row.partition(":")
        if not payload or payload.startswith("I["):
            continue
        try:
            visit(json.loads(payload))
        except (json.JSONDecodeError, ValueError):
            continue

    deduped: list[str] = []
    for text in found:
        if not deduped or deduped[-1] != text:
            deduped.append(text)
    return deduped


def _headline(html: str, name: str) -> str | None:
    """The headline is the first <span> rendered after the member's name."""
    index = html.find(f">{name}<")
    if index == -1:
        return None
    match = re.search(r"<span[^>]*>(.*?)</span>", html[index : index + 4000], re.S)
    return _strip_tags(match.group(1)) or None if match else None


def parse_ssr_page(html: str, public_id: str) -> Profile:
    title = TITLE.search(html)
    name = _strip_tags(title.group(1)).rsplit("|", 1)[0].strip() if title else ""
    if not name:
        raise ProfileNotFound("Profile page carried no member name.")

    first, _, last = name.partition(" ")
    headline = _headline(html, name)

    block = REHYDRATE.search(html)
    texts = _flight_texts(block.group(1)) if block else []

    followers = connections = location = None
    for i, text in enumerate(texts):
        if text.lower() in CHROME:
            continue
        if (m := FOLLOWERS.match(text)) and followers is None:
            followers = int(m.group(1).replace(",", ""))
            # The location renders immediately before the follower count.
            for previous in reversed(texts[:i]):
                if LOCATION.match(previous) and previous.lower() not in CHROME:
                    location = previous
                    break
        elif (m := CONNECTIONS.match(text)) and connections is None:
            digits = m.group(1).replace(",", "").rstrip("+")
            connections = int(digits) if digits.isdigit() else None
        elif location is None and i > 0 and LOCATION.match(text):
            location = text

    company = _current_company(texts, headline, location, name)

    return Profile(
        public_id=public_id,
        profile_url=canonical_url(public_id),
        first_name=first or None,
        last_name=last or None,
        full_name=name,
        headline=headline,
        location=Location(text=location),
        follower_count=followers,
        connection_count=connections,
        experience=[Experience(company=company)] if company else [],
        meta=ResponseMeta(
            source=LoggedInPageSource.name,
            fetched_at=datetime.now(timezone.utc),
            degraded_reason=(
                "Read from the server-rendered page; LinkedIn retired the "
                "JSON profile endpoints."
            ),
            partial_sections=list(LoggedInPageSource.never_provides),
        ),
    )


def _current_company(
    texts: list[str], headline: str | None, location: str | None, name: str
) -> str | None:
    """The employer repeats in the page and is usually named in the headline."""
    if not headline:
        return None
    counts: dict[str, int] = {}
    for text in texts:
        if text.lower() in CHROME or len(text) > 80:
            continue
        if text in (headline, location, name):
            continue
        if FOLLOWERS.match(text) or CONNECTIONS.match(text):
            continue
        counts[text] = counts.get(text, 0) + 1
    repeated = [t for t, n in counts.items() if n >= 2 and t in headline]
    return max(repeated, key=len) if repeated else None


class LoggedInPageSource(Source):
    name = "logged_in_page"
    requires_session = True
    # An out-of-network member's page renders none of these, however it is
    # parsed; the logged-out source supplies some of them.
    never_provides = (
        "about", "skills", "certifications", "languages", "projects",
        "publications", "honors", "volunteer", "education", "dates",
    )

    async def fetch(self, public_id: str) -> Profile:
        async with httpx.AsyncClient(
            headers=document_headers(self.settings),
            cookies=dict(self.settings.cookies),
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(canonical_url(public_id))

        raise_if_challenged(str(response.url))
        if response.status_code != 200:
            raise ProfileNotFound(f"Profile page returned HTTP {response.status_code}.")
        return parse_ssr_page(response.text, public_id)
