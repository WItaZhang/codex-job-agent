"""The fetch funnel before tagging: hold back jobs that confidently break a hard constraint, then rank the
rest with the same scoring function the daily selection uses. Pure; nothing is stored, so a changed intent
re-evaluates every untagged job."""

from .config import Settings
from .domain import IntentModel, Job, Level, Vocabulary, expand, parse_ref
from .pretag import Pretag, pretag
from .scoring import score_job


def held_back(intent: IntentModel, pre: Pretag) -> list[str]:
    """Hard constraints the confidently read pre-tags break (empty: keep the job)."""
    tags = [tag for tag in pre.tags if parse_ref(tag).dimension in pre.confident]
    expanded = expand(tags)
    broken, required = [], {}
    for ref, entry in intent.iter_entries():
        dimension = parse_ref(ref).dimension
        if dimension not in pre.confident:
            continue
        if entry.level == Level.exclude and ref in expanded:
            broken.append(ref)
        elif entry.level == Level.require:
            required.setdefault(dimension, []).append(ref)
    for refs in required.values():
        if not set(refs) & expanded:
            broken.extend(refs)
    return broken


def rank(intent: IntentModel, vocab: Vocabulary, jobs: list[Job], settings: Settings) -> tuple[list, int]:
    """(ranked [(job, pretag)], number held back). Ties keep the incoming order."""
    kept, held = [], 0
    for job in jobs:
        pre = pretag(job, vocab)
        if held_back(intent, pre):
            held += 1
            continue
        kept.append((job, pre, score_job(intent, list(pre.tags), job.salary, settings).score))
    kept.sort(key=lambda item: -item[2])
    return [(job, pre) for job, pre, _ in kept], held
