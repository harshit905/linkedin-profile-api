"""Public response schema.

The challenge leaves the schema to us. The design goals here:
  * stable, flat-ish key names that don't leak LinkedIn's internal urn shapes
  * every list present (possibly empty) so consumers never branch on None
  * dates as both a sortable string and structured parts
  * a `meta` block so callers can tell fresh data from cached data
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DatePart(BaseModel):
    year: int | None = None
    month: int | None = None
    day: int | None = None
    # ISO-ish, truncated to the precision LinkedIn actually gives us: "2021-04".
    text: str | None = None


class DateRange(BaseModel):
    start: DatePart | None = None
    end: DatePart | None = None
    is_current: bool = False
    duration_months: int | None = None


class Image(BaseModel):
    url: str | None = None
    width: int | None = None
    height: int | None = None


class Experience(BaseModel):
    title: str | None = None
    company: str | None = None
    company_url: str | None = None
    company_logo: str | None = None
    employment_type: str | None = None
    location: str | None = None
    description: str | None = None
    dates: DateRange = Field(default_factory=DateRange)


class Education(BaseModel):
    school: str | None = None
    school_url: str | None = None
    school_logo: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    grade: str | None = None
    activities: str | None = None
    description: str | None = None
    dates: DateRange = Field(default_factory=DateRange)


class Certification(BaseModel):
    name: str | None = None
    authority: str | None = None
    license_number: str | None = None
    url: str | None = None
    dates: DateRange = Field(default_factory=DateRange)


class Language(BaseModel):
    name: str | None = None
    proficiency: str | None = None


class Skill(BaseModel):
    name: str
    endorsement_count: int | None = None


class Project(BaseModel):
    title: str | None = None
    description: str | None = None
    url: str | None = None
    dates: DateRange = Field(default_factory=DateRange)


class Publication(BaseModel):
    name: str | None = None
    publisher: str | None = None
    description: str | None = None
    url: str | None = None
    date: DatePart | None = None


class Honor(BaseModel):
    title: str | None = None
    issuer: str | None = None
    description: str | None = None
    date: DatePart | None = None


class VolunteerExperience(BaseModel):
    role: str | None = None
    organization: str | None = None
    cause: str | None = None
    description: str | None = None
    dates: DateRange = Field(default_factory=DateRange)


class Location(BaseModel):
    text: str | None = None
    country: str | None = None


class ResponseMeta(BaseModel):
    # Where this response came from:
    #   linkedin_api    - LinkedIn's own JSON API (retired; returns 410)
    #   logged_in_page  - the profile page as seen while signed in
    #   public_page     - the profile page as seen while signed out
    #   merged          - several of the above combined
    #   sample_file     - demo mode, a saved example
    #   cache           - a previously stored response
    # Anything other than `linkedin_api` may be incomplete; check
    # `partial_sections` for what is missing.
    source: Literal[
        "linkedin_api", "logged_in_page", "public_page",
        "merged", "sample_file", "cache",
    ] = "linkedin_api"
    fetched_at: datetime
    cached: bool = False
    # Set when a degraded tier served the response, explaining why.
    degraded_reason: str | None = None
    # Sections the upstream response did not include, so a consumer can tell
    # "this person has no certifications" from "we could not read that section".
    partial_sections: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    public_id: str
    profile_url: str
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    headline: str | None = None
    about: str | None = None
    location: Location = Field(default_factory=Location)
    industry: str | None = None
    is_student: bool | None = None
    follower_count: int | None = None
    connection_count: int | None = None
    profile_picture: Image | None = None
    profile_picture_variants: list[Image] = Field(default_factory=list)
    background_picture: Image | None = None

    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    honors: list[Honor] = Field(default_factory=list)
    volunteer: list[VolunteerExperience] = Field(default_factory=list)

    meta: ResponseMeta


class ProfileRequest(BaseModel):
    url: str = Field(
        ...,
        description="A LinkedIn profile URL or bare public identifier.",
        examples=["https://www.linkedin.com/in/williamhgates/"],
    )
    refresh: bool = Field(
        default=False, description="Bypass the cache and force an upstream fetch."
    )


class ErrorResponse(BaseModel):
    error: str
    detail: str
    hint: str | None = None


def _dump(model: Any) -> dict:
    return model.model_dump(mode="json")
