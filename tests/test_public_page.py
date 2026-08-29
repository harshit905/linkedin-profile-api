"""Tests for the logged-out fallback tier.

The HTML fragments below are shaped like what linkedin.com actually served
during development, including the character-masking it applies to logged-out
readers.
"""
import pytest

from app.settings import Settings
from app.profile_sources.errors import ProfileNotFound
from app.profile_sources.from_public_page import _clean, _person, parse_ld_person

LD = {
    "@graph": [
        {"@type": "WebPage", "url": "https://www.linkedin.com/in/williamhgates"},
        {
            "@type": "Person",
            "name": "Bill Gates",
            # LinkedIn masks the job title for logged-out readers.
            "jobTitle": ["********", "*******", "**********"],
            "description": "Chair of the Gates Foundation.",
            "address": {
                "addressLocality": "Seattle, Washington",
                "addressCountry": "United States",
            },
            "image": {"contentUrl": "https://media.licdn.com/dms/image/x/photo.jpg"},
            "worksFor": [{"name": "Gates Foundation"}, {"name": "Breakthrough Energy"}],
            "alumniOf": [{"name": "Harvard University"}],
        },
    ]
}


def test_masked_values_are_dropped_not_passed_through():
    # A fully-masked value is indistinguishable from data unless we drop it.
    assert _clean("********, *******, **********") is None
    assert _clean("***") is None
    assert _clean("  ") is None
    # Real values survive, including ones that merely contain an asterisk.
    assert _clean("  Chair of  the Foundation ") == "Chair of the Foundation"
    assert _clean("Founder *and* Chair") == "Founder *and* Chair"


def test_finds_person_node_regardless_of_graph_order():
    person = _person(LD)
    assert person is not None and person["name"] == "Bill Gates"
    # A bare Person document (no @graph wrapper) also works.
    assert _person({"@type": "Person", "name": "X"})["name"] == "X"
    assert _person({"@graph": [{"@type": "WebPage"}]}) is None


def test_parses_public_profile():
    p = parse_ld_person(_person(LD), "williamhgates")

    assert p.full_name == "Bill Gates"
    assert p.first_name == "Bill" and p.last_name == "Gates"
    assert p.about == "Chair of the Gates Foundation."
    assert p.location.text == "Seattle, Washington"
    assert p.location.country == "United States"
    assert p.profile_picture.url.endswith("photo.jpg")
    assert [e.company for e in p.experience] == [
        "Gates Foundation",
        "Breakthrough Energy",
    ]
    assert [e.school for e in p.education] == ["Harvard University"]


def test_masked_headline_is_reported_as_missing():
    p = parse_ld_person(_person(LD), "williamhgates")

    # The masked jobTitle must not leak asterisks into the response...
    assert p.headline is None
    # ...and the caller must be told it was unavailable.
    assert "headline" in p.meta.partial_sections
    assert "experience_titles" in p.meta.partial_sections
    # `about` came through, so it must NOT be listed as missing.
    assert "about" not in p.meta.partial_sections


def test_meta_marks_the_response_degraded():
    p = parse_ld_person(_person(LD), "williamhgates")
    assert p.meta.source == "public_page"
    assert "logged-out" in p.meta.degraded_reason
    # Sections this tier can never supply.
    for section in ("skills", "certifications", "dates"):
        assert section in p.meta.partial_sections


def test_absent_optional_fields_do_not_raise():
    p = parse_ld_person({"@type": "Person", "name": "Ada"}, "ada")
    assert p.full_name == "Ada"
    assert p.headline is None
    assert p.experience == [] and p.education == []
    assert p.profile_picture is None


@pytest.mark.parametrize(
    "cookie_header,expected_names",
    [
        # A pasted browser Cookie header keeps the cookies that matter and
        # drops analytics noise.
        (
            'li_at=abc; JSESSIONID="ajax:123"; bcookie="v=2&x"; lidc="b=OGST03"; '
            "_uetvid=noise; AnalyticsSyncHistory=noise",
            {"li_at", "JSESSIONID", "bcookie", "lidc"},
        ),
        ("li_at=abc", {"li_at"}),
    ],
)
def test_cookie_header_parsing(cookie_header, expected_names):
    s = Settings(LINKEDIN_COOKIE=cookie_header, _env_file=None)
    assert set(s.cookies) == expected_names
    assert s.has_session is True


def test_csrf_token_is_derived_unquoted_from_the_cookie_header():
    s = Settings(LINKEDIN_COOKIE='li_at=abc; JSESSIONID="ajax:999"', _env_file=None)
    # Cookie keeps its quotes; the header must not have them.
    assert s.cookies["JSESSIONID"] == '"ajax:999"'
    assert s.jsessionid == "ajax:999"


def test_individual_vars_still_work_when_no_cookie_header():
    s = Settings(LINKEDIN_LI_AT="abc", LINKEDIN_JSESSIONID="ajax:5", _env_file=None)
    assert s.cookies == {"li_at": "abc", "JSESSIONID": '"ajax:5"'}
    assert s.jsessionid == "ajax:5"
    assert s.has_session is True


def test_no_session_configured():
    s = Settings(LINKEDIN_COOKIE="", LINKEDIN_LI_AT="", LINKEDIN_JSESSIONID="", _env_file=None)
    assert s.has_session is False
