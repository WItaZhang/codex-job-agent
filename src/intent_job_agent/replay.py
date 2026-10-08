"""Replay: rescore every active historical label under an intent model."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from .config import Settings
from .domain import IntentModel, Job, Label
from .scoring import ScoreResult, is_recommended, score_job


class LabelSource(Protocol):
    def labels(self) -> list[Label]: ...
    def job(self, job_id: str) -> Job: ...
    def effective_tags(self, job_id: str) -> list[str]: ...


@dataclass
class Evaluation:
    agree: set[str]
    scores: dict[str, ScoreResult]
    labels: dict[str, Label]


def active_labels(source: LabelSource) -> list[Label]:
    """want/reject labels that still count: not superseded."""
    return [label for label in source.labels() if label.value != "misread" and not label.superseded]


def evaluate(intent: IntentModel, source: LabelSource, settings: Settings) -> Evaluation:
    agree, scores, labels = set(), {}, {}
    for label in active_labels(source):
        result = score_job(intent, source.effective_tags(label.job_id), source.job(label.job_id).salary, settings)
        scores[label.id] = result
        labels[label.id] = label
        if is_recommended(result, settings) == (label.value == "want"):
            agree.add(label.id)
    return Evaluation(agree, scores, labels)


class Flip(BaseModel):
    label_id: str
    title: str
    value: str
    now: str  # "fixed" | "broken"


class ReplayResult(BaseModel):
    before: int
    after: int
    total: int
    fixed: list[str]
    broken: list[str]
    wanted_excluded: list[str]
    shown: list[Flip]

    def line(self) -> str:
        return (
            f"修好 {len(self.fixed)} 条；历史一致率 {self.before}/{self.total} → {self.after}/{self.total}；"
            f"打破 {len(self.broken)} 条"
        )


def replay(
    before: Evaluation, after_intent: IntentModel, source: LabelSource, settings: Settings
) -> tuple[ReplayResult, Evaluation]:
    after = evaluate(after_intent, source, settings)
    fixed = sorted(after.agree - before.agree)
    broken = sorted(before.agree - after.agree)
    wanted_excluded = sorted(
        label_id
        for label_id, label in after.labels.items()
        if label.value == "want" and after.scores[label_id].excluded and not before.scores[label_id].excluded
    )
    shown = [
        Flip(label_id=i, title=source.job(after.labels[i].job_id).title, value=after.labels[i].value, now=kind)
        for kind, ids in (("broken", broken), ("fixed", fixed))
        for i in ids
    ][: settings.analysis.max_flipped_shown]
    result = ReplayResult(
        before=len(before.agree),
        after=len(after.agree),
        total=len(after.labels),
        fixed=fixed,
        broken=broken,
        wanted_excluded=wanted_excluded,
        shown=shown,
    )
    return result, after
