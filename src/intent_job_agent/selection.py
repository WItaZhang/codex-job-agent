"""Daily selection: the top-ranked jobs plus clearly marked exploration slots. Pure."""

from .config import Settings
from .domain import IntentModel, Job, Level
from .scoring import score_job

_AVOIDED = (Level.avoid, Level.strong_avoid)


def select_daily(intent: IntentModel, candidates: list[Job], settings: Settings) -> list[tuple[str, str]]:
    """`(job_id, slot)` in display order. Hard-excluded jobs never enter, not even as exploration."""
    scored = []
    for order, job in enumerate(candidates):
        result = score_job(intent, job.tags, job.salary, settings)
        if not result.excluded:
            scored.append((result, order, job.id))
    ranked = sorted(scored, key=lambda item: (-item[0].score, item[1]))
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
