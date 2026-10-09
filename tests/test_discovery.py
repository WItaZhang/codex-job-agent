"""Slice 2b: public ATS boards. All responses here are SYNTHETIC and served offline via httpx.MockTransport."""

import json
import sqlite3

import httpx
import pytest
import yaml

from intent_job_agent.discovery import DiscoveryError, fetch_board
from intent_job_agent.domain import InvariantError, Salary
from intent_job_agent.store import Store
from intent_job_agent.tools import SCHEDULED_TOOLS, Toolbox

from .conftest import BASE_INTENT, BASE_VOCAB, settings

DAY1, DAY2 = "2026-10-08", "2026-10-09"


def greenhouse_job(n: int, title: str = "Machine Learning Engineer") -> dict:
    return {
        "id": n,
        "title": title,
        "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{n}",
        "location": {"name": "San Francisco, CA"},
        "content": "&lt;p&gt;SYNTHETIC: build &amp;amp; ship models&lt;/p&gt;",
        "departments": [{"name": "Engineering"}],
        "updated_at": "2026-10-01T00:00:00Z",
    }


def lever_job(n: int, title: str = "Backend Engineer") -> dict:
    return {
        "id": f"lev-{n}",
        "text": title,
        "hostedUrl": f"https://jobs.lever.co/beta/lev-{n}",
        "applyUrl": f"https://jobs.lever.co/beta/lev-{n}/apply",
        "categories": {"location": "New York, NY", "commitment": "Full-time", "team": "Platform"},
        "descriptionPlain": "SYNTHETIC posting",
        "lists": [],
        "salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 150000, "max": 200000},
    }


def ashby_job(n: int, listed: bool = True) -> dict:
    return {
        "title": "Research Scientist",
        "jobUrl": f"https://jobs.ashbyhq.com/gamma/0000000{n}-aaaa-bbbb-cccc-dddddddddddd",
        "applyUrl": f"https://jobs.ashbyhq.com/gamma/0000000{n}-aaaa-bbbb-cccc-dddddddddddd/application",
        "location": "Remote - US",
        "isRemote": True,
        "isListed": listed,
        "descriptionPlain": "SYNTHETIC posting",
        "compensation": {
            "summaryComponents": [
                {
                    "compensationType": "Salary",
                    "interval": "1 YEAR",
                    "currencyCode": "USD",
                    "minValue": 180000,
                    "maxValue": 240000,
                }
            ]
        },
    }


class FakeBoards:
    """Serves SYNTHETIC board payloads; boards listed in `failing` answer HTTP 500."""

    def __init__(self):
        self.greenhouse: dict[str, list[dict]] = {}
        self.lever: dict[str, list[dict]] = {}
        self.ashby: dict[str, list[dict]] = {}
        self.failing: set[str] = set()
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        host, parts = request.url.host, request.url.path.strip("/").split("/")
        board = parts[-2] if host == "boards-api.greenhouse.io" else parts[-1]
        if board in self.failing:
            return httpx.Response(500)
        if host == "boards-api.greenhouse.io":
            jobs = self.greenhouse[board]
            return httpx.Response(200, json={"jobs": jobs, "meta": {"total": len(jobs)}})
        if host == "api.lever.co":
            skip, limit = int(request.url.params["skip"]), int(request.url.params["limit"])
            return httpx.Response(200, json=self.lever[board][skip : skip + limit])
        if host == "api.ashbyhq.com":
            return httpx.Response(200, json={"jobs": self.ashby[board]})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


# --- parsing (ported from main) -------------------------------------------------------------


def test_greenhouse_jobs_are_parsed_into_untagged_jobs():
    fake = FakeBoards()
    fake.greenhouse["acme"] = [greenhouse_job(1)]
    (job,) = fetch_board("greenhouse", "acme", company="Acme", timeout_seconds=5, client=fake.client())
    assert (job.title, job.company, job.source, job.board) == (
        "Machine Learning Engineer",
        "Acme",
        "greenhouse",
        "acme",
    )
    assert job.url == "https://boards.greenhouse.io/acme/jobs/1"
    assert job.location == "San Francisco, CA"
    assert job.description == "SYNTHETIC: build & ship models"
    assert job.tags == []
    assert job.attributes["department"] == "Engineering"


def test_lever_pages_through_and_reads_salary():
    fake = FakeBoards()
    fake.lever["beta"] = [lever_job(n) for n in range(103)]
    jobs = fetch_board("lever", "beta", company="Beta", timeout_seconds=5, client=fake.client())
    assert len(jobs) == 103 and len({job.id for job in jobs}) == 103
    assert jobs[0].salary == Salary(currency="USD", period="year", min=150000, max=200000)


def test_ashby_excludes_unlisted_jobs_and_reads_salary():
    fake = FakeBoards()
    fake.ashby["gamma"] = [ashby_job(1), ashby_job(2, listed=False)]
    (job,) = fetch_board("ashby", "gamma", company="Gamma", timeout_seconds=5, client=fake.client())
    assert job.salary == Salary(currency="USD", period="year", min=180000, max=240000)
    assert job.location == "Remote - US"


def test_a_failed_fetch_is_an_error_not_an_empty_board():
    fake = FakeBoards()
    fake.failing.add("acme")
    with pytest.raises(DiscoveryError):
        fetch_board("greenhouse", "acme", company="Acme", timeout_seconds=5, client=fake.client())


@pytest.mark.parametrize("board", ["https://evil.example/x", "../x", ""])
def test_board_must_be_a_token(board):
    with pytest.raises(DiscoveryError):
        fetch_board("greenhouse", board, company="X", timeout_seconds=5, client=FakeBoards().client())


def test_job_ids_are_stable_across_fetches():
    fake = FakeBoards()
    fake.greenhouse["acme"] = [greenhouse_job(1)]
    first = fetch_board("greenhouse", "acme", company="Acme", timeout_seconds=5, client=fake.client())
    second = fetch_board("greenhouse", "acme", company="Acme", timeout_seconds=5, client=fake.client())
    assert first[0].id == second[0].id


# --- tools ------------------------------------------------------------------------------------


@pytest.fixture
def fake():
    return FakeBoards()


@pytest.fixture
def box(tmp_path, fake):
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data, http_client=fake.client())
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": BASE_INTENT, "vocabulary": BASE_VOCAB}, allow_unicode=True), encoding="utf-8"
    )
    box.initialize_intent(str(data / "intent.yaml"))
    return box


def write_boards(box, boards: list[dict]) -> None:
    (box.data_dir / "boards.yaml").write_text(yaml.safe_dump({"boards": boards}), encoding="utf-8")


def test_board_tools_are_available_to_scheduled_runs():
    assert {"check_board", "fetch_boards"} <= SCHEDULED_TOOLS


def test_check_board_previews_without_storing(box, fake):
    fake.greenhouse["acme"] = [greenhouse_job(n) for n in range(5)]
    preview = box.check_board("greenhouse", "acme")
    assert preview["count"] == 5 and len(preview["sample_titles"]) <= 5
    assert box.store.jobs() == []


def test_fetch_boards_stores_new_untagged_jobs_and_reports_each_board(box, fake):
    fake.greenhouse["acme"] = [greenhouse_job(1), greenhouse_job(2)]
    fake.lever["beta"] = [lever_job(1)]
    fake.failing.add("broken")
    write_boards(
        box,
        [
            {"provider": "greenhouse", "board": "acme", "company": "Acme"},
            {"provider": "lever", "board": "beta", "company": "Beta"},
            {"provider": "ashby", "board": "broken", "company": "Broken"},
        ],
    )
    result = {(b["provider"], b["board"]): b for b in box.fetch_boards()["boards"]}
    assert result[("greenhouse", "acme")]["new"] == 2
    assert result[("lever", "beta")]["new"] == 1
    assert result[("ashby", "broken")]["error"]
    assert len(box.list_untagged_jobs()["jobs"]) == 3
    again = {(b["provider"], b["board"]): b for b in box.fetch_boards()["boards"]}
    assert again[("greenhouse", "acme")]["new"] == 0  # no duplicates
    assert len(box.store.jobs()) == 3


def test_title_include_filters_before_storing(box, fake):
    fake.greenhouse["acme"] = [
        greenhouse_job(1, "Machine Learning Engineer"),
        greenhouse_job(2, "Account Executive"),
        greenhouse_job(3, "Research Scientist"),
    ]
    write_boards(
        box,
        [{"provider": "greenhouse", "board": "acme", "company": "Acme", "title_include": ["engineer", "scientist"]}],
    )
    (report,) = box.fetch_boards()["boards"]
    assert (report["new"], report["filtered_out"]) == (2, 1)
    assert sorted(j["title"] for j in box.list_untagged_jobs()["jobs"]) == [
        "Machine Learning Engineer",
        "Research Scientist",
    ]


def _tag_all(box):
    for job in box.list_untagged_jobs(limit=100)["jobs"]:
        box.submit_job_tags(job["job_id"], ["role.ml_engineering", "specialty.ai_infra"])


def test_jobs_gone_from_their_board_are_not_recommended(box, fake):
    fake.greenhouse["acme"] = [greenhouse_job(1), greenhouse_job(2)]
    write_boards(box, [{"provider": "greenhouse", "board": "acme", "company": "Acme"}])
    box.fetch_boards()
    _tag_all(box)
    gone = next(j.id for j in box.store.jobs() if j.url.endswith("/2"))
    fake.greenhouse["acme"] = [greenhouse_job(1)]
    box.fetch_boards()
    assert gone not in {j["job_id"] for j in box.select_today(DAY1)["jobs"]}


def test_a_failed_refetch_keeps_jobs_open(box, fake):
    fake.greenhouse["acme"] = [greenhouse_job(1)]
    write_boards(box, [{"provider": "greenhouse", "board": "acme", "company": "Acme"}])
    box.fetch_boards()
    _tag_all(box)
    fake.failing.add("acme")
    (report,) = box.fetch_boards()["boards"]
    assert report["error"]
    assert len(box.select_today(DAY1)["jobs"]) == 1


def test_manually_imported_jobs_stay_open(box):
    (box.data_dir / "jobs.json").write_text(json.dumps([{"id": "m1", "title": "T", "description": "SYNTHETIC"}]))
    box.import_jobs(str(box.data_dir / "jobs.json"))
    box.submit_job_tags("m1", ["role.ml_engineering"])
    assert [j["job_id"] for j in box.select_today(DAY1)["jobs"]] == ["m1"]


def test_untagged_jobs_are_listed_newest_first_in_configured_batches(box, fake):
    fake.greenhouse["acme"] = [greenhouse_job(n) for n in range(50)]
    write_boards(box, [{"provider": "greenhouse", "board": "acme", "company": "Acme"}])
    box.fetch_boards()
    listed = box.list_untagged_jobs()["jobs"]
    assert len(listed) == box.settings.discovery.untagged_batch
    stored = [j.id for j in box.store.jobs()]
    assert [j["job_id"] for j in listed] == list(reversed(stored))[: len(listed)]


def test_boards_file_must_exist_in_the_data_dir(box):
    with pytest.raises(InvariantError):
        box.fetch_boards()


def test_database_from_the_previous_version_still_loads(tmp_path):
    """Upgrading must not lose the user's intent or jobs (rows written before slice 2b)."""
    path = tmp_path / "agent.sqlite"
    s = settings()
    Store(path, s)
    old_job = {"id": "old", "title": "T", "company": "C", "url": "", "tags": [], "salary": None, "description": ""}
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO jobs (id, data) VALUES (?, ?)", ("old", json.dumps(old_job)))
    job = Store(path, s).job("old")
    assert (job.id, job.source, job.board, job.location, job.attributes) == ("old", "manual", "", "", {})
