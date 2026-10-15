"""Daily selection: the top-ranked jobs plus clearly marked exploration slots, one listing per posting. Pure."""

from collections.abc import Sequence

from . import duplicates
from .config import Settings
from .domain import IntentModel, Job, Level
from .scoring import ScoreResult, score_job

_AVOIDED = (Level.avoid, Level.strong_avoid)


def rank_postings(
    intent: IntentModel,
    candidates: Sequence[Job],
    settings: Settings,
    shown: Sequence[Job] = (),
    others: Sequence[Job] = (),
) -> list[tuple[ScoreResult, int, str]]:
    """Today's candidates that are not hard-excluded, one per posting not shown yet, best first, as
    `(score, order, job_id)` (spec 4.6, section 10 item 82). `candidates` are tagged open jobs not shown yet;
    `shown` the jobs shown on earlier days, in display order; `others` the remaining tagged jobs (closed). All of
    them carry their corrected tags. Postings are found among all of them, so a shown listing keeps its posting
    out even when the listing linking another one to it is closed or excluded; a posting is represented by its
    best-ranked candidate."""
    unshown = list(candidates) + list(others)
    scored = [(score_job(intent, job.tags, job.salary, settings), order, job) for order, job in enumerate(unshown)]
    ranked = sorted(scored, key=lambda item: (-item[0].score, item[1]))
    best = {job.id: (result, order) for result, order, job in ranked}
    eligible = {job.id for job in candidates if not best[job.id][0].excluded}
    shown_ids = {job.id for job in shown}
    chosen = []
    for listings in duplicates.group(list(shown) + [job for _, _, job in ranked], settings):
        if shown_ids.intersection(job.id for job in listings):
            continue
        top = next((job for job in listings if job.id in eligible), None)  # listings keep the ranked order
        if top is not None:
            chosen.append((*best[top.id], top.id))
    return sorted(chosen, key=lambda item: (-item[0].score, item[1]))


def select_daily(
    intent: IntentModel,
    candidates: Sequence[Job],
    settings: Settings,
    shown: Sequence[Job] = (),
    others: Sequence[Job] = (),
) -> list[tuple[str, str]]:
    """`(job_id, slot)` in display order, at most one listing per posting and none of a posting already shown
    (see `rank_postings`). Hard-excluded jobs never enter, not even as exploration."""
    ranked = rank_postings(intent, candidates, settings, shown, others)
    daily = settings.daily
    recommended, rest = ranked[: daily.recommended], ranked[daily.recommended :]

    # Exploration: first jobs held back by avoid entries (best-scored first), then the middle of the rest.
    avoided = [item for item in rest if any(level in _AVOIDED for _, level, _ in item[0].contributions)]
    exploration = avoided[: min(daily.exploration, daily.exploration_max_from_avoid)]
    remaining = [item for item in rest if item not in exploration]
    middle = len(remaining) // 2
    by_middle = sorted(range(len(remaining)), key=lambda i: (abs(i - middle), i))
    exploration += [remaining[i] for i in by_middle][: daily.exploration - len(exploration)]

    return [(item[2], "recommended") for item in recommended] + [(item[2], "exploration") for item in exploration]
