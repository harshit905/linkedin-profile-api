import pytest

from app.profile_sources.errors import InvalidProfileURL
from app.profile_sources.profile_urls import extract_public_id


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.linkedin.com/in/adalovelace", "adalovelace"),
        ("https://www.linkedin.com/in/adalovelace/", "adalovelace"),
        ("http://linkedin.com/in/adalovelace", "adalovelace"),
        ("www.linkedin.com/in/adalovelace", "adalovelace"),
        ("linkedin.com/in/adalovelace", "adalovelace"),
        # Locale subdomains resolve to the same member.
        ("https://uk.linkedin.com/in/adalovelace", "adalovelace"),
        ("https://in.linkedin.com/in/ada-lovelace-1843", "ada-lovelace-1843"),
        # Query strings and fragments are noise.
        ("https://www.linkedin.com/in/adalovelace?trk=nav", "adalovelace"),
        ("https://www.linkedin.com/in/adalovelace/#experience", "adalovelace"),
        # Deep links into profile subpages still identify the member.
        ("https://www.linkedin.com/in/adalovelace/detail/experience/", "adalovelace"),
        # Percent-encoded unicode slugs.
        ("https://www.linkedin.com/in/%C3%A9lodie-martin", "élodie-martin"),
        # A bare slug is accepted as a convenience.
        ("adalovelace", "adalovelace"),
        ("  https://www.linkedin.com/in/adalovelace  ", "adalovelace"),
    ],
)
def test_extracts_public_id(url, expected):
    assert extract_public_id(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        # Not LinkedIn.
        "https://example.com/in/adalovelace",
        "https://linkedin.evil.com/in/adalovelace",
        # LinkedIn, but not a member profile.
        "https://www.linkedin.com/company/anthropic",
        "https://www.linkedin.com/school/mit",
        "https://www.linkedin.com/feed/",
    ],
)
def test_rejects_bad_input(url):
    with pytest.raises(InvalidProfileURL):
        extract_public_id(url)
