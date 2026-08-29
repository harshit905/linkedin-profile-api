"""Normalise Voyager JSON into the public schema.

Everything here is defensive. Voyager's shapes vary by profile completeness,
viewer relationship and locale, so a field we cannot read becomes None and a
section we cannot read becomes []. Nothing raises.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from ..response_models import (
    Certification,
    DatePart,
    DateRange,
    Education,
    Experience,
    Honor,
    Image,
    Language,
    Location,
    Profile,
    Project,
    Publication,
    ResponseMeta,
    Skill,
    VolunteerExperience,
)
from .profile_urls import canonical_url

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


# --------------------------------------------------------------------------
# generic accessors
# --------------------------------------------------------------------------

def _get(node: Any, *path: str, default: Any = None) -> Any:
    """Walk a nested dict path, returning `default` on any miss."""
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
        if node is None:
            return default
    return node


def _elements(node: Any, *path: str) -> list[dict]:
    """Read a Voyager `{"elements": [...]}` collection safely."""
    value = _get(node, *path, "elements", default=[])
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _titleize(value: Any) -> str | None:
    """Humanise Voyager's enums, bare or urn-wrapped.

    NATIVE_OR_BILINGUAL                -> "Native or bilingual"
    urn:li:fs_employmentType:FULL_TIME -> "Full time"
    """
    text = _clean(value)
    if not text:
        return None
    if text.startswith("urn:"):
        text = text.rsplit(":", 1)[-1].strip("()")
    return text.replace("_", " ").capitalize() if text.isupper() else text


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------

def _date_part(node: Any) -> DatePart | None:
    """Render at whatever precision LinkedIn actually supplied."""
    if not isinstance(node, dict):
        return None
    year, month, day = node.get("year"), node.get("month"), node.get("day")
    if not any(isinstance(v, int) for v in (year, month, day)):
        return None

    if isinstance(year, int) and isinstance(month, int):
        text = f"{year:04d}-{month:02d}" + (f"-{day:02d}" if isinstance(day, int) else "")
    elif isinstance(year, int):
        text = f"{year:04d}"
    elif isinstance(month, int) and 1 <= month <= 12:
        text = MONTHS[month - 1]
    else:
        text = None

    return DatePart(year=year, month=month, day=day, text=text)


def _months_between(start: DatePart | None, end: DatePart | None) -> int | None:
    """Inclusive month count, treating a missing month as January."""
    if start is None or not isinstance(start.year, int):
        return None
    if end and isinstance(end.year, int):
        end_year, end_month = end.year, end.month
    else:
        now = datetime.now(timezone.utc)
        end_year, end_month = now.year, now.month
    months = (end_year - start.year) * 12 + ((end_month or 1) - (start.month or 1)) + 1
    return months if months >= 0 else None


def _date_range(node: Any) -> DateRange:
    if not isinstance(node, dict):
        return DateRange()
    start = _date_part(node.get("startDate"))
    end = _date_part(node.get("endDate"))
    return DateRange(
        start=start,
        end=end,
        # Voyager signals "present" by omitting endDate entirely.
        is_current=start is not None and end is None,
        duration_months=_months_between(start, end),
    )


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

def _images(node: Any) -> list[Image]:
    """Expand a VectorImage into concrete URLs, smallest first.

    Voyager stores images as a root URL plus size artifacts; the displayable
    URL is rootUrl + fileIdentifyingUrlPathSegment.
    """
    if not isinstance(node, dict):
        return []
    vector = node.get("com.linkedin.common.VectorImage")
    if isinstance(vector, dict):
        node = vector
    elif not isinstance(node.get("rootUrl"), str):
        nested = _get(node, "displayImageReference", "vectorImage")
        node = nested if isinstance(nested, dict) else node

    root = _clean(node.get("rootUrl"))
    artifacts = node.get("artifacts")
    if not root or not isinstance(artifacts, list):
        return []

    images = [
        Image(
            url=root + segment,
            width=a.get("width"),
            height=a.get("height"),
        )
        for a in artifacts
        if isinstance(a, dict) and (segment := _clean(a.get("fileIdentifyingUrlPathSegment")))
    ]
    images.sort(key=lambda i: i.width or 0)
    return images


def _largest(images: list[Image]) -> Image | None:
    return images[-1] if images else None


def _logo(node: Any) -> str | None:
    for path in (("logo", "image"), ("logo",), ("logoUrl",)):
        candidate = _get(node, *path)
        if isinstance(candidate, str):
            return _clean(candidate)
        if image := _largest(_images(candidate)):
            return image.url
    return None


def _company_url(node: Any) -> str | None:
    name = _clean(_get(node, "miniCompany", "universalName")) or _clean(
        _get(node, "universalName")
    )
    return f"https://www.linkedin.com/company/{name}" if name else None


def _school_url(node: Any) -> str | None:
    for key in ("schoolUrn", "entityUrn"):
        urn = _clean(_get(node, key))
        if urn and ":" in urn:
            ident = urn.rsplit(":", 1)[-1].strip("()")
            if ident.isdigit():
                return f"https://www.linkedin.com/school/{ident}"
    return None


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def _experience(elements: Iterable[dict]) -> list[Experience]:
    out = []
    for el in elements:
        company = el.get("company") if isinstance(el.get("company"), dict) else {}
        out.append(
            Experience(
                title=_clean(el.get("title")),
                company=_clean(el.get("companyName"))
                or _clean(_get(company, "miniCompany", "name")),
                company_url=_company_url(company),
                company_logo=_logo(_get(company, "miniCompany") or company),
                employment_type=_titleize(el.get("employmentTypeUrn"))
                or _titleize(_get(el, "employmentType", "localizedName")),
                location=_clean(el.get("locationName")),
                description=_clean(el.get("description")),
                dates=_date_range(el.get("timePeriod")),
            )
        )
    return out


def _education(elements: Iterable[dict]) -> list[Education]:
    out = []
    for el in elements:
        school = el.get("school") if isinstance(el.get("school"), dict) else {}
        out.append(
            Education(
                school=_clean(el.get("schoolName")) or _clean(school.get("schoolName")),
                school_url=_school_url(school) or _school_url(el),
                school_logo=_logo(school),
                degree=_clean(el.get("degreeName")),
                field_of_study=_clean(el.get("fieldOfStudy")),
                grade=_clean(el.get("grade")),
                activities=_clean(el.get("activities")),
                description=_clean(el.get("description")),
                dates=_date_range(el.get("timePeriod")),
            )
        )
    return out


# The remaining sections are plain field copies, so they are described
# declaratively instead of as nine near-identical functions. Each entry maps a
# schema field to either a Voyager key or a (key, converter) pair.
_TEXT = _clean
_SIMPLE_SECTIONS: dict[str, tuple[type, str, dict[str, Any]]] = {
    "certifications": (Certification, "certificationView", {
        "name": "name",
        "authority": "authority",
        "license_number": "licenseNumber",
        "url": "url",
        "dates": ("timePeriod", _date_range),
    }),
    "languages": (Language, "languageView", {
        "name": "name",
        "proficiency": ("proficiency", _titleize),
    }),
    "projects": (Project, "projectView", {
        "title": "title",
        "description": "description",
        "url": "url",
        "dates": ("timePeriod", _date_range),
    }),
    "publications": (Publication, "publicationView", {
        "name": "name",
        "publisher": "publisher",
        "description": "description",
        "url": "url",
        "date": ("date", _date_part),
    }),
    "honors": (Honor, "honorView", {
        "title": "title",
        "issuer": "issuer",
        "description": "description",
        "date": ("issueDate", _date_part),
    }),
    "volunteer": (VolunteerExperience, "volunteerExperienceView", {
        "role": "role",
        "organization": "companyName",
        "cause": ("cause", _titleize),
        "description": "description",
        "dates": ("timePeriod", _date_range),
    }),
}


def _build_section(model: type, elements: Iterable[dict], mapping: dict) -> list:
    out = []
    for el in elements:
        values = {}
        for field, spec in mapping.items():
            key, convert = spec if isinstance(spec, tuple) else (spec, _TEXT)
            values[field] = convert(el.get(key))
        out.append(model(**values))
    return out


def _skills(bundle: dict) -> list[Skill]:
    """Prefer the dedicated endpoint; profileView truncates to about three."""
    elements = _elements(bundle.get("skills")) or _elements(
        bundle.get("profile_view"), "skillView"
    )
    skills: list[Skill] = []
    seen: set[str] = set()
    for el in elements:
        name = _clean(el.get("name"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        count = el.get("endorsementCount")
        skills.append(
            Skill(name=name, endorsement_count=count if isinstance(count, int) else None)
        )
    return skills


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def parse_profile(bundle: dict, public_id: str) -> Profile:
    view = bundle.get("profile_view") or {}
    profile = view.get("profile") if isinstance(view.get("profile"), dict) else {}
    mini = _get(profile, "miniProfile", default={}) or {}

    first = _clean(profile.get("firstName"))
    last = _clean(profile.get("lastName"))

    variants = _images(mini.get("picture") or profile.get("picture"))

    network = bundle.get("network_info") or {}
    connections = network.get("connectionsCount")
    if not isinstance(connections, int):
        # Voyager sometimes reports a bucket instead of an exact count.
        connections = _get(network, "distance", "connectionCount")
    followers = network.get("followersCount")

    sections = {
        name: _build_section(model, _elements(view, key), mapping)
        for name, (model, key, mapping) in _SIMPLE_SECTIONS.items()
    }

    return Profile(
        public_id=public_id,
        profile_url=canonical_url(public_id),
        first_name=first,
        last_name=last,
        full_name=" ".join(p for p in (first, last) if p) or None,
        headline=_clean(profile.get("headline")),
        about=_clean(profile.get("summary")),
        location=Location(
            text=_clean(profile.get("locationName"))
            or _clean(profile.get("geoLocationName")),
            country=_clean(profile.get("geoCountryName")),
        ),
        industry=_clean(profile.get("industryName")),
        is_student=profile.get("student")
        if isinstance(profile.get("student"), bool)
        else None,
        follower_count=followers if isinstance(followers, int) else None,
        connection_count=connections if isinstance(connections, int) else None,
        profile_picture=_largest(variants),
        profile_picture_variants=variants,
        background_picture=_largest(_images(mini.get("backgroundImage"))),
        experience=_experience(_elements(view, "positionView")),
        education=_education(_elements(view, "educationView")),
        skills=_skills(bundle),
        **sections,
        meta=ResponseMeta(
            source="linkedin_api",
            fetched_at=datetime.now(timezone.utc),
            partial_sections=list(bundle.get("partial_sections") or []),
        ),
    )
