"""Combining profiles from several sources into one response.

No single source sees everything. The authenticated page has the real
headline and follower count; the logged-out page has the about text, photo
and schools. Merging in priority order yields more than any source alone.
"""
from __future__ import annotations

from ..response_models import Experience, Profile

# Scalars filled from the first source that has a value.
_SCALARS = (
    "first_name",
    "last_name",
    "full_name",
    "headline",
    "about",
    "industry",
    "is_student",
    "follower_count",
    "connection_count",
    "profile_picture",
    "background_picture",
)

# Lists taken wholesale from the first source that has a non-empty one.
_LISTS = (
    "education",
    "skills",
    "certifications",
    "languages",
    "projects",
    "publications",
    "honors",
    "volunteer",
)

# Sections reported in `partial_sections` when still empty after merging.
_REPORTABLE = ("about", *_LISTS)


def _richness(items: list[Experience]) -> int:
    """How much detail an experience list carries, for picking the better one."""
    return sum(bool(e.title) + bool(e.company) + bool(e.description) for e in items)


def merge_profiles(profiles: list[Profile]) -> Profile | None:
    """Merge in priority order; earlier profiles win each field.

    `meta` is rewritten to describe the merged result rather than whichever
    source happened to produce the base object.
    """
    profiles = [p for p in profiles if p is not None]
    if not profiles:
        return None
    if len(profiles) == 1:
        return profiles[0]

    merged = profiles[0].model_copy(deep=True)

    for other in profiles[1:]:
        for field in _SCALARS:
            if getattr(merged, field) in (None, ""):
                value = getattr(other, field)
                if value is not None:
                    setattr(merged, field, value)

        if not merged.location.text and other.location.text:
            merged.location = other.location
        if not merged.profile_picture_variants:
            merged.profile_picture_variants = other.profile_picture_variants

        for field in _LISTS:
            if not getattr(merged, field):
                setattr(merged, field, getattr(other, field))

        if _richness(other.experience) > _richness(merged.experience):
            merged.experience = other.experience

    missing = [name for name in _REPORTABLE if not getattr(merged, name)]
    if not merged.headline:
        missing.insert(0, "headline")
    if not merged.experience:
        missing.append("experience")
    missing.append("dates")

    sources = ", ".join(p.meta.source for p in profiles)
    merged.meta = merged.meta.model_copy(
        update={
            "source": "merged",
            "partial_sections": missing,
            "degraded_reason": f"Merged from: {sources}.",
        }
    )
    return merged
