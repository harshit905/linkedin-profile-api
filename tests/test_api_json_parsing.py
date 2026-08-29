import json
from pathlib import Path

import pytest

from app.profile_sources.parse_api_json import parse_profile

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def bundle():
    return {
        "profile_view": json.loads((FIXTURES / "profile_view.json").read_text()),
        "skills": {
            "elements": [
                {"name": "Mathematics", "endorsementCount": 99},
                {"name": "Algorithms"},
                # Duplicate casing should collapse to one entry.
                {"name": "mathematics"},
            ]
        },
        "network_info": {"followersCount": 12000, "connectionsCount": 500},
        "partial_sections": [],
    }


@pytest.fixture
def profile(bundle):
    return parse_profile(bundle, "adalovelace")


def test_identity_fields(profile):
    assert profile.public_id == "adalovelace"
    assert profile.profile_url == "https://www.linkedin.com/in/adalovelace"
    assert profile.full_name == "Ada Lovelace"
    assert profile.headline.startswith("Mathematician")
    # Surrounding whitespace is stripped.
    assert profile.about.startswith("Interested in analytical engines")
    assert profile.location.text == "London"
    assert profile.location.country == "United Kingdom"
    assert profile.industry == "Computer Software"
    assert profile.is_student is False
    assert profile.follower_count == 12000
    assert profile.connection_count == 500


def test_images_are_expanded_and_sorted(profile):
    # Largest variant is promoted to profile_picture.
    assert profile.profile_picture.width == 400
    assert (
        profile.profile_picture.url
        == "https://media.licdn.com/dms/image/C4E03/400_400/0/photo.jpg"
    )
    assert [i.width for i in profile.profile_picture_variants] == [100, 400]
    assert profile.background_picture.width == 1400


def test_experience(profile):
    current, past = profile.experience
    assert current.title == "Analyst"
    assert current.company == "Analytical Engine Project"
    assert current.company_url == "https://www.linkedin.com/company/analytical-engine"
    assert current.company_logo.endswith("200_200/0/logo.png")
    assert current.location == "London, England"

    # No endDate means the role is ongoing.
    assert current.dates.start.text == "1842-06"
    assert current.dates.end is None
    assert current.dates.is_current is True

    assert past.dates.is_current is False
    assert past.dates.start.text == "1840-01"
    assert past.dates.end.text == "1841-12"
    # Jan 1840 through Dec 1841 inclusive.
    assert past.dates.duration_months == 24


def test_education(profile):
    edu = profile.education[0]
    assert edu.school == "University of London"
    assert edu.field_of_study == "Mathematics"
    assert edu.grade == "First"
    # Year-only precision renders without a month.
    assert edu.dates.start.text == "1833"
    assert edu.dates.end.text == "1836"
    assert edu.school_url == "https://www.linkedin.com/school/12345"


def test_skills_prefer_dedicated_endpoint_and_dedupe(profile):
    names = [s.name for s in profile.skills]
    assert names == ["Mathematics", "Algorithms"]
    assert profile.skills[0].endorsement_count == 99
    assert profile.skills[1].endorsement_count is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("NATIVE_OR_BILINGUAL", "Native or bilingual"),
        ("urn:li:fs_employmentType:FULL_TIME", "Full time"),
        ("SCIENCE_AND_TECHNOLOGY", "Science and technology"),
        # Already-readable text passes through untouched.
        ("Full-time", "Full-time"),
        (None, None),
        ("", None),
    ],
)
def test_enum_humanisation(raw, expected):
    from app.profile_sources.parse_api_json import _titleize

    assert _titleize(raw) == expected


def test_employment_type_urn_is_not_leaked():
    parsed = parse_profile(
        {
            "profile_view": {
                "positionView": {
                    "elements": [
                        {
                            "title": "Engineer",
                            "employmentTypeUrn": "urn:li:fs_employmentType:FULL_TIME",
                        }
                    ]
                }
            }
        },
        "someone",
    )
    assert parsed.experience[0].employment_type == "Full time"


def test_remaining_sections(profile):
    assert profile.certifications[0].authority == "Royal Society"
    assert profile.certifications[0].dates.start.text == "1843-03"
    # Enum values are humanised.
    assert profile.languages[0].proficiency == "Native or bilingual"
    assert profile.projects[0].title == "Note G"
    assert profile.publications[0].publisher == "Taylor's Scientific Memoirs"
    assert profile.honors[0].title == "First Programmer"
    assert profile.volunteer[0].cause == "Science and technology"


def test_meta(profile):
    assert profile.meta.source == "linkedin_api"
    assert profile.meta.cached is False
    assert profile.meta.partial_sections == []


def test_missing_sections_degrade_to_empty_lists():
    """A gated or absent section must not fail the whole parse."""
    sparse = parse_profile(
        {
            "profile_view": {"profile": {"firstName": "Ada"}},
            "partial_sections": ["skills"],
        },
        "adalovelace",
    )
    assert sparse.full_name == "Ada"
    assert sparse.experience == []
    assert sparse.education == []
    assert sparse.skills == []
    assert sparse.profile_picture is None
    assert sparse.meta.partial_sections == ["skills"]


def test_completely_empty_payload_does_not_raise():
    empty = parse_profile({}, "ghost")
    assert empty.public_id == "ghost"
    assert empty.full_name is None
    assert empty.experience == []
