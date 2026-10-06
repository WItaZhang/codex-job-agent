"""Prevent evaluation exports from silently promoting missing or inconsistent evidence."""

import json

import pytest

from applypilot_agent.config import Settings
from applypilot_agent.models import Job
from applypilot_agent.observations import export_observations
from applypilot_agent.service import AgentService


@pytest.fixture
def service(tmp_path):
    return AgentService(Settings(data_dir=tmp_path / "data", logs_dir=tmp_path / "logs"))


def test_missing_predictions_are_not_fabricated(service, tmp_path):
    output = tmp_path / "observed.jsonl"
    report = export_observations(service, {"task": "missing-job"}, output)
    assert report["missing_tasks"] == ["task"]
    assert report["count"] == 0
    assert output.read_text() == ""


def test_one_job_cannot_inflate_denominator(service, tmp_path):
    with pytest.raises(ValueError, match="inflate"):
        export_observations(service, {"task-a": "job", "task-b": "job"}, tmp_path / "observed.jsonl")


def test_claimed_success_without_event_is_rejected(service, tmp_path):
    service.import_jobs(
        [
            Job(
                id="job",
                source="manual",
                source_id="job",
                url="https://example.test/job",
                apply_url="https://example.test/apply",
                title="Role",
                company="Co",
                description="Role",
            )
        ]
    )
    with service.store.transaction() as db:
        service.store.update_application(db, "job", state="submitted")
    with pytest.raises(ValueError, match="disagreement"):
        export_observations(service, {"task": "job"}, tmp_path / "observed.jsonl")


def test_unprocessed_job_is_an_observation_not_a_success(service, tmp_path):
    service.import_jobs(
        [
            Job(
                id="job",
                source="manual",
                source_id="job",
                url="https://example.test/job",
                apply_url="https://example.test/apply",
                title="Role",
                company="Co",
                description="Role",
            )
        ]
    )
    output = tmp_path / "observed.jsonl"
    export_observations(service, {"task": "job"}, output)
    observed = json.loads(output.read_text())
    assert observed["state"] == "discovered"
    assert not observed["selected"] and not observed["submitted"]
