"""Strict model-proxy audit contracts. Claims of isolation are recorded, not authenticated."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .rubric import RUBRIC_VERSION


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Reviewer(AuditRecord):
    model: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    context_mode: Literal["fresh_no_history"]
    isolation: Literal["instruction_only", "tool_limited"]


class Reviewers(AuditRecord):
    hiring: Reviewer
    factual: Reviewer

    @model_validator(mode="after")
    def separate_contexts(self):
        if self.hiring.run_id == self.factual.run_id:
            raise ValueError("Hiring and factual reviews require separate fresh contexts")
        return self


class Citation(AuditRecord):
    source_id: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class Issue(AuditRecord):
    id: str = Field(min_length=1)
    view: Literal["hiring", "factual"]
    category: Literal["relevance", "clarity", "specificity", "layout", "factual", "unsupported_claim", "other"]
    severity: Literal["minor", "major", "critical"]
    summary: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)
    recommendation: str = Field(min_length=1)


class QualityReport(AuditRecord):
    ticket_id: str = Field(min_length=1)
    bundle_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    rubric_version: Literal[RUBRIC_VERSION] = RUBRIC_VERSION
    provenance: Literal["model_proxy"] = "model_proxy"
    reviewers: Reviewers
    verdict: Literal["no_issue_found", "issues_found", "inconclusive"]
    issues: list[Issue] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    elapsed_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def consistent_result(self):
        if (self.verdict == "issues_found") != bool(self.issues):
            raise ValueError("issues_found requires issues; other verdicts must not contain issues")
        if self.verdict == "inconclusive" and not any(item.strip() for item in self.limitations):
            raise ValueError("Inconclusive review requires a limitation")
        ids = [item.id for item in self.issues]
        if len(ids) != len(set(ids)):
            raise ValueError("Issue IDs must be unique")
        return self
