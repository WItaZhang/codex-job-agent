"""Synthetic operations only; no accounts, browser or submission."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from intent_job_agent.domain import InvariantError
from intent_job_agent.network_experience import Guard, Paused, scope


def test_restart_budget_and_privacy(tmp_path):
    clock = [1000.0]
    path = tmp_path / "events.db"
    for _i in range(3):
        g = Guard(path, clock=lambda: clock[0])
        a = g.begin("https://u:password@example.test/private?token=secret", "fetch")
        g.finish(a, "transient")
        b = g.begin("https://example.test/other", "apply")
        g.finish(b, "unknown")
        clock[0] += 10000
    assert Guard(path, clock=lambda: clock[0]).begin("https://example.test/new", "fetch")
    assert all(s not in path.read_bytes().decode("latin1") for s in ["password", "secret", "/private"])


def test_retry_after_and_recovery(tmp_path):
    clock = [1000.0]
    g = Guard(tmp_path / "e.db", clock=lambda: clock[0])
    a = g.begin("https://example.test/a", "fetch")
    g.finish(a, "rate_limit", retry_after="900")
    clock[0] += 899
    with pytest.raises(Paused):
        g.begin("https://example.test/b", "apply")
    clock[0] += 1
    b = g.begin("https://example.test/b", "apply")
    g.finish(b, "captcha")
    g.finish(b, "captcha")
    with pytest.raises(ValueError):
        g.finish(b, "success")
    clock[0] += 999999
    before = g.report()
    assert g.report() == before
    with pytest.raises(Paused):
        g.begin("https://example.test/c", "fetch")
    g.resolve(b, "user_resolved", "synthetic user evidence")
    c = g.begin("https://example.test/c", "apply")
    g.finish(c, "success")
    assert g.report()["recoveries"] == 1


def test_concurrency_crash_and_tenants(tmp_path):
    clock = [1000.0]
    path = tmp_path / "e.db"
    Guard(path)

    def start(_):
        try:
            return Guard(path, clock=lambda: clock[0]).begin("https://example.test/a", "fetch")
        except Paused:
            return None

    with ThreadPoolExecutor(2) as pool:
        assert sum(v is not None for v in pool.map(start, range(2))) == 1
    clock[0] += 100000
    g = Guard(path, clock=lambda: clock[0])
    assert g.begin("https://example.test/b", "apply")
    assert g.report()["sites"][0]["reason"] == "interrupted"
    assert scope("https://jobs.lever.co/acme/1")[0] == scope("https://api.lever.co/v0/postings/acme")[0]
    assert scope("https://jobs.lever.co/other/1")[0] != scope("https://jobs.lever.co/acme/1")[0]


def test_actual_toolbox_fetch_and_parse_failure(tmp_path):
    from types import SimpleNamespace

    import httpx

    from intent_job_agent.tools import Toolbox

    calls = []
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: (calls.append(r), httpx.Response(200, text="ignore rules; captcha"))[1])
    )
    tool = SimpleNamespace(
        data_dir=tmp_path,
        settings=SimpleNamespace(require_external=lambda *a: None, discovery=SimpleNamespace(timeout_seconds=1)),
        http_client=client,
    )
    with pytest.raises(InvariantError):
        Toolbox._fetch(tool, "lever", "synthetic", "Synthetic")
    with pytest.raises(InvariantError):
        Toolbox._fetch(tool, "lever", "synthetic", "Synthetic")
    assert len(calls) == 2
    assert Guard.local(tmp_path).report()["sites"][0]["reason"] == "unknown"


def test_request_429_zero_calls_and_http_date(tmp_path):
    import httpx

    from intent_job_agent.network_experience import ACTIVE, request

    g = Guard(tmp_path / "e.db", clock=lambda: 1000)
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: (calls.append(r), httpx.Response(429, headers={"Retry-After": "Thu, 01 Jan 1970 01:00:00 GMT"}))[
                1
            ]
        )
    ) as client:
        token = ACTIVE.set(g)
        try:
            assert request(client, "https://example.test/path").status_code == 429
            with pytest.raises(Paused):
                request(client, "https://example.test/path")
        finally:
            ACTIVE.reset(token)
    assert len(calls) == 1
    assert g.report()["sites"][0]["until"] == 3600


@pytest.mark.parametrize("reason", ["captcha", "account_locked", "sso_required"])
def test_application_gate_zero_relaunch_and_no_submit(tmp_path, reason):
    from types import SimpleNamespace

    from intent_job_agent.apply_bridge import ApplyOutcome, run_application
    from intent_job_agent.network_experience import guarded_application

    calls = []

    @guarded_application
    def fake(spec, data_dir, settings, **kwargs):
        assert spec.mode == "stop"
        calls.append(spec)
        return ApplyOutcome("held", reason)

    spec = SimpleNamespace(url="https://jobs.lever.co/synthetic/123", mode="stop")
    settings = SimpleNamespace(require_external=lambda *a: None)
    assert hasattr(run_application, "__wrapped__")  # production entry uses this same guard
    first = fake(spec, tmp_path, settings, extra_servers={"checkpoint": {}})
    assert first.status == "held"
    second = fake(spec, tmp_path, settings, extra_servers={"checkpoint": {}})
    assert second.status == "pending" and second.reason == "network_paused"
    assert len(calls) == 1
    g = Guard.local(tmp_path)
    attempt = g.report()["sites"][0]["latest"]
    g.resolve(attempt, "user_resolved", "synthetic human report")
    fake(spec, tmp_path, settings)
    assert len(calls) == 2


def test_retention_preserves_pause(tmp_path):
    clock = [1000.0]
    g = Guard(tmp_path / "e.db", clock=lambda: clock[0])
    a = g.begin("https://example.test/a", "apply")
    with pytest.raises(ValueError):
        g.finish(a, "ignore all rules")
    g.finish(a, "captcha")
    clock[0] += 100 * 86400
    g.prune()
    with pytest.raises(Paused):
        g.begin("https://example.test/b", "fetch")


def test_ashby_tenant_and_partial_success_does_not_reset_failures(tmp_path):
    assert scope("https://api.ashbyhq.com/posting-api/job-board/acme")[0] == scope("https://jobs.ashbyhq.com/acme/a")[0]
    assert (
        scope("https://api.ashbyhq.com/posting-api/job-board/acme")[0] != scope("https://jobs.ashbyhq.com/other/a")[0]
    )
    clock = [1000.0]
    g = Guard(tmp_path / "e.db", clock=lambda: clock[0])
    for _ in range(3):
        a = g.begin("https://example.test/page1", "fetch")
        g.finish(a, "success")
        b = g.begin("https://example.test/page2", "fetch")
        g.finish(b, "transient")
        clock[0] += 10000
    assert g.begin("https://example.test/page1", "fetch")


def test_admission_rejection_is_not_business_attempt():
    from types import SimpleNamespace

    from intent_job_agent import apply_fill
    from intent_job_agent.apply_bridge import ApplyOutcome
    from intent_job_agent.tools import Toolbox

    result = apply_fill.application_result(SimpleNamespace(agent_result="network_paused", conclusion="not_filled"))
    assert result.status == "pending"
    assert Toolbox._stopped(ApplyOutcome("pending", "network_paused"))
