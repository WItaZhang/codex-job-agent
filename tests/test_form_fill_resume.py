"""Synthetic continuation cases: no real account, code, website or application."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from intent_job_agent import apply_bridge
from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill import continuation
from intent_job_agent.form_fill.models import PageState
from intent_job_agent.form_fill.redact import Redactor
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox

from . import test_form_fill_runner as runner_cases
from .conftest import settings
from .test_form_fill_runner import FakeBrowser

ff = runner_cases.ff


class ResumeBrowser(FakeBrowser):
    """A worker that stays on the retained page; never creates or submits a tab."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.glance = PageState()

    def resume_chrome(self, generation):
        assert generation == "owned-generation"
        return self.ensure_chrome()

    def tab_state(self, target):
        return self.glance

    def fill(self, spec, **options):
        assert spec.resume and spec.mode == "stop"
        assert spec.resume_tab == "JOB"
        self.calls.append((spec, options))
        code = options.get("email_code", "")
        options["log_path"].write_text(
            options["mask"](
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": f"Synthetic code: {code}"},
                                {"type": "tool_use", "name": "browser_type", "input": {"text": code[:1]}},
                            ]
                        },
                    }
                )
                + "\n"
            )
        )
        status, reason, notes = self.outcomes.get(spec.job_id, ("waiting", None, ["stopped before Submit"]))
        return apply_bridge.ApplyOutcome(status, reason, notes, [], [], str(options["log_path"]))


@pytest.fixture
def ready(ff):
    box, path = ff
    browser = ResumeBrowser()
    for item in browser.tabs:
        item["generation"] = "owned-generation"
    box.form_fill_runner = browser
    folder = box.data_dir / box.settings.form_fill.dir / "runs" / "source" / "acme-ml-01"
    folder.mkdir(parents=True)
    meta = {
        "job_id": "acme-ml-01",
        "url": "https://jobs.example.test/acme-ml-01",
        "company": "Acme Robotics",
        "title": "ML Engineer",
        "tab": "https://identity.example.test/code",
        "tab_id": "JOB",
        "generation": "owned-generation",
        "agent_result": "email_verification",
        "status": "gate",
        "checks": {"not_submitted": True},
        "started": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
    }
    (folder / "meta.json").write_text(json.dumps(meta))
    browser.tabs.append({"id": "JOB", "url": meta["tab"], "generation": "owned-generation", "type": "page"})
    return box, path, browser, folder


def resume(box, path, **options):
    return box.resume_form_fill(path, "source", "acme-ml-01", **options)


def test_manual_gate_clear_uses_original_tab_and_durable_receipt(ready):
    box, path, browser, _ = ready
    before = browser.targets()
    assert box.get_form_fill_gate("source", "acme-ml-01")["can_resume"]
    result = resume(box, path)
    assert result["status"] == "done", box.form_fill_status()
    assert len(browser.calls) == 1 and browser.targets() == before
    assert browser.reads[0]["id"] == "JOB"
    status = box.form_fill_status()["jobs"][0]
    assert status["status"] == "filled" and status["checks"]["not_submitted"]
    assert box.store.activity() is None
    other = Toolbox(box.settings, Store(box.store.path, box.settings), data_dir=box.data_dir)
    try:
        duplicate = resume(other, path)
        assert duplicate["reused"] and duplicate["run_id"] == result["run_id"]
        assert other.get_form_fill_gate("source", "acme-ml-01")["continuation"]["run_id"] == result["run_id"]
        assert len(browser.calls) == 1
    finally:
        other.store.db.close()


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "original_tab_missing"),
        ("duplicate", "ambiguous_url"),
        ("wrong_site", "site_changed"),
        ("wrong_generation", "browser_generation_changed"),
        ("unavailable", "browser_unavailable"),
        ("failure", "inspection_failed"),
        ("identity", "browser_identity_unverified"),
    ],
)
def test_gate_reports_safe_specific_reason_without_actions(ready, monkeypatch, change, code):
    box, _, browser, _ = ready
    if change == "missing":
        browser.tabs.pop()
    elif change == "duplicate":
        browser.tabs.append({**browser.tabs[-1], "id": "OTHER"})
    elif change == "wrong_site":
        browser.tabs[-1]["url"] = "https://unrelated.example.test/private?secret=synthetic-secret"
    elif change == "wrong_generation":
        for item in browser.tabs:
            item["generation"] = "restarted"
    elif change == "unavailable":
        monkeypatch.setattr(browser, "open_tabs", lambda: None)
    else:

        def fail():
            if change == "identity":
                from intent_job_agent.browser_session import BrowserIdentityLost

                raise BrowserIdentityLost("synthetic-secret /private/browser/path")
            raise RuntimeError("synthetic-secret /private/applicant/path")

        monkeypatch.setattr(browser, "open_tabs", fail)
    before = list(browser.tabs)
    result = box.get_form_fill_gate("source", "acme-ml-01")
    assert not result["can_resume"] and result["reason_code"] == code
    assert result["reason"] and "synthetic-secret" not in json.dumps(result)
    assert "/private/" not in json.dumps(result)
    assert browser.tabs == before and not browser.calls and not browser.ensured
    assert box.store.activity() is None
    assert box.store.form_fill_continuation("source", "acme-ml-01") is None


def test_code_handoff_redacts_run_recording_and_receipt(ready):
    box, path, browser, _ = ready
    code = "481592"
    result = resume(box, path, verification_code=code, code_received_at=datetime.now(UTC).isoformat())
    assert result["status"] == "done", box.form_fill_status()
    spec, options = browser.calls[0]
    assert options["email_code"] == code and code in options["extra_prompt"]
    assert "Never submit" in options["extra_prompt"]
    assert not options["extra_allowed"] or all("gmail" not in t for t in options["extra_allowed"])
    assert box.store.activity() is None
    for file in (box.data_dir / box.settings.form_fill.dir).rglob("*"):
        if file.is_file():
            assert code.encode() not in file.read_bytes(), file.name
    assert code not in json.dumps(box.store.form_fill_continuation("source", "acme-ml-01"))
    assert code not in repr(box._form_fill_run)
    config = json.loads(
        (box.data_dir / "form-fill" / "runs" / result["run_id"] / spec.job_id / "checkpoint.json").read_text()
    )
    assert config["resume_tab"] == "JOB" and "JOB" not in config["before"]


@pytest.mark.parametrize("change", ["wrong_generation", "missing", "duplicate", "wrong_site", "wrong_list", "old_code"])
def test_invalid_continuation_launches_nothing(ready, change):
    box, path, browser, _ = ready
    options = {}
    if change == "wrong_generation":
        browser.tabs[-1]["generation"] = "restarted"
    elif change == "missing":
        browser.tabs.pop()
    elif change == "duplicate":
        browser.tabs.append({**browser.tabs[-1], "id": "OTHER"})
    elif change == "wrong_site":
        browser.tabs[-1]["url"] = "https://bank.example.test/"
    elif change == "wrong_list":
        from pathlib import Path

        p = Path(path)
        p.write_text(p.read_text().replace("Acme Robotics", "Other company"))
    else:
        options = {
            "verification_code": "481592",
            "code_received_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        }
    with pytest.raises(InvariantError):
        resume(box, path, **options)
    assert not browser.calls and not browser.ensured
    assert box.store.activity() is None
    assert box.store.form_fill_continuation("source", "acme-ml-01") is None


def test_receipt_seen_before_resume_never_starts_agent(ready):
    box, path, browser, _ = ready
    browser.glance = browser.state = PageState(received="Application received")
    result = resume(box, path)
    assert result["status"] == "done", box.form_fill_status()
    assert not browser.calls
    job = box.form_fill_status()["jobs"][0]
    assert job["agent_result"] == "already_applied" and not job["checks"]["not_submitted"]


def test_rejected_code_keeps_gate_and_second_request_reuses_it(ready):
    box, path, browser, _ = ready
    browser.outcomes["acme-ml-01"] = ("failed", "email_verification", ["code expired"])
    result = resume(box, path, verification_code="481592", code_received_at=datetime.now(UTC).isoformat())
    assert result["status"] == "done"
    assert box.form_fill_status()["jobs"][0]["status"] == "gate"
    assert resume(box, path)["run_id"] == result["run_id"]
    assert len(browser.calls) == 1
    assert box.get_form_fill_gate(result["run_id"], "acme-ml-01")["can_resume"]


@pytest.mark.parametrize("reason", ["stuck", "needs_answer"])
def test_readback_of_incomplete_page_is_not_filled(ff, reason):
    box, path = ff
    box.form_fill_runner = FakeBrowser(outcomes={"acme-ml-01": ("failed", reason, ["form did not advance"])})
    box.start_form_fill(path)
    job = box.form_fill_status()["jobs"][0]
    assert job["fields"] > 0 and job["checks"]["not_submitted"]
    assert job["status"] == "incomplete" and job["agent_result"] == reason


def test_sensitive_capture_and_network_are_withheld():
    from intent_job_agent.form_fill.checkpoint import LiveBrowser
    from intent_job_agent.form_fill.recording import network_entry

    redactor = Redactor([]).with_verification_code("481592")
    # No browser methods may be invoked for raw captures in a sensitive handoff.
    browser = object.__new__(LiveBrowser)
    browser.redactor = redactor
    capture = browser.capture("JOB", mhtml=True)
    assert capture.screenshot is None and capture.mhtml is None and capture.why
    assert network_entry({"body": b"481592"}, redactor) == (None, True)
    assert "481592" not in redactor.mask_log('{"text":"\\u0034\\u0038\\u0031\\u0035\\u0039\\u0032"}')
    assert '"text": "4"' not in redactor.mask_log('{"input":{"text":"4"}}')


def test_old_run_requires_matching_checkpoint_scope(ready):
    box, _, _, folder = ready
    meta = json.loads((folder / "meta.json").read_text())
    del meta["generation"]
    (folder / "meta.json").write_text(json.dumps(meta))
    config = {
        "run_id": "source",
        "job_id": "acme-ml-01",
        "root": str(box.data_dir),
        "db_path": str(box.store.path),
        "binding": {"generation": "owned-generation"},
    }
    (folder / "checkpoint.json").write_text(json.dumps(config))
    assert box.get_form_fill_gate("source", "acme-ml-01")["can_resume"]
    config["run_id"] = "other"
    (folder / "checkpoint.json").write_text(json.dumps(config))
    with pytest.raises(InvariantError):
        box.get_form_fill_gate("source", "acme-ml-01")


def test_concurrent_reservation_has_exactly_one_worker(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    s = settings()
    path = tmp_path / "concurrent.db"
    Store(path, s).db.close()
    barrier = Barrier(2)

    def reserve_one(n):
        store = Store(path, s)
        try:
            barrier.wait(timeout=5)
            return store.reserve_form_fill_continuation("source", "job", f"next-{n}", (n, "synthetic"))
        finally:
            store.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve_one, (1, 2)))
    assert sum(created for _, created in results) == 1
    assert results[0][0]["run_id"] == results[1][0]["run_id"]


def test_interrupted_successor_is_retained_and_does_not_restart(ready):
    box, path, browser, _ = ready
    box.store.reserve_form_fill_continuation("source", "acme-ml-01", "ambiguous", box._owner())
    box.store.end_activity(box._owner())
    result = resume(box, path)
    assert result == {
        "run_id": "ambiguous",
        "reused": True,
        "status": "interrupted",
        "message": "Existing continuation retained; no duplicate worker was started",
    }
    assert not browser.calls


def test_receipt_write_failure_always_releases_activity(ready, monkeypatch):
    box, path, _, _ = ready

    def broken(*args):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(Store, "finish_form_fill_continuation", broken)
    with pytest.raises(OSError):
        resume(box, path)
    assert box.store.activity() is None and box._form_fill_run["closed"]


def test_thread_start_failure_releases_activity_even_if_receipt_write_fails(ready, monkeypatch):
    import threading

    box, path, _, _ = ready
    box.form_fill_in_background = True

    def broken(*args):
        raise OSError("synthetic start/storage failure")

    monkeypatch.setattr(threading.Thread, "start", broken)
    monkeypatch.setattr(Store, "finish_form_fill_continuation", broken)
    with pytest.raises(OSError):
        resume(box, path)
    assert box.store.activity() is None and box._form_fill_run["closed"]


def test_page_rechecked_after_glance_before_worker_start(ready):
    box, path, browser, _ = ready

    def move(target):
        browser.tabs[-1]["url"] = "https://bank.example.test/"
        return PageState()

    browser.tab_state = move
    resume(box, path)
    assert not browser.calls
    assert box.form_fill_status()["jobs"][0]["status"] == "system_error"
    assert box.store.activity() is None


def test_checkpoint_retains_only_authorized_old_tab(tmp_path):
    from intent_job_agent.form_fill.checkpoint import Session

    from .test_form_fill_checkpoint import INFO, config
    from .test_form_fill_checkpoint import FakeBrowser as CheckpointBrowser

    browser = CheckpointBrowser()
    browser.show("JOB", INFO)
    session = Session(config(tmp_path, resume_tab="JOB"), browser, Redactor([]))
    assert session.checkpoint("blank", INFO["url"]).startswith("Page p1")
    assert browser.reads == ["JOB"]
    assert "T0" not in session.family


def test_real_browser_original_page_with_another_browser_at_same_url(tmp_path):
    from playwright.sync_api import sync_playwright

    from intent_job_agent import browser_session
    from intent_job_agent.form_fill.runner import FormFillRunner

    from .browser_fixture import finish_owned, start_owned

    url = "http://127.0.0.1/synthetic-application"
    html = '<label>First Name<input value="Synthetic"></label><button>Submit application</button>'
    with sync_playwright() as pw:
        context, binding = start_owned(tmp_path, pw)
        ordinary = pw.chromium.launch(headless=True)
        try:
            for ctx in (context, ordinary.new_context()):
                ctx.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html"))
                p = ctx.new_page()
                p.goto(url)
            ordinary_page = ordinary.contexts[0].pages[0]
            ordinary_page.locator("input").fill("Other browser untouched")
            own_page = context.pages[-1]
            own_page.locator("input").fill("481592")
            cfg = settings()
            cfg = cfg.model_copy(
                update={
                    "storage": cfg.storage.model_copy(update={"data_dir": binding.root, "db_path": binding.db_path})
                }
            )
            runner = FormFillRunner(tmp_path, cfg, tmp_path / "synthetic-applicant.json")
            runner.binding = browser_session.ensure(cfg, existing_generation=binding.generation)
            target = next(t for t in runner.targets() if t.get("url") == url)
            source = continuation.Source(
                "run",
                "job",
                url,
                "Synthetic",
                "Engineer",
                url,
                target["id"],
                binding.generation,
                datetime.now(UTC).isoformat(),
                "login_issue",
            )
            selected = continuation.select_target(source, runner.open_tabs())
            assert runner.tab_state(selected).before_submit
            out = tmp_path / "readback"
            final = runner.read(selected, out, Redactor([]).with_verification_code("481592"))
            assert final.state.before_submit and final.fields
            assert ordinary_page.locator("input").input_value() == "Other browser untouched"
            assert len(context.pages) == 2  # initial blank page plus original job; readback opened none
            assert all(b"481592" not in p.read_bytes() for p in out.iterdir() if p.is_file())
            assert not list(out.glob("*.png"))
            with pytest.raises(browser_session.BrowserIdentityLost):
                browser_session.ensure(cfg, existing_generation="wrong-generation")
            own_page.close()
            with pytest.raises(InvariantError):
                continuation.select_target(source, runner.open_tabs())
        finally:
            ordinary.close()
            finish_owned(binding)


def test_supplied_code_reaches_child_without_gmail_or_persisted_config(ready, tmp_path, monkeypatch):
    from intent_job_agent.form_fill.runner import job_spec, load_inputs

    from .apply_fakes import synthetic_binding
    from .test_slice6 import fake_claude

    box, path, _, folder = ready
    fake_claude(
        tmp_path,
        monkeypatch,
        """#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
args = sys.argv[1:]
assert "--no-session-persistence" in args
assert os.environ["INTENT_FORM_FILL_EMAIL_CODE"] == "481592"
assert "481592" in prompt and "no one-time email code was supplied" in prompt
servers = json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]
assert "gmail" not in servers
print(json.dumps({"type":"result", "result":"RESULT:APPLIED"}))
""",
        gmail_ready=False,
    )
    applicant = box._applicant()
    inputs = load_inputs(path, applicant, box.settings)
    spec = job_spec(inputs.jobs.jobs[0], applicant, folder)
    redactor = inputs.redactor.with_verification_code("481592")
    apply_bridge.configure(box.data_dir, box.settings, applicant.profile)
    outcome = apply_bridge.run_application(
        spec,
        box.data_dir,
        box.settings,
        applicant.profile,
        synthetic_binding(),
        email_code="481592",
        extra_prompt="one-time email code: 481592",
        mask=redactor.mask_log,
        extra_disallowed=tuple(box.settings.form_fill.disallowed_tools),
    )
    assert outcome.status == "waiting", outcome
    for p in (box.data_dir / "applypilot").rglob("*.json"):
        assert "481592" not in p.read_text()


@pytest.fixture
def source(tmp_path):
    s = settings()
    folder = tmp_path / s.form_fill.dir / "runs" / "synthetic-run" / "synthetic-job"
    folder.mkdir(parents=True)
    meta = {
        "job_id": "synthetic-job",
        "url": "https://jobs.example.test/apply",
        "company": "Synthetic",
        "title": "Engineer",
        "tab": "https://identity.example.test/code",
        "tab_id": "JOB",
        "generation": "owned-generation",
        "agent_result": "email_verification",
        "status": "gate",
        "started": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
        "checks": {"not_submitted": True},
    }
    (folder / "meta.json").write_text(json.dumps(meta))
    return continuation.load_source(tmp_path, s, "synthetic-run", "synthetic-job")


def tab(source, **extra):
    return {"id": source.tab_id, "generation": source.generation, "type": "page", "url": source.tab_url, **extra}


def test_exact_original_page_not_default_browser_or_new_tab(source):
    other = tab(source, generation="ordinary-chrome")
    assert continuation.select_target(source, [other, tab(source)])["generation"] == source.generation
    with pytest.raises(InvariantError):
        continuation.select_target(source, [other])
    with pytest.raises(InvariantError):
        continuation.select_target(source, [tab(source, id="replacement")])
    with pytest.raises(InvariantError):
        continuation.select_target(source, [tab(source), tab(source, id="duplicate")])
    with pytest.raises(InvariantError):
        continuation.select_target(source, [tab(source, url="https://bank.example.test/")])


def test_codes_are_for_this_email_challenge_and_expire(source):
    now = datetime.now(UTC)
    assert continuation.validate_code(source, "481592", now.isoformat(), max_age=300, now=now) == "481592"
    for stamp in ((now - timedelta(minutes=3)).isoformat(), (now + timedelta(seconds=10)).isoformat(), "2026-01-01"):
        with pytest.raises(InvariantError):
            continuation.validate_code(source, "481592", stamp, max_age=300, now=now)
    with pytest.raises(InvariantError):
        continuation.validate_code(source, "481592", (now - timedelta(seconds=60)).isoformat(), max_age=30, now=now)
    with pytest.raises(InvariantError, match="email"):
        continuation.validate_code(source._replace(gate="captcha"), "481592", now.isoformat(), max_age=300, now=now)


def test_one_durable_successor_across_two_stores(tmp_path):
    s = settings()
    first, second = Store(tmp_path / "coord.db", s), Store(tmp_path / "coord.db", s)
    try:
        a, created = first.reserve_form_fill_continuation("source", "job", "next", (123, "synthetic"))
        assert created and a["run_id"] == "next"
        b, created = second.reserve_form_fill_continuation("source", "job", "duplicate", (456, "synthetic"))
        assert not created and b["run_id"] == "next"
        first.finish_form_fill_continuation("source", "job", {"run_id": "next", "status": "done"})
        first.end_activity((123, "synthetic"))
        c, created = second.reserve_form_fill_continuation("source", "job", "third", (456, "synthetic"))
        assert not created and c["result"]["status"] == "done"
        assert second.activity() is None
    finally:
        first.db.close()
        second.db.close()
