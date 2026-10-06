"""Strict contracts for synthetic judge calibration; labels never enter packets."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["redundant_padding", "same_length_degradation", "useful_longer"]
KINDS = ("redundant_padding", "same_length_degradation", "useful_longer")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Case(Strict):
    case_id: str = Field(min_length=1)
    provenance: Literal["synthetic"]
    kind: Kind
    job_context: str = Field(min_length=1)
    confirmed_facts: list[str] = Field(min_length=1)
    reference: str = Field(min_length=1)
    alternative: str = Field(min_length=1)
    preferred: Literal["reference", "alternative", "tie"]
    control_explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def controlled_contrast(self):
        if any(not value.strip() for value in self.confirmed_facts):
            raise ValueError("confirmed facts cannot be blank")
        ref_words, alt_words = len(self.reference.split()), len(self.alternative.split())
        if self.reference == self.alternative:
            raise ValueError("contrast must contain distinct texts")
        if self.kind == "same_length_degradation":
            if ref_words != alt_words or self.preferred != "reference":
                raise ValueError("degradation must preserve word count and prefer reference")
        elif alt_words <= ref_words:
            raise ValueError("padding and useful-longer alternatives must be longer")
        elif self.kind == "redundant_padding" and self.preferred != "tie":
            raise ValueError("padding has equal factual utility; expected criterion is tie")
        elif self.kind == "useful_longer" and self.preferred != "alternative":
            raise ValueError("useful-longer positive control must prefer alternative")
        return self


class Judge(Strict):
    judge_id: str = Field(min_length=1)
    judge_version: str = Field(min_length=1)
    provenance: Literal["model_proxy", "synthetic_metric_fixture"]
    # Record the actual model and inference configuration, or the fixture generator.
    configuration: dict[str, str] = Field(min_length=1)
    # Host-reported exact model metadata is necessary for a reusable release gate.
    # Unknown metadata supports protocol checks but must never qualify a judge.
    metadata_complete: bool


class Rubric(Strict):
    version: str = Field(min_length=1)
    role: str = Field(min_length=1)
    criteria: list[str] = Field(min_length=1)


class Gates(Strict):
    minimum_distinct_cases_per_kind: int = Field(ge=1)
    minimum_resolved_pairs_per_kind: int = Field(ge=1)
    minimum_completeness: float = Field(ge=0, le=1)
    maximum_unknown_rate: float = Field(ge=0, le=1)
    maximum_padding_preference: float = Field(ge=0, le=1)
    maximum_position_inconsistency: float = Field(ge=0, le=1)
    maximum_positive_control_failure: float = Field(ge=0, le=1)


class Config(Strict):
    experiment_name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    cases_path: str = Field(min_length=1)
    log_dir: str = Field(min_length=1)
    seed: int
    repetitions: int = Field(ge=1)
    maximum_trials: int = Field(ge=2)
    judge: Judge
    rubric: Rubric
    gates: Gates


class Evidence(Strict):
    candidate: Literal["A", "B"]
    quote: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Result(Strict):
    trial_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    judge_id: str = Field(min_length=1)
    judge_version: str = Field(min_length=1)
    judge_config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rubric_version: str = Field(min_length=1)
    rubric_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance: Literal["model_proxy", "synthetic_metric_fixture"]
    reviewer_session_id: str = Field(min_length=1)
    winner: Literal["A", "B", "tie", "unknown"]
    evidence: list[Evidence]
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def cited_comparison(self):
        if not self.rationale.strip() or not self.reviewer_session_id.strip():
            raise ValueError("rationale and reviewer session cannot be blank")
        if self.winner != "unknown" and {item.candidate for item in self.evidence} != {"A", "B"}:
            raise ValueError("resolved decisions must cite both candidates")
        return self
