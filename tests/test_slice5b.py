"""Slice 5b: one apply-list row per posting found on several boards (spec 4.9 item 6, section 10 items 69-74).
All data is SYNTHETIC (tests/fixtures/duplicates.yaml); the cases are the ones listed in plan.md (slice 5b)."""

import csv
import json
import random
from itertools import permutations

import pytest
import yaml

from intent_job_agent.domain import InvariantError, Job
from intent_job_agent.duplicates import group, requisition, same_posting

from .conftest import ROOT, settings
from .test_slice5 import label, make_box
from .test_slice6 import FakeRunner, letter, write_applicant

FIXTURE = yaml.safe_load((ROOT / "tests/fixtures/duplicates.yaml").read_text(encoding="utf-8"))
assert FIXTURE["synthetic"] is True
LISTINGS = {item["id"]: item for item in FIXTURE["listings"] + FIXTURE["ambiguous"]}
POSTINGS = {frozenset(ids) for ids in FIXTURE["postings"]}
DAY1, DAY2 = "2026-10-14", "2026-10-15"


def text(item: dict) -> str:
    if "raw" in item:  # what a board stored instead of a text
        return item["raw"]
    body = "\n\n".join(FIXTURE["paragraphs"][name] for name in item["text"])
    if "cut" in item:  # a board that shows only the start of the posting
        body = " ".join(body.split()[: item["cut"]])
    if "heading" in item:
        body = f"{item['heading']}\n{body}"
    return body


def job(job_id: str) -> Job:
    item = LISTINGS[job_id]
    fields = ("source", "board", "company", "title", "location", "url")
    return Job(id=job_id, tags=[], description=text(item), **{field: item[field] for field in fields})


def groups_of(jobs: list[Job]) -> set[frozenset[str]]:
    return {frozenset(j.id for j in found) for found in group(jobs, settings())}


# --- what counts as the same posting (cases 1-10) ------------------------------------------------------


def test_which_listings_are_one_posting():
    assert groups_of([job(item["id"]) for item in FIXTURE["listings"]]) == POSTINGS


@pytest.mark.parametrize("seed", range(5))
def test_the_grouping_does_not_depend_on_the_input_order(seed):
    jobs = [job(item["id"]) for item in FIXTURE["listings"]]
    random.Random(seed).shuffle(jobs)
    assert groups_of(jobs) == POSTINGS


def test_groups_keep_the_input_order():
    found = group([job("beta-zip"), job("acme-ny"), job("beta-indeed"), job("acme-sf")], settings())
    assert [[j.id for j in listings] for listings in found] == [["beta-zip", "beta-indeed"], ["acme-ny", "acme-sf"]]


def test_different_requisitions_stay_apart_even_with_the_same_text():
    s = settings()
    r1, r2, linkedin = job("delta-r1"), job("delta-r2"), job("delta-linkedin")
    # Only the company site's numbers keep these two apart: as plain board listings they would be one posting.
    assert same_posting(r1.model_copy(update={"source": "jobspy"}), r2.model_copy(update={"source": "jobspy"}), s)
    assert not same_posting(r1, r2, s)
    assert same_posting(linkedin, r1, s) and same_posting(linkedin, r2, s)
    for order in permutations([r1, r2, linkedin]):  # the board listing joins one of them, never links the two
        found = groups_of(list(order))
        assert len(found) == 2 and not any({"delta-r1", "delta-r2"} <= listings for listings in found)


def test_the_same_requisition_under_two_links_is_one_posting():
    assert same_posting(job("theta-austin"), job("theta-seattle"), settings())


def test_without_text_the_place_decides():
    s = settings()
    assert same_posting(job("eta-1"), job("eta-2"), s)  # no text, same place
    assert not same_posting(job("eta-1"), job("eta-3"), s) and not same_posting(job("eta-2"), job("eta-3"), s)
    assert same_posting(job("acme-ny"), job("acme-glassdoor"), s)
    assert not same_posting(job("acme-sf"), job("acme-glassdoor"), s)
    # it still joins the posting found in three cities, through the listing in its own city
    found = groups_of([job("acme-glassdoor"), job("acme-sf"), job("acme-sea"), job("acme-ny")])
    assert found == {frozenset({"acme-sf", "acme-sea", "acme-ny", "acme-glassdoor"})}


WORKDAY = "https://x.wd5.example/c/job/"
GREENHOUSE = "https://boards.greenhouse.example/acme/jobs/"
ASHBY = "https://jobs.ashbyhq.example/beta/"


@pytest.mark.parametrize(
    ("source", "a", "b", "same"),
    [
        ("workday", WORKDAY + "San-Jose/MLE_R171718", WORKDAY + "San-Jose/MLE_R171719", False),
        ("workday", WORKDAY + "Zurich/Intern_JR2026775", WORKDAY + "Munich/Intern_JR2026775", True),
        ("workday", WORKDAY + "Pune/DFT_JR2024390-1", WORKDAY + "Pune/DFT_JR2024390", True),
        ("workday", WORKDAY + "Pune/DFT_JR2024390-1", WORKDAY + "Pune/DFT_JR2024391", False),
        ("workday", WORKDAY + "OFallon/SSE_R-292353", WORKDAY + "OFallon/SSE-2_R-292354", False),
        ("workday", WORKDAY + "Budapest/SE_2024875-1", WORKDAY + "Budapest/SE_2024875/", True),
        ("greenhouse", GREENHOUSE + "123?gh_src=li", GREENHOUSE + "123", True),
        ("greenhouse", GREENHOUSE + "123", GREENHOUSE + "124", False),
        ("ashby", ASHBY + "42aca7f5", ASHBY + "77b0c1d2", False),
    ],
)
def test_company_site_requisition_numbers(source, a, b, same):
    first, second = (requisition(Job(id=f"j{n}", source=source, url=url, tags=[])) for n, url in enumerate((a, b)))
    assert first and second and (first == second) is same


def test_board_listings_have_no_requisition_number():
    assert requisition(job("acme-sf")) is None and requisition(job("nocomp-1")) is None


def test_thresholds_are_in_the_config():
    apply_list = settings().apply_list
    assert apply_list.duplicate_shingle_words == 5 and apply_list.duplicate_overlap == 0.9


# --- the apply list (cases 1, 11-16) -----------------------------------------------------------------


@pytest.fixture
def box(tmp_path):
    box = make_box(tmp_path)
    box.apply_runner = FakeRunner()
    box.letter_renderer = lambda text, pdf_path, name, day: pdf_path.write_bytes(b"%PDF-1.4 letter")
    box.apply_in_background = False
    return box


def add(box, *job_ids) -> None:
    for job_id in job_ids:
        box.store.add_job(job(job_id))
        places = [f"location.{place}" for place in LISTINGS[job_id]["places"]]
        box.store.set_job_tags(job_id, ["role.ml_engineering", *places])


def rows(result) -> dict[str, dict]:
    """Rows by company (each test uses at most one posting per company, except Delta)."""
    return {item["company"]: item for item in result["jobs"]}


def url(job_id: str) -> str:
    return LISTINGS[job_id]["url"]


def test_one_row_per_posting(box):
    add(box, "acme-sf", "acme-sea", "acme-ny", "beta-indeed", "beta-linkedin", "beta-zip", "delta-r1", "delta-r2")
    result = box.build_apply_list(DAY1)
    assert result["pending"] == 4 and result["new"] == 4
    acme = rows(result)["Acme Pay"]
    assert acme["job_id"] == "acme-sf" and acme["url"] == url("acme-sf")
    assert acme["location"] == "us.bay_area, us.seattle, us.new_york"
    assert acme["also"] == [url("acme-sea"), url("acme-ny")]
    assert acme["why"] == ["role.ml_engineering 强烈偏好"]
    assert [item["job_id"] for item in result["jobs"] if item["company"] == "Delta"] == ["delta-r1", "delta-r2"]
    assert rows(result)["Delta"]["also"] == []
    assert len(box.store.applications()) == 8  # every listing of a listed posting counts as listed


def test_files_carry_the_other_links(box):
    add(box, "acme-sf", "acme-sea")
    box.build_apply_list(DAY1)
    folder = box.data_dir / box.settings.apply_list.dir
    (item,) = json.loads((folder / f"{DAY1}.json").read_text(encoding="utf-8"))["jobs"]
    assert item["also"] == [url("acme-sea")]
    with (folder / f"{DAY1}.csv").open(encoding="utf-8-sig", newline="") as handle:
        (row,) = list(csv.DictReader(handle))
    assert row["also"] == url("acme-sea") and row["location"] == "us.bay_area, us.seattle"


def test_a_reject_on_one_listing_keeps_the_posting_out_unless_another_is_wanted(box):
    add(box, "acme-sf", "acme-sea", "acme-ny")
    label(box, "acme-ny", "reject")
    assert box.build_apply_list(DAY1)["jobs"] == []
    label(box, "acme-sf", "want")
    (item,) = box.build_apply_list(DAY1)["jobs"]
    assert item["job_id"] == "acme-sf" and item["wanted"] is True and item["why"][0] == "你标了想投"
    assert url("acme-ny") in item["also"]


def test_one_handled_listing_takes_the_whole_posting_off_the_list(box):
    add(box, "beta-indeed", "beta-linkedin", "beta-zip", "gamma-linkedin")
    box.build_apply_list(DAY1)
    box.store.record_application_result("beta-indeed", "failed", "captcha")
    assert [item["company"] for item in box.build_apply_list(DAY1)["jobs"]] == ["Gamma Infotech LLC"]

    box.mark_applications(["beta-zip"], "pending")  # one listing back to pending brings the posting back
    applications = box.store.applications()
    assert {applications[j]["status"] for j in ("beta-indeed", "beta-linkedin", "beta-zip")} == {"pending"}
    assert "Beta" in rows(box.build_apply_list(DAY1))

    box.mark_applications(["beta-indeed"], "applied")
    applications = box.store.applications()
    assert {applications[j]["status"] for j in ("beta-indeed", "beta-linkedin", "beta-zip")} == {"applied"}
    assert "Beta" not in rows(box.build_apply_list(DAY1))


def test_marking_reaches_a_listing_found_after_the_list_was_built(box):
    add(box, "acme-sf")
    box.build_apply_list(DAY1)
    add(box, "acme-sea")  # imported later, never listed on its own
    result = box.mark_applications(["acme-sf"], "skipped")
    assert result["job_ids"] == ["acme-sf"] and result["same_posting"] == ["acme-sea"]
    assert box.store.applications()["acme-sea"]["status"] == "skipped"
    assert box.build_apply_list(DAY1)["jobs"] == []


def test_new_is_counted_from_the_posting_first_day(box):
    add(box, "acme-sf")
    box.build_apply_list(DAY1)
    add(box, "acme-sea")
    (item,) = box.build_apply_list(DAY2)["jobs"]
    assert item["new"] is False and item["also"] == [url("acme-sea")]


def test_the_listing_with_a_cover_letter_is_the_one_applied_to(box):
    write_applicant(box.data_dir)
    add(box, "acme-sf", "acme-sea", "acme-ny")
    box.build_apply_list(DAY1)
    assert box.save_cover_letter("acme-sea", "ml", letter())["ok"]
    (item,) = box.build_apply_list(DAY1)["jobs"]
    assert item["job_id"] == "acme-sea" and item["url"] == url("acme-sea")
    assert item["also"] == [url("acme-sf"), url("acme-ny")]
    batch = box.prepare_apply_batch(DAY1)
    assert [j["job_id"] for j in batch["jobs"]] == ["acme-sea"] and batch["missing_letters"] == []


# --- batches (cases 17-19) ----------------------------------------------------------------------------


def test_a_batch_has_each_posting_once(box):
    write_applicant(box.data_dir)
    add(box, *LISTINGS)
    batch = box.prepare_apply_batch(DAY1)
    chosen = [j["job_id"] for j in batch["jobs"]]
    assert len(chosen) == len(POSTINGS)
    for listings in POSTINGS:
        assert len(listings & set(chosen)) == 1, listings
    assert {"delta-r1", "delta-r2"} <= set(chosen)  # two requisitions, two rows


def old_batch(box, batch_id: str, jobs: list[tuple[str, str]]) -> str:
    """A batch prepared before this slice, when the same posting could appear twice. Returns its summary."""
    items = [
        {"n": n, "job_id": j, "title": LISTINGS[j]["title"], "company": LISTINGS[j]["company"], "mode": mode}
        for n, (j, mode) in enumerate(jobs, start=1)
    ]
    phases = {
        "review": "proposed" if any(mode == "review" for _, mode in jobs) else "none",
        "rest": "proposed" if any(mode != "review" for _, mode in jobs) else "none",
    }
    box.store.save_apply_batch(batch_id, DAY1, {"jobs": items, "phases": phases})
    return box._batch_view(batch_id)["summary"]


def write_letters(box, job_ids):
    for job_id in job_ids:
        assert box.save_cover_letter(job_id, "ml", letter())["ok"]


def test_an_old_batch_with_one_posting_twice_in_a_step_cannot_start(box):
    write_applicant(box.data_dir)
    add(box, "acme-sf", "acme-sea", "gamma-linkedin")
    box.build_apply_list(DAY1)
    write_letters(box, ["acme-sf", "acme-sea", "gamma-linkedin"])
    steps = [("acme-sf", "review"), ("gamma-linkedin", "review"), ("acme-sea", "review")]
    summary = old_batch(box, "batch-old", steps)
    with pytest.raises(InvariantError, match="new batch"):
        box.start_applying("batch-old", summary)
    assert box.apply_runner.calls == []


def test_an_old_batch_cannot_go_on_to_a_posting_already_tried_in_step_one(box):
    write_applicant(box.data_dir)
    add(box, "beta-indeed", "beta-zip", "gamma-linkedin")
    box.build_apply_list(DAY1)
    write_letters(box, ["beta-indeed", "beta-zip", "gamma-linkedin"])
    steps = [("beta-indeed", "review"), ("gamma-linkedin", "review"), ("beta-zip", "submit")]
    summary = old_batch(box, "batch-old", steps)
    box.apply_runner = FakeRunner({"beta-indeed": ("failed", "captcha", [])})
    box.start_applying("batch-old", summary)  # the later listing is still pending, so step one may run
    rest = box.apply_status()["next"]["summary"]
    with pytest.raises(InvariantError, match="new batch"):
        box.start_applying("batch-old", rest)
    assert [spec.job_id for spec in box.apply_runner.calls] == ["beta-indeed", "gamma-linkedin"]
    assert box.store.applications()["beta-zip"]["status"] == "pending"
