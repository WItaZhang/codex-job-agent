"""Synthetic audit integration: exact bytes, sampling, isolation and durable evidence.

The browser double never makes a network request. These are engineering tests,
not a measured LLM quality improvement or an employer outcome experiment.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from applypilot_agent.config import Settings
from applypilot_agent.execution import Executor
from applypilot_agent.models import Answer, Assessment, Attachment, Job, Packet, Profile
from applypilot_agent.profile_models import PersonalInfo, Project, Provenance, SearchPreferences
from applypilot_agent.quality import QualityReport, QualityService
from applypilot_agent.quality.archive import verified_snapshot
from applypilot_agent.serialization import digest
from applypilot_agent.service import AgentService


class BrowserDouble:
    def __init__(self, *, status="submitted", before_submit=None):
        self.status = status
        self.before_submit = before_submit
        self.clicks = 0

    def __call__(self, **_):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def prepare(self, _):
        return {"status": "prepared"}

    def submit(self, _):
        if self.before_submit:
            self.before_submit()
        self.clicks += 1
        return {"status": self.status}


@pytest.fixture
def service(tmp_path):
    result = AgentService(Settings(data_dir=tmp_path / "state", logs_dir=tmp_path / "logs"))
    result.save_profile(
        Profile(
            personal=PersonalInfo(
                id="contact",
                full_name="Synthetic Candidate",
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
                Project(
                    id="unconfirmed",
                    name="Private project",
                    summary="Private unconfirmed fact",
                    evidence=Provenance(source="Synthetic fixture"),
                ),
            ],
            preferences=SearchPreferences(preferred_terms=["Python"]),
        )
    )
    return result


def prepare_job(service, number=0, *, extra_answer=False):
    job_id = f"job-{number}"
    service.import_jobs(
        [
            Job(
                id=job_id,
                source="synthetic",
                source_id=job_id,
                url=f"https://example.test/{job_id}",
                apply_url=f"https://example.test/{job_id}/apply",
                title="Data Engineer",
                company="Synthetic Employer",
                description="Build Python pipelines",
            )
        ]
    )
    context = service.context(job_id)
    service.assess(
        job_id,
        Assessment(
            job_id=job_id,
            job_hash=context["job_hash"],
            profile_hash=context["profile_hash"],
            fit="strong",
            eligibility="pass",
            reasons=["PRODUCER_SELF_GRADE_MUST_NOT_LEAK"],
            evidence_fact_ids=["python"],
        ),
    )
    attachment = Attachment.model_validate(service.render(job_id, ["python"], pdf=False)["attachment"])
    answers = {"#name": Answer(value="Synthetic Candidate", fact_ids=["contact.full_name"])}
    if extra_answer:
        answers["#unused"] = Answer(value="UNSENT_ANSWER_MUST_NOT_LEAK", fact_ids=["contact.full_name"])
    packet = Packet(
        job_id=job_id,
        job_hash=context["job_hash"],
        profile_hash=context["profile_hash"],
        answers=answers,
        attachments=[attachment],
        browser_plan={
            "url": context["job"]["apply_url"],
            "fields": [
                {"selector": "#name", "kind": "text", "value": "Synthetic Candidate"},
                {"selector": "#resume", "kind": "file", "value": attachment.path},
            ],
            "submit_selector": "#submit",
            "confirmation_selector": "#receipt",
            "confirmation_text": "Received",
        },
    )
    saved = service.save_packet(packet)
    service.approve(job_id, saved["packet_hash"], "Synthetic test approval")
    return job_id


def submit(service, job_id, *, status="submitted"):
    return Executor(service, BrowserDouble(status=status)).execute(job_id, dry_run=False)


def records(service, namespace):
    with service.store.transaction() as db:
        return service.store.records(db, namespace)


def one_bundle(service, *, extra_answer=False):
    service.settings.quality.batch_size = 1
    submit(service, prepare_job(service, extra_answer=extra_answer))
    quality = QualityService(service)
    ticket = quality.queue()[0]
    return quality, ticket, quality.prepare(ticket["id"])


def report_for(bundle, *, issue=False):
    result = {
        "ticket_id": bundle["ticket_id"],
        "bundle_hash": bundle["bundle_hash"],
        "reviewers": {
            view: {
                "model": "synthetic-judge",
                "run_id": view,
                "context_mode": "fresh_no_history",
                "isolation": "instruction_only",
            }
            for view in ("hiring", "factual")
        },
        "verdict": "issues_found" if issue else "no_issue_found",
        "limitations": ["Synthetic report for engineering tests; no real model was invoked"],
    }
    if issue:
        result["issues"] = [
            {
                "id": "finding-1",
                "view": "hiring",
                "category": "specificity",
                "severity": "major",
                "summary": "Synthetic example of a reported issue",
                "citations": [
                    {"source_id": "hiring", "locator": "/job/description", "quote": "Build Python pipelines"}
                ],
                "recommendation": "Have a human check whether the evidence needs more specificity",
            }
        ]
    return result


def test_snapshot_is_durable_before_click_and_preserves_exact_bytes(service):
    job_id = prepare_job(service)
    original_path = Path(service.context(job_id)["application"]["packet"]["attachments"][0]["path"])
    original = original_path.read_bytes()

    def inspect_before_click():
        snapshots = records(service, "quality_snapshot")
        assert len(snapshots) == 1
        assert service.context(job_id)["application"]["state"] == "submitting"
        manifest, directory = verified_snapshot(QualityService(service).directory, snapshots[0])
        assert (directory / manifest["attachments"][0]["path"]).read_bytes() == original
        intent = next(item for item in service.store.events(job_id) if item["kind"] == "submit_intent")
        assert snapshots[0]["attempt_id"] == intent["payload"]["attempt_id"]
        assert digest(manifest["packet"]) == intent["payload"]["packet_hash"]

    browser = BrowserDouble(before_submit=inspect_before_click)
    assert Executor(service, browser).execute(job_id, dry_run=False)["state"] == "submitted"
    assert browser.clicks == 1


def test_freeze_failure_prevents_intent_and_click(service, monkeypatch):
    job_id = prepare_job(service)

    def fail(*_):
        raise OSError("Synthetic disk full")

    monkeypatch.setattr("applypilot_agent.quality.service.freeze", fail)
    browser = BrowserDouble()
    with pytest.raises(OSError, match="disk full"):
        Executor(service, browser).execute(job_id, dry_run=False)
    assert browser.clicks == 0
    assert not any(item["kind"] == "submit_intent" for item in service.store.events(job_id))
    assert service.context(job_id)["application"]["state"] == "ready"


def test_auxiliary_audit_failure_preserves_confirmed_delivery_and_can_catch_up(service, monkeypatch):
    job_id = prepare_job(service)
    with monkeypatch.context() as context:

        def fail(*_):
            raise ValueError("Synthetic audit failure")

        context.setattr(QualityService, "_sync", fail)
        result = submit(service, job_id)
    assert result["state"] == "submitted"
    assert result["quality"]["status"] == "failed"
    assert result["quality"]["local_alert_saved"]
    assert service.context(job_id)["application"]["state"] == "submitted"
    assert any(item["kind"] == "submission_observed" for item in service.store.events(job_id))
    assert QualityService(service).sync()["confirmed_jobs"] == 1
    assert QualityService(service).alerts()[0]["kind"] == "quality_sync_failed"


def test_persistent_catchup_failure_does_not_hide_cli_status_inbox_or_dashboard(service, monkeypatch, tmp_path):
    from applypilot_agent.cli import app

    job_id = prepare_job(service)

    def fail(*_):
        raise ValueError("Persistent synthetic audit failure")

    monkeypatch.setattr(QualityService, "_sync", fail)
    submit(service, job_id)
    quality = QualityService(service)
    assert quality.summary()["open_alerts"] == 1
    assert "Persistent synthetic" in quality.summary()["catch_up_warnings"][0]
    assert quality.queue() == []
    assert quality.alerts()[0]["kind"] == "quality_sync_failed"
    config = tmp_path / "agent.yaml"
    config.write_text(yaml.safe_dump(service.settings.model_dump(mode="json")), encoding="utf-8")
    runner = CliRunner()
    prefix = ["--config", str(config)]
    for command in ("status", "quality-inbox"):
        result = runner.invoke(app, [*prefix, command])
        assert result.exit_code == 0, result.output
        assert "Persistent synthetic audit failure" in result.output
    result = runner.invoke(app, [*prefix, "dashboard"])
    assert result.exit_code == 0, result.output
    dashboard = Path(json.loads(result.output)["path"]).read_text(encoding="utf-8")
    assert "待查看提醒 1 条" in dashboard
    assert "复核流程需要检查" in dashboard


def test_every_ten_unique_confirmations_draws_once_and_restart_does_not_resample(service, monkeypatch):
    draws = []

    def draw(size):
        draws.append(size)
        return 7

    monkeypatch.setattr("applypilot_agent.quality.service.secrets.randbelow", draw)
    quality = QualityService(service)
    for number in range(9):
        submit(service, prepare_job(service, number))
    assert quality.queue() == []
    submit(service, prepare_job(service, 9))
    first = quality.queue()
    assert len(first) == 1 and first[0]["job_id"] == "job-7"
    for _ in range(3):
        assert QualityService(AgentService(service.settings)).queue() == first
    assert draws == [10]
    batch = records(service, "quality_batch")[0]
    assert [item["job_id"] for item in batch["members"]] == [f"job-{number}" for number in range(10)]
    assert quality.summary()["confirmed_jobs"] == 10


def test_concurrent_confirmations_make_disjoint_batches_and_stable_exports(service):
    jobs = [prepare_job(service, number) for number in range(20)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda job_id: submit(service, job_id), jobs))
    assert all(item["state"] == "submitted" for item in outcomes)
    quality = QualityService(service)
    batches = records(service, "quality_batch")
    assert len(batches) == 2
    members = [item["job_id"] for batch in batches for item in batch["members"]]
    assert len(members) == len(set(members)) == 20
    ticket_id = quality.queue()[0]["id"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        bundles = list(pool.map(lambda _: QualityService(service).prepare(ticket_id), range(3)))
    assert len({item["bundle_hash"] for item in bundles}) == 1
    assert len({item["bundle_path"] for item in bundles}) == 1


def test_dry_run_unknown_and_reconciliation_are_counted_correctly(service, tmp_path):
    service.settings.quality.batch_size = 1
    job_id = prepare_job(service)
    browser = BrowserDouble(status="unknown")
    executor = Executor(service, browser)
    executor.execute(job_id, dry_run=True)
    assert records(service, "quality_snapshot") == []
    result = executor.execute(job_id, dry_run=False)
    assert result["state"] == "unknown"
    assert QualityService(service).queue() == []
    assert len(records(service, "quality_snapshot")) == 1
    evidence = tmp_path / "reconciliation.json"
    evidence.write_text(json.dumps({"source": "synthetic independent ledger", "observation": "Accepted"}))
    executor.reconcile(job_id, evidence, "submitted", "Synthetic user verified the ledger")
    quality = QualityService(service)
    assert quality.summary()["confirmed_jobs"] == 1
    assert len(quality.queue()) == 1
    assert quality.queue()[0]["attempt_id"] == result["attempt_id"]
    assert quality.summary()["confirmed_jobs"] == 1


def test_not_submitted_reconciliation_then_retry_counts_only_successful_attempt(service, tmp_path):
    job_id = prepare_job(service)
    first = submit(service, job_id, status="unknown")
    evidence = tmp_path / "reconciliation.json"
    evidence.write_text(json.dumps({"source": "synthetic ledger", "observation": "No request accepted"}))
    Executor(service).reconcile(job_id, evidence, "not_submitted", "Synthetic user checked ledger")
    service.approve(job_id, service.context(job_id)["application"]["packet_hash"], "Synthetic reapproval")
    second = submit(service, job_id)
    assert first["attempt_id"] != second["attempt_id"]
    assert len(records(service, "quality_snapshot")) == 2
    confirmed = records(service, "quality_confirmed")
    assert len(confirmed) == 1 and confirmed[0]["attempt_id"] == second["attempt_id"]


def test_bundle_uses_frozen_versions_and_separate_views_without_unsent_data(service):
    quality, ticket, bundle = one_bundle(service, extra_answer=True)
    original = Path(bundle["attachments"][0]).read_bytes()
    current = service.context(ticket["job_id"])
    Path(current["application"]["packet"]["attachments"][0]["path"]).write_text("Later edited resume")
    profile = service.profile()
    profile.projects[0].summary = "Later changed source fact"
    service.save_profile(profile)
    replay = quality.prepare(ticket["id"])
    assert replay["bundle_hash"] == bundle["bundle_hash"]
    assert Path(replay["attachments"][0]).read_bytes() == original
    hiring = Path(bundle["hiring_path"]).read_text()
    factual = Path(bundle["factual_path"]).read_text()
    for forbidden in ("PRODUCER_SELF_GRADE", "UNSENT_ANSWER", "Private unconfirmed fact", "fact_ids", "profile_hash"):
        assert forbidden not in hiring
    assert "Built Python pipelines" in factual and "Later changed" not in factual
    assert "Private unconfirmed fact" not in factual


def test_legacy_missing_snapshot_is_visible_and_blocked_sample_is_not_replaced(service):
    service.settings.quality.enabled = False
    job_id = prepare_job(service)
    submit(service, job_id)
    assert records(service, "quality_snapshot") == []
    service.settings.quality.enabled = True
    service.settings.quality.batch_size = 1
    quality = QualityService(service)
    quality.sync()
    ticket = quality.queue()[0]
    assert ticket["status"] == "blocked"
    assert quality.summary()["missing_snapshots"] == 1
    assert any(item["kind"] == "missing_snapshot" for item in quality.alerts())
    with pytest.raises(ValueError, match="do not resample"):
        quality.prepare(ticket["id"])
    assert quality.queue()[0]["id"] == ticket["id"]


@pytest.mark.parametrize(
    "target", ["snapshot_manifest", "snapshot_attachment", "bundle_manifest", "bundle_json", "bundle_attachment"]
)
def test_tampering_blocks_review_and_persists_integrity_alert(service, target):
    quality, ticket, bundle = one_bundle(service)
    snapshot = records(service, "quality_snapshot")[0]
    manifest_path = Path(snapshot["manifest_path"])
    manifest = json.loads(manifest_path.read_bytes())
    paths = {
        "snapshot_manifest": manifest_path,
        "snapshot_attachment": manifest_path.parent / manifest["attachments"][0]["path"],
        "bundle_manifest": Path(bundle["bundle_path"]),
        "bundle_json": Path(bundle["hiring_path"]),
        "bundle_attachment": Path(bundle["attachments"][0]),
    }
    paths[target].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        quality.record_report(report_for(bundle))
    assert quality.queue()[0]["status"] == "blocked"
    assert records(service, "quality_report") == []
    assert any(item["kind"] == "artifact_integrity" for item in quality.alerts())
    assert quality.queue()[0]["id"] == ticket["id"]


def test_report_binding_citations_provenance_alert_and_usage(service):
    quality, ticket, bundle = one_bundle(service)
    value = report_for(bundle, issue=True)
    with pytest.raises(ValueError, match="bundle hash"):
        quality.record_report({**value, "bundle_hash": "0" * 64})
    invalid = report_for(bundle, issue=True)
    invalid["issues"][0]["citations"][0]["quote"] = "Invented quote"
    with pytest.raises(ValueError, match="quote"):
        quality.record_report(invalid)
    invalid["issues"][0]["citations"][0] = {
        "source_id": "factual",
        "locator": "/facts/0/text",
        "quote": "Synthetic Candidate",
    }
    with pytest.raises(ValueError, match="outside"):
        quality.record_report(invalid)
    recorded = quality.record_report(value)
    assert quality.record_report(value) == recorded
    assert recorded["report"]["provenance"] == "model_proxy"
    assert quality.report(ticket["id"]) == recorded
    assert quality.detail(ticket["id"])["report"] == recorded
    assert quality.detail(ticket["id"])["alerts"][0]["issue_ids"] == ["finding-1"]
    assert recorded["confirmation_to_report_seconds"] >= 0
    assert quality.summary()["measured_review_usage"]["tokens"]["sum"] is None
    assert quality.summary()["reports_with_suspected_issues"] == 1
    with pytest.raises(ValueError, match="immutable"):
        quality.record_report(report_for(bundle))
    alerts = quality.alerts()
    assert len(alerts) == 1 and alerts[0]["local_only"]
    assert "suspected" in alerts[0]["message"]
    assert quality.acknowledge_alert(alerts[0]["id"])["acknowledged_at"]
    assert quality.summary()["open_alerts"] == 0
    assert service.context(ticket["job_id"])["application"]["state"] == "submitted"


def test_report_contract_requires_separate_contexts_and_honest_unknowns(service):
    _, _, bundle = one_bundle(service)
    value = report_for(bundle)
    value["reviewers"]["factual"]["run_id"] = value["reviewers"]["hiring"]["run_id"]
    with pytest.raises(ValidationError, match="separate fresh"):
        QualityReport.model_validate(value)
    value = report_for(bundle)
    with pytest.raises(ValidationError):
        QualityReport.model_validate({**value, "provenance": "human_verified"})
    with pytest.raises(ValidationError, match="limitation"):
        QualityReport.model_validate({**value, "verdict": "inconclusive", "limitations": []})
    with pytest.raises(ValidationError):
        QualityReport.model_validate({**value, "cost_usd": -1})


def test_unverified_submitted_row_is_not_sampled(service):
    job_id = prepare_job(service)
    with service.store.transaction() as db:
        service.store.update_application(db, job_id, state="submitted")
    quality = QualityService(service)
    quality.sync()
    assert quality.summary()["confirmed_jobs"] == 0
    assert quality.queue() == []
    assert quality.alerts()[0]["kind"] == "missing_confirmation"
