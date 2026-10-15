"""Slice 5c: the daily picks show a posting once, and the same-posting rule looks at apply links, requisition ids,
cities and countries (spec 4.6, 4.9 item 6, section 10 items 77-85). All data is SYNTHETIC
(tests/fixtures/duplicates.yaml); the cases are the ones listed in plan.md (slice 5c)."""

import json
import os
import random
import subprocess
import sys

import pytest

from intent_job_agent.apply_list import agreement
from intent_job_agent.domain import IntentModel, InvariantError, Job, Label
from intent_job_agent.duplicates import Link, apply_key, group, same_posting
from intent_job_agent.selection import select_daily

from .conftest import BASE_INTENT, ROOT, settings
from .test_slice2f import frontmatter
from .test_slice5 import make_box
from .test_slice5b import FIXTURE, LISTINGS, POSTINGS
from .test_slice5b import job as job_5b
from .test_slice6 import write_applicant

LISTINGS_5C = {item["id"]: item for item in FIXTURE["listings_5c"]}
POSTINGS_5C = {frozenset(ids) for ids in FIXTURE["postings_5c"]}
DAY1, DAY2, DAY3 = "2026-10-14", "2026-10-15", "2026-10-16"


def text(item: dict) -> str:
    if "raw" in item:
        return item["raw"]
    body = "\n\n".join(FIXTURE["paragraphs"][name] for name in item["text"])
    return f"{body}\n\n{item['append']}" if "append" in item else body


def job(job_id: str, **update) -> Job:
    """A 5c listing with its tags and the apply link a board recorded, or a 5b listing with its places as tags."""
    if job_id in LISTINGS:
        return job_5b(job_id).model_copy(update=update)
    item = LISTINGS_5C[job_id]
    attributes = {"application_url": item["application_url"]} if "application_url" in item else {}
    fields = {field: item.get(field, "") for field in ("source", "board", "company", "title", "location", "url")}
    built = Job(id=job_id, tags=item["tags"], description=text(item), attributes=attributes, **fields)
    return built.model_copy(update=update)


def groups_of(jobs: list[Job]) -> set[frozenset[str]]:
    return {frozenset(j.id for j in found) for found in group(jobs, settings())}


# --- what counts as the same posting (cases 20-31) -----------------------------------------------------


def test_which_listings_are_one_posting():
    assert groups_of([job(item_id) for item_id in LISTINGS_5C]) == POSTINGS_5C


@pytest.mark.parametrize("seed", range(5))
def test_the_grouping_does_not_depend_on_the_input_order(seed):
    jobs = [job(item_id) for item_id in LISTINGS_5C]
    random.Random(seed).shuffle(jobs)
    assert groups_of(jobs) == POSTINGS_5C


def test_the_5b_and_5c_cases_do_not_mix():
    jobs = [job(item["id"]) for item in FIXTURE["listings"]] + [job(item_id) for item_id in LISTINGS_5C]
    assert groups_of(jobs) == POSTINGS | POSTINGS_5C


def test_the_same_apply_link_joins_cities_and_countries():
    s = settings()
    # the company site names its city, so the text alone would not join the Seattle board listing
    assert not same_posting(job("iota-lever"), job("iota-indeed", attributes={}), s)
    assert same_posting(job("iota-lever"), job("iota-indeed"), s)
    assert same_posting(job("iota-indeed"), job("iota-zip"), s)  # Seattle and Toronto: one application form


def test_different_ids_of_one_system_stay_apart_whatever_the_text():
    s = settings()
    assert not same_posting(job("kappa-atl"), job("kappa-dal"), s)
    # without their links they are board listings with the same text in two cities: one posting
    assert same_posting(job("kappa-atl", attributes={}), job("kappa-dal", attributes={}), s)


def test_a_board_listing_joins_the_company_site_requisition_in_its_own_city():
    found = groups_of([job(j) for j in ("mu-li-sj", "mu-li-chi", "mu-r1", "mu-r2")])
    assert found == {frozenset({"mu-r1", "mu-li-chi"}), frozenset({"mu-r2", "mu-li-sj"})}
    assert not same_posting(job("mu-li-sj"), job("mu-r1"), settings())


def test_n_locations_counts_as_no_city_and_a_named_city_does_not():
    s = settings()
    assert same_posting(job("nu-wd"), job("nu-li"), s)  # "3 Locations", although tagged with one other city
    assert not same_posting(job("xi-wd"), job("xi-li"), s)  # Chicago on the company site, Seattle on LinkedIn


def test_job_numbers_written_in_the_texts():
    s = settings()
    assert not same_posting(job("omicron-sd"), job("omicron-sea"), s)  # A10570976 vs A10570970
    assert same_posting(job("omicron2-sd"), job("omicron2-sea"), s)  # only one text has a number


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("Req #: 2024875", "Req #: 2024876", False),
        ("Requisition ID 12345", "Requisition ID: 12345", True),
        ("Job Number: R0251266", "Job number R-0251266", True),
        ("Apply for JR2024390 today.", "Apply for JR2024391 today.", False),
        ("REQ-55501 and Job ID: A10570976", "Job ID: A10570976", True),  # one number in common
        ("Req ID: 7012345", "Requisition ID: 7012399", False),
        ("Req # 1234", "Req # 1235", True),  # fewer than 5 digits: not a job number
    ],
)
def test_job_number_patterns(first, second, same):
    """The two texts also differ in a word, so the 'only numbers differ' rule never decides these."""
    base = job("omicron2-sea")  # a board listing; its copy below is in another city
    a = base.model_copy(update={"id": "a", "description": f"{base.description}\n\nApply now. {first}"})
    text_b = f"{base.description}\n\nApply today. {second}"
    b = base.model_copy(update={"id": "b", "tags": ["location.us.austin"], "description": text_b})
    assert same_posting(a, b, settings()) is same


@pytest.mark.parametrize(
    ("first", "second"),
    [("10/11/2026", "10/25/2026"), ("2026-10-11", "2026-10-25"), ("Oct 11, 2026", "Oct 25, 2026")],
)
def test_dates_do_not_count(first, second):
    base = job("pi2-sj")
    short = " ".join(FIXTURE["paragraphs"]["role_pi2"].split()[:40])  # a short text, where dates weigh more
    a = base.model_copy(update={"description": f"{short} Posted {first}. Apply by {first}. Start {first}."})
    b = a.model_copy(
        update={"id": "b", "tags": ["location.us.seattle"], "description": a.description.replace(first, second)}
    )
    assert same_posting(a, b, settings())


def test_texts_that_differ_only_in_numbers_are_two_postings_but_dates_do_not_count():
    s = settings()
    assert not same_posting(job("pi-sj"), job("pi-sea"), s)  # each city's own pay range
    assert same_posting(job("pi2-sj"), job("pi2-sea"), s)  # only the closing date differs


def test_different_countries_do_not_join_by_text_but_a_link_still_joins_them():
    s = settings()
    assert not same_posting(job("rho-us"), job("rho-ca"), s)
    assert same_posting(job("sigma-us"), job("sigma-ca"), s)  # one Workday requisition in two countries
    # the LinkedIn copy in Boulder is not the Vancouver listing's, but it still joins the requisition's group
    assert not same_posting(job("sigma-li"), job("sigma-ca"), s)
    assert groups_of([job("sigma-li"), job("sigma-ca"), job("sigma-us")]) == {
        frozenset({"sigma-us", "sigma-ca", "sigma-li"})
    }


def test_a_manual_company_page_with_an_id_counts_as_a_company_site():
    s = settings()
    assert not same_posting(job("tau-sj"), job("tau-kir"), s)  # two ids on the company's own page
    assert not same_posting(job("tau-li"), job("tau-sj"), s) and not same_posting(job("tau-li"), job("tau-kir"), s)
    # the same pages without a recognizable id would be board-like listings with the same text
    plain = {"url": "https://jobs.tau.example/careers"}
    assert same_posting(job("tau-li"), job("tau-sj", **plain), s)


def test_an_empty_company_uses_the_one_company_tag():
    s = settings()
    assert same_posting(job("ups-1"), job("ups-2"), s)
    assert not same_posting(job("ups-1"), job("ups-3"), s)  # no company and no tag
    assert not same_posting(job("ups-1"), job("ups-4"), s)  # two company tags: neither is used
    assert same_posting(job("phi-talent"), job("phi-li"), s)  # tag phi_pay = name "Phi Pay"; no text, same city


def test_short_links_count_only_when_equal():
    s = settings()
    assert same_posting(job("chi-a"), job("chi-b"), s)  # different grnh.se codes say nothing; same text, same city
    assert same_posting(job("chi-c"), job("chi-d"), s)  # the same grnh.se code, although the texts differ
    assert not same_posting(job("chi-c", attributes={}), job("chi-d", attributes={}), s)


def test_cities_and_metro_areas():
    s = settings()
    sf, sj = job("acme-sf"), job("acme-sf", id="acme-sj", location="San Jose, CA", source="workday", board="Acme")
    sj = sj.model_copy(update={"url": "https://acme.wd5.myworkdayjobs.example/c/job/San-Jose/MLE_R1"})
    assert same_posting(sf, sj, s)  # us.bay_area covers both
    seattle = sj.model_copy(update={"id": "acme-sea-wd", "tags": ["location.us.seattle"]})
    assert not same_posting(sf, seattle, s)
    no_city = sj.model_copy(update={"id": "acme-us-wd", "tags": ["location.us"], "location": "United States"})
    assert same_posting(sf, no_city, s)


# --- apply links (section 10 item 77) ------------------------------------------------------------------

LEVER = "2347453a-f11f-4dc2-ad14-572bc1f6247f"
ASHBY = "42aca7f5-2f58-40ef-a6ce-19027d32a12f"


@pytest.mark.parametrize(
    ("link", "key"),
    [
        (f"https://jobs.lever.co/zoo/{LEVER}?lever-source=Indeed", Link("lever", LEVER)),
        (f"https://jobs.lever.co/zoo/{LEVER.upper()}/apply", Link("lever", LEVER)),
        (
            "https://jsv3.recruitics.com/redirect?rx_cid=1&rx_jobId=a_1001&rx_url=https%3A%2F%2Fwww.metacareers.com"
            "%2Fjobs%2F1401447865502287%2F%3Futm_source%3Dindeed",
            Link("meta", "1401447865502287"),
        ),
        (f"https://jobs.ashbyhq.com/handshake/{ASHBY}/application?utm_source=x", Link("ashby", ASHBY)),
        (f"https://www.handshake.example/careers?ashby_jid={ASHBY}", Link("ashby", ASHBY)),
        ("https://boards.greenhouse.io/acme/jobs/4743066005?gh_src=li", Link("greenhouse", "4743066005")),
        ("https://job-boards.greenhouse.io/acme/jobs/4743066005", Link("greenhouse", "4743066005")),
        ("https://www.acme.example/open-positions/?gh_jid=4743066005&src=zip", Link("greenhouse", "4743066005")),
        (
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/"
            "Machine-Learning-Engineer--AI-Safety_JR2021784-1/apply?utm_source=ziprecruiter",
            Link("workday", "nvidia/jr2021784"),
        ),
        (
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/"
            "Machine-Learning-Engineer--AI-Safety_JR2021784?source=Eightfold",
            Link("workday", "nvidia/jr2021784"),
        ),
        (
            "https://wd1.myworkdaysite.com/recruiting/paypal/jobs/job/Chicago-Illinois/Data-Scientist_R0138042",
            Link("workday", "paypal/r0138042"),
        ),
        (
            "https://jobs.smartrecruiters.com/KappaLearning/744000155325258-analytics-engineer?trid=x",
            Link("smartrecruiters", "744000155325258"),
        ),
        ("https://recruiting.paylocity.com/Recruiting/Jobs/Details/4571749", Link("paylocity", "4571749")),
        (
            "https://careers-acme.icims.com/jobs/5358/data-scientist/job?mode=job&iis=Indeed",
            Link("icims", "5358"),
        ),
        (
            "https://jobs.lenovo.com/en_US/careers/JobDetail/Backend-Engineer/81634",
            Link("avature", "81634"),
        ),
        (
            "https://www.google.com/about/careers/applications/jobs/results/86191709230113478-software-engineer",
            Link("google", "86191709230113478"),
        ),
        ("https://careers.google.com/jobs/results/86191709230113478/", Link("google", "86191709230113478")),
        ("https://www.amazon.jobs/en/jobs/2849372/applied-scientist", Link("amazon", "2849372")),
        ("https://jobs.apple.com/en-us/details/200573215-0836/ml-engineer", Link("apple", "200573215-0836")),
        ("https://jobs.careers.microsoft.com/global/en/job/1789012/ML-Engineer", Link("microsoft", "1789012")),
        (
            "https://careers.example.com/en-us/jobs/data-scientist-1234/apply?utm_source=indeed&src=x",
            Link("", "careers.example.com/jobs/data-scientist-1234"),
        ),
        ("https://Careers.Example.com/jobs/view?jobId=55&ref=abc", Link("", "careers.example.com/jobs/view?jobid=55")),
        ("https://grnh.se/4dzb5wth1us", Link("", "grnh.se/4dzb5wth1us")),
        (  # one Avature instance under two hosts
            "https://deloitteus.avature.net/careers/InviteToApply?jobId=370285",
            Link("avature", "370285"),
        ),
        ("https://apply.deloitte.com/en_US/careers/JobDetail/Senior-Consultant/370285", Link("avature", "370285")),
        ("https://boards.greenhouse.io/embed/job_app?for=acme&token=4743066005", Link("greenhouse", "4743066005")),
        (  # Workday's own apply steps after the requisition
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Seattle/ML-Engineer_R100/apply/applyManually",
            Link("workday", "acme/r100"),
        ),
        (
            "https://jsv3.recruitics.com/redirect?rx_url=HTTPS%3A%2F%2Fjobs.lever.co%2Fzoo%2F" + LEVER,
            Link("lever", LEVER),
        ),
        (  # a hash route names the job
            "https://pm.healthcaresource.com/CS/alamedahighland/#/job/25195?source=Indeed",
            Link("", "pm.healthcaresource.com/cs/alamedahighland/job/25195"),
        ),
        (
            "https://careers.kearney.example/jobs/5358/job?indeed-apply-token=abc123&mode=job",
            Link("", "careers.kearney.example/jobs/5358/job"),
        ),
        # not links: junk, trackers without a target, the boards' own pages, career home and list pages
        ("None", None),
        ("null", None),
        ("", None),
        ("/redirect?id=123&action=quickapply", None),
        ("https://click.appcast.io/track/abc123?cs=ziprecruiter", None),
        ("https://dsp.prng.co/fe27IWb", None),
        ("https://www.indeed.com/viewjob?jk=c3e3f04065b70e9e", None),
        (
            "https://www.linkedin.com/signup/cold-join?session_redirect=https%3A%2F%2Fwww.linkedin.com%2Fjobs%2Fview"
            "%2F4476982356",
            None,
        ),
        ("https://www.talent.com/view?id=abc", None),
        ("https://www.example.com/careers/", None),
        ("https://jobs.example.com/en/search?q=ml", None),
        ("https://example.com", None),
        ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", None),
        ("https://acme.wd5.myworkdayjobs.com/en-US/External?q=ml", None),
        ("https://wd1.myworkdaysite.com/recruiting/acme/jobs", None),
        ("https://jobs.ashbyhq.com/acme/jobs", None),
        ("https://jobs.lever.co/acme", None),
        ("https://boards.greenhouse.io/acme", None),
        ("https://www.google.com/about/careers/applications/jobs/results/", None),
    ],
)
def test_apply_links(link, key):
    listing = Job(id="j", source="jobspy", board="indeed", attributes={"application_url": link}, tags=[])
    assert apply_key(listing) == key


@pytest.mark.parametrize(
    ("source", "url", "key"),
    [  # company-site listings are read in their source's format, whatever the host
        ("workday", "https://x.wd5.example/c/job/Budapest/SE_2026-1225", Link("workday", "x/2026-1225")),
        ("workday", "https://x.wd5.example/c/job/Pune/DFT_JR2024390-12", Link("workday", "x/jr2024390")),
        ("workday", "https://x.wd5.example/c/job/OFallon/SSE_R-292353", Link("workday", "x/r-292353")),
        ("lever", "https://jobs.lever.example/iota/abc-123/apply", Link("lever", "abc-123")),
        ("greenhouse", "https://boards.greenhouse.example/acme/jobs/123?gh_src=li", Link("greenhouse", "123")),
        ("ashby", "https://jobs.ashbyhq.example/beta/42aca7f5", Link("ashby", "42aca7f5")),
        (
            "avature",
            "https://careers.acme.example/en_US/jobs/JobDetail/Data-Engineer/7001",
            Link("avature", "7001"),
        ),
    ],
)
def test_company_site_links_by_source(source, url, key):
    assert apply_key(Job(id="j", source=source, url=url, tags=[])) == key


def test_a_company_site_link_without_the_source_id_is_compared_like_any_other_link():
    odd = Job(id="g", source="greenhouse", url="https://www.acme.example/careers/positions/12345", tags=[])
    assert apply_key(odd) == Link("", "acme.example/careers/positions/12345")  # never a 'different id' veto
    home = Job(id="w", source="workday", url="https://acme.wd5.myworkdayjobs.com/en-US/External", tags=[])
    assert apply_key(home) is None


def test_boards_use_the_recorded_apply_link_and_manual_imports_their_own():
    assert apply_key(job("acme-sf")) is None  # LinkedIn: no link recorded
    manual = Job(id="m", source="manual", url="https://jobs.lever.co/zoo/" + LEVER, tags=[])
    assert apply_key(manual) == Link("lever", LEVER)


def test_thresholds_moved_to_their_own_config_section():
    duplicates = settings().duplicates
    assert duplicates.shingle_words == 5 and duplicates.overlap == 0.9
    assert not hasattr(settings().apply_list, "duplicate_overlap")


# --- the daily picks (cases 32-34, 38) -----------------------------------------------------------------

INTENT = IntentModel.build({**BASE_INTENT, "location": {"us": {"level": "require"}}})
TOP = ["role.ml_engineering", "specialty.ai_infra", "tech_stack.python", "company_type.startup_growth"]  # 6 points


def scored(job_id: str, tags: list[str]) -> Job:
    """A listing from the fixture with tags that set its rank (and its places from the fixture)."""
    found = job(job_id)
    places = [tag for tag in found.tags if tag.startswith("location.")]
    return found.model_copy(update={"tags": [*tags, *places]})


def fillers(n: int, points=("role.ml_engineering",)) -> list[Job]:
    """Distinct postings: unique companies and titles, no text."""
    return [
        Job(id=f"f{i:02d}", company=f"Filler {i}", title=f"Role {i}", tags=[*points, "location.us.austin"])
        for i in range(n)
    ]


def picks(candidates, shown=(), closed=()) -> list[str]:
    return [job_id for job_id, _ in select_daily(INTENT, candidates, settings(), shown=shown, closed=closed)]


def test_a_posting_appears_once_a_day():
    acme = [
        scored("acme-sf", TOP),  # 6 points, ranks first
        scored("acme-sea", TOP[:3]),  # 5
        scored("acme-ny", ["role.ml_engineering"]),  # 2
    ]
    rest = fillers(4, TOP[:3]) + fillers(12)[4:]
    chosen = picks(acme + rest)
    assert "acme-sf" in chosen and "acme-sea" not in chosen and "acme-ny" not in chosen
    assert chosen == picks([acme[0], *rest])  # exactly as if the other two listings were not there
    assert len(chosen) == 10


def test_a_posting_shown_before_never_comes_back_even_if_that_listing_is_gone():
    shown = [scored("acme-ny", ["role.ml_engineering"])]  # shown yesterday, closed now: passed as shown anyway
    candidates = [scored("acme-sf", TOP), scored("acme-sea", TOP), *fillers(12)]
    chosen = picks(candidates, shown=shown)
    assert not {"acme-sf", "acme-sea"} & set(chosen) and len(chosen) == 10


def test_a_hard_excluded_or_closed_listing_still_links_a_posting_to_its_shown_listing():
    shown = [scored("acme-sf", TOP)]
    bridge = scored("acme-ny", ["role.ml_engineering", "seniority.senior"])  # hard-excluded: never picked
    glassdoor = scored("acme-glassdoor", TOP)  # no text, New York: only the New York listing links it to Acme
    assert "acme-glassdoor" in picks([glassdoor, *fillers(12)])
    assert "acme-glassdoor" not in picks([bridge, glassdoor, *fillers(12)], shown=shown)
    closed = bridge.model_copy(update={"tags": ["role.ml_engineering", "location.us.new_york"]})
    assert "acme-glassdoor" not in picks([closed, glassdoor, *fillers(12)], shown=shown, closed={closed.id})


def test_score_ties_keep_the_storage_order_also_for_closed_listings():
    """R100001 is closed and stored first; the LinkedIn listing looks like both requisitions (5b case 5)."""
    r1, r2, linkedin = (scored(j, ["role.ml_engineering"]) for j in ("delta-r1", "delta-r2", "delta-linkedin"))
    # in storage order the LinkedIn listing joins the closed R100001 and stands for it; R100002 is its own posting
    assert picks([r1, r2, linkedin], closed={"delta-r1"}) == ["delta-r2", "delta-linkedin"]


def test_exploration_does_not_take_another_listing_of_a_recommended_posting():
    beta = [scored("beta-indeed", TOP), scored("beta-zip", ["role.ml_engineering", "company_type.big_tech"])]
    alone = select_daily(INTENT, [beta[1], *fillers(12)], settings())
    assert ("beta-zip", "exploration") in alone  # on its own it would be explored (it hits an avoided entry)
    chosen = select_daily(INTENT, beta + fillers(12), settings())
    assert ("beta-indeed", "recommended") in chosen and "beta-zip" not in [job_id for job_id, _ in chosen]


def test_the_picks_do_not_depend_on_hash_seeds():
    script = (
        "from tests.test_slice5c import scored, fillers, picks, TOP\n"
        "ids = ['acme-sf', 'acme-sea', 'beta-indeed', 'beta-zip', 'delta-r1', 'delta-r2', 'delta-linkedin',"
        " 'mu-r1', 'mu-r2', 'mu-li-sj', 'mu-li-chi']\n"
        "print(picks([scored(j, TOP) for j in ids] + fillers(6)))\n"
    )
    runs = set()
    for seed in ("1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        run = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        runs.add(run.stdout)
    assert len(runs) == 1


# --- the tools (cases 33, 35-37, 39, 40) ---------------------------------------------------------------


@pytest.fixture
def box(tmp_path):
    return make_box(tmp_path)


def add(box, job_id: str, points=("role.ml_engineering",), **update) -> Job:
    listing = job(job_id, **update)
    box.store.add_job(listing.model_copy(update={"tags": []}))
    box.store.set_job_tags(listing.id, [*points, *listing.tags])
    return listing


def copy(box, job_id: str, new_id: str, **update) -> Job:
    """Another board's listing of the same posting."""
    return add(box, job_id, id=new_id, url=f"https://www.board.example/{new_id}", **update)


def label(box, job_id, value, day, reasons=()) -> None:
    box.store.add_label(
        Label(id=f"label-{job_id}", job_id=job_id, day=day, slot="recommended", value=value, reason_keys=list(reasons))
    )


def today(box, day) -> list[str]:
    return [item["job_id"] for item in box.select_today(day)["jobs"]]


def test_select_today_skips_postings_shown_on_earlier_days(box):
    for job_id in ("acme-sf", "acme-sea", "acme-ny", "beta-indeed", "beta-zip", "gamma-zip"):
        add(box, job_id)
    box.store.save_selection(DAY1, [("acme-ny", "recommended")])
    chosen = today(box, DAY2)
    assert not {"acme-sf", "acme-sea", "acme-ny"} & set(chosen)
    assert len({"beta-indeed", "beta-zip"} & set(chosen)) == 1 and "gamma-zip" in chosen


def test_select_today_ranks_by_corrected_tags(box):
    add(box, "beta-indeed", points=["role.data_science"])
    add(box, "beta-zip", points=["role.data_science"])
    box.store.set_effective_tags("beta-zip", ["role.ml_engineering", *TOP[1:], "location.us.bay_area"], None)
    assert today(box, DAY2) == ["beta-zip"]  # the corrected listing ranks first, so it represents Beta


def test_a_stored_selection_is_not_recomputed(box):
    add(box, "acme-sf")
    add(box, "acme-sea")
    box.store.save_selection(DAY2, [("acme-sf", "recommended"), ("acme-sea", "recommended")])  # chosen before 5c
    assert today(box, DAY2) == ["acme-sf", "acme-sea"]


def test_fetch_status_counts_postings_that_can_still_be_picked(box):
    for job_id in ("acme-sf", "acme-sea", "acme-ny", "beta-indeed", "beta-zip", "delta-r1", "delta-r2", "gamma-zip"):
        add(box, job_id)
    add(box, "zeta-1", points=["role.ml_engineering", "seniority.senior"])  # hard-excluded
    assert box.get_fetch_status(DAY2)["tagged_open"] == 5  # Acme, Beta, Delta R100001, Delta R100002, Gamma
    box.store.save_selection(DAY1, [("acme-ny", "recommended")])
    assert box.get_fetch_status(DAY2)["tagged_open"] == 4


def test_a_label_counts_only_for_the_listing_it_is_on(box):
    for job_id in ("acme-sf", "acme-sea", "acme-ny", "beta-indeed", "beta-zip"):
        add(box, job_id)
    label(box, "acme-sf", "want", DAY1)  # shown after 5c: the other two listings are never shown
    label(box, "beta-indeed", "want", DAY1)  # shown before 5c, both listings labelled
    label(box, "beta-zip", "want", DAY1)
    rate = agreement(box.store.current_intent(), box.store, settings(), 7)
    assert (rate.want_agree, rate.want_total) == (3, 3)  # Acme once, Beta's two old labels each once
    assert {item["company"]: item["wanted"] for item in box.build_apply_list(DAY2)["jobs"]} == {
        "Acme Pay": True,
        "Beta": True,
    }


def test_a_reject_for_the_place_only_blocks_that_city(box):
    add(box, "acme-sf")
    add(box, "acme-sea")
    copy(box, "acme-sea", "acme-sea-indeed", board="indeed")
    add(box, "acme-ny")
    label(box, "acme-sea", "reject", DAY1, reasons=["location.us.seattle"])
    (acme,) = box.build_apply_list(DAY2)["jobs"]
    assert acme["job_id"] == "acme-sf" and acme["location"] == "us.bay_area, us.new_york"
    assert acme["also"] == [LISTINGS["acme-ny"]["url"]]  # both Seattle listings are held back
    label(box, "acme-ny", "want", DAY2)  # a want still wins: nothing is held back any more
    (acme,) = box.build_apply_list(DAY2)["jobs"]
    assert acme["wanted"] and "https://www.board.example/acme-sea-indeed" in acme["also"]


@pytest.mark.parametrize("reasons", [["location.us.bay_area", "work_mode.onsite"], []])
def test_a_reject_for_more_than_the_place_blocks_every_city(box, reasons):
    add(box, "pi2-sj")
    add(box, "pi2-sea")  # the same posting in Seattle
    label(box, "pi2-sj", "reject", DAY1, reasons=reasons)
    assert box.build_apply_list(DAY2)["jobs"] == []


def test_a_place_only_reject_does_not_hold_back_a_listing_in_several_cities(box):
    add(box, "nu-li")  # LinkedIn Seattle
    add(box, "nu-wd", tags=["location.us.seattle"])  # Workday "3 Locations", tagged with Seattle among them
    label(box, "nu-li", "reject", DAY1, reasons=["location.us.seattle"])
    assert [item["job_id"] for item in box.build_apply_list(DAY2)["jobs"]] == ["nu-wd"]


def test_the_apply_list_follows_the_new_rule(box):
    for job_id in ("kappa-atl", "kappa-dal", "kappa-chi", "mu-r1", "mu-r2", "mu-li-sj", "mu-li-chi"):
        add(box, job_id)
    add(box, "phi-talent")
    add(box, "phi-li")
    label(box, "phi-talent", "reject", DAY1)  # the Talent.com copy, with no company name
    rows = box.build_apply_list(DAY2)["jobs"]
    assert sorted((item["company"], item["job_id"]) for item in rows) == [
        ("Kappa Learning", "kappa-atl"),
        ("Kappa Learning", "kappa-chi"),
        ("Kappa Learning", "kappa-dal"),
        ("Mu Payments", "mu-r1"),
        ("Mu Payments", "mu-r2"),
    ]
    also = {item["job_id"]: item["also"] for item in rows}
    assert also["mu-r1"] == [LISTINGS_5C["mu-li-chi"]["url"]] and also["mu-r2"] == [LISTINGS_5C["mu-li-sj"]["url"]]


def test_the_apply_list_groups_by_corrected_tags(box):
    add(box, "rho-us")
    add(box, "rho-ca")  # Toronto: kept out by the intent, and another country
    assert [item["job_id"] for item in box.build_apply_list(DAY2)["jobs"]] == ["rho-us"]
    box.store.set_effective_tags("rho-ca", ["role.ml_engineering", "location.us.boston"], None)  # really Boston
    (row,) = box.build_apply_list(DAY2)["jobs"]
    assert row["job_id"] == "rho-us" and row["also"] == [LISTINGS_5C["rho-ca"]["url"]]


def test_a_row_merged_by_company_tag_names_its_company_and_the_careful_list_sees_it(box):
    write_applicant(box.data_dir, careful=("Phi Pay", "Upsilon Retail"))
    apply = box.settings.apply.model_copy(update={"review_first": 0})
    box.settings = box.settings.model_copy(update={"apply": apply})
    add(box, "phi-talent", points=TOP)  # no company name; ranks first, so its row applies with it
    add(box, "phi-li")
    add(box, "ups-1")
    rows = {item["job_id"]: item for item in box.build_apply_list(DAY2)["jobs"]}
    assert rows["phi-talent"]["company"] == "Phi Pay" and rows["ups-1"]["company"] == "upsilon retail"
    modes = {job["job_id"]: job["mode"] for job in box.prepare_apply_batch(DAY2)["jobs"]}
    assert modes == {"phi-talent": "careful", "ups-1": "careful"}


# --- a misread fix reaches the posting's other listings (case 36) -------------------------------------


@pytest.fixture
def misread(box):
    """Acme in Seattle shown today and labelled misread; another board's Seattle copy (tagged); New York shown
    before 5c and labelled want; San Francisco not tagged yet."""
    add(box, "acme-sea", points=["role.ml_engineering", "work_mode.hybrid"])
    copy(box, "acme-sea", "acme-sea-indeed", board="indeed", points=["role.ml_engineering", "work_mode.hybrid"])
    add(box, "acme-ny", points=["role.ml_engineering", "work_mode.hybrid"])
    box.store.add_job(job("acme-sf").model_copy(update={"tags": []}))
    box.store.save_selection(DAY1, [("acme-ny", "recommended")])
    label(box, "acme-ny", "want", DAY1)
    box.store.save_selection(DAY2, [("acme-sea", "recommended")])
    label(box, "acme-sea", "misread", DAY2)
    return box


FIX = {"remove": ["work_mode.hybrid", "location.us.seattle"], "add": ["work_mode.onsite", "location.us.portland"]}


def test_show_job_lists_the_listings_a_fix_would_also_change(misread):
    view = misread.show_job("acme-sea")
    assert {item["job_id"] for item in view["same_posting"]} == {"acme-sea-indeed", "acme-ny"}
    assert all({"job_id", "board", "location"} <= set(item) for item in view["same_posting"])


def test_the_fix_must_name_the_other_listings(misread):
    with pytest.raises(InvariantError, match="acme-ny"):
        misread.correct_tags("acme-sea", **FIX)
    with pytest.raises(InvariantError):
        misread.correct_tags("acme-sea", **FIX, also=["acme-sea-indeed"])
    with pytest.raises(InvariantError):
        misread.correct_tags("acme-sea", **FIX, also=["acme-sea-indeed", "acme-ny", "acme-sf"])
    assert misread.store.effective_tags("acme-sea") == [
        "role.ml_engineering",
        "work_mode.hybrid",
        "location.us.seattle",
    ]


def test_a_misread_fix_reaches_the_other_tagged_listings_except_their_place(misread):
    revision = misread.store.tag_revision()
    result = misread.correct_tags("acme-sea", **FIX, also=["acme-ny", "acme-sea-indeed"])
    assert result["tags"] == ["role.ml_engineering", "work_mode.onsite", "location.us.portland"]
    assert sorted(result["also"]) == ["acme-ny", "acme-sea-indeed"]
    assert misread.store.effective_tags("acme-sea-indeed") == [
        "role.ml_engineering",
        "location.us.seattle",
        "work_mode.onsite",
    ]
    assert misread.store.effective_tags("acme-ny") == [
        "role.ml_engineering",
        "location.us.new_york",
        "work_mode.onsite",
    ]
    assert not misread.store.is_tagged("acme-sf") and misread.store.effective_tags("acme-sf") == []
    # New York was labelled want, so its new tags change replay: open analyses must refresh
    assert misread.store.tag_revision() == revision + 1
    fixes = [json.loads(d["payload"]) for d in misread.store.decisions() if d["kind"] == "tag_fix"]
    assert {fix["job_id"]: fix.get("from_job") for fix in fixes} == {
        "acme-sea": None,
        "acme-sea-indeed": "acme-sea",
        "acme-ny": "acme-sea",
    }


def test_a_fix_reaching_only_unlabelled_listings_leaves_analyses_alone(misread):
    add(misread, "beta-indeed", points=["role.ml_engineering", "work_mode.hybrid"])
    add(misread, "beta-zip", points=["role.ml_engineering", "work_mode.hybrid"])
    revision = misread.store.tag_revision()
    result = misread.correct_tags(
        "beta-indeed", remove=["work_mode.hybrid"], add=["work_mode.remote"], also=["beta-zip"]
    )
    assert result["also"] == ["beta-zip"] and "work_mode.remote" in misread.store.effective_tags("beta-zip")
    assert misread.store.tag_revision() == revision


def test_an_ambiguous_listing_follows_the_requisition_being_fixed(box):
    for job_id in ("delta-r1", "delta-r2", "delta-linkedin"):
        add(box, job_id)
    assert [j["job_id"] for j in box.show_job("delta-r2")["same_posting"]] == ["delta-linkedin"]
    assert [j["job_id"] for j in box.show_job("delta-r1")["same_posting"]] == ["delta-linkedin"]


def test_a_listing_a_fix_reached_cannot_be_tagged_again(misread):
    misread.correct_tags("acme-sea", **FIX, also=["acme-ny", "acme-sea-indeed"])
    with pytest.raises(InvariantError, match="misread"):
        misread.submit_job_tags("acme-sea-indeed", ["role.data_science", "location.us.seattle"])


def test_the_skills_name_the_other_listings_before_a_fix():
    for skill in ("today", "apply-list"):
        _, text = frontmatter(ROOT / ".claude/skills" / skill / "SKILL.md")
        assert "same_posting" in text and "correct_tags(job_id, remove, add, also)" in text, skill
