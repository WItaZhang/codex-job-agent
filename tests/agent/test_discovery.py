"""Public-feed contracts tested without real network or applicant data."""

from copy import deepcopy
from datetime import UTC, datetime

import httpx
import pytest

from applypilot_agent.discovery import DiscoveryError, fetch_board, normalize_job, stable_job_id


def greenhouse_posting(posting_id=123):
    return {
        "id": posting_id,
        "title": "Platform Engineer",
        "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{posting_id}",
        "location": {"name": "New York"},
        "content": "&lt;p&gt;Build &amp;amp; improve&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;",
        "departments": [{"name": "Engineering"}],
    }


def lever_posting(posting_id="123"):
    return {
        "id": posting_id,
        "text": "Platform Engineer",
        "hostedUrl": f"https://jobs.lever.co/acme/{posting_id}",
        "applyUrl": f"https://jobs.lever.co/acme/{posting_id}/apply",
        "categories": {"location": "Remote", "allLocations": ["Remote", "New York"], "commitment": "Full-time"},
        "descriptionPlain": "Build List<T> tooling.",
        "lists": [{"text": "Requirements", "content": "<ul><li>Python</li><li>SQL</li></ul>"}],
        "additionalPlain": "Everyone is welcome.",
        "salaryDescriptionPlain": "Salary depends on location.",
        "salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 100000, "max": 150000},
    }


def ashby_posting(posting_id="123"):
    return {
        "title": "Platform Engineer",
        "jobUrl": f"https://jobs.ashbyhq.com/acme/{posting_id}",
        "applyUrl": f"https://jobs.ashbyhq.com/acme/{posting_id}/application",
        "location": "New York",
        "secondaryLocations": [{"location": "San Francisco"}],
        "descriptionPlain": "Build List<T> tooling.",
        "isListed": True,
        "isRemote": False,
        "employmentType": "FullTime",
        "compensation": {"compensationTierSummary": "$100k–$150k"},
    }


def mock_client(payload, status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)))


def test_greenhouse_gets_full_content_normalizes_and_deduplicates():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"jobs": [greenhouse_posting(), greenhouse_posting()], "meta": {"total": 2}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        jobs = fetch_board("greenhouse", "acme", timeout_seconds=4, client=client)
        assert not client.is_closed
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source_id"] == "acme:123"
    assert job["description"] == "Build & improve\nPython"
    assert job["attributes"]["department"] == "Engineering"
    assert job["company"] == "acme"
    assert job["apply_url"] == job["url"]
    assert datetime.fromisoformat(job["fetched_at"]).utcoffset() == UTC.utcoffset(None)
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.host == "boards-api.greenhouse.io"
    assert calls[0].url.params["content"] == "true"
    assert calls[0].extensions["timeout"]["read"] == 4


def test_lever_includes_requirements_closing_salary_and_all_pages():
    offsets = []

    def respond(request):
        offset = int(request.url.params["skip"])
        offsets.append(offset)
        assert request.method == "GET"
        assert request.url.params["mode"] == "json"
        assert request.url.params["limit"] == "100"
        count = 100 if offset == 0 else 1
        return httpx.Response(200, json=[lever_posting(str(i)) for i in range(offset, offset + count)])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        jobs = fetch_board("lever", "acme", client=client)
    assert len(jobs) == 101
    assert offsets == [0, 100]
    assert jobs[0]["description"] == (
        "Build List<T> tooling.\nRequirements\nPython\nSQL\nEveryone is welcome.\nSalary depends on location."
    )
    assert jobs[0]["attributes"]["all_locations"] == "Remote; New York"
    assert '"min": 100000' in jobs[0]["attributes"]["salary_range"]


def test_lever_html_and_split_opening_body_fallback():
    posting = lever_posting()
    del posting["descriptionPlain"]
    posting["opening"] = "<p>Opening.</p>"
    posting["descriptionBody"] = "<p>Full body.</p>"
    with mock_client([posting]) as client:
        job = fetch_board("lever", "acme", client=client)[0]
    assert job["description"].startswith("Opening.\nFull body.\nRequirements")


def test_ashby_retains_compensation_and_excludes_explicitly_unlisted():
    hidden = ashby_posting("private")
    hidden["isListed"] = False

    def respond(request):
        assert request.method == "GET"
        assert request.url.params["includeCompensation"] == "true"
        return httpx.Response(200, json={"apiVersion": "1", "jobs": [ashby_posting(), hidden]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        jobs = fetch_board("ashby", "acme", client=client)
    assert len(jobs) == 1
    assert jobs[0]["source_id"] == "acme:123"
    assert jobs[0]["description"] == "Build List<T> tooling."
    assert jobs[0]["attributes"]["is_remote"] == "false"
    assert jobs[0]["attributes"]["secondary_locations"] == "San Francisco"
    assert "$100k" in jobs[0]["attributes"]["compensation"]


def test_stable_identity_survives_text_and_url_updates_but_separates_boards():
    original = stable_job_id("greenhouse", "acme:123", "https://acme.com/job/123")
    assert original == stable_job_id("greenhouse", "acme:123", "https://acme.com/careers/123?ref=updated")
    assert original != stable_job_id("greenhouse", "other:123", "https://acme.com/job/123")
    assert original != stable_job_id("lever", "acme:123", "https://acme.com/job/123")
    assert ":" not in original


@pytest.mark.parametrize("source_id", ["", "123", "acme:", ":123", "acme:../123", "a:b:c"])
def test_stable_id_requires_board_namespace(source_id):
    with pytest.raises(DiscoveryError):
        stable_job_id("greenhouse", source_id, "https://example.com/job/123")


def test_normalize_manual_job_defaults_and_full_text():
    text = "A very long description. " * 1000
    job = normalize_job(
        {
            "source": "manual",
            "url": "https://careers.example.com/123#details",
            "source_id": "",
            "title": " Engineer ",
            "company": "Acme",
            "description": f"<p>{text}</p>",
            "fetched_at": "2026-10-02T10:00:00-07:00",
        }
    )
    assert job["description"] == text.strip()
    assert job["title"] == "Engineer"
    assert job["url"] == "https://careers.example.com/123"
    assert job["apply_url"] == job["url"]
    assert job["fetched_at"] == "2026-10-02T17:00:00+00:00"
    assert job["location"] == ""
    assert job["attributes"] == {}


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/jobs",
        "javascript:alert(1)",
        "https://localhost/jobs",
        "https://127.0.0.1/jobs",
        "https://[::1]/jobs",
        "https://user:secret@example.com/jobs",
        "https://example.com:8080/jobs",
        "https://foo.local/jobs",
        "https://example.com/hello world",
        "https://example.com\\@evil.com/jobs",
        "https://example.com\n/jobs",
        "https://bad_host.example/jobs",
    ],
)
def test_reject_unsafe_job_urls(url):
    with pytest.raises(DiscoveryError):
        stable_job_id("manual", "", url)


@pytest.mark.parametrize(
    "provider,board",
    [
        ("unsupported", "acme"),
        ("manual", "acme"),
        ("lever", "https://example.com"),
        ("ashby", "../secret"),
        ("greenhouse", "acme?limit=0"),
        ("lever", ""),
        ("lever", "foo/bar"),
        ("lever", "foo%2fbar"),
        ("lever", "-acme"),
    ],
)
def test_invalid_source_or_board_is_rejected_before_network(provider, board):
    def unexpected_request(request):
        pytest.fail("invalid board caused an HTTP request")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as client, pytest.raises(DiscoveryError):
        fetch_board(provider, board, client=client)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "20"])
def test_timeout_must_be_finite_and_positive(timeout):
    with mock_client([]) as client, pytest.raises(DiscoveryError, match="timeout_seconds"):
        fetch_board("lever", "acme", timeout_seconds=timeout, client=client)


@pytest.mark.parametrize(
    "provider,payload",
    [
        ("greenhouse", {}),
        ("greenhouse", []),
        ("greenhouse", {"jobs": ["bad"]}),
        ("greenhouse", {"jobs": [greenhouse_posting()], "meta": {"total": 2}}),
        ("lever", {}),
        ("lever", [123]),
        ("ashby", {"jobs": {}}),
        ("ashby", {"jobs": [None]}),
    ],
)
def test_malformed_feed_fails_instead_of_becoming_empty_board(provider, payload):
    with mock_client(payload) as client, pytest.raises(DiscoveryError):
        fetch_board(provider, "acme", client=client)


@pytest.mark.parametrize(
    "provider,payload",
    [
        ("greenhouse", {"jobs": []}),
        ("lever", []),
        ("ashby", {"jobs": []}),
    ],
)
def test_valid_empty_board(provider, payload):
    with mock_client(payload) as client:
        assert fetch_board(provider, "acme", client=client) == []


@pytest.mark.parametrize("status", [301, 403, 404, 429, 500])
def test_http_failure_is_explicit_and_redirects_are_not_followed(status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={}, headers={"Location": "https://elsewhere.example.com"})

    with (
        httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client,
        pytest.raises(DiscoveryError, match="Could not read public board"),
    ):
        fetch_board("greenhouse", "acme", client=client)
    assert len(calls) == 1


def test_non_json_and_timeout_are_explicit():
    with (
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not json"))) as client,
        pytest.raises(DiscoveryError),
    ):
        fetch_board("lever", "acme", client=client)

    def timeout(request):
        raise httpx.ReadTimeout("fixture", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(timeout)) as client,
        pytest.raises(DiscoveryError, match="ReadTimeout"),
    ):
        fetch_board("lever", "acme", client=client)


def test_one_malformed_posting_rejects_entire_refresh():
    invalid = greenhouse_posting(456)
    del invalid["content"]
    with (
        mock_client({"jobs": [greenhouse_posting(), invalid]}) as client,
        pytest.raises(DiscoveryError, match="batch index 1"),
    ):
        fetch_board("greenhouse", "acme", client=client)


def test_conflicting_duplicates_are_not_silently_discarded():
    original = greenhouse_posting()
    duplicate = deepcopy(original)
    duplicate["title"] = "Different role"
    with (
        mock_client({"jobs": [original, duplicate]}) as client,
        pytest.raises(DiscoveryError, match="Conflicting duplicate"),
    ):
        fetch_board("greenhouse", "acme", client=client)


def test_repeated_lever_page_is_detected():
    with (
        mock_client([lever_posting(str(index)) for index in range(100)]) as client,
        pytest.raises(DiscoveryError, match="pagination made no progress"),
    ):
        fetch_board("lever", "acme", client=client)


@pytest.mark.parametrize(
    "change",
    [
        {"id": "forged"},
        {"fetched_at": "2026-10-02T17:00:00"},
        {"title": ""},
        {"attributes": {"score": 1}},
        {"unexpected": "ignored?"},
    ],
)
def test_normalization_rejects_invalid_canonical_fields(change):
    job = {
        "source": "manual",
        "source_id": "",
        "url": "https://example.com/job",
        "title": "Engineer",
        "company": "Acme",
        "description": "Build things.",
    }
    with pytest.raises(ValueError):
        normalize_job({**job, **change})


def test_ashby_boolean_strings_are_not_truthy_filters():
    posting = ashby_posting()
    posting["isListed"] = "false"
    with mock_client({"jobs": [posting]}) as client, pytest.raises(DiscoveryError, match="isListed"):
        fetch_board("ashby", "acme", client=client)
