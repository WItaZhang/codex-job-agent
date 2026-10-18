"""Matching (specs/form-fill.md 4.4; slice-1 plan section 1): which answer-sheet entry each field read back answers.

A question is matched by the `asks` phrases first: a phrase must appear as whole words, the longest phrase wins, an
entry whose `overrides` cover every other hit wins outright, and equal phrases on different entries are ambiguous.
Without an `asks` hit, a few common labels built into the code match when they are the whole question ("First
name", "Email", "Location"); contact labels go to the entry whose from_profile names that profile field.

Education and work history come in segments: consecutive fields whose labels are record fields (School, Degree,
Company, Job Title, From, To, City, Description ...). A label seen again starts the next segment. A label that hits
an `asks` phrase is a question, not a record field; one unrelated field inside a segment (Workday's "Overall Result
(GPA)") does not end it.

Segments are matched to record entries over the whole page: exact school or company names and aliases (and the
anchor's otherwise) first, then names held as whole words ("百度（Baidu）" finds the alias Baidu). Records tied on
the name are told apart by the segment's other fields (degree or title, dates), and a segment whose school or company
is left empty is matched by those fields alone. A segment no entry matches is extra: a job or school the answer sheet
does not have. Entries of a kind the page shows segments of, that no segment matched or might be, are missing.
"""

import re
from dataclasses import dataclass, field

from .models import AnswerEntry


def norm(text) -> str:
    """Lower case, every run of punctuation or space as one space: how questions, phrases and values are compared."""
    return " ".join(re.sub(r"[\W_]+", " ", str(text or "").casefold()).split())


def _contains(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


# Whole-question labels, normalized. Names go to the name.* keys; the rest to the entry whose from_profile names the
# profile field.
NAME_LABELS = {
    "name.first": ("first name", "given name", "legal first name"),
    "name.last": ("last name", "family name", "surname", "legal last name"),
    "name.legal": ("name", "full name", "legal name", "full legal name", "your name"),
    "name.preferred": ("preferred name", "preferred first name"),
}
PROFILE_LABELS = {
    "personal.email": ("email", "email address", "e mail", "e mail address"),
    "personal.phone": (
        "phone",
        "phone number",
        "mobile",
        "mobile phone",
        "mobile phone number",
        "mobile number",
        "cell phone",
        "telephone",
    ),
    "personal.city": ("city", "location", "location city", "current location", "current city", "city of residence"),
    "personal.address": ("address", "street address", "address line 1"),
    "personal.postal_code": ("postal code", "zip", "zip code", "zip postal code"),
    "personal.province_state": ("state", "state province", "province"),
    "personal.country": ("country",),
    "personal.linkedin_url": ("linkedin", "linkedin profile", "linkedin url", "linkedin profile url"),
    "personal.github_url": ("github", "github url", "github profile"),
}
TRAILING = re.compile(r"( (optional|required))+$")

# Record field labels, normalized. "range" is a start and an end in one field; "other" belongs to the segment but is
# not a record field (a description, "I currently work here").
PART_LABELS = {
    "school": ("school", "school or university", "school name", "university", "college", "institution"),
    "degree": ("degree", "degree type"),
    "major": ("major", "field of study", "major field of study", "discipline", "area of study"),
    "company": ("company", "company name", "employer", "employer name", "organization"),
    "title": ("title", "job title", "your title", "position", "position title"),
    "start": ("from", "start", "start date", "from date", "date from"),
    "end": ("to", "end", "end date", "to date", "date to", "to actual or expected", "graduation date"),
    "range": ("dates", "dates of employment", "dates attended", "employment dates", "dates of attendance"),
    "city": ("city", "location"),
    "other": ("description", "role description", "i currently work here", "currently work here", "responsibilities"),
}
PART_OF = {label: part for part, labels in PART_LABELS.items() for label in labels}
KIND_OF_PART = {"school": "education", "degree": "education", "major": "education", "company": "experience"}
KIND_OF_PART["title"] = "experience"
ANCHOR = {"education": "school", "experience": "company"}
SECOND = {"education": "degree", "experience": "title"}
NAVIGATION = re.compile(r"^((completed|current) )?step \d+ of \d+$")
# Normalized values that mean nothing was chosen.
EMPTY = frozenset(
    {
        "",
        "n a",
        "na",
        "none",
        "no response",
        "select",
        "select one",
        "select an option",
        "please select",
        "choose one",
        "choose an option",
        "no answer provided",
        "not provided",
    }
)


# Placeholder spellings beyond the list: "-- Make a Selection --", "Choose...", "No Selection", "Nothing selected".
PLACEHOLDER_LIKE = re.compile(
    r"^((please )?(select|choose|pick|make a selection)( (one|an option|an answer|a value|from the list))?"
    r"|(no|nothing|not) (selection|selected|answer|response|option|value)( (selected|provided|given))?)$"
)


def is_empty(value: str) -> bool:
    v = norm(value)
    return v in EMPTY or bool(PLACEHOLDER_LIKE.match(v))


@dataclass(frozen=True)
class FieldMatch:
    """Where one field went. `key` is the answer-sheet entry (None: no entry, or ambiguous between `ambiguous`).
    Inside a segment, `part` is the record field it shows and `segment` the segment's number; `extra` marks a segment
    no entry matches, `unanchored` one whose school or company is empty and that no entry fits by its other fields.
    `navigation` marks a step indicator ("step 2 of 6"), not a question."""

    key: str | None = None
    ambiguous: tuple[str, ...] = ()
    part: str | None = None
    segment: int | None = None
    extra: bool = False
    unanchored: bool = False
    navigation: bool = False


@dataclass(frozen=True)
class Segment:
    """One education or work segment of the page: its fields in order, the entry it matched (or the tied entries),
    and a short label (school or company, then degree or title) for the report."""

    index: int
    kind: str
    field_ids: tuple[str, ...]
    key: str | None
    ambiguous: tuple[str, ...]
    label: str
    unanchored: bool = False

    @property
    def extra(self) -> bool:
        return self.key is None and not self.ambiguous and not self.unanchored


@dataclass(frozen=True)
class PageMatch:
    fields: dict[str, FieldMatch]
    segments: tuple[Segment, ...] = ()
    missing: tuple[str, ...] = ()  # record entries the page lacks


# --- one question -----------------------------------------------------------------------------------------------


def _asks_hits(question: str, answers: dict[str, AnswerEntry]) -> dict[str, int]:
    """Entries whose asks phrases appear as whole words in the (normalized) question, with the longest one's length."""
    hits: dict[str, int] = {}
    for key, entry in answers.items():
        for phrase in entry.asks:
            p = norm(phrase)
            if _contains(question, p):
                hits[key] = max(hits.get(key, 0), len(p))
    return hits


def _pick(hits: dict[str, int], answers: dict[str, AnswerEntry]) -> FieldMatch:
    overriding = [key for key in hits if len(hits) > 1 and set(hits) - {key} <= set(answers[key].overrides)]
    if len(overriding) == 1:
        return FieldMatch(key=overriding[0])
    best = max(hits.values())
    top = tuple(key for key, length in hits.items() if length == best)
    return FieldMatch(key=top[0]) if len(top) == 1 else FieldMatch(ambiguous=top)


def _built_in(question: str, answers: dict[str, AnswerEntry]) -> FieldMatch:
    label = TRAILING.sub("", question)
    for key, labels in NAME_LABELS.items():
        if label in labels and key in answers:
            return FieldMatch(key=key)
    for path, labels in PROFILE_LABELS.items():
        if label in labels:
            keys = tuple(key for key, entry in answers.items() if entry.from_profile == path)
            if len(keys) == 1:
                return FieldMatch(key=keys[0])
            return FieldMatch(ambiguous=keys) if keys else FieldMatch()
    return FieldMatch()


def match_question(question: str, answers: dict[str, AnswerEntry]) -> FieldMatch:
    """The entry a question outside any segment answers (see the module docstring)."""
    q = norm(question)
    if NAVIGATION.match(q):
        return FieldMatch(navigation=True)
    hits = _asks_hits(q, answers)
    return _pick(hits, answers) if hits else _built_in(q, answers)


# --- segments ---------------------------------------------------------------------------------------------------


@dataclass
class _Draft:
    kind: str
    parts: dict[str, int] = field(default_factory=dict)  # record field -> index of its (first) field
    indexes: list[int] = field(default_factory=list)


def part_of(question: str, answers: dict[str, AnswerEntry]) -> str | None:
    """The record field a label names, unless an asks phrase claims the question (asks win over built-in labels)."""
    q = norm(question)
    part = PART_OF.get(TRAILING.sub("", q))
    return None if part is None or _asks_hits(q, answers) else part


def _fits(draft: _Draft, part: str | None) -> bool:
    """Whether a field naming `part` would join this segment (rather than start the next or end it)."""
    if part is None:
        return False
    kind = KIND_OF_PART.get(part)
    if kind is not None:
        return kind == draft.kind and part not in draft.parts
    return part not in draft.parts or part == "other"


def _segments(fields, answers) -> list[_Draft]:
    parts = [part_of(f.question, answers) for f in fields]
    drafts: list[_Draft] = []
    current: _Draft | None = None
    for i, part in enumerate(parts):
        if part is None:  # one unrelated field inside a segment does not end it
            if current is not None and not _fits(current, parts[i + 1] if i + 1 < len(parts) else None):
                current = None
            continue
        if KIND_OF_PART.get(part) is not None:
            if current is None or not _fits(current, part):
                current = _Draft(KIND_OF_PART[part])
                drafts.append(current)
        elif current is None:
            continue
        elif not _fits(current, part):
            current = None
            continue
        current.parts.setdefault(part, i)
        current.indexes.append(i)
    return drafts


def _names(entry: AnswerEntry, part: str) -> list[str]:
    names = [
        entry.record.get(part, ""),
        *entry.aliases,
        *entry.record_accept.get(part, ()),
        *entry.record_otherwise.get(part, ()),
    ]
    return [n for n in (norm(name) for name in names) if n]


def _strength(value: str, entry: AnswerEntry, part: str) -> int:
    """2: the value is the school or company (or an alias); 1: it holds one as whole words; 0: neither."""
    v = norm(value)
    names = _names(entry, part)
    if v in names:
        return 2
    return 1 if any(_contains(v, n) for n in names) else 0


def _label(draft: _Draft, fields) -> str:
    parts = [ANCHOR[draft.kind], SECOND[draft.kind]]
    return " · ".join(fields[draft.parts[p]].value for p in parts if p in draft.parts and fields[draft.parts[p]].value)


def _same_second(entry: AnswerEntry, values: dict[str, str]) -> int:
    """The default tie-break: how many of degree/title and major agree as text."""
    return sum(
        1
        for part, value in values.items()
        if part in ("degree", "major", "title") and norm(value) and norm(value) in _names(entry, part)
    )


def _assign(drafts, fields, answers, score) -> tuple[dict[int, str], dict[int, tuple[str, ...]], set[int]]:
    """Segments -> entries over the whole page: (assigned, ambiguous, unanchored segment numbers).

    Repeatedly gives the segment with the strongest unique best candidate its entry, so a tie is decided only after
    every unique match has taken its entry: a segment tied between two entries gets the one left over."""
    candidates: dict[int, list[tuple[int, int, str]]] = {}
    unanchored: set[int] = set()
    for n, draft in enumerate(drafts):
        anchor = draft.parts.get(ANCHOR[draft.kind])
        value = fields[anchor].value if anchor is not None else ""
        values = {part: fields[i].value for part, i in draft.parts.items() if part != ANCHOR[draft.kind]}
        records = [(key, e) for key, e in answers.items() if e.kind == "record" and e.category == draft.kind]
        if is_empty(value):
            unanchored.add(n)
            found = [(0, score(e, values), key) for key, e in records]
            candidates[n] = [c for c in found if c[1] >= 1]
        else:
            found = [(_strength(value, e, ANCHOR[draft.kind]), key, e) for key, e in records]
            candidates[n] = [(st, score(e, values), key) for st, key, e in found if st]
    assigned: dict[int, str] = {}
    open_ = {n for n, cands in candidates.items() if cands}

    def best(n: int) -> tuple[tuple[int, int], list[str]]:
        free = [(st, sc, key) for st, sc, key in candidates[n] if key not in assigned.values()]
        if not free:
            return (-1, -1), []
        level = max((st, sc) for st, sc, _ in free)
        return level, [key for st, sc, key in free if (st, sc) == level]

    while open_:
        tops = {n: best(n) for n in open_}
        unique = [(level, n, keys[0]) for n, (level, keys) in tops.items() if len(keys) == 1]
        if not unique:
            break
        _, n, key = max(unique, key=lambda item: (item[0], -item[1]))
        assigned[n] = key
        open_.discard(n)
        open_ -= {m for m, (_, keys) in tops.items() if not keys}
    ambiguous = {n: tuple(sorted(keys)) for n in open_ for _, keys in [best(n)] if len(keys) > 1}
    return assigned, ambiguous, {n for n in unanchored if n not in assigned and n not in ambiguous}


def match_page(fields, answers: dict[str, AnswerEntry], score=None) -> PageMatch:
    """Every field of the page matched (fields are ReadFields in page order). `score(entry, values)` counts how many
    of a segment's fields (record field -> value, without the school or company) agree with a record entry; it breaks
    ties between records and matches a segment whose school or company is empty. Without it, degree/title and major
    are compared as text."""
    fields = list(fields)
    score = score or _same_second
    drafts = _segments(fields, answers)
    assigned, ambiguous, unanchored = _assign(drafts, fields, answers, score)
    out: dict[str, FieldMatch] = {}
    segments: list[Segment] = []
    for n, draft in enumerate(drafts):
        key, tied = assigned.get(n), ambiguous.get(n, ())
        segment = Segment(
            n, draft.kind, tuple(fields[i].id for i in draft.indexes), key, tied, _label(draft, fields), n in unanchored
        )
        segments.append(segment)
        parts = {i: part for part, i in draft.parts.items()}
        for i in draft.indexes:
            part = parts.get(i) or PART_OF[TRAILING.sub("", norm(fields[i].question))]
            out[fields[i].id] = FieldMatch(
                key=key,
                ambiguous=tied,
                part=part,
                segment=n,
                extra=segment.extra,
                unanchored=segment.unanchored,
            )
    for f in fields:
        if f.id not in out:
            out[f.id] = match_question(f.question, answers)
    kinds = {segment.kind for segment in segments}
    present = set(assigned.values()) | {key for keys in ambiguous.values() for key in keys}
    missing = tuple(
        key for key, e in answers.items() if e.kind == "record" and e.category in kinds and key not in present
    )
    return PageMatch(out, tuple(segments), missing)
