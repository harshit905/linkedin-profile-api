"""Tests for reading the signed-in profile page, and for combining sources.

The HTML below mirrors the real structure captured from linkedin.com: an
obfuscated-class markup body plus a `rehydrate-data` script carrying a React
Server Components flight stream. Class names and flight row ids are
deliberately arbitrary here, because the parser must not depend on either.
"""
import json

import pytest

from app.profile_sources.errors import ProfileNotFound
from app.profile_sources.from_logged_in_page import parse_ssr_page
from app.profile_sources.merge import merge_profiles
from app.response_models import Education, Image, Location, Profile, ResponseMeta

from datetime import datetime, timezone


def _flight(rows: list[str]) -> str:
    """Build a rehydrate-data script body from flight rows."""
    return "window.__como_rehydration__ = " + json.dumps(["\n".join(rows)])


def _text(value: str) -> dict:
    return {"textProps": {"children": [value]}}


HTML = """<html><head><title>Bill Gates | LinkedIn</title></head><body>
<p class="_02484ad3 bb60b960">Bill Gates</p><div class="_1736033f"><p class="a1b7f320">
<span>Chair, Gates Foundation and Founder, Breakthrough Energy</span></p></div>
<script type="text/javascript" id="rehydrate-data">%s</script>
</body></html>""" % _flight([
    '1:I["abc",[],"default"]',
    "3a:" + json.dumps([_text("Explore Premium profiles")]),
    "3b:" + json.dumps([_text("Gates Foundation")]),
    "3c:" + json.dumps([_text("Seattle, Washington, United States")]),
    "3d:" + json.dumps([_text("40,603,794 followers")]),
    "3e:" + json.dumps([_text("Gates Foundation")]),
    "3f:" + json.dumps([_text("Save to PDF")]),
])


def test_parses_name_and_unmasked_headline():
    p = parse_ssr_page(HTML, "williamhgates")
    assert p.full_name == "Bill Gates"
    assert p.first_name == "Bill" and p.last_name == "Gates"
    # The authenticated page is the only tier that gets a real headline;
    # the logged-out page masks it.
    assert p.headline == "Chair, Gates Foundation and Founder, Breakthrough Energy"


def test_extracts_location_and_follower_count_from_the_flight_stream():
    p = parse_ssr_page(HTML, "williamhgates")
    assert p.location.text == "Seattle, Washington, United States"
    assert p.follower_count == 40603794


def test_identifies_current_company():
    p = parse_ssr_page(HTML, "williamhgates")
    assert [e.company for e in p.experience] == ["Gates Foundation"]


def test_reports_its_own_provenance_honestly():
    p = parse_ssr_page(HTML, "williamhgates")
    assert p.meta.source == "logged_in_page"
    assert "server-rendered" in p.meta.degraded_reason
    # Sections this tier cannot see for an out-of-network member.
    for section in ("skills", "education", "about"):
        assert section in p.meta.partial_sections


def test_chrome_strings_are_never_mistaken_for_data():
    p = parse_ssr_page(HTML, "williamhgates")
    for value in (p.headline, p.location.text):
        assert value not in ("Save to PDF", "Explore Premium profiles")


def test_page_without_a_title_is_rejected():
    with pytest.raises(ProfileNotFound):
        parse_ssr_page("<html><body>nothing</body></html>", "x")


def test_missing_rehydrate_block_still_yields_name_and_headline():
    """A markup-only page must degrade, not raise."""
    html = (
        "<html><head><title>Ada Lovelace | LinkedIn</title></head>"
        "<body><p>Ada Lovelace</p><div><p><span>Mathematician</span></p></div>"
        "</body></html>"
    )
    p = parse_ssr_page(html, "ada")
    assert p.full_name == "Ada Lovelace"
    assert p.headline == "Mathematician"
    assert p.follower_count is None
    assert p.location.text is None


def test_malformed_flight_payload_does_not_raise():
    html = HTML.replace(_flight([]), "window.__como_rehydration__ = not-json")
    p = parse_ssr_page(html, "williamhgates")
    assert p.full_name == "Bill Gates"


# --------------------------------------------------------------------------
# tier merge
# --------------------------------------------------------------------------

def _profile(**kw) -> Profile:
    base = dict(
        public_id="x",
        profile_url="https://www.linkedin.com/in/x",
        meta=ResponseMeta(source="logged_in_page", fetched_at=datetime.now(timezone.utc)),
    )
    base.update(kw)
    return Profile(**base)


def test_merge_prefers_the_earlier_source_field_by_field():
    logged_in = _profile(full_name="Bill Gates", headline="Real headline",
                   follower_count=40603794)
    public = _profile(full_name="Bill Gates", headline=None,
                   about="About text", profile_picture=Image(url="http://img"),
                   education=[Education(school="Harvard University")])

    merged = merge_profiles([logged_in, public])

    # Authenticated values win where both tiers have one.
    assert merged.headline == "Real headline"
    assert merged.follower_count == 40603794
    # Gaps are filled from the public tier.
    assert merged.about == "About text"
    assert merged.profile_picture.url == "http://img"
    assert [e.school for e in merged.education] == ["Harvard University"]


def test_merge_recomputes_partial_sections_for_the_merged_result():
    logged_in = _profile(headline="H")
    public = _profile(about="A", education=[Education(school="S")])

    merged = merge_profiles([logged_in, public])

    # Supplied by the merge, so no longer missing...
    assert "about" not in merged.meta.partial_sections
    assert "education" not in merged.meta.partial_sections
    # ...but genuinely absent sections still are.
    assert "skills" in merged.meta.partial_sections
    assert merged.meta.source == "merged"


def test_merge_tolerates_absent_sources():
    only = _profile(full_name="Solo")
    assert merge_profiles([only]) is only
    assert merge_profiles([None, only]) is only
    assert merge_profiles([]) is None
    assert merge_profiles([None, None]) is None
