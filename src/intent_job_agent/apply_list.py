"""The apply list (spec 4.9): which tagged open jobs go to the next step, in what order and why, and how well
the current intent agrees with the labels of the last labelled days. Pure; the same scoring as everywhere else."""

from dataclasses import dataclass

from .config import Settings
from .domain import IntentModel, Job, Label
from .replay import LabelSource, evaluate
from .scoring import ScoreResult, is_recommended, score_job


@dataclass(frozen=True)
class Candidate:
    job: Job
    tags: list[str]
    wanted: bool  # the user labelled it want, whatever the current score
    result: ScoreResult


def candidates(
    intent: IntentModel, jobs: list[tuple[Job, list[str]]], labels: list[Label], settings: Settings
) -> list[Candidate]:
    """Jobs (tagged, open, with their effective tags) that enter the list: recommended by the current intent or
    labelled want, never labelled reject (superseded labels included). Wanted first, then by score; ties keep the
    incoming order."""
    values = {label.job_id: label.value for label in labels if label.value in ("want", "reject")}
    chosen = []
    for job, tags in jobs:
        value = values.get(job.id)
        if value == "reject":
            continue
        result = score_job(intent, tags, job.salary, settings)
        if value == "want" or is_recommended(result, settings):
            chosen.append(Candidate(job, tags, value == "want", result))
    return sorted(chosen, key=lambda c: (not c.wanted, -c.result.score))


@dataclass(frozen=True)
class Agreement:
    days: tuple[str, ...]  # the labelled days in the window, oldest first
    want_agree: int
    want_total: int
    reject_agree: int
    reject_total: int

    def text(self) -> str:
        total = self.want_total + self.reject_total
        if total == 0:
            return "还没有 label"
        agree = self.want_agree + self.reject_agree
        return (
            f"最近 {len(self.days)} 个标注日一致率 {round(100 * agree / total)}%（{agree}/{total}；"
            f"想投 {self.want_agree}/{self.want_total}，不要 {self.reject_agree}/{self.reject_total}）"
        )


def agreement(intent: IntentModel, source: LabelSource, settings: Settings, window_days: int) -> Agreement:
    """Agreement of the current intent with the active want/reject labels of the last `window_days` days that
    have any label (days without labels do not count, so a break keeps the last labelled days)."""
    days = tuple(sorted({label.day for label in source.labels()})[-window_days:])
    evaluation = evaluate(intent, source, settings)  # active labels only: no misread, no superseded
    counts = {"want": [0, 0], "reject": [0, 0]}
    for label_id, label in evaluation.labels.items():
        if label.day in days:
            counts[label.value][1] += 1
            counts[label.value][0] += label_id in evaluation.agree
    return Agreement(days, *counts["want"], *counts["reject"])
