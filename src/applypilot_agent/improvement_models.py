"""Development-only root-cause proposals; never instructions to the live executor."""

from typing import Literal

from pydantic import Field

from .models import Record


class ImprovementProposal(Record):
    source_alert_id: str = Field(min_length=1)
    author: str = Field(min_length=1)
    root_cause: Literal["source_facts", "matching", "materials", "browser", "skill", "evaluator", "unknown"]
    diagnosis: str = Field(min_length=1)
    supporting_evidence: list[str] = Field(min_length=1)
    corrective_action: str = Field(min_length=1)
    regression_cases: list[str] = Field(min_length=1)
    expected_effect: str = Field(min_length=1)


class ImprovementValidation(Record):
    proposal_id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    baseline_revision: str = Field(min_length=1)
    candidate_revision: str = Field(min_length=1)
    regression_report: str = Field(min_length=1)
    evaluation_report: str = Field(min_length=1)
    decision: Literal["accept", "reject"]
    rationale: str = Field(min_length=1)
