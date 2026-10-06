"""Strict contracts for independent, evidence-labelled agent evaluations.

These types intentionally do not import the runtime. A runtime change must not
silently change the acceptance criteria used to evaluate that change.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Expectations(StrictRecord):
    eligible: bool | None
    interested: bool | None
    high_priority: bool | None
    must_review: bool
    allowed_fact_ids: list[str]
    expected_outcome: list[str] | None = None

    @field_validator("allowed_fact_ids", "expected_outcome")
    @classmethod
    def unique_nonempty_values(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        if len(values) != len(set(values)) or any(not value.strip() for value in values):
            raise ValueError("values must be unique, nonempty strings")
        return values

    @model_validator(mode="after")
    def outcome_list_is_not_empty(self) -> "Expectations":
        if self.expected_outcome == []:
            raise ValueError("expected_outcome must contain an outcome or be null")
        return self


class Case(StrictRecord):
    task_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    split: Literal["dev", "holdout"]
    provenance: Literal["synthetic", "human_verified"]
    description: str = Field(min_length=1)
    label_source: str = Field(min_length=1)
    expected: Expectations


class Prediction(StrictRecord):
    task_id: str = Field(min_length=1)
    selected: bool
    state: str = Field(min_length=1)
    claim_fact_ids: list[str]
    submitted: bool
    review_obtained: bool

    @field_validator("claim_fact_ids")
    @classmethod
    def unique_fact_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(not value.strip() for value in values):
            raise ValueError("claim_fact_ids must be unique, nonempty strings")
        return values


class QualityLabel(StrictRecord):
    """An external comparison, never an agent's own quality score.

    Synthetic labels exercise the plumbing only. Model-proxy and human judgements
    remain separate in reports; agreement does not promote one into the other.
    """

    task_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    provenance: Literal["synthetic", "human_verified", "model_proxy"]
    winner: Literal["baseline", "candidate", "tie", "unknown"]
    blinded: bool
    presented_first: Literal["baseline", "candidate"]
    evidence: str = Field(min_length=1)


class Gates(StrictRecord):
    max_unauthorized_submissions: int = Field(ge=0)
    max_invalid_submissions: int = Field(ge=0)
    max_unsupported_fact_references: int = Field(ge=0)
    minimum_prediction_completeness: float = Field(ge=0, le=1)
    minimum_coverage: float = Field(ge=0, le=1)
    maximum_false_selection_rate: float = Field(ge=0, le=1)
    minimum_outcome_accuracy: float = Field(ge=0, le=1)


class EvaluationConfig(StrictRecord):
    experiment_name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    cases_path: str = Field(min_length=1)
    candidate_predictions_path: str = Field(min_length=1)
    baseline_predictions_path: str | None
    quality_labels_path: str | None
    log_dir: str = Field(min_length=1)
    split: Literal["all", "dev", "holdout"]
    gates: Gates
