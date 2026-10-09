"""Slice 2c: ApplyPilot's discovery and enrichment, vendored. All data here is SYNTHETIC and offline.

JobSpy and Workday network calls are replaced with fakes; enrichment runs the real Playwright
cascade against a page served on the loopback interface.
"""

import http.server
import json
import os
import threading
import time

import pandas as pd
import pytest
import yaml

from intent_job_agent import applypilot_bridge as bridge
from intent_job_agent.domain import Salary
from intent_job_agent.store import Store
from intent_job_agent.tools import SCHEDULED_TOOLS, TOOL_TIERS, Toolbox
from intent_job_agent.vendor.applypilot.discovery import jobspy as ap_jobspy
from intent_job_agent.vendor.applypilot.discovery import smartextract as ap_smart
from intent_job_agent.vendor.applypilot.discovery import workday as ap_workday

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
