"""Deterministic scoring of a job against the intent model. The model never scores."""

from dataclasses import dataclass

from .config import Settings
from .domain import IntentModel, Level, Salary, expand, parse_ref


@dataclass(frozen=True)
class ScoreResult:
    excluded: bool
    score: float
    excluded_by: tuple[str, ...] = ()
    contributions: tuple[tuple[str, Level, float], ...] = ()


def effective_level(intent: IntentModel, ref: str, expanded: set[str]) -> Level:
    """Entry level at `ref`, replaced by the first exception whose condition the job meets."""
    entry = intent.entry(ref)
    for rule in entry.exceptions:
        if rule.when in expanded:
            return rule.level
    return entry.level


def score_job(intent: IntentModel, tags: list[str], salary: Salary | None, settings: Settings) -> ScoreResult:
    expanded = expand(tags)
    excluded_by = []
    required: dict[str, set[str]] = {}
    for ref, entry in intent.iter_entries():
        if entry.level == Level.exclude and ref in expanded:
            excluded_by.append(ref)
        elif entry.level == Level.require:
            required.setdefault(parse_ref(ref).dimension, set()).add(ref)
    # Requires within one dimension are alternatives (OR); dimensions combine with AND.
    for refs in required.values():
        if not refs & expanded:
            excluded_by.extend(sorted(refs))

    points = settings.scoring.level_points
    contributions = []
    for tag in dict.fromkeys(tags):
        governing = intent.governing(tag)  # location: only the most specific entry counts
        if governing is None:
            continue
        ref = governing[0]
        level = effective_level(intent, ref, expanded)
        if not level.hard:
            contributions.append((ref, level, points[level.value]))
    score = sum(value for _, _, value in contributions)

    comp = intent.compensation
    if comp and salary and (salary.currency, salary.period) == (comp.currency, comp.period):
        first, second = (
            (salary.max, salary.min) if settings.scoring.salary_compare == "max" else (salary.min, salary.max)
        )
        value = first if first is not None else second
        if value is not None and value < comp.floor:
            score -= settings.scoring.salary_below_floor_penalty
    return ScoreResult(bool(excluded_by), score, tuple(excluded_by), tuple(contributions))


def is_recommended(result: ScoreResult, settings: Settings) -> bool:
    return not result.excluded and result.score >= settings.scoring.recommend_threshold


def entry_sign(intent: IntentModel, tag: str, tags: list[str]) -> int:
    """Direction (+1/-1/0) of the entry governing `tag` for a job with `tags`."""
    governing = intent.governing(tag)
    if governing is None:
        return 0
    level = effective_level(intent, governing[0], expand(tags))
    return (level.rank > 0) - (level.rank < 0)
