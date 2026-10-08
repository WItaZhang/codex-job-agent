"""Edits to the intent model. Pure: `apply_changes` returns a new, validated model."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .domain import (
    Compensation,
    ExceptionRule,
    IntentEntry,
    IntentModel,
    InvariantError,
    Level,
    Ref,
    Vocabulary,
    parse_ref,
    rank,
)


class _Change(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SetLevel(_Change):
    op: Literal["set"] = "set"
    ref: Ref
    from_level: Level | None = None
    to_level: Level

    def summary(self):
        return ("set", self.ref, self.to_level)


class AddEntry(_Change):
    op: Literal["add"] = "add"
    ref: Ref
    level: Level

    def summary(self):
        return ("add", self.ref, self.level)


class DeleteEntry(_Change):
    op: Literal["delete"] = "delete"
    ref: Ref
    from_level: Level | None = None

    def summary(self):
        return ("delete", self.ref)


class AddException(_Change):
    op: Literal["add_exception"] = "add_exception"
    ref: Ref
    when: Ref
    level: Level

    def summary(self):
        return ("add_exception", self.ref, self.when, self.level)


class RemoveException(_Change):
    op: Literal["remove_exception"] = "remove_exception"
    ref: Ref
    when: Ref

    def summary(self):
        return ("remove_exception", self.ref, self.when)


class SetCompensation(_Change):
    """Enable or change the soft salary floor. `floor` may be left for the user to fill in."""

    op: Literal["set_compensation"] = "set_compensation"
    currency: str
    period: Literal["year", "month", "hour"]
    floor: float | None = None

    def summary(self):
        return ("set_compensation",)


Change = Annotated[
    SetLevel | AddEntry | DeleteEntry | AddException | RemoveException | SetCompensation,
    Field(discriminator="op"),
]
CHANGE = TypeAdapter(Change)


def parse_change(data: dict) -> Change:
    try:
        return CHANGE.validate_python(data)
    except ValidationError as error:
        raise InvariantError(str(error)) from error


def target_level(change: Change, intent: IntentModel) -> tuple[int, int] | None:
    """(rank before, rank after) for level-changing edits; None otherwise."""
    if isinstance(change, SetLevel):
        return rank(change.from_level or intent.entry(change.ref).level), rank(change.to_level)
    if isinstance(change, AddEntry):
        return 0, rank(change.level)
    if isinstance(change, DeleteEntry):
        entry = intent.entry(change.ref)
        return rank(entry.level if entry else change.from_level), 0
    return None


def apply_changes(
    intent: IntentModel, changes: list[Change], vocab: Vocabulary, evidence: tuple[str, ...] = ()
) -> IntentModel:
    """Apply all edits, then validate the final state as a whole."""
    entries = {d: dict(keys) for d, keys in intent.entries.items()}
    compensation = intent.compensation

    def get(ref):
        parsed = parse_ref(ref)
        return entries.get(parsed.dimension, {}).get(parsed.key)

    def put(ref, entry):
        parsed = parse_ref(ref)
        if entry is None:
            entries[parsed.dimension].pop(parsed.key)
            if not entries[parsed.dimension]:
                del entries[parsed.dimension]
        else:
            entries.setdefault(parsed.dimension, {})[parsed.key] = entry

    def merged_evidence(entry):
        return tuple(dict.fromkeys((*entry.evidence, *evidence)))

    for change in changes:
        if isinstance(change, SetCompensation):
            if change.floor is None:
                raise InvariantError("The salary floor must be chosen by the user before applying")
            compensation = Compensation(currency=change.currency, period=change.period, floor=change.floor)
            continue
        alias = vocab.alias_of(change.ref)
        if alias:
            raise InvariantError(f"{change.ref} is an alias of {alias}; use the canonical key")
        entry = get(change.ref)
        if isinstance(change, AddEntry):
            if entry is not None:
                raise InvariantError(f"{change.ref} already has an entry; change it instead of adding another")
            put(change.ref, IntentEntry.create(change.ref, change.level, evidence=evidence))
            continue
        if entry is None:
            raise InvariantError(f"{change.ref} has no entry")
        if isinstance(change, SetLevel):
            if change.from_level is not None and change.from_level != entry.level:
                raise InvariantError(f"{change.ref} is {entry.level}, not {change.from_level}")
            if change.to_level == entry.level:
                raise InvariantError(f"{change.ref} is already {entry.level}")
            put(change.ref, _replace(entry, level=change.to_level, evidence=merged_evidence(entry)))
        elif isinstance(change, DeleteEntry):
            if change.from_level is not None and change.from_level != entry.level:
                raise InvariantError(f"{change.ref} is {entry.level}, not {change.from_level}")
            put(change.ref, None)
        elif isinstance(change, AddException):
            if change.when == change.ref or not vocab.has(change.when):
                raise InvariantError(f"Exception condition {change.when} must be another known key")
            rule = ExceptionRule(when=change.when, level=change.level)
            put(
                change.ref,
                _replace(entry, exceptions=(*entry.exceptions, rule), evidence=merged_evidence(entry)),
            )
        elif isinstance(change, RemoveException):
            kept = tuple(rule for rule in entry.exceptions if rule.when != change.when)
            if len(kept) == len(entry.exceptions):
                raise InvariantError(f"{change.ref} has no exception on {change.when}")
            put(change.ref, _replace(entry, exceptions=kept))
    try:
        return IntentModel(version=intent.version, entries=entries, compensation=compensation)
    except ValidationError as error:
        raise InvariantError(str(error)) from error


def _replace(entry: IntentEntry, **update) -> IntentEntry:
    try:
        return IntentEntry.model_validate({**entry.model_dump(), **update})
    except ValidationError as error:
        raise InvariantError(str(error)) from error


def change_class(changes: list[Change]) -> int:
    """Structural size: 0 = one key, 1 = several keys, 2 = adds an exception."""
    if any(isinstance(change, AddException) for change in changes):
        return 2
    refs = {change.ref for change in changes if not isinstance(change, SetCompensation)}
    return 1 if len(refs) > 1 else 0


def change_cost(changes: list[Change], intent: IntentModel) -> tuple[int, int]:
    """(number of edits, level steps moved) — smaller is a smaller change."""
    steps = 0
    for change in changes:
        moved = target_level(change, intent)
        if moved:
            steps += abs(moved[1] - moved[0])
    return len(changes), steps
