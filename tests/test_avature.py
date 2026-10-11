"""Slice 2g: Avature career sites as boards. Every page here is SYNTHETIC and served offline via httpx.MockTransport.

Search pages are generated below in the markup of Avature's standard template (checked read-only on
jobs.lenovo.com and bloomberg.avature.net on 2026-10-10); detail and error pages are in tests/fixtures/avature/.
Hosts, companies and postings are made up.
"""

import json
import re
import threading
import time
from dataclasses import dataclass, field
from html import escape

import httpx
import pytest
import yaml

from intent_job_agent import avature
from intent_job_agent.boards import BoardSpec, load_boards
from intent_job_agent.discovery import DiscoveryError
from intent_job_agent.domain import InvariantError, Job
from intent_job_agent.store import Store
from intent_job_agent.tools import SCHEDULED_TOOLS, TOOL_TIERS, Toolbox

from .conftest import BASE_INTENT, BASE_VOCAB, ROOT, settings

PAGES = ROOT / "tests/fixtures/avature"
DETAIL_A = (PAGES / "detail_a.html").read_text(encoding="utf-8")
DETAIL_B = (PAGES / "detail_b.html").read_text(encoding="utf-8")
OOPS = (PAGES / "oops.html").read_text(encoding="utf-8")
assert all("SYNTHETIC" in page for page in (DETAIL_A, DETAIL_B, OOPS))

SITE_A = "https://careers.example.com/en_US/careers"  # layout A: "Country, State, City", career area, Req #, posted
SITE_B = "https://terminal.example.org/careers"  # layout B: "City, State, Country" only
US = "United States of America, North Carolina, Morrisville"
DAY1 = "2026-10-11"


# --- a SYNTHETIC Avature site -------------------------------------------------------------------


@dataclass
class Posting:
    id: int
    title: str
    location: str = ""
    category: str = ""
    req: str = ""
    posted: str = ""
    href: str | None = ""  # "" links to the posting on its own site; None renders a card without a link

    def link(self, base: str) -> str:
        return self.href or f"{base}/JobDetail/{re.sub(r'[^A-Za-z0-9]+', '-', self.title).strip('-')}/{self.id}"


def us(n: int, title: str = "Machine Learning Engineer", **kw) -> Posting:
    return Posting(n, title, location=US, category="Software Engineering", req=f"WD{n:07d}", posted="09-Oct-2026", **kw)


def abroad(n: int, title: str = "Machine Learning Engineer") -> Posting:
    return Posting(n, title, location="China, Beijing, Beijing", category="Software Engineering", req=f"WD{n:07d}")


def city_first(n: int, title: str = "Senior Software Engineer") -> Posting:
    return Posting(n, title, location="New York, New York, United States of America")


NO_JOBS_CARD = """<article class="article article--result">
  <div class="article__header"><div class="article__header__text">
    <h3 class="article__header__text__title article__header__text__title--4">No jobs found</h3>
  </div></div>
</article>"""


def card(base: str, posting: Posting) -> str:
    spans = [posting.location] if posting.location else []
    spans += [f"Req #: {posting.req}"] if posting.req else []
    spans += [f"Posted {posting.posted}"] if posting.posted else []
    subtitle = "<br>\n".join(f"<span>{escape(span)}</span>" for span in spans)
    category = f'<span class="paragraph">{escape(posting.category)}</span>' if posting.category else ""
    if posting.href is None:
        title, apply = escape(posting.title), ""
    else:
        link = escape(posting.link(base))
        title = f'<a href="{link}">{escape(posting.title)}</a>'
        apply = f'<a class="button button--primary" href="{link}">Apply</a>'
    return f"""<article class="article article--result">
  <div class="article__header">
    <div class="article__header__text">
      <h3 class="article__header__text__title article__header__text__title--4">{title}</h3>
      {category}
      <div class="article__header__text__subtitle">{subtitle}</div>
    </div>
    <div class="article__header__actions">{apply}</div>
  </div>
</article>"""


def results_page(base: str, cards: list[str], legend: str, next_offset: int | None, page_size: int, offset: int) -> str:
    nav = ""
    if next_offset is not None:
        href = f"{base}/SearchJobs/?jobRecordsPerPage={page_size}&amp;jobOffset={next_offset}"
        nav = f'<a class="list-controls__pagination__item paginationNextLink" href="{href}">Next &gt;&gt;</a>'
    other_language = base.replace("/en_US/", "/de_DE/")
    return f"""<!-- SYNTHETIC: an Avature-style search results page generated for tests. -->
<!DOCTYPE html>
<html lang="en"><body>
<a class="portalLanguages__link" href="{other_language}/SearchJobs/?jobOffset={offset}">Deutsch</a>
<main>
  <div class="list-controls">
    <div class="list-controls__text"><div class="list-controls__text__legend" tabindex="0">{legend}</div></div>
    <div class="list-controls__pagination">{nav}</div>
  </div>
  <section class="section section--list">
{"".join(cards)}
  </section>
  <div class="list-controls__pagination">{nav}</div>
</main>
</body></html>"""


@dataclass
class Site:
    base: str
    postings: list[Posting]
    page_size: int = 3
    plus_total: bool = True  # "999+" until the last page, like jobs.lenovo.com; False: exact everywhere, like Bloomberg
    claimed_total: int | None = None  # what the last page claims; defaults to the real count
    overrides: dict[int, str] = field(default_factory=dict)  # offset -> page served instead
    next_overrides: dict[int, int] = field(default_factory=dict)  # offset -> next-link offset served instead
    details: dict[int, str | int] = field(default_factory=dict)  # posting id -> page or HTTP status (default A)

    def page(self, offset: int) -> str:
        if offset in self.overrides:
            return self.overrides[offset]
        total = len(self.postings)
        if offset < total:
            chunk = self.postings[offset : offset + self.page_size]
            last = offset + len(chunk) >= total
            claimed = str(total if self.claimed_total is None else self.claimed_total)
            legend = f"{offset + 1}-{offset + len(chunk)} of {claimed if last or not self.plus_total else '999+'} jobs"
            next_offset = None if last else self.next_overrides.get(offset, offset + len(chunk))
            return results_page(
                self.base, [card(self.base, p) for p in chunk], legend, next_offset, self.page_size, offset
            )
        if total == 0 and offset == 0:
            return results_page(self.base, [NO_JOBS_CARD], "1-0 of 0 jobs", None, self.page_size, offset)
        if offset < total + 20:  # just past the end: an empty page whose Next link wraps back to the start
            return results_page(self.base, [NO_JOBS_CARD], "1-0 of 999+", self.page_size, self.page_size, offset)
        return OOPS


class FakeAvature:
    """Serves SYNTHETIC Avature sites. With `gate` set, every request waits for it (to observe a running fetch)."""

    def __init__(self, *sites: Site):
        self.sites = list(sites)
        self.requests: list[str] = []
        self.gate: threading.Event | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if self.gate is not None:
            self.gate.wait(5)
        url = str(request.url)
        for site in self.sites:
            if url.startswith(f"{site.base}/SearchJobs"):
                return httpx.Response(200, html=site.page(int(request.url.params.get("jobOffset", "0"))))
            if url.startswith(f"{site.base}/JobDetail/"):
                detail = site.details.get(int(request.url.path.rstrip("/").rsplit("/", 1)[-1]), DETAIL_A)
                if isinstance(detail, int):
                    return httpx.Response(detail, headers={"Location": site.base} if detail in (301, 302) else {})
                return httpx.Response(200, html=detail)
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def detail_ids(self) -> list[str]:
        return [url.rsplit("/", 1)[-1] for url in self.requests if "/JobDetail/" in url]


def listing(fake: FakeAvature, board: str = SITE_A, max_pages: int = 50) -> list[Job]:
    return avature.list_site(
        board,
        company="Example Devices",
        timeout_seconds=5,
        request_delay_seconds=0,
        max_pages=max_pages,
        client=fake.client(),
    )


def posting_ids(jobs: list[Job]) -> list[str]:
    return [job.url.rsplit("/", 1)[-1] for job in jobs]


# --- listing ------------------------------------------------------------------------------------


def test_cards_of_both_layouts_are_parsed_into_untagged_jobs():
    fake = FakeAvature(
        Site(SITE_A, [us(1), Posting(2, "Data Scientist", req="100009", posted="01-Oct-2026")]),
        Site(SITE_B, [city_first(3)], page_size=4, plus_total=False),
    )
    a1, a2 = listing(fake)
    assert (a1.title, a1.company, a1.source, a1.board) == (
        "Machine Learning Engineer",
        "Example Devices",
        "avature",
        SITE_A,
    )
    assert a1.url == f"{SITE_A}/JobDetail/Machine-Learning-Engineer/1"
    assert a1.location == US
    assert (a1.attributes["category"], a1.attributes["req"]) == ("Software Engineering", "WD0000001")
    assert "Posted 09-Oct-2026" in a1.attributes["listing"]
    assert (a1.tags, a1.description) == ([], "")
    assert (a2.location, a2.attributes["req"]) == ("", "100009")  # first subtitle item was "Req #: …"
    (b,) = listing(fake, SITE_B)
    assert b.location == "New York, New York, United States of America"
    assert "category" not in b.attributes


@pytest.mark.parametrize(("page_size", "plus_total"), [(3, True), (4, False)])
def test_pages_are_followed_by_offset_until_the_last_page(page_size, plus_total):
    fake = FakeAvature(Site(SITE_A, [us(n) for n in range(1, 11)], page_size=page_size, plus_total=plus_total))
    assert posting_ids(listing(fake)) == [str(n) for n in range(1, 11)]
    offsets = [httpx.URL(url).params.get("jobOffset", "0") for url in fake.requests]
    assert offsets == [str(offset) for offset in range(0, 10, page_size)]  # never asks past the last page


def _broken_site(case: str) -> tuple[Site, int]:
    """Seven postings, three per page (offsets 0, 3, 6), broken in one way; returns the site and max_pages."""
    postings = [us(n) for n in range(1, 8)]
    site, max_pages = Site(SITE_A, postings), 50
    if case == "error page midway":
        site.overrides[3] = OOPS
    elif case == "no jobs midway":
        site.overrides[3] = results_page(SITE_A, [NO_JOBS_CARD], "1-0 of 999+", 3, 3, 3)
    elif case == "next link skips a page":
        site.next_overrides[0] = 6
    elif case == "total does not match":
        site.claimed_total = 8
    elif case == "too many pages":
        max_pages = 2
    elif case == "card without a link":
        postings[4] = us(5, href=None)
    elif case == "link to another host":
        postings[4] = us(5, href="https://elsewhere.example.net/en_US/careers/JobDetail/X/5")
    elif case == "link outside the site path":
        postings[4] = us(5, href="https://careers.example.com/other/JobDetail/X/5")
    elif case == "link without a numeric id":
        postings[4] = us(5, href=f"{SITE_A}/JobDetail/X/five")
    return site, max_pages


@pytest.mark.parametrize(
    "case",
    [
        "error page midway",
        "no jobs midway",
        "next link skips a page",
        "total does not match",
        "too many pages",
        "card without a link",
        "link to another host",
        "link outside the site path",
        "link without a numeric id",
    ],
)
def test_an_incomplete_or_unexpected_listing_fails_the_whole_site(case):
    site, max_pages = _broken_site(case)
    with pytest.raises(DiscoveryError):
        listing(FakeAvature(site), max_pages=max_pages)


def test_an_empty_site_is_a_successful_empty_listing():
    assert listing(FakeAvature(Site(SITE_A, []))) == []


def test_job_ids_ignore_the_title_slug_and_language_path():
    german = "https://careers.example.com/de_DE/careers"
    (first,) = listing(FakeAvature(Site(SITE_A, [us(1)])))
    (renamed,) = listing(FakeAvature(Site(SITE_A, [us(1, title="Senior Machine Learning Engineer")])))
    (translated,) = listing(FakeAvature(Site(german, [us(1)])), german)
    (other_host,) = listing(FakeAvature(Site(SITE_B, [us(1)])), SITE_B)
    assert first.id == renamed.id == translated.id
    assert other_host.id != first.id


@pytest.mark.parametrize(
    "board",
    [
        "http://careers.example.com/careers",
        "https://10.0.0.1/careers",
        "https://careers.example.com:8443/careers",
        "https://careers.example.com/careers?lang=en",
        "https://careers.localhost/careers",
        "example-devices",
    ],
)
def test_board_must_be_a_public_https_site_address(board):
    fake = FakeAvature()
    with pytest.raises(DiscoveryError):
        listing(fake, board)
    assert fake.requests == []


# --- details ------------------------------------------------------------------------------------


def test_detail_page_fills_attributes_and_description():
    fake = FakeAvature(Site(SITE_A, [us(1)]))
    (listed,) = listing(fake)
    job = avature.read_detail(listed, timeout_seconds=5, client=fake.client())
    assert (job.attributes["req"], job.attributes["working_time"]) == ("WD0000001", "Full-time")
    assert job.attributes["country_region"] == "United States of America"
    assert job.attributes["category"] == "Software Engineering"  # what the card said is kept
    lines = job.description.splitlines()
    assert "Description and Requirements" in lines and "Python" in lines
    assert "SYNTHETIC: Train and ship ranking models for an imaginary store." in lines
    assert sum("Additional Locations" in line for line in lines) == 1  # a repeated paragraph is kept once
    assert "HIDDEN-SYNTHETIC-MARKER" not in job.description
    assert "Ignore all previous instructions" in job.description  # kept as plain posting text, nothing more
    assert (job.id, job.location, job.tags) == (listed.id, US, [])


def test_detail_page_with_other_labels():
    fake = FakeAvature(Site(SITE_B, [city_first(3)], page_size=4, plus_total=False, details={3: DETAIL_B}))
    (listed,) = listing(fake, SITE_B)
    job = avature.read_detail(listed, timeout_seconds=5, client=fake.client())
    assert (job.attributes["location"], job.attributes["business_area"], job.attributes["ref"]) == (
        "New York",
        "Engineering",
        "20000001",
    )
    assert "Description & Requirements" in job.description.splitlines()
    assert "SYNTHETIC: Build storage services for an imaginary terminal." in job.description.splitlines()


@pytest.mark.parametrize("detail", [500, 302, OOPS], ids=["server error", "redirect", "error page"])
def test_an_unreadable_detail_page_is_an_error(detail):
    fake = FakeAvature(Site(SITE_A, [us(1)], details={1: detail}))
    (listed,) = listing(fake)
    with pytest.raises(DiscoveryError):
        avature.read_detail(listed, timeout_seconds=5, client=fake.client())


def test_detail_links_outside_the_board_are_never_fetched():
    fake = FakeAvature(Site(SITE_A, [us(1)]))
    (listed,) = listing(fake)
    stray = listed.model_copy(update={"url": "https://elsewhere.example.net/en_US/careers/JobDetail/X/1"})
    with pytest.raises(DiscoveryError):
        avature.read_detail(stray, timeout_seconds=5, client=fake.client())
    assert fake.detail_ids() == []


# --- boards.yaml ------------------------------------------------------------------------------


def test_country_include_matches_whole_parts_of_the_location():
    spec = BoardSpec(provider="avature", board=SITE_A, country_include=("United States of America",))

    def keeps(location: str) -> bool:
        return spec.keeps(Job(id="j", title="Engineer", location=location, tags=[]))

    assert keeps(US)
    assert keeps("Austin, Texas, United States of America")
    assert keeps("UNITED STATES OF AMERICA , Texas, Austin")
    assert keeps("")  # unknown location: kept
    assert not keeps("China, Beijing, Beijing")
    assert not keeps("Austin, Texas, United States")  # only the site's own wording matches


def test_an_avature_entry_loads(tmp_path):
    path = tmp_path / "boards.yaml"
    entry = {"provider": "avature", "board": SITE_A, "company": "Example Devices", "country_include": ["Canada"]}
    path.write_text(yaml.safe_dump({"boards": [entry]}), encoding="utf-8")
    (spec,) = load_boards(path)
    assert (spec.provider, spec.board, spec.country_include) == ("avature", SITE_A, ("Canada",))


@pytest.mark.parametrize(
    "entry",
    [
        {"provider": "avature", "board": "example-devices"},  # a token, not a site address
        {"provider": "avature", "board": "https://careers.example.com/careers?lang=en"},
        {"provider": "greenhouse", "board": "acme", "country_include": ["United States of America"]},
    ],
)
def test_invalid_avature_entries_are_rejected(tmp_path, entry):
    path = tmp_path / "boards.yaml"
    path.write_text(yaml.safe_dump({"boards": [entry]}), encoding="utf-8")
    with pytest.raises(InvariantError):
        load_boards(path)


# --- tools --------------------------------------------------------------------------------------


def fast_settings():
    """Default settings without the politeness delay, so tests do not sleep."""
    s = settings()
    site = s.discovery.avature.model_copy(update={"request_delay_seconds": 0})
    return s.model_copy(update={"discovery": s.discovery.model_copy(update={"avature": site})})


@pytest.fixture
def fake():
    return FakeAvature()


@pytest.fixture
def box(tmp_path, fake):
    data = tmp_path / "data"
    data.mkdir()
    s = fast_settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data, http_client=fake.client())
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": BASE_INTENT, "vocabulary": BASE_VOCAB}, allow_unicode=True), encoding="utf-8"
    )
    box.initialize_intent(str(data / "intent.yaml"))
    return box


def write_boards(box, **extra) -> None:
    entry = {"provider": "avature", "board": SITE_A, "company": "Example Devices", **extra}
    (box.data_dir / "boards.yaml").write_text(yaml.safe_dump({"boards": [entry]}), encoding="utf-8")


def fetch(box) -> dict:
    (report,) = box.fetch_boards(wait=True)["boards"]
    return report


def _tag_all(box):
    for job in box.list_untagged_jobs(limit=100)["jobs"]:
        box.submit_job_tags(job["job_id"], ["role.ml_engineering", "specialty.ai_infra"])


def test_check_board_reads_only_the_first_page(box, fake):
    fake.sites.append(Site(SITE_A, [us(1), abroad(2, "Data Scientist"), us(3, "Research Scientist"), us(4), us(5)]))
    preview = box.check_board("avature", SITE_A)
    assert len(fake.requests) == 1
    assert preview["total"] == "999+"
    assert preview["sample_titles"][:3] == ["Machine Learning Engineer", "Data Scientist", "Research Scientist"]
    assert {US, "China, Beijing, Beijing"} <= set(preview["sample_locations"])
    assert box.store.jobs() == []


def test_details_are_read_only_for_new_jobs_that_pass_the_filters(box, fake):
    fake.sites.append(
        Site(
            SITE_A,
            [us(1), us(2, "Account Executive"), abroad(3), us(4, "Research Scientist"), Posting(5, "Data Engineer")],
        )
    )
    write_boards(box, title_include=["engineer", "scientist"], country_include=["United States of America"])
    report = fetch(box)
    assert (report["fetched"], report["filtered_out"], report["new"], report["detail_errors"]) == (5, 2, 3, 0)
    assert sorted(fake.detail_ids()) == ["1", "4", "5"]  # 5 has no location: kept
    untagged = box.list_untagged_jobs()["jobs"]
    assert len(untagged) == 3 and all("Train and ship ranking models" in j["description"] for j in untagged)
    fake.requests.clear()
    assert fetch(box)["new"] == 0
    assert fake.detail_ids() == []  # stored jobs are not read again


def test_a_failed_detail_page_skips_only_that_job_until_the_next_fetch(box, fake):
    site = Site(SITE_A, [us(1), us(2)], details={2: 500})
    fake.sites.append(site)
    write_boards(box)
    report = fetch(box)
    assert (report["new"], report["detail_errors"], report["error"]) == (1, 1, None)
    assert posting_ids(box.store.jobs()) == ["1"]
    del site.details[2]
    assert fetch(box)["new"] == 1
    assert sorted(posting_ids(box.store.jobs())) == ["1", "2"]


def test_jobs_gone_from_the_site_are_not_recommended(box, fake):
    site = Site(SITE_A, [us(1), us(2)])
    fake.sites.append(site)
    write_boards(box)
    fetch(box)
    _tag_all(box)
    gone = next(job.id for job in box.store.jobs() if job.url.endswith("/2"))
    site.postings = [us(1)]
    fetch(box)
    assert gone not in {j["job_id"] for j in box.select_today(DAY1)["jobs"]}


def test_a_listing_that_breaks_midway_keeps_jobs_open(box, fake):
    site = Site(SITE_A, [us(n) for n in range(1, 5)])  # two pages
    fake.sites.append(site)
    write_boards(box)
    fetch(box)
    _tag_all(box)
    site.overrides[3] = OOPS
    assert fetch(box)["error"]
    assert len(box.select_today(DAY1)["jobs"]) == 4


def test_a_job_already_known_by_its_url_is_not_stored_again(box, fake):
    fake.sites.append(Site(SITE_A, [us(1), us(2)]))
    url = f"{SITE_A}/JobDetail/Machine-Learning-Engineer/1"
    hand = {"id": "m1", "title": "Machine Learning Engineer", "url": url, "description": "SYNTHETIC: imported by hand"}
    (box.data_dir / "jobs.json").write_text(json.dumps([hand]), encoding="utf-8")
    box.import_jobs(str(box.data_dir / "jobs.json"))
    write_boards(box)
    report = fetch(box)
    assert (report["already_known"], report["new"]) == (1, 1)
    assert fake.detail_ids() == ["2"]
    manual = box.store.job("m1")
    assert (manual.source, manual.description) == ("manual", "SYNTHETIC: imported by hand")
    box.submit_job_tags("m1", ["role.ml_engineering"])
    assert "m1" in {j["job_id"] for j in box.select_today(DAY1)["jobs"]}  # manual imports stay open


def test_imported_jobs_can_carry_a_location(box):
    rows = [
        {"id": "m1", "title": "T", "location": US, "description": "SYNTHETIC"},
        {"id": "m2", "title": "T", "description": "SYNTHETIC"},
    ]
    (box.data_dir / "jobs.json").write_text(json.dumps(rows), encoding="utf-8")
    box.import_jobs(str(box.data_dir / "jobs.json"))
    assert (box.store.job("m1").location, box.store.job("m2").location) == (US, "")


# --- background fetch -----------------------------------------------------------------------------


def test_board_status_is_preparation_available_to_scheduled_runs():
    assert TOOL_TIERS["fetch_boards"] == TOOL_TIERS["boards_status"] == "prepare"
    assert {"fetch_boards", "boards_status"} <= SCHEDULED_TOOLS


def test_boards_status_is_idle_before_any_run(box):
    assert box.boards_status() == {"status": "idle"}


def test_fetch_boards_runs_in_the_background_one_run_at_a_time(box, fake):
    fake.sites.append(Site(SITE_A, [us(1), us(2)]))
    write_boards(box)
    fake.gate = threading.Event()
    assert box.fetch_boards()["status"] == "running"
    with pytest.raises(InvariantError):
        box.fetch_boards()
    fake.gate.set()
    for _ in range(100):
        status = box.boards_status()
        if status["status"] != "running":
            break
        time.sleep(0.05)
    assert status["status"] == "done"
    (report,) = status["boards"]
    assert report["new"] == 2 and len(box.store.jobs()) == 2


def test_a_finished_run_nobody_collected_is_stored_before_the_next_one(box, fake):
    site = Site(SITE_A, [us(1)])
    fake.sites.append(site)
    write_boards(box)
    box.fetch_boards()
    for _ in range(100):  # wait for the background run without collecting it through boards_status
        if box._boards["status"] != "running":
            break
        time.sleep(0.05)
    site.postings = [us(1), us(2)]
    assert fetch(box)["new"] == 1
    assert sorted(posting_ids(box.store.jobs())) == ["1", "2"]
    assert sorted(fake.detail_ids()) == ["1", "2"]  # the first run's detail read was kept, not repeated


def test_an_invalid_boards_file_is_reported_before_starting(box):
    (box.data_dir / "boards.yaml").write_text(yaml.safe_dump({"boards": [{"provider": "avature"}]}), encoding="utf-8")
    with pytest.raises(InvariantError):
        box.fetch_boards()
    assert box.boards_status() == {"status": "idle"}
