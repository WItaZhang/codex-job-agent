"""Pre-tags: what code can read from a posting's structured fields and title, without a model. Pure.

`confident` lists the dimensions read unambiguously; only those may hold a job back before tagging.
Everything else is a suggestion for ranking and a starting point for the tagger.
"""

import re
from dataclasses import dataclass

from .domain import InvariantError, Job, Vocabulary, parse_ref

_US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
    "ct": "connecticut", "de": "delaware", "dc": "district of columbia", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts",
    "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico",
    "ny": "new york", "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina", "sd": "south dakota",
    "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
}  # fmt: skip
_STATE_NAMES = set(_US_STATES.values())
_COUNTRIES = {
    "us": "us", "usa": "us", "u.s.": "us", "u.s.a.": "us", "united states": "us",
    "united states of america": "us", "america": "us",
    "canada": "canada", "uk": "uk", "united kingdom": "uk", "england": "uk", "great britain": "uk",
    "india": "india", "china": "china", "germany": "germany", "france": "france", "ireland": "ireland",
    "netherlands": "netherlands", "spain": "spain", "poland": "poland", "singapore": "singapore",
    "japan": "japan", "australia": "australia", "israel": "israel", "mexico": "mexico", "brazil": "brazil",
}  # fmt: skip
_REMOTE = ("remote", "work from home", "wfh", "anywhere")
_ONSITE = ("on-site", "onsite", "on site", "in office", "in-office")
_WORK_MODE_FIELD = {
    "remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite", "on site": "onsite",
}  # fmt: skip
_EMPLOYMENT = {
    "full-time": "full_time", "fulltime": "full_time", "full time": "full_time",
    "contract": "contract", "contractor": "contract", "temporary": "contract",
    "intern": "internship", "internship": "internship", "part-time": "part_time", "parttime": "part_time",
}  # fmt: skip
_SENIORITY = [
    (re.compile(r"\b(senior|sr\.?|staff|principal)\b"), "senior"),
    (re.compile(r"\b(junior|jr\.?)\b"), "junior"),
    (re.compile(r"\b(new grad|new graduate|entry[- ]level|early career)\b"), "new_grad"),
]
_INTERN = re.compile(r"\b(intern|internship|co-op)\b")
_TITLE_DIMENSIONS = ("role", "specialty", "domain", "tech_stack")


@dataclass(frozen=True)
class Pretag:
    tags: tuple[str, ...]
    confident: frozenset[str]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _place(part: str) -> str | None:
    """One location like "San Francisco, CA" or "London, UK" -> a location ref; None when unsure."""
    tokens = [t.strip() for t in part.split(",") if t.strip()]
    if not tokens:
        return None
    first, last = tokens[0].casefold(), tokens[-1].casefold()
    if len(tokens) == 1:
        country = _COUNTRIES.get(first)
        return f"location.{country}" if country else None
    if last in _US_STATES or last in _STATE_NAMES:  # "New York, NY": the first part is the city
        return f"location.us.{_slug(tokens[0])}"
    country = _COUNTRIES.get(last)
    if country is None:
        return None
    if len(tokens) >= 3:  # "Seattle, WA, United States"
        middle = tokens[-2].casefold()
        if country == "us" and middle not in _US_STATES and middle not in _STATE_NAMES:
            return None
        return f"location.{country}.{_slug(tokens[0])}"
    if first in _COUNTRIES or (country == "us" and (first in _US_STATES or first in _STATE_NAMES)):
        return f"location.{country}"  # "Texas, United States": a region, not a city
    return f"location.{country}.{_slug(tokens[0])}"


def _locations(text: str) -> tuple[set[str], str | None, bool]:
    """Location refs, a work mode stated in the text, and whether every place was read."""
    low = text.casefold()
    mode = "hybrid" if "hybrid" in low else "onsite" if any(w in low for w in _ONSITE) else None
    if mode is None and any(w in low for w in _REMOTE):
        mode = "remote"
    cleaned = re.sub(r"\([^)]*\)", " ", text)
    for word in ("remote", "hybrid", "on-site", "onsite", "on site", "anywhere"):
        cleaned = re.sub(word, " ", cleaned, flags=re.I)
    parts = [p for p in re.split(r";|\||\bor\b|/| - ", cleaned) if p.strip(" ,-")]
    refs, unsure = set(), False
    for part in parts:
        ref = _place(part.strip(" ,-"))
        if ref is None:
            unsure = True
        else:
            refs.add(ref)
    return refs, mode, bool(refs) and not unsure


def _title_suggestions(title: str, vocab: Vocabulary) -> set[str]:
    low = f" {title.casefold()} "
    found = set()
    for dimension in _TITLE_DIMENSIONS:
        for key, aliases in vocab.keys.get(dimension, {}).items():
            phrase = key.replace("_", " ")
            phrases = {phrase, phrase.replace("engineering", "engineer"), *(a.casefold() for a in aliases)}
            if any(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", low) for p in phrases if p):
                found.add(f"{dimension}.{key}")
    return found


def pretag(job: Job, vocab: Vocabulary) -> Pretag:
    tags: set[str] = set()
    confident: set[str] = set()
    places, mode, places_read = _locations(job.location) if job.location else (set(), None, False)
    if places_read:
        tags |= places
        confident.add("location")

    attributes = {k: v.casefold() for k, v in job.attributes.items()}
    field_mode = _WORK_MODE_FIELD.get(attributes.get("workplace_type", "").replace("_", " "))
    if attributes.get("is_remote") == "true":
        field_mode = field_mode or "remote"
    mode = field_mode or mode
    if mode:
        tags.add(f"work_mode.{mode}")
        confident.add("work_mode")

    title = job.title.casefold()
    employment = _EMPLOYMENT.get(attributes.get("employment_type", "").strip())
    if _INTERN.search(title):
        employment = "internship"
    if employment:
        tags.add(f"employment_type.{employment}")
        confident.add("employment_type")
    for pattern, level in _SENIORITY:
        if pattern.search(title):
            tags.add(f"seniority.{level}")
            confident.add("seniority")
            break

    tags |= _title_suggestions(job.title, vocab)
    canonical = []
    for tag in sorted(tags):
        try:
            canonical.append(vocab.canonical(parse_ref(tag).ref))
        except InvariantError:
            continue  # a slug outside the key syntax: leave it to the tagger
    return Pretag(tuple(dict.fromkeys(canonical)), frozenset(confident))
