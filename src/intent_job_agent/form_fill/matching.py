"""Matching (specs/form-fill.md 4.4; slice-1 plan section 1): which answer-sheet entry each field read back answers.

A question is matched by the `asks` phrases first: a phrase must appear as whole words, the longest phrase wins, an
entry whose `overrides` cover every other hit wins outright, and equal phrases on different entries are ambiguous.
Without an `asks` hit, a few common labels built into the code match when they are the whole question ("First
name", "Email", "Location"); contact labels go to the entry whose from_profile names that profile field.

Education and work history come in segments: consecutive fields whose labels are record fields (School, Degree,
Company, Job Title, From, To, City, Description ...). A label seen again starts the next segment. A segment is
matched to a record entry by its school or company, equal to the entry's name or an alias, or holding one of them as
whole words ("百度（Baidu）" finds the alias Baidu). A segment that matches no entry is extra: a job or school the
answer sheet does not have. Entries of a kind the page shows segments of, but that no segment matched, are missing.
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


@dataclass(frozen=True)
class FieldMatch:
    """Where one field went. `key` is the answer-sheet entry (None: no entry, or ambiguous between `ambiguous`).
    Inside a segment, `part` is the record field it shows and `segment` the segment's number; `extra` marks a segment
    no entry matches. `navigation` marks a step indicator ("step 2 of 6"), not a question."""

    key: str | None = None
    ambiguous: tuple[str, ...] = ()
    part: str | None = None
    segment: int | None = None
    extra: bool = False
    navigation: bool = False


@dataclass(frozen=True)
class Segment:
    """One education or work segment of the page: its fields in order, the entry it matched, and a short label
    (school or company, then degree or title) for the report."""

    index: int
    kind: str
    field_ids: tuple[str, ...]
    key: str | None
    ambiguous: tuple[str, ...]
    label: str

    @property
    def extra(self) -> bool:
        return self.key is None and not self.ambiguous


@dataclass(frozen=True)
class PageMatch:
    fields: dict[str, FieldMatch]
    segments: tuple[Segment, ...] = ()
    missing: tuple[str, ...] = ()  # record entries the page lacks


# --- one question -----------------------------------------------------------------------------------------------


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
    hits: dict[str, int] = {}
    for key, entry in answers.items():
        for phrase in entry.asks:
            p = norm(phrase)
            if _contains(q, p):
                hits[key] = max(hits.get(key, 0), len(p))
    return _pick(hits, answers) if hits else _built_in(q, answers)


# --- segments ---------------------------------------------------------------------------------------------------


@dataclass
class _Draft:
    kind: str
    parts: dict[str, int] = field(default_factory=dict)  # record field -> index of its (first) field
    indexes: list[int] = field(default_factory=list)


def _segments(fields) -> list[_Draft]:
    drafts: list[_Draft] = []
    current: _Draft | None = None
    for i, f in enumerate(fields):
        part = PART_OF.get(norm(f.question))
        if part is None:
            current = None
            continue
        kind = KIND_OF_PART.get(part)
        if kind is not None:
            if current is None or current.kind != kind or part in current.parts:
                current = _Draft(kind)
                drafts.append(current)
        elif current is None:
            continue
        elif part in current.parts and part != "other":
            current = None
            continue
        current.parts.setdefault(part, i)
        current.indexes.append(i)
    return drafts


def _names(entry: AnswerEntry, part: str) -> list[str]:
    names = [entry.record.get(part, ""), *entry.aliases, *entry.record_accept.get(part, ())]
    return [n for n in (norm(name) for name in names) if n]


def _record_for(value: str, kind: str, answers: dict[str, AnswerEntry]) -> tuple[str, ...]:
    """Entries of this kind whose school or company is the value, or appears in it as whole words."""
    v = norm(value)
    if not v:
        return ()
    part = ANCHOR[kind]
    entries = [(key, e) for key, e in answers.items() if e.kind == "record" and e.category == kind]
    exact = tuple(key for key, e in entries if v in _names(e, part))
    if exact:
        return exact
    found = {}
    for key, e in entries:
        lengths = [len(n) for n in _names(e, part) if _contains(v, n)]
        if lengths:
            found[key] = max(lengths)
    if not found:
        return ()
    best = max(found.values())
    return tuple(key for key, length in found.items() if length == best)


def _label(draft: _Draft, fields) -> str:
    parts = [ANCHOR[draft.kind], SECOND[draft.kind]]
    return " · ".join(fields[draft.parts[p]].value for p in parts if p in draft.parts and fields[draft.parts[p]].value)


def match_page(fields, answers: dict[str, AnswerEntry]) -> PageMatch:
    """Every field of the page matched (fields are ReadFields in page order)."""
    fields = list(fields)
    out: dict[str, FieldMatch] = {}
    segments: list[Segment] = []
    used: set[str] = set()
    for n, draft in enumerate(_segments(fields)):
        anchor = draft.parts.get(ANCHOR[draft.kind])
        keys = _record_for(fields[anchor].value, draft.kind, answers) if anchor is not None else ()
        key, ambiguous = (keys[0], ()) if len(keys) == 1 else (None, keys)
        if key in used:
            key = None
        if key:
            used.add(key)
        segment = Segment(
            n, draft.kind, tuple(fields[i].id for i in draft.indexes), key, ambiguous, _label(draft, fields)
        )
        segments.append(segment)
        parts = {i: part for part, i in draft.parts.items()}
        for i in draft.indexes:
            part = parts.get(i) or PART_OF[norm(fields[i].question)]
            out[fields[i].id] = FieldMatch(key=key, ambiguous=ambiguous, part=part, segment=n, extra=segment.extra)
    for f in fields:
        if f.id not in out:
            out[f.id] = match_question(f.question, answers)
    kinds = {segment.kind for segment in segments}
    missing = tuple(key for key, e in answers.items() if e.kind == "record" and e.category in kinds and key not in used)
    return PageMatch(out, tuple(segments), missing)
