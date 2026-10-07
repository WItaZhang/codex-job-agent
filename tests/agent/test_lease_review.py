"""Independent review of ownership and authorization with actual child processes.

The child uses a deliberately blocked local executor double, never a public
website. Locks, SQLite transactions, process death, and recovery are real.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from applypilot_agent.config import Settings
from applypilot_agent.execution import Executor
from applypilot_agent.lease import LeaseBusy
from applypilot_agent.matching import check_constraints
from applypilot_agent.models import Assessment, Constraint, Job, Packet, Policy, Profile
from applypilot_agent.profile_models import PersonalInfo, Project, Provenance, SearchPreferences
from applypilot_agent.serialization import digest
from applypilot_agent.service import AgentService

_CHILD_EXECUTOR = """
import sys
from pathlib import Path
from applypilot_agent.config import Settings
from applypilot_agent.execution import Executor
from applypilot_agent.service import AgentService

class WaitingBrowser:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def prepare(self, plan): return {"status": "prepared"}
    def submit(self, plan):
        print("SUBMITTING", flush=True)
        sys.stdin.readline()
        return {"status": "unknown", "reason": "Synthetic process test; no external action"}

settings = Settings(data_dir=Path(sys.argv[1]), logs_dir=Path(sys.argv[2]))
Executor(AgentService(settings), session_factory=WaitingBrowser).execute("job", dry_run=False)
"""


@pytest.fixture
def reviewed_service(tmp_path):
    service = AgentService(Settings(data_dir=tmp_path / "data", logs_dir=tmp_path / "logs"))
    service.save_profile(
        Profile(
            personal=PersonalInfo(
                id="contact",
                full_name="Synthetic Applicant",
                email="candidate@example.test",
                evidence=Provenance(source="Synthetic fixture", confirmed=True),
            ),
            projects=[
                Project(
                    id="python",
                    name="Python pipelines",
                    summary="Built Python services",
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
                source="manual",
                source_id="manual-job",
                url="https://ats.example.test/jobs/1",
                apply_url="https://ats.example.test/apply?job=1",
                title="Python Engineer",
                company="Example",
                location="Onsite",
                description="Build Python services",
            )
        ]
    )
    context = service.context("job")
    assessment = Assessment(
        job_id="job",
        job_hash=context["job_hash"],
        profile_hash=context["profile_hash"],
        fit="strong",
        eligibility="pass",
        evidence_fact_ids=["python"],
        reasons=["Synthetic evidenced match"],
    )
    service.assess("job", assessment)
    packet = Packet(
        job_id="job",
        job_hash=context["job_hash"],
        profile_hash=context["profile_hash"],
        claims=[{"text": "Built Python services", "fact_ids": ["python"]}],
        browser_plan={
            "url": context["job"]["apply_url"],
            "fields": [],
            "submit_selector": "#submit",
            "confirmation_selector": "#receipt",
            "confirmation_text": "Application received",
        },
    )
    service.save_packet(packet)
    service.approve("job", digest(packet), "Synthetic user approval for process ownership test")
    return service


@contextmanager
def _running_executor(service):
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _CHILD_EXECUTOR, str(service.settings.data_dir), str(service.settings.logs_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    output = queue.Queue()
    reader = threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True)
    reader.start()
    try:
        try:
            message = output.get(timeout=15)
        except queue.Empty:
            pytest.fail("Child executor did not reach its submission boundary")
        assert message.strip() == "SUBMITTING", f"Child stopped early: {message!r}"
        assert process.poll() is None
        yield process
    finally:
        if process.poll() is None:
            process.communicate(input="release\n", timeout=10)
        else:
            process.communicate(timeout=10)
        reader.join(timeout=1)


def test_live_cross_process_executor_cannot_be_recovered(reviewed_service):
    executor = Executor(reviewed_service)
    with _running_executor(reviewed_service):
        assert reviewed_service.context("job")["application"]["state"] == "submitting"
        result = executor.recover()
        assert result == {"unknown": [], "active": ["job"]}
        assert reviewed_service.context("job")["application"]["state"] == "submitting"
        with pytest.raises(LeaseBusy):
            executor.execute("job", dry_run=False)
    assert reviewed_service.context("job")["application"]["state"] == "unknown"


def test_reconcile_cannot_unlock_live_cross_process_executor(reviewed_service, tmp_path):
    evidence = tmp_path / "synthetic-evidence.json"
    evidence.write_text(json.dumps({"source": "local test", "observation": "No real external request"}))
    with _running_executor(reviewed_service):
        with pytest.raises(LeaseBusy):
            Executor(reviewed_service).reconcile("job", evidence, "not_submitted", "Synthetic user verified")
        assert reviewed_service.context("job")["application"]["state"] == "submitting"


def test_process_death_releases_lease_and_recovery_preserves_unknown(reviewed_service, tmp_path):
    with _running_executor(reviewed_service) as process:
        process.kill()
        process.wait(timeout=10)
        assert process.returncode is not None
        executor = Executor(reviewed_service)
        assert executor.recover() == {"unknown": ["job"], "active": []}
        assert reviewed_service.context("job")["application"]["state"] == "unknown"
        with pytest.raises(ValueError, match="reconciliation"):
            executor.execute("job", dry_run=False)
        evidence = tmp_path / "verified.json"
        evidence.write_text(json.dumps({"source": "synthetic ledger", "observation": "No accepted request"}))
        assert executor.reconcile("job", evidence, "not_submitted", "Synthetic user verified")["state"] == "retryable"
        assert reviewed_service.context("job")["application"]["approval"] is None


def test_same_destination_across_sources_keeps_one_application(reviewed_service):
    original = Job.model_validate(reviewed_service.context("job")["job"])
    alias = original.model_copy(
        update={
            "id": "provider-job",
            "source": "greenhouse",
            "source_id": "example:1",
            "apply_url": original.apply_url + "&utm_source=tracker#application",
        }
    )
    result = reviewed_service.import_jobs([alias])
    assert result["aliases"] == {"provider-job": "job"}
    assert len(reviewed_service.list_jobs()) == 1
    assert reviewed_service.context("job")["application"]["approval"] is not None
    another = original.model_copy(update={"id": "different-job", "apply_url": "https://ats.example.test/apply?job=2"})
    reviewed_service.import_jobs([another])
    assert len(reviewed_service.list_jobs()) == 2


def test_explicit_rejection_survives_codex_reassessment(reviewed_service):
    assessment = Assessment.model_validate(reviewed_service.context("job")["application"]["assessment"])
    reviewed_service.record_feedback("job", "no", "I do not want this employer")
    result = reviewed_service.assess("job", assessment)
    assert result["assessment"]["fit"] == "no"
    assert result["state"] == "skipped"
    with pytest.raises(ValueError):
        Executor(reviewed_service).authorize("job")


@pytest.mark.parametrize("value", [[], "", [""], ["   "]])
def test_empty_constraint_values_rejected(value):
    with pytest.raises(ValidationError):
        Constraint(field="location", operator="equals", value=value)


def test_attributes_cannot_override_authoritative_location(reviewed_service):
    profile = reviewed_service.profile()
    profile.preferences.constraints = [Constraint(field="location", operator="equals", value="Remote")]
    job = Job.model_validate(reviewed_service.context("job")["job"])
    job.attributes["location"] = "Remote"
    assert check_constraints(profile, job)[0] == "fail"


def test_auto_submission_needs_matching_evidence(reviewed_service):
    reviewed_service.settings.policy = Policy(auto_fit=["strong"], allowed_domains=["ats.example.test"])
    context = reviewed_service.context("job")
    assessment = Assessment.model_validate(context["application"]["assessment"])
    assessment.evidence_fact_ids = []
    reviewed_service.assess("job", assessment)
    packet = Packet.model_validate(context["application"]["packet"])
    saved = reviewed_service.save_packet(packet)
    assert saved["state"] == "review"
    with pytest.raises(ValueError, match="approval"):
        Executor(reviewed_service).authorize("job")
