"""External-action boundaries, durable intent and one real-browser integration demo."""

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from applypilot_agent.config import Settings
from applypilot_agent.demo import run_demo
from applypilot_agent.execution import Executor
from applypilot_agent.lease import LeaseBusy
from applypilot_agent.models import Answer, Assessment, Attachment, Job, Packet, Policy, Profile
from applypilot_agent.profile_models import PersonalInfo, Project, Provenance, SearchPreferences
from applypilot_agent.service import AgentService


class SessionFactory:
    """Inject timing/failure observations; count calls independently of runtime logs."""

    def __init__(self, *, status="submitted", prepare_hook=None, submit_hook=None, submit_error=None):
        self.status = status
        self.prepare_hook = prepare_hook
        self.submit_hook = submit_hook
        self.submit_error = submit_error
        self.opened = self.prepared = self.submitted = 0
        self.lock = threading.Lock()

    def __call__(self, **_kwargs):
        owner = self

        class Session:
            def __enter__(self):
                with owner.lock:
                    owner.opened += 1
                return self

            def __exit__(self, *_):
                pass

            def prepare(self, _plan):
                with owner.lock:
                    owner.prepared += 1
                if owner.prepare_hook:
                    owner.prepare_hook()
                return {"status": "prepared"}

            def submit(self, _plan):
                with owner.lock:
                    owner.submitted += 1
                if owner.submit_hook:
                    owner.submit_hook()
                if owner.submit_error:
                    raise owner.submit_error
                return {"status": owner.status, "attempt_id": "browser-attempt", "receipt": None}

        return Session()


def test_zero_budget_blocks_before_browser_open(tmp_path):
    service = prepared_service(tmp_path)
    service.settings.policy.daily_submission_limit = 0
    packet_hash = service.context("job")["application"]["packet_hash"]
    service.approve("job", packet_hash, "Synthetic approval under updated zero budget policy")
    factory = SessionFactory()
    with pytest.raises(ValueError, match="budget"):
        Executor(service, factory).execute("job", dry_run=False)
    assert factory.opened == 0
    assert not any(event["kind"] == "submit_intent" for event in service.store.events())


def test_reservation_is_not_a_public_unleased_write(tmp_path):
    service = prepared_service(tmp_path)
    with pytest.raises(ValueError, match="internal"):
        Executor(service).authorize("job", reserve=True)
    assert service.context("job")["application"]["state"] == "ready"


def prepared_service(tmp_path: Path, *, automatic=False, approve=True) -> AgentService:
    policy = Policy(auto_fit=["strong"] if automatic else [], allowed_domains=["example.test"])
    service = AgentService(Settings(data_dir=tmp_path / "state", logs_dir=tmp_path / "logs", policy=policy))
    service.save_profile(
        Profile(
            personal=PersonalInfo(
                id="contact",
                full_name="Synthetic Person",
                email="candidate@example.test",
                evidence=Provenance(source="Synthetic fixture", confirmed=True),
            ),
            projects=[
                Project(
                    id="python",
                    name="Python pipelines",
                    summary="Built Python pipelines",
                    evidence=Provenance(source="Synthetic fixture", confirmed=True),
                ),
            ],
            preferences=SearchPreferences(preferred_terms=["Python"]),
        )
    )
    service.import_jobs(
        [
            Job(
                id="job",
                source="test",
                source_id="job",
                url="https://example.test/job",
                apply_url="https://example.test/apply",
                title="Data Engineer",
                company="Synthetic Company",
                description="Build Python pipelines",
            )
        ]
    )
    context = service.context("job")
    service.assess(
        "job",
        Assessment(
            job_id="job",
            job_hash=context["job_hash"],
            profile_hash=context["profile_hash"],
            fit="strong",
            eligibility="pass",
            evidence_fact_ids=["python"],
            reasons=["Synthetic match"],
            source="codex",
        ),
    )
    material = service.render("job", ["python"], pdf=False)
    attachment = Attachment.model_validate(material["attachment"])
    packet = Packet(
        job_id="job",
        job_hash=context["job_hash"],
        profile_hash=context["profile_hash"],
        answers={"#name": Answer(value="Synthetic Person", fact_ids=["contact.full_name"])},
        attachments=[attachment],
        browser_plan={
            "url": "https://example.test/apply",
            "fields": [
                {"selector": "#name", "kind": "text", "value": "Synthetic Person"},
                {"selector": "#resume", "kind": "file", "value": attachment.path},
            ],
            "submit_selector": "#submit",
            "confirmation_selector": "#receipt",
            "confirmation_text": "Received",
        },
    )
    saved = service.save_packet(packet)
    if approve and not automatic:
        service.approve("job", saved["packet_hash"], "Synthetic unit-test approval")
    return service


def test_concurrent_executors_reserve_once_and_submit_once(tmp_path):
    service = prepared_service(tmp_path)
    start_together = threading.Barrier(2)
    factory = SessionFactory()

    def attempt():
        start_together.wait(timeout=10)
        try:
            return Executor(service, factory).execute("job", dry_run=False)
        except (ValueError, LeaseBusy) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, (ValueError, LeaseBusy)) for result in results) == 1
    assert factory.prepared == 1
    assert factory.submitted == 1
    assert service.context("job")["application"]["state"] == "submitted"
    assert len([event for event in service.store.events() if event["kind"] == "submit_intent"]) == 1


@pytest.mark.parametrize("change", ["policy", "facts", "attachment"])
def test_change_during_preparation_is_rechecked_before_submission(tmp_path, change):
    service = prepared_service(tmp_path, automatic=change == "policy")

    def mutate():
        if change == "policy":
            service.settings.policy.auto_fit = []
        elif change == "facts":
            profile = service.profile()
            profile.projects[0].summary = "Maintained Python pipelines"
            service.save_profile(profile)
        else:
            packet = service.context("job")["application"]["packet"]
            Path(packet["attachments"][0]["path"]).write_text("Changed after preparation", encoding="utf-8")

    factory = SessionFactory(prepare_hook=mutate)
    with pytest.raises(ValueError):
        Executor(service, factory).execute("job", dry_run=False)
    assert factory.prepared == 1
    assert factory.submitted == 0
    assert not any(event["kind"] == "submit_intent" for event in service.store.events())


def test_default_review_blocks_execution_before_browser_opens(tmp_path):
    service = prepared_service(tmp_path, approve=False)
    factory = SessionFactory()
    with pytest.raises(ValueError, match="approval"):
        Executor(service, factory).execute("job", dry_run=False)
    assert factory.opened == factory.submitted == 0
    assert service.context("job")["application"]["state"] == "review"


def test_approval_is_invalidated_by_policy_change(tmp_path):
    service = prepared_service(tmp_path)
    service.settings.policy.review_companies.append("Synthetic Company")
    with pytest.raises(ValueError, match="approval"):
        Executor(service, SessionFactory()).execute("job", dry_run=False)


def test_dry_run_cannot_become_success_or_consume_submission_budget(tmp_path):
    service = prepared_service(tmp_path)
    factory = SessionFactory()
    result = Executor(service, factory).execute("job", dry_run=True)
    assert result["submitted"] is False
    assert result["status"] == "prepared"
    assert service.context("job")["application"]["state"] == "ready"
    assert factory.prepared == 1 and factory.submitted == 0
    assert not any(event["kind"] == "submit_intent" for event in service.store.events())


def test_unknown_is_locked_until_external_reconciliation_and_new_approval(tmp_path):
    service = prepared_service(tmp_path)
    factory = SessionFactory(status="unknown")
    executor = Executor(service, factory)
    assert executor.execute("job", dry_run=False)["state"] == "unknown"
    with pytest.raises(ValueError, match="reconciliation"):
        executor.execute("job", dry_run=False)
    assert factory.submitted == 1
    evidence = tmp_path / "verification.json"
    evidence.write_text(
        json.dumps({"source": "synthetic server ledger", "observation": "Verified no accepted request"})
    )
    result = executor.reconcile("job", evidence, "not_submitted", "Synthetic user checked independent ledger")
    assert result["state"] == "retryable"
    assert service.context("job")["application"]["approval"] is None
    with pytest.raises(ValueError, match="approval"):
        executor.execute("job", dry_run=False)
    packet_hash = service.context("job")["application"]["packet_hash"]
    service.approve("job", packet_hash, "Synthetic approval after reconciliation")
    factory.status = "submitted"
    assert executor.execute("job", dry_run=False)["state"] == "submitted"
    assert factory.submitted == 2


def test_submit_exception_preserves_unknown_instead_of_retryable(tmp_path):
    service = prepared_service(tmp_path)
    factory = SessionFactory(submit_error=TimeoutError("Response lost after click"))
    result = Executor(service, factory).execute("job", dry_run=False)
    assert result["state"] == "unknown"
    assert "TimeoutError" in service.context("job")["application"]["last_error"]
    assert factory.submitted == 1


def test_browser_attempt_id_does_not_overwrite_durable_intent_id(tmp_path):
    service = prepared_service(tmp_path)
    result = Executor(service, SessionFactory()).execute("job", dry_run=False)
    events = service.store.events("job")
    intent = next(event for event in events if event["kind"] == "submit_intent")
    observation = next(event for event in events if event["kind"] == "submission_observed")
    assert result["attempt_id"] == intent["payload"]["attempt_id"]
    assert observation["payload"]["attempt_id"] == intent["payload"]["attempt_id"]


def test_stopped_process_recovery_preserves_uncertainty(tmp_path):
    service = prepared_service(tmp_path)
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(yaml.safe_dump(service.settings.model_dump(mode="json")), encoding="utf-8")
    program = """
import os, sys
from pathlib import Path
from applypilot_agent.config import load_settings
from applypilot_agent.service import AgentService
from applypilot_agent.execution import Executor
class StoppedSession:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def prepare(self, plan): return {'status': 'prepared'}
    def submit(self, plan): os._exit(19)
Executor(AgentService(load_settings(Path(sys.argv[1]))), StoppedSession).execute('job', dry_run=False)
"""
    stopped = subprocess.run(
        [sys.executable, "-c", program, str(settings_path)], capture_output=True, text=True, timeout=15, check=False
    )
    assert stopped.returncode == 19, stopped.stderr
    assert service.context("job")["application"]["state"] == "submitting"
    restarted = AgentService(service.settings)
    executor = Executor(restarted, SessionFactory())
    assert executor.recover() == {"unknown": ["job"], "active": []}
    assert restarted.context("job")["application"]["state"] == "unknown"
    with pytest.raises(ValueError, match="reconciliation"):
        executor.execute("job", dry_run=False)
    assert len([event for event in restarted.store.events() if event["kind"] == "submit_intent"]) == 1


def test_recovery_does_not_override_a_live_submission(tmp_path):
    service = prepared_service(tmp_path)
    submitting, release = threading.Event(), threading.Event()

    def pause_at_submission():
        submitting.set()
        assert release.wait(timeout=10)

    factory = SessionFactory(submit_hook=pause_at_submission)
    with ThreadPoolExecutor(max_workers=1) as pool:
        attempt = pool.submit(Executor(service, factory).execute, "job", dry_run=False)
        try:
            assert submitting.wait(timeout=10)
            assert Executor(service, factory).recover() == {"unknown": [], "active": ["job"]}
            assert service.context("job")["application"]["state"] == "submitting"
        finally:
            release.set()
        assert attempt.result(timeout=10)["state"] == "submitted"


def test_real_browser_demo_agrees_with_independent_employer_ledger(tmp_path):
    source = Path(__file__).resolve().parents[2] / "configs" / "demo.yaml"
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    config["logs_dir"], config["state_dir"] = str(tmp_path / "logs"), str(tmp_path / "state")
    config["render_pdf"] = False
    config_path = tmp_path / "demo.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    report = run_demo(config_path)
    assert report["passed"] is True
    assert report["synthetic"] is True and report["live_model_evaluated"] is False
    states = {scenario["job_id"]: scenario["state"] for scenario in report["scenarios"]}
    assert states == {"review-required": "submitted", "auto-authorized": "submitted", "receipt-missing": "unknown"}
    ledger = [json.loads(line) for line in Path(report["ledger_path"]).read_text().splitlines()]
    assert len(ledger) == 3
    assert {entry["job_id"] for entry in ledger} == set(states)
    assert all(scenario["employer_posts"] == 1 for scenario in report["scenarios"])
    assert Path(report["database_path"]).is_file()
    assert Path(report["overview_path"]).is_file()
