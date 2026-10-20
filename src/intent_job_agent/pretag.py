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
_CA_PROVINCES = {
    "ab": "alberta", "bc": "british columbia", "mb": "manitoba", "nb": "new brunswick",
    "nl": "newfoundland and labrador", "ns": "nova scotia", "nt": "northwest territories", "nu": "nunavut",
    "on": "ontario", "pe": "prince edward island", "qc": "quebec", "sk": "saskatchewan", "yt": "yukon",
}  # fmt: skip
# The states or provinces a place may name besides its city (spec §10 item 31: only the US and Canada): code -> name.
_REGION_CODES = {"us": _US_STATES, "canada": _CA_PROVINCES}
# Well-known cities named like a state or province: read as the city only beside that state or province.
_NAMED_LIKE_REGION = {
    ("us", "new york", "new york"),
    ("us", "district of columbia", "washington"),
    ("canada", "quebec", "quebec"),
}
_COUNTRIES = {
    "us": "us", "usa": "us", "u.s.": "us", "u.s.a.": "us", "united states": "us",
    "united states of america": "us", "america": "us",
    "canada": "canada", "uk": "uk", "united kingdom": "uk", "england": "uk", "great britain": "uk",
    "scotland": "uk", "wales": "uk", "northern ireland": "uk",
    "india": "india", "china": "china", "germany": "germany", "france": "france", "ireland": "ireland",
    "netherlands": "netherlands", "the netherlands": "netherlands", "spain": "spain", "poland": "poland",
    "singapore": "singapore", "japan": "japan", "australia": "australia", "israel": "israel", "mexico": "mexico",
    "brazil": "brazil", "switzerland": "switzerland", "sweden": "sweden", "denmark": "denmark", "norway": "norway",
    "finland": "finland", "belgium": "belgium", "austria": "austria", "italy": "italy", "portugal": "portugal",
    "czech republic": "czechia", "czechia": "czechia", "romania": "romania", "hungary": "hungary",
    "greece": "greece", "turkey": "turkey", "türkiye": "turkey", "ukraine": "ukraine", "bulgaria": "bulgaria",
    "serbia": "serbia", "croatia": "croatia", "estonia": "estonia", "latvia": "latvia", "lithuania": "lithuania",
    "slovakia": "slovakia", "slovenia": "slovenia", "luxembourg": "luxembourg",
    "united arab emirates": "uae", "uae": "uae", "saudi arabia": "saudi_arabia", "egypt": "egypt",
    "south africa": "south_africa", "nigeria": "nigeria", "kenya": "kenya", "pakistan": "pakistan",
    "south korea": "south_korea", "korea": "south_korea", "republic of korea": "south_korea",
    "taiwan": "taiwan", "hong kong": "hong_kong", "vietnam": "vietnam", "viet nam": "vietnam",
    "thailand": "thailand", "malaysia": "malaysia", "indonesia": "indonesia", "philippines": "philippines",
    "new zealand": "new_zealand", "argentina": "argentina", "chile": "chile", "colombia": "colombia",
    "peru": "peru", "costa rica": "costa_rica",
}  # fmt: skip
# Work mode phrases; their words may be joined by spaces or hyphens ("Work-From-Home", "On-site").
_REMOTE = ("remote", "remote first", "work from home", "wfh", "anywhere")
_ONSITE = ("onsite", "on site", "in office")
# Every word a work mode is read from is no place, so it is taken out before the place is read (spec §10 item 31).
_MODE_WORDS = re.compile(
    r"(?<!\w)(?:"
    + "|".join(p.replace(" ", r"[\s-]+") for p in sorted(("hybrid", *_REMOTE, *_ONSITE), key=len, reverse=True))
    + r")(?!\w)",
    re.I,
)
# Made regular before anything is read: every dash is "-", "D.C." is "DC", any run of white space is one space.
_DASHES = re.compile(r"[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]")
_DOTTED = re.compile(r"\b[A-Za-z](?:\.[A-Za-z])+\b\.?")
# A dash with a space on either side parts a region from a city; one inside a word ("Winston-Salem") does not.
_DASH = re.compile(r"\s*(?:(?<=\s)-|-(?=\s))\s*")
# Words that name no place: a city position holding any of them, as a whole word, is not read as a city. Words
# outside this list that name no place may still be read as a city (spec §10 item 31).
_NOT_PLACE_WORDS = {
    "virtual", "nationwide", "distributed", "flexible", "telecommute", "telecommuting", "telework", "teleworking",
    "home", "office", "work", "working", "field", "travel", "traveling", "travelling", "global", "worldwide",
    "international", "various", "multiple", "location", "locations", "all", "any", "other", "tbd", "headquarters",
    "hq", "corporate", "based", "eligible", "friendly", "optional", "first",
}  # fmt: skip
# Between places; " - " is not one (spec §10 item 31): it joins a region and a city.
_SEPARATOR = re.compile(r";|\||\bor\b|/")
_PARENTHESES = re.compile(r"\([^)]*\)")
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
# No stated level: "Manager" means senior, except in PM titles where it names the role (spec §10 item 31).
_PM_TITLE = re.compile(r"\b(product marketing|product|program|project) manager\b")
_MANAGER = re.compile(r"\bmanager\b")
_INTERN = re.compile(r"\b(intern|internship|co-op)\b")
_TITLE_DIMENSIONS = ("role", "specialty", "domain", "tech_stack")


@dataclass(frozen=True)
class Pretag:
    tags: tuple[str, ...]
    confident: frozenset[str]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _region(country: str | None, text: str) -> str | None:
    """The full name of the state or province `text` names in `country` ("NY" or "New York" -> "new york")."""
    low, codes = text.casefold(), _REGION_CODES.get(country or "", {})
    return codes.get(low) or (low if low in codes.values() else None)


def _city(country: str, text: str, region: str | None = None) -> str | None:
    """The city position, the same rule in every format (spec §10 item 31): a city-level ref, or None when what
    stands there names no city: a code (two letters or fewer, or with digits), a country, a state or province (a
    well-known city named like one counts beside that state only, "New York, NY"), or a word such as "Nationwide"."""
    slug, low = _slug(text), " ".join(text.casefold().split())
    if len(slug.replace("_", "")) <= 2 or any(c.isdigit() for c in text) or low in _COUNTRIES:
        return None
    if set(re.split(r"[\s-]+", low)) & _NOT_PLACE_WORDS:
        return None
    if (
        any(_region(c, low) for c in _REGION_CODES)
        and (country, _region(country, region or ""), low) not in _NAMED_LIKE_REGION
    ):
        return None
    return f"location.{country}.{slug}"


def _region_city(part: str) -> str | None:
    """ "California - Palo Alto", "Israel - Tel Aviv": a country, US state or Canadian province, then the city."""
    pieces = part.split(" - ")
    if len(pieces) != 2 or any("," in p for p in pieces):  # "San Francisco, CA - New York, NY" is not read
        return None
    region, city = pieces
    country = _COUNTRIES.get(region.casefold())
    if country:  # "USA - California": a country and its state name no city
        if _region(country, city) or _COUNTRIES.get(city.casefold()) == country:
            return f"location.{country}"
        return _city(country, city)
    country = next((c for c in _REGION_CODES if _region(c, region)), None)
    return _city(country, city, region) if country else None


def _place(part: str) -> str | None:
    """One place (spec §10 item 31) -> a location ref; None when unsure."""
    if " - " in part:
        return _region_city(part)
    tokens = [t.strip() for t in part.split(",") if t.strip()]
    low = [t.casefold() for t in tokens]
    if len(low) == 1:
        country = _COUNTRIES.get(low[0])
        return f"location.{country}" if country else None
    head, tail = _COUNTRIES.get(low[0]), _COUNTRIES.get(low[-1])
    if head and tail and head != tail:  # two countries in one place
        return None
    if len(low) == 2:
        if head and (_region(head, tokens[1]) or tail == head):  # "US, CA": the country only
            return f"location.{head}"
        if _region("us", tokens[1]):  # "Austin, TX": the first part is the city
            return _city("us", tokens[0], tokens[1])
        if tail:  # "London, UK"; "Texas, United States" names a region, not a city
            return f"location.{tail}" if _region(tail, tokens[0]) else _city(tail, tokens[0])
        return _city(head, tokens[1]) if head else None  # "India, Bengaluru"
    if len(low) == 3:
        if tail:  # "Seattle, WA, United States"
            region = tokens[1] if _region(tail, tokens[1]) else None
            if region is None and tail in _REGION_CODES:
                return None
            return _city(tail, tokens[0], region)
        if head in _REGION_CODES:  # "US, CA, Santa Clara"; "United States of America, McLean, Virginia"
            pairs = ((tokens[1], tokens[2]), (tokens[2], tokens[1]))  # either part may be the state
            readings = {_city(head, city, region) for region, city in pairs if _region(head, region)} - {None}
            return readings.pop() if len(readings) == 1 else None
    return None


def _normalize(text: str) -> str:
    text = _DOTTED.sub(lambda m: m.group().replace(".", ""), _DASHES.sub("-", text))
    return " ".join(text.split())


def _mode(text: str) -> str | None:
    words = {re.sub(r"[\s-]+", " ", word.casefold()) for word in _MODE_WORDS.findall(text)}
    if "hybrid" in words:
        return "hybrid"
    if words & set(_ONSITE):
        return "onsite"
    return "remote" if words & set(_REMOTE) else None


def _locations(text: str) -> tuple[list[str], str | None, bool]:
    """Location refs, the work mode every place states, and whether every place was read (spec §10 item 31)."""
    # "(Remote/Hybrid)" is one place: separators inside parentheses part nothing.
    text = _PARENTHESES.sub(lambda m: _SEPARATOR.sub(" ", m.group()), _normalize(text))
    parts = [p for p in _SEPARATOR.split(text) if p.strip(" ,-")]
    refs, modes, unsure = [], set(), False
    for part in parts:
        modes.add(_mode(part))
        cleaned = " ".join(_MODE_WORDS.sub(" ", _PARENTHESES.sub(" ", part)).split())
        pieces = [p.strip(" ,-") for p in _DASH.split(cleaned)]
        cleaned = " - ".join(p for p in pieces if p)  # a word taken out leaves no empty piece
        if not cleaned:  # only a work mode, such as "Remote"
            continue
        ref = _place(cleaned)
        if ref is None:
            unsure = True
        else:
            refs.append(ref)
    mode = next(iter(modes)) if len(modes) == 1 else None  # a mode only some places state is not read
    return list(dict.fromkeys(refs)), mode, bool(refs) and not unsure


def _canonical_places(refs: list[str], vocab: Vocabulary) -> list[str] | None:
    try:
        return list(dict.fromkeys(vocab.canonical(ref) for ref in refs))
    except InvariantError:
        return None


def read_locations(text: str, vocab: Vocabulary) -> list[str] | None:
    """The canonical location tags of a location when every place in it is read; None otherwise."""
    refs, _, read = _locations(text)
    return _canonical_places(refs, vocab) if read else None


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
    refs, mode, read = _locations(job.location)
    places = _canonical_places(refs, vocab) if read else None
    if places is not None:
        tags |= set(places)
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
    level = next((level for pattern, level in _SENIORITY if pattern.search(title)), None)
    if level is None and _MANAGER.search(_PM_TITLE.sub(" ", title)):
        level = "senior"
    if level:
        tags.add(f"seniority.{level}")
        confident.add("seniority")

    tags |= _title_suggestions(job.title, vocab)
    canonical = []
    for tag in sorted(tags):
        try:
            canonical.append(vocab.canonical(parse_ref(tag).ref))
        except InvariantError:
            continue  # a slug outside the key syntax: leave it to the tagger
    return Pretag(tuple(dict.fromkeys(canonical)), frozenset(confident))
