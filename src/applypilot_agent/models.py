"""Validated domain contracts. No model calls, filesystem access or database code."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Fact(Record):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)
    confirmed: bool = False
    key: str | None = None
    value: str | bool | None = None
    scope_job_ids: list[str] = Field(default_factory=list)


class Constraint(Record):
    field: str
    operator: Literal["equals", "contains", "excludes", "one_of"]
    value: str | list[str]

    @model_validator(mode="after")
    def valid_values(self):
        values = self.value if isinstance(self.value, list) else [self.value]
        if not self.field.strip() or not values or any(not item.strip() for item in values):
            raise ValueError("Constraint field and values must not be empty")
        if self.operator == "equals" and len(values) != 1:
            raise ValueError("equals accepts one value; use one_of for alternatives")
        return self


class Profile(Record):
    id: str = "local"
    name: str = Field(min_length=1)
    facts: list[Fact] = Field(default_factory=list)
    directions: list[str] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    preferred_terms: list[str] = Field(default_factory=list)
    avoided_terms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_facts(self):
        ids = [fact.id for fact in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError("Fact IDs must be unique")
        keys = [fact.key for fact in self.facts if fact.key]
        if len(keys) != len(set(keys)):
            raise ValueError("Fact keys must be unique; resolve conflicting answers first")
        return self


class Job(Record):
    id: str = Field(min_length=1)
    source: str
    source_id: str
    url: str
    apply_url: str
    title: str
    company: str
    location: str = ""
    description: str
    attributes: dict[str, str] = Field(default_factory=dict)
    fetched_at: str = ""

    @field_validator("url", "apply_url")
    @classmethod
    def http_url(cls, value):
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Job URLs must be absolute HTTP(S) URLs without credentials")
        return value


class Assessment(Record):
    job_id: str
    job_hash: str
    profile_hash: str
    fit: Literal["strong", "possible", "no", "unknown"]
    eligibility: Literal["pass", "fail", "unknown"]
    reasons: list[str] = Field(min_length=1)
    evidence_fact_ids: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    source: Literal["baseline", "codex", "user"] = "codex"
    priority: float = Field(default=0, ge=0, le=1)


class Claim(Record):
    text: str = Field(min_length=1)
    fact_ids: list[str] = Field(min_length=1)


class Answer(Record):
    value: str | bool
    fact_ids: list[str] = Field(min_length=1)


class Attachment(Record):
    path: str
    sha256: str
    label: str = "resume"
    fact_ids: list[str] = Field(default_factory=list)


class Packet(Record):
    job_id: str
    job_hash: str
    profile_hash: str
    answers: dict[str, Answer] = Field(default_factory=dict)
    claims: list[Claim] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    # Browser module validates the executable plan independently.
    browser_plan: dict = Field(default_factory=dict)


class Policy(Record):
    auto_fit: list[Literal["strong", "possible"]] = Field(default_factory=list)
    review_companies: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    daily_submission_limit: int = Field(default=50, ge=0, le=1000)
    require_review_for_rewrites: bool = True


class Decision(Record):
    action: Literal["skip", "clarify", "review", "auto"]
    reasons: list[str]


ApplicationState = Literal[
    "discovered",
    "assessed",
    "needs_info",
    "review",
    "ready",
    "submitting",
    "unknown",
    "submitted",
    "skipped",
    "retryable",
]
