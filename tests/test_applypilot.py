"""Slice 2c: ApplyPilot's discovery and enrichment, vendored. All data here is SYNTHETIC and offline.

JobSpy and Workday network calls are replaced with fakes; enrichment runs the real Playwright
cascade against a page served on the loopback interface.
"""

import http.server
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pandas as pd
import pytest
import yaml

from intent_job_agent import applypilot_bridge as bridge
from intent_job_agent.domain import Job, Salary
from intent_job_agent.store import Store
from intent_job_agent.tools import SCHEDULED_TOOLS, TOOL_TIERS, Toolbox
from intent_job_agent.vendor.applypilot.discovery import jobspy as ap_jobspy
from intent_job_agent.vendor.applypilot.discovery import smartextract as ap_smart
from intent_job_agent.vendor.applypilot.discovery import workday as ap_workday
from intent_job_agent.vendor.applypilot.enrichment import detail as ap_detail

from .conftest import BASE_INTENT, BASE_VOCAB, settings

LONG = "SYNTHETIC posting. " + "Build and ship machine learning systems with a small team. " * 8
LLM_KEYS = ("GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_URL")


def row(n: int, **overrides) -> dict:
    base = {
        "site": "indeed",
        "job_url": f"https://www.indeed.com/viewjob?jk=synthetic{n}",
        "job_url_direct": f"https://careers.example.com/jobs/{n}",
        "title": "Machine Learning Engineer",
        "company": "Acme",
        "location": "San Francisco, CA",
        "is_remote": False,
        "description": LONG,
        "min_amount": 150000.0,
        "max_amount": 200000.0,
        "interval": "yearly",
        "currency": "USD",
    }
    return {**base, **overrides}


class FakeJobSpy:
    """Stands in for jobspy.scrape_jobs: returns SYNTHETIC rows per search term and records calls."""

    def __init__(self, rows_by_query: dict[str, list[dict]]):
        self.rows_by_query = rows_by_query
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return pd.DataFrame(self.rows_by_query.get(kwargs["search_term"], []))


@pytest.fixture(autouse=True)
def no_llm_keys(monkeypatch):
    for key in LLM_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("INTENT_AGENT_PROXY", raising=False)


@pytest.fixture
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": BASE_INTENT, "vocabulary": BASE_VOCAB}, allow_unicode=True), encoding="utf-8"
    )
    box.initialize_intent(str(data / "intent.yaml"))
    return box


def searches(box, **cfg) -> None:
    cfg.setdefault("queries", [{"query": "machine learning engineer", "tier": 1}])
    cfg.setdefault("locations", [{"location": "San Francisco, CA", "remote": False}])
    (box.data_dir / "searches.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def discover(box, sources=("jobspy",), enrich=False):
    result = box.discover_jobs(sources=list(sources), enrich=enrich, wait=True)
    assert result["status"] == "done", result
    return result


# --- JobSpy -----------------------------------------------------------------------------------


def test_jobspy_results_become_untagged_jobs(box, monkeypatch):
    fake = FakeJobSpy({"machine learning engineer": [row(1)]})
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", fake)
    searches(box)
    result = discover(box)
    assert result["sources"]["jobspy"]["error"] is None
    assert result["synced"]["new"] == 1
    (job,) = box.store.jobs()
    assert (job.source, job.board, job.company, job.title) == ("jobspy", "indeed", "Acme", "Machine Learning Engineer")
    assert job.location == "San Francisco, CA"
    assert job.salary == Salary(currency="USD", period="year", min=150000, max=200000)
    assert job.description.startswith("SYNTHETIC posting.")
    assert job.attributes["application_url"] == "https://careers.example.com/jobs/1"
    assert [j["job_id"] for j in box.list_untagged_jobs()["jobs"]] == [job.id]
    # ApplyPilot's call shape is preserved.
    call = fake.calls[0]
    assert call["search_term"] == "machine learning engineer" and call["location"] == "San Francisco, CA"
    assert call["hours_old"] == 72 and call["description_format"] == "markdown"


def test_documented_example_keys_and_title_exclusions_apply(box, monkeypatch):
    fake = FakeJobSpy(
        {
            "machine learning engineer": [
                row(1),
                row(2, location="Bangalore, India"),
                row(3, title="Machine Learning Intern"),
                row(4, location="Remote", is_remote=True),
            ]
        }
    )
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", fake)
    searches(
        box,
        boards=["indeed", "zip_recruiter"],
        location={"accept_patterns": ["California", "CA"], "reject_patterns": ["India"]},
        exclude_titles=["intern"],
    )
    result = discover(box)
    assert sorted(j.url.rsplit("synthetic", 1)[1] for j in box.store.jobs()) == ["1", "4"]
    assert result["synced"]["excluded_titles"] == 1
    assert fake.calls[0]["site_name"] == ["indeed", "zip_recruiter"]


def test_without_location_patterns_every_location_is_kept(box, monkeypatch):
    monkeypatch.setattr(
        ap_jobspy, "scrape_jobs", FakeJobSpy({"machine learning engineer": [row(1, location="Austin, TX")]})
    )
    searches(box)
    discover(box)
    assert len(box.store.jobs()) == 1


def test_the_same_posting_is_stored_once(box, monkeypatch):
    rows = {"machine learning engineer": [row(1)], "ml engineer": [row(1)]}
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", FakeJobSpy(rows))
    searches(box, queries=[{"query": "machine learning engineer", "tier": 1}, {"query": "ml engineer", "tier": 1}])
    discover(box)
    assert discover(box)["synced"]["new"] == 0
    assert len(box.store.jobs()) == 1


def test_proxy_from_the_environment_reaches_jobspy(box, monkeypatch):
    fake = FakeJobSpy({"machine learning engineer": [row(1)]})
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", fake)
    monkeypatch.setenv("INTENT_AGENT_PROXY", "10.0.0.1:8080")
    searches(box)
    discover(box)
    assert fake.calls[0]["proxies"] == ["10.0.0.1:8080"]


def test_search_jobs_are_always_open(box, monkeypatch):
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", FakeJobSpy({"machine learning engineer": [row(1)]}))
    searches(box)
    discover(box)
    (job,) = box.store.jobs()
    box.submit_job_tags(job.id, ["role.ml_engineering"])
    assert [j["job_id"] for j in box.select_today("2026-10-09")["jobs"]] == [job.id]


def test_untagged_queue_lists_jobs_with_full_text_first(box, monkeypatch):
    rows = [row(1, description="short"), row(2)]
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", FakeJobSpy({"machine learning engineer": rows}))
    searches(box)
    discover(box)
    listed = box.list_untagged_jobs()["jobs"]
    assert listed[0]["title"] == "Machine Learning Engineer" and "synthetic2" in box.store.job(listed[0]["job_id"]).url


# --- Workday, smartextract and source isolation ------------------------------------------------


def test_workday_jobs_use_the_employer_as_company(box, monkeypatch):
    (box.data_dir / "employers.yaml").write_text(
        yaml.safe_dump(
            {
                "employers": {
                    "example": {
                        "name": "Example Corp",
                        "tenant": "example",
                        "site_id": "External",
                        "base_url": "https://example.wd1.myworkdayjobs.com",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    search = {
        "total": 1,
        "jobPostings": [{"title": "ML Engineer", "locationsText": "Seattle, WA", "externalPath": "/job/Seattle/ML_R1"}],
    }
    detail = {
        "jobPostingInfo": {
            "jobDescription": f"<p>{LONG}</p>",
            "externalUrl": "https://example.wd1.myworkdayjobs.com/External/job/Seattle/ML_R1",
        }
    }
    monkeypatch.setattr(ap_workday, "workday_search", lambda *a, **k: search)
    monkeypatch.setattr(ap_workday, "workday_detail", lambda *a, **k: detail)
    searches(box)
    result = discover(box, sources=("workday",))
    assert result["sources"]["workday"]["error"] is None
    (job,) = box.store.jobs()
    assert (job.source, job.company, job.location) == ("workday", "Example Corp", "Seattle, WA")
    assert job.description.startswith("SYNTHETIC posting.")


def test_smartextract_is_skipped_without_an_llm_key(box, monkeypatch):
    called = []
    monkeypatch.setattr(ap_smart, "run_smart_extract", lambda **k: called.append(k) or {"total_new": 0})
    searches(box)
    result = discover(box, sources=("smartextract",))
    assert result["sources"]["smartextract"]["skipped"] == "no LLM key configured"
    assert called == []
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    result = discover(box, sources=("smartextract",))
    assert called and result["sources"]["smartextract"]["skipped"] is None


def test_llm_key_can_live_in_the_data_dir_env_file(box):
    (box.data_dir / ".env").write_text("# SYNTHETIC\nOPENAI_API_KEY=synthetic-key\n", encoding="utf-8")
    try:
        bridge.configure(box.data_dir, box.settings)
        assert bridge.llm_configured()
    finally:
        os.environ.pop("OPENAI_API_KEY", None)


def test_a_failing_source_does_not_stop_the_others(box, monkeypatch):
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", FakeJobSpy({"machine learning engineer": [row(1)]}))

    def broken(**kwargs):
        raise RuntimeError("synthetic outage")

    monkeypatch.setattr(ap_workday, "run_workday_discovery", broken)
    searches(box)
    result = discover(box, sources=("workday", "jobspy"))
    assert "synthetic outage" in result["sources"]["workday"]["error"]
    assert result["sources"]["jobspy"]["error"] is None
    assert len(box.store.jobs()) == 1


def test_missing_searches_file_is_reported(box):
    result = box.discover_jobs(sources=["jobspy"], enrich=False, wait=True)
    assert result["status"] == "failed" and "searches.yaml" in result["error"]


# --- background runs and tools ------------------------------------------------------------------


def test_discovery_tools_are_preparation_available_to_scheduled_runs():
    assert TOOL_TIERS["discover_jobs"] == TOOL_TIERS["discovery_status"] == "prepare"
    assert {"discover_jobs", "discovery_status"} <= SCHEDULED_TOOLS


def test_background_run_is_imported_when_status_is_checked(box, monkeypatch):
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", FakeJobSpy({"machine learning engineer": [row(1)]}))
    searches(box)
    started = box.discover_jobs(sources=["jobspy"], enrich=False)
    assert started["status"] == "running"
    for _ in range(100):
        status = box.discovery_status()
        if status["status"] != "running":
            break
        time.sleep(0.05)
    assert status["status"] == "done" and status["synced"]["new"] == 1
    assert len(box.store.jobs()) == 1
    assert box.discovery_status()["synced"]["new"] == 1  # imported once


# --- enrichment (real Playwright against loopback) ---------------------------------------------


class _JobPage(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": "ML Engineer", "description": LONG}
        body = (
            "<html><head><title>ML Engineer</title>"
            f'<script type="application/ld+json">{json.dumps(posting)}</script></head>'
            "<body><h1>ML Engineer</h1></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def job_page():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _JobPage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/job/1"
    server.shutdown()


def test_enrichment_fills_in_short_descriptions(box, monkeypatch, job_page):
    rows = {"machine learning engineer": [row(1, job_url=job_page, description="Short SYNTHETIC snippet")]}
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", FakeJobSpy(rows))
    searches(box)
    result = discover(box, enrich=True)
    assert result["sources"]["enrich"]["error"] is None
    (job,) = box.store.jobs()
    assert job.description.startswith("SYNTHETIC posting.")


def test_discovery_setup_reports_choices_without_secrets(box, monkeypatch):
    status = box.get_discovery_setup()
    assert status["searches_yaml"] is False and status["llm_provider"] is None
    assert status["employers_yaml"] == status["sites_yaml"] == "ApplyPilot defaults"
    searches(box)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-secret")
    status = box.get_discovery_setup()
    assert status["searches_yaml"] is True and status["llm_provider"] == "gemini"
    assert "synthetic-secret" not in json.dumps(status)


# --- postings without text (slice 2c items 18-19, 2026-10-15) ------------------------------------------

MISSING = yaml.safe_load((Path(__file__).parent / "fixtures" / "applypilot_missing_text.yaml").read_text("utf-8"))
COLUMNS = (
    "url",
    "title",
    "salary",
    "description",
    "location",
    "site",
    "strategy",
    "discovered_at",
    "full_description",
    "application_url",
    "detail_scraped_at",
    "detail_error",
)
SIMPLYHIRED = "https://www.simplyhired.com/job/SYNTHETIC9000004"


def url_of(name: str) -> str:
    return MISSING["rows"][name]["url"]


def ap_db(box) -> sqlite3.Connection:
    bridge.configure(box.data_dir, box.settings)
    return sqlite3.connect(bridge.staging_dir(box.data_dir, box.settings) / "applypilot.db")


def stage(box, *names: str) -> None:
    """Put fixture rows into ApplyPilot's working DB, as its sources stored them."""
    db = ap_db(box)
    with db:
        for name in names:
            values = {"discovered_at": "2026-10-13T07:00:00+00:00", **MISSING["rows"][name]}
            db.execute(
                f"INSERT INTO jobs ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                [values.get(column) for column in COLUMNS],
            )
    db.close()


def ap_set(box, url: str, **fields) -> None:
    db = ap_db(box)
    with db:
        db.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE url = ?", [*fields.values(), url])
    db.close()


def ap_resolve_urls(box) -> None:
    """What ApplyPilot's enrichment does first: rewrite relative links it has a base URL for."""
    db = ap_db(box)
    ap_detail.resolve_all_urls(db)
    db.close()


def sync(box) -> dict:
    return bridge.sync(box.store, box.data_dir, box.settings)


def job_at(box, url: str) -> Job:
    (job,) = [job for job in box.store.jobs() if job.url == url]
    return job


def stored_before_the_fix(box, job: Job, description: str) -> Job:
    """A job as the bridge stored it before 2026-10-15 (placeholder text), tagged and on the apply list."""
    job = job.model_copy(update={"description": description})
    box.store.add_job(job)
    box.submit_job_tags(job.id, ["role.ml_engineering"])
    box.store.list_applications([job.id], "2026-10-14")
    return box.store.job(job.id)


class FakeScraper:
    """Stands in for ApplyPilot's per-site browser batch: records the links it is given and stores `text`."""

    def __init__(self, text: str):
        self.text = text
        self.urls: list[str] = []

    def __call__(self, conn, site, jobs, delay=2.0, max_jobs=None):
        for url, _title in jobs:
            self.urls.append(url)
            conn.execute(
                "UPDATE jobs SET full_description = ?, detail_scraped_at = ?, detail_error = NULL WHERE url = ?",
                (self.text, "2026-10-15T08:00:00+00:00", url),
            )
        conn.commit()
        return {"processed": len(jobs), "ok": len(jobs), "partial": 0, "error": 0, "tiers": {}}


def enrich(box) -> dict:
    searches(box)
    report = bridge.run_sources(box.data_dir, box.settings, [], enrich=True)
    assert report["enrich"]["error"] is None, report
    return report


def test_glassdoor_rows_without_a_description_are_stored_without_text(box, monkeypatch):
    glassdoor = row(1, site="glassdoor", job_url="https://www.glassdoor.com/job-listing/j?jl=1", description=None)
    glassdoor["job_url_direct"] = None
    monkeypatch.setattr(ap_jobspy, "scrape_jobs", FakeJobSpy({"machine learning engineer": [glassdoor]}))
    searches(box)
    discover(box)
    (job,) = box.store.jobs()
    assert job.description == ""
    assert "application_url" not in job.attributes


def test_placeholders_from_applypilot_count_as_no_text(box):
    stage(box, "glassdoor_none", "linkedin_null", "odd_spelling", "null_in_text")
    assert sync(box)["new"] == 4
    for name in ("glassdoor_none", "linkedin_null", "odd_spelling"):
        assert job_at(box, url_of(name)).description == "", name
    assert "application_url" not in job_at(box, url_of("glassdoor_none")).attributes
    assert job_at(box, url_of("null_in_text")).description == MISSING["rows"]["null_in_text"]["full_description"]


def test_search_description_is_used_when_enrichment_wrote_null(box):
    stage(box, "ziprecruiter_null")
    sync(box)
    job = job_at(box, url_of("ziprecruiter_null"))
    assert job.description == MISSING["text"]
    assert "application_url" not in job.attributes


def test_null_from_enrichment_never_replaces_text(box):
    stage(box, "ziprecruiter_null")
    ap_set(box, url_of("ziprecruiter_null"), full_description=None)
    sync(box)
    ap_set(box, url_of("ziprecruiter_null"), full_description="null")
    assert sync(box)["updated"] == 0
    assert job_at(box, url_of("ziprecruiter_null")).description == MISSING["text"]


def test_placeholder_text_stored_earlier_is_cleared_and_nothing_else_changes(box):
    stage(box, "glassdoor_none", "linkedin_null")
    sync(box)
    glassdoor = stored_before_the_fix(box, job_at(box, url_of("glassdoor_none")), "None")
    linkedin = stored_before_the_fix(box, job_at(box, url_of("linkedin_null")), "null")
    box.store.save_selection("2026-10-14", [(linkedin.id, "recommended")])
    counts = sync(box)
    assert counts["retagged"] == 0
    for job in (glassdoor, linkedin):
        after = box.store.job(job.id)
        assert after.description == ""
        assert box.store.is_tagged(job.id) and after.tags == job.tags
        assert box.store.applications()[job.id]["status"] == "pending"


def test_tagged_job_without_text_takes_real_text_and_goes_back_to_tagging(box):
    stage(box, "ziprecruiter_null")
    sync(box)
    job = stored_before_the_fix(box, job_at(box, url_of("ziprecruiter_null")), "null")
    assert sync(box)["retagged"] == 1
    after = box.store.job(job.id)
    assert after.description == MISSING["text"] and after.tags == []
    assert job.id in [waiting.id for waiting in box.store.untagged_jobs()]
    assert box.store.applications()[job.id]["status"] == "pending"
    box.submit_job_tags(job.id, ["role.ml_engineering"])
    assert sync(box)["retagged"] == 0
    assert box.store.is_tagged(job.id)


def test_shown_job_without_text_keeps_it(box):
    stage(box, "linkedin_null")
    sync(box)
    job = stored_before_the_fix(box, job_at(box, url_of("linkedin_null")), "")
    box.store.save_selection("2026-10-14", [(job.id, "recommended")])
    ap_set(box, url_of("linkedin_null"), full_description=MISSING["text"])
    assert sync(box)["retagged"] == 0
    assert box.store.job(job.id).description == "" and box.store.is_tagged(job.id)


def test_rows_enriched_without_text_are_retried_once(box, monkeypatch):
    stage(box, "glassdoor_none", "linkedin_null", "simplyhired_relative", "odd_spelling", "null_in_text")
    scraper = FakeScraper("null")
    monkeypatch.setattr(ap_detail, "scrape_site_batch", scraper)
    enrich(box)
    # Glassdoor is never enriched by ApplyPilot; the row with real text is done.
    assert sorted(scraper.urls) == sorted([url_of("linkedin_null"), SIMPLYHIRED, url_of("odd_spelling")])
    scraper.urls.clear()
    stage(box, "ziprecruiter_null")  # a row that came back without text after the first retry
    enrich(box)
    assert scraper.urls == [url_of("ziprecruiter_null")]
    scraper.urls.clear()
    enrich(box)
    assert scraper.urls == []


def test_text_found_on_retry_reaches_the_tagged_job(box, monkeypatch):
    stage(box, "linkedin_null")
    sync(box)
    job = stored_before_the_fix(box, job_at(box, url_of("linkedin_null")), "null")
    monkeypatch.setattr(ap_detail, "scrape_site_batch", FakeScraper(MISSING["text"]))
    enrich(box)
    assert sync(box)["retagged"] == 1
    assert box.store.job(job.id).description == MISSING["text"]
    assert not box.store.is_tagged(job.id)


def test_sites_without_a_base_url_use_their_search_page_domain(box):
    sites = [
        {"name": "SimplyHired", "url": "https://www.simplyhired.com/search?q={query_encoded}", "type": "search"},
        {"name": "Synthetic Board", "url": "https://jobs.example.org/search?q={query_encoded}", "type": "search"},
        {"name": "RemoteOK", "url": "https://remoteok.com/remote-{query_encoded}-jobs", "type": "search"},
    ]
    (box.data_dir / "sites.yaml").write_text(yaml.safe_dump({"sites": sites}), encoding="utf-8")
    stage_dir = bridge.configure(box.data_dir, box.settings)
    base = yaml.safe_load((stage_dir / "config" / "sites.yaml").read_text(encoding="utf-8"))["base_urls"]
    assert base["SimplyHired"] == "https://www.simplyhired.com"
    assert base["Synthetic Board"] == "https://jobs.example.org"
    assert base["RemoteOK"] is None  # ApplyPilot says these links cannot be resolved
    assert base["Talent.com"] == "https://www.talent.com"  # ApplyPilot's own entries are kept


def test_relative_links_become_full_links_and_stay_one_job(box):
    stage(box, "simplyhired_relative")
    sync(box)
    (job,) = box.store.jobs()
    assert job.url == SIMPLYHIRED
    ap_resolve_urls(box)
    assert sync(box)["new"] == 0
    assert [j.id for j in box.store.jobs()] == [job.id]


def test_jobs_stored_with_a_relative_link_get_the_full_link(box):
    relative = url_of("simplyhired_relative")
    old = Job(
        id=bridge.external_job_id(relative),
        source="smartextract",
        board="SimplyHired",
        title="Data Scientist",
        url=relative,
        location="Remote",
        tags=[],
    )
    box.store.add_job(old)
    box.store.mark_external(relative, old.id, "imported")
    box.submit_job_tags(old.id, ["role.ml_engineering"])
    box.store.list_applications([old.id], "2026-10-14")
    stage(box, "simplyhired_relative")
    assert sync(box)["new"] == 0
    (job,) = box.store.jobs()
    assert (job.id, job.url, job.tags) == (old.id, SIMPLYHIRED, ["role.ml_engineering"])
    assert box.store.is_tagged(job.id) and box.store.applications()[job.id]["status"] == "pending"
    ap_resolve_urls(box)
    assert sync(box)["new"] == 0
    assert [j.id for j in box.store.jobs()] == [old.id]
