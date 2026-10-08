"""Root-cause analysis. Code enumerates causes and edits; the model only maps text and ranks.

Every proposal is validated by `apply_changes` and replayed against history before the user
sees it. Nothing here writes the intent.
"""

from collections import Counter
from dataclasses import dataclass

from .changes import (
    AddEntry,
    AddException,
    Change,
    DeleteEntry,
    RemoveException,
    SetCompensation,
    SetLevel,
    apply_changes,
    change_class,
    change_cost,
    target_level,
)
from .config import Settings
from .domain import (
    LEVELS_BY_RANK,
    SOFT_TWIN,
    IntentEntry,
    IntentModel,
    InvariantError,
    Label,
    Level,
    Vocabulary,
    expand,
    parse_ref,
    rank,
)
from .proposals import Cause, Proposal
from .replay import Evaluation, active_labels, evaluate, replay
from .scoring import entry_sign, is_recommended, score_job
from .store import Store, new_id


@dataclass
class Context:
    intent: IntentModel
    vocab: Vocabulary
    store: Store
    settings: Settings

    def tags(self, label: Label) -> list[str]:
        return self.store.effective_tags(label.job_id)


# --- classification -----------------------------------------------------------------


def classify(ctx: Context, label: Label) -> str | None:
    """Mismatch kind for a want/reject label, or None when it agrees with the recommendation."""
    if label.slot == "recommended" and label.value == "reject":
        return "false_positive"
    if label.slot == "exploration" and label.value == "want":
        return "false_negative"
    tags = ctx.tags(label)
    if any(entry_sign(ctx.intent, ref, tags) == -label.direction for ref in label.reason_keys):
        return "reason_conflict"
    return None


def supporting_refs(ctx: Context, label: Label) -> list[str]:
    """Entries a consistent label supports (their direction matches the label)."""
    tags = ctx.tags(label)
    refs = []
    for tag in tags:
        governing = ctx.intent.governing(tag)
        if governing and entry_sign(ctx.intent, tag, tags) == label.direction:
            refs.append(governing[0])
    return sorted(set(refs))


def common_groups(ctx: Context, labels: list[Label]) -> list[tuple[list[Label], list[str]]]:
    """Group no-reason mismatches of one kind by shared tags; singletons stay alone."""
    remaining = list(labels)
    groups = []
    while remaining:
        counts = Counter(tag for label in remaining for tag in set(ctx.tags(label)))
        shared = [(n, tag) for tag, n in counts.items() if n >= 2]
        if not shared:
            groups.extend(([label], sorted(set(ctx.tags(label)))) for label in remaining)
            break
        best = max(n for n, _ in shared)
        anchor = min(tag for n, tag in shared if n == best)
        group = [label for label in remaining if anchor in ctx.tags(label)]
        common = set.intersection(*(set(ctx.tags(label)) for label in group))
        groups.append((group, sorted(common)))
        remaining = [label for label in remaining if label not in group]
    return groups


# --- causes -----------------------------------------------------------------------------


def misattributed_by(ctx: Context, ref: str, entry: IntentEntry) -> list[str]:
    """Other entries that explain every label this entry was derived from, if any."""
    sign = (entry.level.rank > 0) - (entry.level.rank < 0)
    evidence = [ctx.store.label(i) for i in entry.evidence]
    evidence = [label for label in evidence if not label.superseded and label.value != "misread"]
    if not evidence:
        return []
    explanations = []
    for label in evidence:
        tags = ctx.tags(label)
        others = set()
        for tag in tags:
            governing = ctx.intent.governing(tag)
            if governing and governing[0] != ref and entry_sign(ctx.intent, tag, tags) == sign:
                others.add(governing[0])
        if not others:
            return []
        explanations.append(others)
    common = set.intersection(*explanations)
    return sorted(common or set.union(*explanations))


def describe(ctx: Context, refs: list[str], cause_type: str) -> str:
    current = ", ".join(f"{r}（{ctx.intent.entry(r).level if ctx.intent.entry(r) else '未设置'}）" for r in refs)
    return {
        "uncovered": f"意图里还没有 {current}",
        "too_weak": f"{current} 的力度不够",
        "entry_wrong": f"{current} 可能设错了",
        "scope": f"范围：{current}",
        "salary": "薪资",
    }[cause_type]


def cause_for(ctx: Context, refs: list[str], direction: int) -> Cause:
    if len(refs) == 1:
        entry = ctx.intent.entry(refs[0])
        if entry is None:
            cause_type, by = "uncovered", []
        else:
            sign = (entry.level.rank > 0) - (entry.level.rank < 0)
            cause_type = "too_weak" if sign == direction else "entry_wrong"
            by = misattributed_by(ctx, refs[0], entry)
            if by:
                cause_type = "entry_wrong"
    else:
        cause_type, by = "scope", []
    return Cause(
        id=new_id("cause"),
        refs=refs,
        type=cause_type,
        label=describe(ctx, refs, cause_type),
        misattributed_by=by,
    )


def candidate_refs(ctx: Context, tags: list[str]) -> list[str]:
    """Each tag, plus the country entry that governs a city without its own entry."""
    refs = []
    for tag in tags:
        refs.append(tag)
        governing = ctx.intent.governing(tag)
        if governing and governing[0] != tag:
            refs.append(governing[0])
    return list(dict.fromkeys(refs))


def enumerate_causes(ctx: Context, tags: list[str], direction: int) -> list[Cause]:
    causes = [cause_for(ctx, [ref], direction) for ref in candidate_refs(ctx, tags)]
    priority = {"entry_wrong": 2, "too_weak": 1, "uncovered": 1}
    # Misattributed entries first, then weak or missing entries, then other possibly wrong entries.
    return sorted(causes, key=lambda c: 0 if c.misattributed_by else priority[c.type])


# --- proposals ----------------------------------------------------------------------------


@dataclass
class Candidate:
    changes: list[Change]
    twin: tuple | None = None  # summary set of the soft counterpart of a hard candidate

    @property
    def key(self) -> frozenset:
        return frozenset(change.summary() for change in self.changes)


def cleanup(entry: IntentEntry | None, ref: str, level: Level) -> list[Change]:
    """Exceptions made redundant (or invalid) by a new default level are removed in the same diff."""
    if entry is None:
        return []
    return [RemoveException(ref=ref, when=rule.when) for rule in entry.exceptions if level.hard or rule.level == level]


def move(intent: IntentModel, ref: str, level: Level | None) -> list[Change]:
    """Edits that put `ref` at `level` (None = no entry)."""
    entry = intent.entry(ref)
    if entry is None:
        return [] if level is None else [AddEntry(ref=ref, level=level)]
    if level is None:
        return [DeleteEntry(ref=ref, from_level=entry.level)]
    if level == entry.level:
        return []
    return [*cleanup(entry, ref, level), SetLevel(ref=ref, from_level=entry.level, to_level=level)]


def levels_toward(current: Level | None, direction: int, include_neutral: bool) -> list[Level | None]:
    targets = [level for level in LEVELS_BY_RANK if (level.rank - rank(current)) * direction > 0]
    if include_neutral and current is not None and (0 - rank(current)) * direction > 0:
        targets.insert(0, None)
    return sorted(targets, key=lambda level: abs(rank(level) - rank(current)))


def single_ref_candidates(ctx: Context, ref: str, direction: int) -> list[Candidate]:
    entry = ctx.intent.entry(ref)
    candidates = []
    for level in levels_toward(entry.level if entry else None, direction, include_neutral=True):
        changes = move(ctx.intent, ref, level)
        twin = None
        if level is not None and level.hard:
            soft = move(ctx.intent, ref, SOFT_TWIN[level])
            twin = frozenset(change.summary() for change in soft) if soft else None
        if changes:
            candidates.append(Candidate(changes, twin))
    return candidates


def multi_ref_candidates(ctx: Context, refs: list[str], direction: int) -> list[Candidate]:
    candidates = []
    levels = (
        [Level.avoid, Level.strong_avoid, Level.exclude]
        if direction < 0
        else [
            Level.prefer,
            Level.strong_prefer,
            Level.require,
        ]
    )
    for level in levels:
        changes = []
        for ref in refs:
            entry = ctx.intent.entry(ref)
            if (level.rank - rank(entry.level if entry else None)) * direction > 0:
                changes.extend(move(ctx.intent, ref, level))
        if changes:
            candidates.append(Candidate(changes))
    hard = [c for c in candidates if any(_is_hard(ch) for ch in c.changes)]
    soft_keys = {c.key for c in candidates}
    for candidate in hard:
        twin = _soft_version(ctx, candidate.changes)
        if twin and twin.key in soft_keys:
            candidate.twin = twin.key
    return candidates


def _is_hard(change: Change) -> bool:
    level = getattr(change, "to_level", None) or getattr(change, "level", None)
    return isinstance(change, SetLevel | AddEntry) and level is not None and level.hard


def _soft_version(ctx: Context, changes: list[Change]) -> Candidate | None:
    soft = []
    for change in changes:
        if _is_hard(change):
            level = change.to_level if isinstance(change, SetLevel) else change.level
            soft.extend(move(ctx.intent, change.ref, SOFT_TWIN[level]))
    return Candidate(soft) if soft else None


def exception_candidates(ctx: Context, ref: str) -> list[Candidate]:
    """An exception is possible only when labels on this key split cleanly on another known key."""
    entry = ctx.intent.entry(ref)
    if entry is None or entry.level.hard:
        return []
    labelled = [(label, expand(ctx.tags(label))) for label in active_labels(ctx.store)]
    labelled = [(label, tags) for label, tags in labelled if ref in tags]
    wants = [tags for label, tags in labelled if label.value == "want"]
    rejects = [tags for label, tags in labelled if label.value == "reject"]
    if not wants or not rejects:
        return []
    existing = {rule.when for rule in entry.exceptions}
    candidates = []
    for key in sorted(set.union(*wants, *rejects) - {ref} - existing):
        if not ctx.vocab.has(key):
            continue
        if all(key in tags for tags in wants) and not any(key in tags for tags in rejects):
            level = Level.prefer if entry.level != Level.prefer else Level.strong_prefer
        elif all(key in tags for tags in rejects) and not any(key in tags for tags in wants):
            level = Level.avoid if entry.level != Level.avoid else Level.strong_avoid
        else:
            continue
        candidates.append(Candidate([AddException(ref=ref, when=key, level=level)]))
    return candidates


def range_candidates(ctx: Context, ref: str) -> list[Candidate]:
    """From where the user has wanted jobs: keep only those places (hard), or prefer them strongly."""
    country = parse_ref(ref).key.split(".")[0]
    keep = set()
    for label in active_labels(ctx.store):
        if label.value != "want":
            continue
        for tag in ctx.tags(label):
            parsed = parse_ref(tag)
            if parsed.dimension != "location":
                continue
            tag_country = parsed.key.split(".")[0]
            if tag_country != country:
                keep.add(f"location.{tag_country}")
            elif parsed.parent and tag != ref:
                keep.add(tag)
    if not keep:
        return []
    keep = sorted(keep)
    hard = [change for r in keep for change in move(ctx.intent, r, Level.require)]
    soft = [change for r in keep for change in move(ctx.intent, r, Level.strong_prefer)]
    soft_candidate = Candidate(soft) if soft else None
    hard_candidate = Candidate(hard, soft_candidate.key if soft_candidate else None) if hard else None
    return [c for c in (hard_candidate, soft_candidate) if c]


def evidence_for(ctx: Context, changes: list[Change], targets: list[Label]) -> list[str]:
    """Target labels plus active history labels pointing the same way on the edited keys."""
    ids = [label.id for label in targets]
    for change in changes:
        moved = target_level(change, ctx.intent)
        if not moved or moved[1] == moved[0]:
            continue
        direction = 1 if moved[1] > moved[0] else -1
        for label in active_labels(ctx.store):
            if label.direction == direction and change.ref in expand(ctx.tags(label)):
                ids.append(label.id)
    return list(dict.fromkeys(ids))


def build_proposals(
    ctx: Context,
    analysis_id: str,
    cause_id: str | None,
    candidates: list[Candidate],
    targets: list[Label],
    kind: str,
    reason_refs: list[str],
    origin: str = "analysis",
    rejected: list[str] | None = None,
) -> list[Proposal]:
    """Validate, replay, filter, de-duplicate and pair candidates into proposals."""
    before = evaluate(ctx.intent, ctx.store, ctx.settings)
    built: dict[frozenset, Built] = {}
    for candidate in candidates:
        if candidate.key in built:
            continue
        new_keys = sorted(
            {
                change.ref
                for change in candidate.changes
                if isinstance(change, AddEntry) and not ctx.vocab.has(change.ref)
            }
        )
        vocab = ctx.vocab
        for ref in new_keys:
            vocab = vocab.with_key(ref)
        try:
            after_intent = apply_changes(ctx.intent, candidate.changes, vocab)
        except InvariantError as error:
            if rejected is not None:
                rejected.append(str(error))
            continue
        result, after = replay(before, after_intent, ctx.store, ctx.settings)
        is_hard = any(_is_hard(change) for change in candidate.changes)
        proposal = Proposal(
            id=new_id("proposal"),
            analysis_id=analysis_id,
            base_version=ctx.intent.version,
            cause_id=cause_id,
            origin=origin,
            changes=candidate.changes,
            evidence=evidence_for(ctx, candidate.changes, targets),
            new_keys=new_keys,
            replay=result,
            fixes_targets=all(_fixed(ctx, after_intent, after, label, kind, reason_refs) for label in targets),
            warning=len(result.broken) > len(result.fixed),
            is_hard=is_hard,
        )
        built[candidate.key] = Built(proposal, after_intent, after, candidate)

    def moves_all(item: Built) -> bool:
        return all(_moved(ctx, before, item, label, kind, reason_refs) for label in targets)

    # User-written feedback is offered as asked; analysis options must push today's labels the right way.
    kept = {key: item for key, item in built.items() if origin == "feedback" or moves_all(item)}
    # No patches: an exception is not offered when a plain level change has the same replay outcome.
    plain = {item.signature for item in kept.values() if item.change_class < 2}
    kept = {key: item for key, item in kept.items() if item.change_class < 2 or item.signature not in plain}
    # Every hard option is shown next to its soft counterpart.
    for item in list(kept.values()):
        twin = item.candidate.twin
        if twin and twin not in kept and twin in built:
            kept[twin] = built[twin]
    proposals = [item.proposal for item in kept.values()]
    return sorted(
        proposals,
        key=lambda p: (p.is_hard, change_class(p.changes), change_cost(p.changes, ctx.intent)),
    )


@dataclass
class Built:
    proposal: Proposal
    intent: IntentModel
    evaluation: Evaluation
    candidate: Candidate

    @property
    def signature(self) -> tuple:
        return frozenset(self.evaluation.agree), self.proposal.is_hard

    @property
    def change_class(self) -> int:
        return change_class(self.proposal.changes)


def _resolved(ctx: Context, intent: IntentModel, label: Label, reason_refs: list[str]) -> bool:
    tags = ctx.tags(label)
    return all(entry_sign(intent, ref, tags) != -label.direction for ref in reason_refs)


def _moved(ctx: Context, before: Evaluation, item: Built, label: Label, kind: str, reason_refs: list[str]) -> bool:
    """The edit pushes this target in the label's direction (it need not flip it)."""
    if kind == "reason_conflict":
        return _resolved(ctx, item.intent, label, reason_refs)
    old, new = before.scores[label.id], item.evaluation.scores[label.id]
    if label.direction < 0:
        return (new.excluded and not old.excluded) or (not new.excluded and new.score < old.score)
    return (old.excluded and not new.excluded) or (not new.excluded and new.score > old.score)


def target_resolved(ctx: Context, kind: str, label: Label) -> bool:
    """Whether the current intent already agrees with this target (e.g. after another accepted change)."""
    result = score_job(ctx.intent, ctx.tags(label), ctx.store.job(label.job_id).salary, ctx.settings)
    if kind == "reason_conflict":
        return _resolved(ctx, ctx.intent, label, label.reason_keys)
    return is_recommended(result, ctx.settings) == (label.value == "want")


def _fixed(ctx, intent: IntentModel, after: Evaluation, label: Label, kind, reason_refs) -> bool:
    agrees = is_recommended(after.scores[label.id], ctx.settings) == (label.value == "want")
    if kind == "reason_conflict":
        return agrees and _resolved(ctx, intent, label, reason_refs)
    return agrees


def salary_proposal(ctx: Context, analysis_id: str, cause_id: str, label: Label) -> Proposal | None:
    salary = ctx.store.job(label.job_id).salary
    comp = ctx.intent.compensation
    currency, period = (
        (comp.currency, comp.period) if comp else (salary.currency, salary.period) if salary else (None, None)
    )
    if currency is None:
        return None
    return Proposal(
        id=new_id("proposal"),
        analysis_id=analysis_id,
        base_version=ctx.intent.version,
        cause_id=cause_id,
        origin="analysis",
        changes=[SetCompensation(currency=currency, period=period)],
        evidence=[label.id],
        requires_input=["floor"],
    )
