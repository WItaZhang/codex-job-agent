"""Canonical profile v2: typed background records, separate search preferences.

Stable record IDs identify an entity across edits; provenance describes the
current value, while immutable profile versions retain the change history.
"""

import calendar
import re
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from .contracts import Record

Text = Annotated[str, Field(min_length=1)]
EntityId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]


class Provenance(Record):
    source: Text
    confirmed: StrictBool = False
    scope_job_ids: list[Text] = Field(default_factory=list)


class ProfileRecord(Record):
    id: EntityId
    evidence: Provenance
    # An override applies to one populated top-level domain field, never metadata.
    field_evidence: dict[str, Provenance] = Field(default_factory=dict)

    def domain_values(self) -> dict:
        return self.model_dump(exclude={"id", "evidence", "field_evidence"}, exclude_none=True, exclude_defaults=True)

    @model_validator(mode="after")
    def valid_field_evidence(self):
        values = self.domain_values()
        if set(self.field_evidence) - values.keys():
            raise ValueError("field_evidence must name a populated domain field")
        return self


def date_bounds(value: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", value):
        raise ValueError("Use YYYY, YYYY-MM or YYYY-MM-DD, preserving known precision")
    parts = [int(part) for part in value.split("-")]
    year, month = parts[0], parts[1] if len(parts) > 1 else 1
    low = date(year, month, parts[2] if len(parts) == 3 else 1)
    high_month = parts[1] if len(parts) > 1 else 12
    high = date(year, high_month, parts[2] if len(parts) == 3 else calendar.monthrange(year, high_month)[1])
    return low, high


class DatedRecord(ProfileRecord):
    start_date: Text | None = None
    end_date: Text | None = None
    current: StrictBool | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def partial_date(cls, value):
        if value is not None:
            date_bounds(value)
        return value

    @model_validator(mode="after")
    def coherent_dates(self):
        if self.current is True and self.end_date is not None:
            raise ValueError("A current record cannot have an end_date")
        if self.start_date and self.end_date and date_bounds(self.start_date)[0] > date_bounds(self.end_date)[1]:
            raise ValueError("end_date precedes start_date")
        return self


class PersonalInfo(ProfileRecord):
    full_name: Text | None = None
    preferred_name: Text | None = None
    email: Text | None = None
    phone: Text | None = None
    location: Text | None = None
    linkedin_url: Text | None = None
    github_url: Text | None = None
    website_url: Text | None = None


class WorkAuthorization(ProfileRecord):
    country: Text
    status: Text | None = None
    authorized_to_work: StrictBool | None = None
    requires_sponsorship_now: StrictBool | None = None
    requires_sponsorship_future: StrictBool | None = None
    valid_until: Text | None = None

    @field_validator("valid_until")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date_bounds(value)
        return value


class Education(DatedRecord):
    institution: Text
    degree: Text | None = None
    field_of_study: Text | None = None
    location: Text | None = None
    gpa: Text | None = None
    thesis: Text | None = None
    highlights: list[Text] = Field(default_factory=list)


class WorkExperience(DatedRecord):
    employer: Text
    title: Text
    employment_type: Text | None = None
    location: Text | None = None
    summary: Text | None = None
    highlights: list[Text] = Field(default_factory=list)
    technologies: list[Text] = Field(default_factory=list)


class Project(DatedRecord):
    name: Text
    role: Text | None = None
    summary: Text | None = None
    highlights: list[Text] = Field(default_factory=list)
    technologies: list[Text] = Field(default_factory=list)
    url: Text | None = None
    work_experience_id: EntityId | None = None


class Publication(ProfileRecord):
    title: Text
    authors: list[Text] = Field(default_factory=list)
    venue: Text | None = None
    year: int | None = Field(default=None, ge=1000, le=9999, strict=True)
    status: Literal["draft", "submitted", "under_review", "accepted", "published", "withdrawn"] | None = None
    doi: Text | None = None
    url: Text | None = None
    contribution: Text | None = None


class Competition(ProfileRecord):
    name: Text
    organizer: Text | None = None
    year: int | None = Field(default=None, ge=1000, le=9999, strict=True)
    award: Text | None = None
    rank: Text | None = None
    team: Text | None = None
    contribution: Text | None = None
    url: Text | None = None


class Skill(ProfileRecord):
    name: Text
    category: Text | None = None
    proficiency: Text | None = None
    supporting_record_ids: list[EntityId] = Field(default_factory=list)


class Availability(ProfileRecord):
    earliest_start: Text | None = None
    notice_period: Text | None = None

    @field_validator("earliest_start")
    @classmethod
    def valid_date(cls, value):
        if value is not None:
            date_bounds(value)
        return value


class Constraint(Record):
    field: Text
    operator: Literal["equals", "contains", "excludes", "one_of"]
    value: Text | list[Text]

    @model_validator(mode="after")
    def valid_values(self):
        values = self.value if isinstance(self.value, list) else [self.value]
        if not values:
            raise ValueError("Constraint values must not be empty")
        if self.operator == "equals" and len(values) != 1:
            raise ValueError("equals accepts one value; use one_of for alternatives")
        return self


class SearchPreferences(Record):
    directions: list[Text] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    preferred_terms: list[Text] = Field(default_factory=list)
    avoided_terms: list[Text] = Field(default_factory=list)


class Profile(Record):
    schema_version: Literal[2] = 2
    id: EntityId = "local"
    personal: PersonalInfo
    work_authorization: list[WorkAuthorization] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    competitions: list[Competition] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    availability: Availability | None = None
    preferences: SearchPreferences = Field(default_factory=SearchPreferences)

    def records(self) -> list[tuple[str, ProfileRecord]]:
        records = [("personal", self.personal)]
        for section in (
            "work_authorization",
            "education",
            "work_experience",
            "projects",
            "publications",
            "competitions",
            "skills",
        ):
            records.extend((section, item) for item in getattr(self, section))
        if self.availability is not None:
            records.append(("availability", self.availability))
        return records

    @model_validator(mode="before")
    @classmethod
    def reject_legacy(cls, value):
        if isinstance(value, dict) and ("facts" in value or value.get("schema_version", 2) != 2):
            raise ValueError(
                "Legacy profile: convert to typed profile v2 with source review; original data is unchanged"
            )
        return value

    @model_validator(mode="after")
    def valid_references(self):
        records = self.records()
        ids = [item.id for _, item in records]
        if len(ids) != len(set(ids)):
            raise ValueError("Profile record IDs must be globally unique")
        work_ids = {item.id for item in self.work_experience}
        if any(item.work_experience_id and item.work_experience_id not in work_ids for item in self.projects):
            raise ValueError("Project references missing work experience")
        background_ids = {
            item.id
            for section, item in records
            if section in {"education", "work_experience", "projects", "publications", "competitions"}
        }
        if any(set(item.supporting_record_ids) - background_ids for item in self.skills):
            raise ValueError("Skill references missing background records")
        return self
