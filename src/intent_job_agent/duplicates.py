"""Listings of the same posting found on several boards or in several cities (spec 4.9 item 6, section 10 items
71-72). Pure and deterministic: the same company and title, nearly the same text (or the same place when a listing
has no text), and never two different requisitions of the company's own career site."""

import re
from dataclasses import dataclass, field
from functools import cached_property
from itertools import combinations
from urllib.parse import urlsplit

from .config import Settings
from .domain import Job

COMPANY_SITES = frozenset({"workday", "greenhouse", "lever", "ashby", "avature"})
COMPANY_WORDS = frozenset({"inc", "llc", "ltd", "corp", "corporation", "co", "company", "the"})


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def requisition(job: Job) -> str | None:
    """The company site's own id for a posting: for Workday the number at the end of the link (R171718,
    R-292353, JR2024390; the -1 Workday adds to a repeated link is not part of it), for the other company sites the
    link without its query. Board listings (LinkedIn, Indeed, ...) have none."""
    if job.source not in COMPANY_SITES:
        return None
    link = urlsplit(job.url)
    path = link.path.rstrip("/")
    if job.source == "workday":
        last = path.rsplit("/", 1)[-1].rsplit("_", 1)[-1]
        number = re.match(r"[A-Za-z]*-?\d+", last)
        return f"workday {link.netloc} {number.group() if number else last}"
    return f"{job.source} {link.netloc}{path}"


TEXT, PLACE, DIFFERENT = "text", "place", "different"  # how two listings compare; None: cannot tell


@dataclass
class _Listing:
    job: Job
    shingle_words: int
    key: tuple[str, str] | None = field(init=False)
    place: str = field(init=False)
    requisition: str | None = field(init=False)

    def __post_init__(self):
        company = " ".join(w for w in _words(self.job.company) if w not in COMPANY_WORDS)
        self.key = (company, " ".join(_words(self.job.title))) if company else None
        self.place = " ".join(_words(self.job.location))
        self.requisition = requisition(self.job)

    @cached_property
    def shingles(self) -> frozenset[tuple[str, ...]]:
        words, size = _words(self.job.description), self.shingle_words
        return frozenset(tuple(words[i : i + size]) for i in range(len(words) - size + 1))

    def compare(self, other: "_Listing", overlap: float) -> str | None:
        """TEXT or PLACE when they are the same posting (by text, or by place when one has no text), DIFFERENT when
        they surely are not, None when a missing text leaves it open."""
        if self.key is None or self.key != other.key:
            return DIFFERENT
        if self.requisition and other.requisition and self.requisition != other.requisition:
            return DIFFERENT
        if not self.shingles or not other.shingles:
            return PLACE if self.place and self.place == other.place else None
        small, large = sorted((self.shingles, other.shingles), key=len)
        return TEXT if len(small & large) >= overlap * len(small) else DIFFERENT


def same_posting(a: Job, b: Job, settings: Settings) -> bool:
    """Same company and title (case, punctuation and words like Inc or LLC ignored), and at least `overlap` of the
    shorter text's word runs appear in the longer one, so a board's cut-off copy still matches; location does not
    matter. When either has no text, the same place instead. Two company-site listings with different requisitions
    are never the same posting, nor are listings without a company."""
    rules = settings.apply_list
    a_listing, b_listing = (_Listing(job, rules.duplicate_shingle_words) for job in (a, b))
    return a_listing.compare(b_listing, rules.duplicate_overlap) in (TEXT, PLACE)


def group(jobs: list[Job], settings: Settings) -> list[list[Job]]:
    """The listings grouped by posting, groups and listings in input order. Pairs that are the same posting are
    joined, those matched by text before those matched by place, each in input order; a join that would put two
    listings that surely differ in one group is skipped. So a board listing that looks like two requisitions joins
    only one of them and never links the two, and a listing without text joins the posting it shares a place
    with even when the posting's other listings are elsewhere."""
    rules = settings.apply_list
    listings = [_Listing(job, rules.duplicate_shingle_words) for job in jobs]
    by_key: dict[tuple[str, str], list[int]] = {}
    for n, listing in enumerate(listings):
        if listing.key:
            by_key.setdefault(listing.key, []).append(n)
    joins, apart = [], set()
    for same_key in by_key.values():
        for a, b in combinations(same_key, 2):
            verdict = listings[a].compare(listings[b], rules.duplicate_overlap)
            if verdict == DIFFERENT:
                apart.add((a, b))
            elif verdict is not None:
                joins.append((verdict != TEXT, a, b))
    members = {n: [n] for n in range(len(listings))}  # by each group's first listing
    root = list(range(len(listings)))
    for _, a, b in sorted(joins):
        first, second = sorted((root[a], root[b]))
        if first == second or any((min(x, y), max(x, y)) in apart for x in members[first] for y in members[second]):
            continue
        members[first] = sorted(members[first] + members.pop(second))
        for n in members[first]:
            root[n] = first
    return [[listings[n].job for n in members[first]] for first in sorted(members)]
