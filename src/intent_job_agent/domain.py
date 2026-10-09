"""Domain contracts: intent model, vocabulary, jobs and labels, and their invariants.

Every reference is a `dimension.key` string. Dimensions are a closed set; keys are open
but normalized through the vocabulary. Only `location` is hierarchical (`country.city`).
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator


class InvariantError(ValueError):
    """A state or input the specification forbids."""


DIMENSIONS = (
    "role",
    "domain",
    "specialty",
    "seniority",
    "company_type",
    "company",
    "location",
    "work_mode",
    "employment_type",
    "tech_stack",
    "sponsorship",
)
_DEPTH = {"location": 2}
_SEGMENT = re.compile(r"^[a-z0-9_\-\u0080-\U0010ffff]+$")


@dataclass(frozen=True)
class ParsedRef:
    dimension: str
    key: str

    @property
    def ref(self) -> str:
        return f"{self.dimension}.{self.key}"

    @property
    def parent(self) -> str | None:
        """Country of a `location.country.city` ref; None otherwise."""
        if "." in self.key:
            return f"{self.dimension}.{self.key.split('.', 1)[0]}"
        return None


def parse_ref(value: str) -> ParsedRef:
    if not isinstance(value, str) or "." not in value:
        raise InvariantError(f"Not a dimension.key reference: {value!r}")
    dimension, key = value.split(".", 1)
    if dimension not in DIMENSIONS:
        raise InvariantError(f"Unknown dimension {dimension!r}; dimensions change only through development")
    segments = key.split(".")
    if len(segments) > _DEPTH.get(dimension, 1) or not all(_SEGMENT.match(s) for s in segments):
        raise InvariantError(f"Invalid key for {dimension}: {key!r}")
    return ParsedRef(dimension, key)


def _check_ref(value: str) -> str:
    return parse_ref(value).ref


Ref = Annotated[str, AfterValidator(_check_ref)]


def expand(tags: list[str]) -> set[str]:
    """Tags plus the countries implied by city-level location tags."""
    expanded = set(tags)
    for tag in tags:
        parent = parse_ref(tag).parent
        if parent:
            expanded.add(parent)
    return expanded


class Level(StrEnum):
    exclude = "exclude"
    strong_avoid = "strong_avoid"
    avoid = "avoid"
    prefer = "prefer"
    strong_prefer = "strong_prefer"
    require = "require"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @property
    def hard(self) -> bool:
        return self in (Level.exclude, Level.require)


_RANK = {
    Level.exclude: -3,
    Level.strong_avoid: -2,
    Level.avoid: -1,
    Level.prefer: 1,
    Level.strong_prefer: 2,
    Level.require: 3,
}
LEVELS_BY_RANK = sorted(Level, key=lambda level: level.rank)
SOFT_TWIN = {Level.exclude: Level.strong_avoid, Level.require: Level.strong_prefer}


def rank(level: Level | None) -> int:
    """Rank on the 6-level scale; a missing entry is neutral (0)."""
    return 0 if level is None else level.rank


def _wrap(build):
    try:
        return build()
    except ValidationError as error:
        raise InvariantError(str(error)) from error


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExceptionRule(Record):
    when: Ref
    level: Level


class IntentEntry(Record):
    id: str
    level: Level
    note: str = ""
    evidence: tuple[str, ...] = ()
    exceptions: tuple[ExceptionRule, ...] = ()

    @model_validator(mode="after")
    def valid_exceptions(self):
        if self.exceptions and self.level.hard:
            raise ValueError("Exceptions are only allowed on soft levels")
        whens = [rule.when for rule in self.exceptions]
        if len(whens) != len(set(whens)):
            raise ValueError("At most one exception per condition")
        for rule in self.exceptions:
            if rule.level.hard:
                raise ValueError("Exception levels must be soft")
            if rule.level == self.level:
                raise ValueError("An exception equal to the default level is redundant")
        return self

    @staticmethod
    def entry_id(ref: str) -> str:
        return "intent-" + ref.replace(".", "-")

    @classmethod
    def create(cls, ref: str, level: Level, note: str = "", evidence=(), exceptions=()) -> "IntentEntry":
        parse_ref(ref)
        return _wrap(
            lambda: cls(
                id=cls.entry_id(ref),
                level=level,
                note=note,
                evidence=tuple(evidence),
                exceptions=tuple(exceptions),
            )
        )


class Compensation(Record):
    currency: str
    period: Literal["year", "month", "hour"]
    floor: float = Field(gt=0)


class Salary(Record):
    currency: str
    period: Literal["year", "month", "hour"]
    min: float | None = None
    max: float | None = None


class IntentModel(Record):
    """`dimension -> key -> entry`: one entry per key by construction."""

    version: int = 1
    entries: dict[str, dict[str, IntentEntry]] = Field(default_factory=dict)
    compensation: Compensation | None = None

    @model_validator(mode="after")
    def valid_entries(self):
        for dimension, keys in self.entries.items():
            if dimension not in DIMENSIONS:
                raise ValueError(f"Unknown dimension {dimension!r}")
            for key, entry in keys.items():
                ref = parse_ref(f"{dimension}.{key}").ref
                if entry.id != IntentEntry.entry_id(ref):
                    raise ValueError(f"Entry id {entry.id!r} does not match {ref}")
                if any(rule.when == ref for rule in entry.exceptions):
                    raise ValueError(f"An exception on {ref} must depend on another key")
        for key, entry in self.entries.get("location", {}).items():
            if "." not in key and entry.level == Level.exclude:
                for child, child_entry in self.entries["location"].items():
                    if child.startswith(key + ".") and child_entry.level != Level.exclude:
                        raise ValueError(f"location.{child} conflicts with excluded country location.{key}")
        return self

    @classmethod
    def build(cls, spec: dict, compensation: Compensation | None = None, evidence: dict | None = None) -> "IntentModel":
        """Build from `{dimension: {key: {level, note?, exceptions?}}}`."""
        evidence = evidence or {}
        entries: dict[str, dict[str, IntentEntry]] = {}
        for dimension, keys in spec.items():
            for key, data in keys.items():
                ref = f"{dimension}.{key}"
                exceptions = [ExceptionRule(**rule) for rule in data.get("exceptions", [])]
                entries.setdefault(dimension, {})[key] = IntentEntry.create(
                    ref, Level(data["level"]), data.get("note", ""), evidence.get(ref, ()), exceptions
                )
        return _wrap(lambda: cls(entries=entries, compensation=compensation))

    def entry(self, ref: str) -> IntentEntry | None:
        parsed = parse_ref(ref)
        return self.entries.get(parsed.dimension, {}).get(parsed.key)

    def iter_entries(self) -> list[tuple[str, IntentEntry]]:
        return sorted((f"{d}.{k}", e) for d, keys in self.entries.items() for k, e in keys.items())

    def governing(self, tag: str) -> tuple[str, IntentEntry] | None:
        """The most specific entry on a tag's path (city before country)."""
        for ref in (tag, parse_ref(tag).parent):
            if ref and (entry := self.entry(ref)) is not None:
                return ref, entry
        return None


class Vocabulary(Record):
    """Known keys per dimension, each with aliases. New keys enter only through a user decision."""

    keys: dict[str, dict[str, tuple[str, ...]]] = Field(default_factory=dict)

    @classmethod
    def build(cls, mapping: dict[str, dict[str, list[str]]]) -> "Vocabulary":
        for dimension, keys in mapping.items():
            for key in keys:
                parse_ref(f"{dimension}.{key}")
        return cls(keys={d: {k: tuple(a) for k, a in keys.items()} for d, keys in mapping.items()})

    def has(self, ref: str) -> bool:
        parsed = parse_ref(ref)
        return parsed.key in self.keys.get(parsed.dimension, {})

    def alias_of(self, ref: str) -> str | None:
        parsed = parse_ref(ref)
        needle = parsed.key.casefold()
        for key, aliases in self.keys.get(parsed.dimension, {}).items():
            if key != parsed.key and needle in (alias.casefold() for alias in aliases):
                return f"{parsed.dimension}.{key}"
        return None

    def canonical(self, ref: str) -> str:
        return self.alias_of(ref) or parse_ref(ref).ref

    def with_key(self, ref: str) -> "Vocabulary":
        parsed = parse_ref(ref)
        keys = {d: dict(k) for d, k in self.keys.items()}
        dimension = keys.setdefault(parsed.dimension, {})
        dimension.setdefault(parsed.key, ())
        if parsed.parent:
            dimension.setdefault(parse_ref(parsed.parent).key, ())
        return Vocabulary(keys=keys)


class Job(Record):
    """A posting. Its text fields are untrusted: shown for tagging and display, never used in analysis."""

    id: str
    source: Literal["manual", "greenhouse", "lever", "ashby", "jobspy", "workday", "smartextract"] = "manual"
    board: str = ""
    title: str = ""
    company: str = ""
    url: str = ""
    location: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    tags: list[Ref]
    salary: Salary | None = None
    description: str = ""


class Label(Record):
    id: str
    job_id: str
    day: str
    slot: Literal["recommended", "exploration"]
    value: Literal["want", "reject", "misread"]
    reason_keys: list[Ref] = Field(default_factory=list)
    reason_text: str | None = None
    superseded: bool = False

    @property
    def direction(self) -> int:
        return {"want": 1, "reject": -1}.get(self.value, 0)
