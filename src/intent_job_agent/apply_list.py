"""The apply list (spec 4.9): which tagged open jobs go to the next step, in what order and why, one row per
posting found on several boards, and how well the current intent agrees with the labels of the last labelled days.
Pure; the same scoring as everywhere else."""

from collections.abc import Callable
from dataclasses import dataclass

from . import duplicates
from .config import Settings
from .domain import IntentModel, Job, Label, parse_ref
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
class Posting:
    """One posting with all its listings (spec 4.9 item 6). Its row takes its place and its why from the best
    candidate and applies with `use`."""

    listings: tuple[Job, ...]  # candidates first, in list order; with their corrected tags
    offered: tuple[Job, ...]  # the listings a reject does not hold back: the row's places and other links
    places: tuple[str, ...]  # location tags of the offered listings, without repeats
    candidates: tuple[Candidate, ...]  # the offered listings that enter the list on their own, best first
    wanted: bool  # a listing is labelled want
    blocked: bool  # a reject keeps the whole posting out
    use: Candidate | None  # the candidate to apply with: one with a cover letter, else the best

    @property
    def listed(self) -> bool:
        return bool(self.candidates) and not self.blocked


def _place_only(label: Label) -> bool:
    """A reject whose reasons are all location keys (spec section 10 item 85)."""
    return bool(label.reason_keys) and all(parse_ref(ref).dimension == "location" for ref in label.reason_keys)


def postings(
    chosen: list[Candidate],
    others: list[tuple[Job, list[str]]],
    labels: list[Label],
    settings: Settings,
    has_letter: Callable[[str], bool] = lambda job_id: False,
) -> list[Posting]:
    """`chosen` (from `candidates`) and `others` (listings that are not candidates but were listed before or
    labelled, with their tags) grouped by posting. Candidates come first in list order and the others by id, so a
    posting's first listing is its best candidate. A reject keeps the posting out unless a listing is wanted; a
    reject only for its place keeps out just its own city. Wanted postings first; otherwise list order."""
    values: dict[str, set[str]] = {}
    place_only = set()
    for label in labels:
        values.setdefault(label.job_id, set()).add(label.value)
        if label.value == "reject" and _place_only(label):
            place_only.add(label.job_id)
    tags = {c.job.id: c.tags for c in chosen} | {job.id: job_tags for job, job_tags in others}
    by_id = {c.job.id: c for c in chosen}
    jobs = [c.job for c in chosen] + sorted((job for job, _ in others), key=lambda job: job.id)
    found = []
    for listings in duplicates.group([job.model_copy(update={"tags": tags[job.id]}) for job in jobs], settings):
        wanted = any("want" in values.get(job.id, ()) for job in listings)
        rejected = [job for job in listings if "reject" in values.get(job.id, ())]
        whole = not wanted and any(job.id not in place_only for job in rejected)
        held = (
            set()
            if wanted
            else {j.id for r in rejected for j in listings if j.id == r.id or duplicates.same_city(j, r)}
        )
        offered = tuple(job for job in listings if job.id not in held)
        own = tuple(by_id[job.id] for job in offered if job.id in by_id)
        with_letter = [c for c in own if has_letter(c.job.id)] if len(own) > 1 else []
        places = [tag.removeprefix("location.") for job in offered for tag in job.tags if tag.startswith("location.")]
        found.append(
            Posting(
                listings=tuple(listings),
                offered=offered,
                places=tuple(dict.fromkeys(places)),
                candidates=own,
                wanted=wanted,
                blocked=whole,
                use=(with_letter or own or [None])[0],
            )
        )
    return sorted(found, key=lambda p: not p.wanted)


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
