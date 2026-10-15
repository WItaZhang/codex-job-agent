"""Listings of the same posting found on several boards or in several cities (spec 4.9 item 6, section 10 items
71-72 and 77-81). Pure and deterministic, no network and no model. Only listings with the same company and title
are compared; then, in this order: the same apply link joins them; two ids of one applicant tracking system, two
job numbers in the texts or texts that differ only in numbers keep them apart; nearly the same text joins them
where their places allow it; and a listing without text joins one in the same city."""

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from itertools import combinations
from typing import NamedTuple
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from .config import Settings
from .domain import Job

COMPANY_SITES = frozenset({"workday", "greenhouse", "lever", "ashby", "avature"})
COMPANY_WORDS = frozenset({"inc", "llc", "ltd", "corp", "corporation", "co", "company", "the"})


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


# --- apply links (section 10 item 77) ---------------------------------------------------------------------


class Link(NamedTuple):
    """Where a listing's application starts: an applicant tracking system and its id for the posting, or ``""``
    and the link itself (host, path and the parameters that name the job) for any other site."""

    system: str
    id: str


_JUNK = frozenset({"", "none", "null", "nan", "undefined"})
_WRAPPER_HOSTS = ("recruitics.com", "hodes.com", "jobiqo.com")  # hosts that only redirect
_WRAPPER_PATH = re.compile(r"(^|/)(redirect|clicktracker|click|track|out|exit|r|url)(/|$)")
_TARGET_PARAMS = ("rx_url", "url", "u", "destination", "dest", "target", "redirect", "redirect_url", "redirecturl",
                  "goto", "link", "to", "q", "session_redirect")  # fmt: skip
_TRACKERS = ("appcast.io", "prng.co", "jometer.com", "gohiring.com", "easyapply.jobs", "jobswipe.net")
_BOARDS = ("linkedin.com", "indeed.com", "ziprecruiter.com", "glassdoor.com", "glassdoor.ca", "glassdoor.co.uk",
           "talent.com", "simplyhired.com", "simplyhired.ca", "monster.com", "careerbuilder.com", "dice.com",
           "jooble.org", "adzuna.com", "lensa.com", "jobrapido.com", "hackajob.com", "career.io")  # fmt: skip
_TRACKING = frozenset({"source", "src", "gh_src", "lever-source", "ref", "referrer", "refer", "trk", "trackingid",
                       "refid", "cmpid", "intlink", "iis", "iisn", "jobsite", "jbsrc", "posteddate", "clickid",
                       "click_id", "gclid", "fbclid", "msclkid", "_ga", "_gl", "mc_cid", "mc_eid", "sid", "lang",
                       "locale", "embed", "in_iframe", "board_id", "codes", "mode", "trid"})  # fmt: skip
_TRACKING_PREFIXES = ("utm_", "rx_", "air_")
_JOB_PARAMS = frozenset({"gh_jid", "jk", "jobid", "job_id", "job", "jid", "id", "pid", "rid", "req", "reqid",
                         "requisitionid", "career_job_req_id", "opportunityid", "postingid", "jobopeningid", "jobreq",
                         "jobpost", "token", "ashby_jid", "lever_jid"})  # fmt: skip
_LIST_SEGMENTS = frozenset({"careers", "career", "jobs", "job", "search", "search-jobs", "searchjobs", "job-search",
                            "jobsearch", "results", "openings", "positions", "open-positions", "opportunities",
                            "vacancies", "join-us", "work-with-us", "careers-home", "home", "index", "index.html",
                            "listings", "all-jobs"})  # fmt: skip
_TAIL = frozenset({"apply", "application", "apply-now", "applynow", "login"})
_LOCALE = re.compile(
    r"^(?:en|fr|de|es|pt|nl|ja|zh|ko|"
    r"(?:en|fr|de|es|it|pt|nl|ja|zh|ko|sv|da|fi|no|nb|pl|ru|tr|cs|hu|ro|he|ar|th|vi|id)[-_][a-z]{2})$"
)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MAX_UNWRAP = 5


@dataclass(frozen=True)
class _Url:
    host: str
    segments: tuple[str, ...]  # lowercased, without empty, locale and trailing apply segments
    params: dict[str, str]  # lowercased names

    def next_after(self, marker: str) -> str | None:
        """The segment right after `marker`."""
        if marker in self.segments[:-1]:
            return self.segments[self.segments.index(marker) + 1]
        return None


def _parse(link: str) -> _Url | None:
    try:
        parts = urlsplit(link.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    segments = [s for s in unquote(parts.path).lower().split("/") if s and not _LOCALE.match(s)]
    while segments and segments[-1] in _TAIL:
        segments.pop()
    params = {name.lower(): value for name, value in parse_qsl(parts.query, keep_blank_values=True)}
    return _Url(host.removeprefix("www."), tuple(segments), params)


def _on(host: str, domains) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _target(url: _Url) -> str | None:
    """The link a redirect wrapper carries."""
    if not (
        _on(url.host, _WRAPPER_HOSTS)
        or "session_redirect" in url.params
        or _WRAPPER_PATH.search("/".join(url.segments))
    ):
        return None
    return next((url.params[name] for name in _TARGET_PARAMS if re.match(r"https?://", url.params.get(name, ""))), None)


def _digits(segment: str | None) -> str | None:
    found = re.match(r"\d+", segment or "")
    return found.group() if found else None


def _workday(url: _Url) -> Link | None:
    """Tenant (the host's first label, or the segment after `recruiting` on myworkdaysite.com) and the requisition:
    the last segment after its last `_`, without the 1-2 digit repeat suffix Workday adds (`-1`)."""
    segments = list(url.segments)
    tenant = segments[1] if len(segments) > 1 and segments[0] == "recruiting" else url.host.split(".")[0]
    if not segments:
        return None
    requisition = re.sub(r"(?<=\d)-\d{1,2}$", "", segments[-1].rsplit("_", 1)[-1])
    return Link("workday", f"{tenant}/{requisition}")


def _avature(url: _Url) -> Link | None:
    found = _digits(url.segments[-1]) if "jobdetail" in url.segments[:-1] else None
    found = found or (url.params.get("jobid") if url.params.get("jobid", "").isdigit() else None)
    return Link("avature", f"{url.host}/{found}") if found else None


def _uuid_param(url: _Url, name: str) -> str | None:
    value = url.params.get(name, "").lower()
    return value if _UUID.match(value) else None


def _system(url: _Url) -> Link | None:
    """A known applicant tracking system or company career page, recognized by its host."""
    host, segments = url.host, url.segments
    if url.params.get("gh_jid", "").isdigit():
        return Link("greenhouse", url.params["gh_jid"])
    if _uuid_param(url, "ashby_jid"):
        return Link("ashby", _uuid_param(url, "ashby_jid"))
    if _on(host, ("myworkdayjobs.com", "myworkdaysite.com", "myworkday.com")):
        return _workday(url)
    if _on(host, ("greenhouse.io",)) and segments and segments[-1].isdigit() and "jobs" in segments:
        return Link("greenhouse", segments[-1])
    if _on(host, ("lever.co",)) and len(segments) >= 2:
        return Link("lever", segments[1])
    if host == "jobs.ashbyhq.com" and len(segments) >= 2:
        return Link("ashby", segments[1])
    if _on(host, ("smartrecruiters.com",)) and len(segments) >= 2 and _digits(segments[1]):
        return Link("smartrecruiters", _digits(segments[1]))
    if _on(host, ("icims.com",)) and _digits(url.next_after("jobs")):
        return Link("icims", f"{host}/{_digits(url.next_after('jobs'))}")
    if _on(host, ("paylocity.com",)) and _digits(url.next_after("details")):
        return Link("paylocity", _digits(url.next_after("details")))
    if _on(host, ("avature.net",)) or "jobdetail" in segments:
        return _avature(url)
    if _on(host, ("google.com",)) and _digits(url.next_after("results")):
        return Link("google", _digits(url.next_after("results")))
    if _on(host, ("metacareers.com",)) and _digits(url.next_after("jobs")):
        return Link("meta", _digits(url.next_after("jobs")))
    if _on(host, ("amazon.jobs",)) and _digits(url.next_after("jobs")):
        return Link("amazon", _digits(url.next_after("jobs")))
    if host == "jobs.apple.com" and url.next_after("details"):
        found = re.match(r"\d[\d-]*", url.next_after("details"))
        return Link("apple", found.group()) if found else None
    if _on(host, ("careers.microsoft.com",)) and _digits(url.next_after("job")):
        return Link("microsoft", _digits(url.next_after("job")))
    return None


def _other(url: _Url) -> Link | None:
    """Any other site: host, path and the parameters that are not tracking; None for a career home or list page."""
    params = sorted(
        (name, value)
        for name, value in url.params.items()
        if value and name not in _TRACKING and not name.startswith(_TRACKING_PREFIXES)
    )
    if all(s in _LIST_SEGMENTS for s in url.segments) and not any(name in _JOB_PARAMS for name, _ in params):
        return None
    query = f"?{urlencode(params)}" if params else ""
    return Link("", f"{url.host}/{'/'.join(url.segments)}{query}".lower())


def link_key(link: str) -> Link | None:
    """The comparison key of an apply link found on a board or a company page, or None when it cannot name one
    posting: junk, a click tracker without a target, a board's own page, a career home or list page."""
    url = _parse(link)
    for _ in range(_MAX_UNWRAP):
        target = _target(url) if url else None
        if target is None:
            break
        url = _parse(target)
    if url is None or _target(url) or _on(url.host, _WRAPPER_HOSTS) or _on(url.host, _TRACKERS):
        return None
    if _on(url.host, _BOARDS):
        return None
    return _system(url) or _other(url)


def _company_site_key(job: Job) -> Link | None:
    """A company-site listing's own link, read in its source's format whatever the host."""
    url = _parse(job.url)
    if url is None:
        return None
    if job.source == "workday":
        return _workday(url)
    found = None
    if job.source == "greenhouse":
        gh_jid = url.params.get("gh_jid", "")
        found = gh_jid if gh_jid.isdigit() else _digits(url.next_after("jobs"))
    elif job.source in ("lever", "ashby"):
        found = _uuid_param(url, f"{job.source}_jid") or (url.segments[1] if len(url.segments) >= 2 else None)
    elif job.source == "avature":
        return _avature(url) or Link("avature", f"{url.host}/{'/'.join(url.segments)}")
    return Link(job.source, found or f"{url.host}/{'/'.join(url.segments)}")


def apply_key(job: Job) -> Link | None:
    """Where the listing's application starts (section 10 item 77): a company-site listing's own link, a board
    listing's recorded `application_url`, a manual import's link; None when there is no usable link."""
    if job.source in COMPANY_SITES:
        return _company_site_key(job)
    link = job.url if job.source == "manual" else job.attributes.get("application_url", "")
    if link.strip().lower() in _JUNK:
        return None
    return link_key(link)


# --- evidence in the texts (section 10 item 79) -----------------------------------------------------------

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATES = re.compile(
    rf"\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b"
    rf"|\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b|\b\d{{1,2}}\s+{_MONTH},?\s+\d{{4}}\b",
    re.IGNORECASE,
)
_NUMBER_AFTER = re.compile(
    r"\b(?:job\s*(?:id|number|no\.?|#)|req(?:uisition)?\s*(?:id|number|no\.?|#)|requisition)\s*[:#.]?\s*"
    r"([a-z0-9][a-z0-9-]*)",
    re.IGNORECASE,
)
_NUMBER_ALONE = re.compile(r"\b(?:jr|req|r)-?\d{5,}\b", re.IGNORECASE)


def job_numbers(text: str) -> frozenset[str]:
    """Job numbers written in a text: after Job ID / Job Number / Req # / Req ID / Requisition ID, or a bare R, JR or
    REQ number; at least 5 digits. Compared without case and dashes."""
    found = [m.group(1) for m in _NUMBER_AFTER.finditer(text)] + [m.group() for m in _NUMBER_ALONE.finditer(text)]
    return frozenset(n.upper().replace("-", "") for n in found if sum(c.isdigit() for c in n) >= 5)


# --- listings ---------------------------------------------------------------------------------------------

LINK, CITY_TEXT, TEXT, PLACE, DIFFERENT = "link", "city_text", "text", "place", "different"
_SAME = (LINK, CITY_TEXT, TEXT, PLACE)  # in the order group() joins them


def _company(job: Job) -> str:
    """The company name without words like Inc; for a listing without one, its only company tag (item 80)."""
    name = " ".join(w for w in _words(job.company) if w not in COMPANY_WORDS)
    if name:
        return name
    tags = [tag.removeprefix("company.") for tag in job.tags if tag.startswith("company.")]
    return " ".join(w for w in _words(tags[0]) if w not in COMPANY_WORDS) if len(tags) == 1 else ""


def posting_key(job: Job) -> tuple[str, str] | None:
    """Only listings with the same key are ever the same posting; None for a listing without a company."""
    company = _company(job)
    return (company, " ".join(_words(job.title))) if company else None


@dataclass
class _Listing:
    job: Job
    shingle_words: int
    key: tuple[str, str] | None = field(init=False)
    cities: frozenset[str] = field(init=False)  # city-level location keys such as us.seattle (a metro area counts)
    countries: frozenset[str] = field(init=False)
    place: str = field(init=False)  # the normalized raw place, empty for "N Locations"
    no_city: bool = field(init=False)

    def __post_init__(self):
        self.key = posting_key(self.job)
        places = [tag.removeprefix("location.") for tag in self.job.tags if tag.startswith("location.")]
        self.cities = frozenset(p for p in places if "." in p)
        self.countries = frozenset(p.split(".")[0] for p in places)
        raw = " ".join(_words(self.job.location))
        several = re.fullmatch(r"\d+ locations?", raw) is not None
        self.place = "" if several else raw
        self.no_city = several or not self.cities

    @cached_property
    def link(self) -> Link | None:
        return apply_key(self.job)

    @cached_property
    def company_site(self) -> bool:
        """A company's own career site (item 78): its sources, and manual imports whose link names a system id."""
        if self.job.source in COMPANY_SITES:
            return True
        return self.job.source == "manual" and self.link is not None and self.link.system != ""

    @cached_property
    def shingles(self) -> frozenset[tuple[str, ...]]:
        words, size = _words(self.job.description), self.shingle_words
        return frozenset(tuple(words[i : i + size]) for i in range(len(words) - size + 1))

    @cached_property
    def numbers(self) -> frozenset[str]:
        return job_numbers(self.job.description)

    @cached_property
    def word_counts(self) -> Counter:
        return Counter(_words(_DATES.sub(" ", self.job.description)))

    def same_city(self, other: "_Listing") -> bool:
        return bool(self.cities & other.cities) or bool(self.place and self.place == other.place)

    def compare(self, other: "_Listing", overlap: float) -> str | None:
        """How the two listings relate (item 81): one of the _SAME kinds, DIFFERENT when they surely are not the
        same posting, or None when it cannot be told."""
        if self.key is None or self.key != other.key:
            return DIFFERENT
        if self.link and self.link == other.link:
            return LINK
        if self.link and other.link and self.link.system and self.link.system == other.link.system:
            return DIFFERENT
        if not self.shingles or not other.shingles:
            return PLACE if self.same_city(other) else None
        if self.numbers and other.numbers and not self.numbers & other.numbers:
            return DIFFERENT
        differ = (self.word_counts - other.word_counts) + (other.word_counts - self.word_counts)
        if differ and all(word.isdigit() for word in differ):
            return DIFFERENT
        small, large = sorted((self.shingles, other.shingles), key=len)
        if len(small & large) < overlap * len(small):
            return DIFFERENT
        if self.countries and other.countries and not self.countries & other.countries:
            return None
        if self.same_city(other):
            return CITY_TEXT
        if self.no_city or other.no_city or not (self.company_site or other.company_site):
            return TEXT
        return None


def _listing(job: Job, settings: Settings) -> _Listing:
    return _Listing(job, settings.duplicates.shingle_words)


def same_posting(a: Job, b: Job, settings: Settings) -> bool:
    """Whether two listings are the same posting by spec 4.9 item 6. Cities, countries and company tags are read
    from the jobs' tags, so pass jobs with their corrected tags."""
    return _listing(a, settings).compare(_listing(b, settings), settings.duplicates.overlap) in _SAME


def same_city(a: Job, b: Job) -> bool:
    """A shared city-level location tag, or the same raw place (item 78)."""
    return _Listing(a, 1).same_city(_Listing(b, 1))


def group(jobs: list[Job], settings: Settings) -> list[list[Job]]:
    """The listings grouped by posting, groups and listings in input order (item 81). Pairs that are the same
    posting are joined: by link first, then by text in one city, then by other text, then by place; each kind in
    input order. A join that would put two listings that surely differ in one group is skipped, so a board listing
    that looks like two requisitions joins only one of them and never links the two."""
    overlap = settings.duplicates.overlap
    listings = [_listing(job, settings) for job in jobs]
    by_key: dict[tuple[str, str], list[int]] = {}
    for n, listing in enumerate(listings):
        if listing.key:
            by_key.setdefault(listing.key, []).append(n)
    joins, apart = [], set()
    for same_key in by_key.values():
        for a, b in combinations(same_key, 2):
            verdict = listings[a].compare(listings[b], overlap)
            if verdict == DIFFERENT:
                apart.add((a, b))
            elif verdict is not None:
                joins.append((_SAME.index(verdict), a, b))
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
