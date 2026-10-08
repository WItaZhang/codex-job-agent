"""Contracts for analyses shown to the user: causes, proposals, and the day's report."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .changes import Change, SetCompensation
from .domain import InvariantError, Job
from .replay import ReplayResult

FIXED_CHOICES = ("feedback", "no_change")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cause(_Model):
    id: str
    refs: list[str]
    type: Literal["uncovered", "too_weak", "entry_wrong", "scope", "salary"]
    label: str = ""
    misattributed_by: list[str] = Field(default_factory=list)


class Proposal(_Model):
    id: str
    analysis_id: str
    base_version: int
    cause_id: str | None
    origin: Literal["analysis", "feedback"]
    changes: list[Change]
    evidence: list[str]
    new_keys: list[str] = Field(default_factory=list)
    replay: ReplayResult | None = None
    fixes_targets: bool | None = None
    warning: bool = False
    is_hard: bool = False
    requires_input: list[str] = Field(default_factory=list)

    def resolved_changes(self, inputs: dict | None) -> list[Change]:
        inputs = inputs or {}
        missing = [name for name in self.requires_input if name not in inputs]
        if missing:
            raise InvariantError(f"This option needs the user to provide: {', '.join(missing)}")
        return [
            change.model_copy(update={"floor": float(inputs["floor"])})
            if isinstance(change, SetCompensation) and change.floor is None
            else change
            for change in self.changes
        ]


class Analysis(_Model):
    id: str
    day: str
    kind: Literal["false_positive", "false_negative", "reason_conflict"]
    direction: int
    base_version: int
    label_ids: list[str]
    step1: Literal["skip", "ask", "ask_scope"]
    causes: list[Cause]
    chosen_cause: str | None = None
    proposals: list[Proposal] = Field(default_factory=list)
    rejected_suggestions: list[str] = Field(default_factory=list)
    status: Literal["open", "accepted", "declined", "resolved"] = "open"

    @property
    def choices(self) -> list[str]:
        return [p.id for p in self.proposals] + list(FIXED_CHOICES)


class MisreadRequest(_Model):
    job_id: str
    label_id: str
    tags: list[str]


class DayReport(_Model):
    analyses: list[Analysis] = Field(default_factory=list)
    misread: list[MisreadRequest] = Field(default_factory=list)
    consistent: list[str] = Field(default_factory=list)


class LabelInput(_Model):
    """One label the user gave today. Reason chips must be tags of the job."""

    job: Job
    value: Literal["want", "reject", "misread"]
    slot: Literal["recommended", "exploration"]
    reason_keys: list[str] = Field(default_factory=list)
    reason_text: str | None = None
    label_id: str | None = None
