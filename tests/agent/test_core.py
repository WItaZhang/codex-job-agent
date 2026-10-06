"""Core behavioral contracts: stale approvals, unknown facts, recovery and isolation."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from applypilot_agent.config import Settings
from applypilot_agent.matching import baseline, check_constraints
from applypilot_agent.models import Assessment, Constraint, Fact, Job, Packet, Policy, Profile
from applypilot_agent.serialization import digest, job_digest
from applypilot_agent.service import AgentService


@pytest.fixture
def service(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", logs_dir=tmp_path / "logs")
    service = AgentService(settings)
    service.save_profile(
        Profile(
            name="Example Candidate",
            facts=[
                Fact(id="python", text="Built Python data pipelines", source="test resume", confirmed=True),
                Fact(id="email", text="candidate@example.test", source="user", confirmed=True, key="email"),
            ],
            preferred_terms=["Python"],
        )
    )
    service.import_jobs(
        [
            Job(
                id="job-1",
                source="manual",
                source_id="job-1",
                url="https://example.test/jobs/1",
                apply_url="https://example.test/apply/1",
                title="Data Engineer",
                company="Example",
                description="Build Python data pipelines",
            )
        ]
    )
    service.assess("job-1")
    return service


def packet_for(service):
    context = service.context("job-1")
    return Packet(
        job_id="job-1",
        job_hash=context["job_hash"],
        profile_hash=context["profile_hash"],
        claims=[{"text": "Built Python data pipelines", "fact_ids": ["python"]}],
    )


def test_changed_profile_invalidates_packet_and_approval(service):
    packet = packet_for(service)
    saved = service.save_packet(packet)
    service.approve("job-1", saved["packet_hash"], "User approved this exact packet")
    profile = service.profile()
    profile.facts[0].text = "Maintained Python pipelines"
    service.save_profile(profile)
    with pytest.raises(ValueError, match="stale"):
        service.approve("job-1", saved["packet_hash"], "Old approval")
    assert service.context("job-1")["application"]["approval"] is None


def test_unsupported_fact_cannot_be_packet_evidence(service):
    packet = packet_for(service)
    packet.claims[0].fact_ids = ["imagined-kubernetes"]
    with pytest.raises(ValueError, match="Unsupported"):
        service.save_packet(packet)


def test_new_packet_invalidates_previous_approval(service):
    packet = packet_for(service)
    first = service.save_packet(packet)
    service.approve("job-1", first["packet_hash"], "Approved")
    packet.claims[0].text = "Built data pipelines with Python"
    second = service.save_packet(packet)
    assert second["packet_hash"] != first["packet_hash"]
    assert service.context("job-1")["application"]["approval"] is None
    with pytest.raises(ValueError, match="exact"):
        service.approve("job-1", first["packet_hash"], "Old approval")


def test_missing_constraint_is_unknown_not_rejection(service):
    profile = service.profile()
    profile.constraints = [Constraint(field="sponsorship", operator="equals", value="yes")]
    job = Job.model_validate(service.context("job-1")["job"])
    assert check_constraints(profile, job)[0] == "unknown"
    assessment = baseline(profile, job)
    assert assessment.eligibility == "unknown"
    assert assessment.fit != "no"


def test_codex_cannot_override_explicit_constraint(service):
    profile = service.profile()
    profile.constraints = [Constraint(field="location", operator="equals", value="Remote")]
    service.save_profile(profile)
    context = service.context("job-1")
    assessment = Assessment(
        job_id="job-1",
        profile_hash=context["profile_hash"],
        job_hash=context["job_hash"],
        fit="strong",
        eligibility="pass",
        reasons=["Looks suitable"],
    )
    with pytest.raises(ValueError, match="contradicts"):
        service.assess("job-1", assessment)


def test_refetch_timestamp_does_not_invalidate_material(service):
    packet = packet_for(service)
    service.save_packet(packet)
    job = Job.model_validate(service.context("job-1")["job"])
    before = job_digest(job)
    job.fetched_at = "2026-10-02T12:00:00+00:00"
    service.import_jobs([job])
    assert job_digest(job) == before
    assert service.context("job-1")["application"]["assessment"] is not None


def test_changed_posting_requires_reassessment(service):
    service.save_packet(packet_for(service))
    job = Job.model_validate(service.context("job-1")["job"])
    job.description += " Now requires onsite work."
    service.import_jobs([job])
    assert service.context("job-1")["application"]["state"] == "discovered"
    assert service.context("job-1")["application"]["assessment"] is None


def test_concurrent_duplicate_imports_keep_one_application(service):
    job = Job.model_validate(service.context("job-1")["job"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: service.import_jobs([job]), range(8)))
    assert len(service.list_jobs()) == 1


def test_baseline_is_not_automatic_submission_authority(service):
    service.settings.policy = Policy(auto_fit=["strong"], allowed_domains=["example.test"])
    result = service.save_packet(packet_for(service))
    assert result["state"] == "review"


def test_plan_excludes_blocked_work_and_respects_budget(service):
    service.save_packet(packet_for(service))
    assert service.plan(1)["jobs"] == []
    assert service.plan(1)["inbox_count"] == 1


def test_store_does_not_leak_to_another_user(service, tmp_path):
    other = AgentService(Settings(data_dir=tmp_path / "other", logs_dir=tmp_path / "logs"))
    assert other.list_jobs() == []
    with pytest.raises(ValueError, match="onboarding"):
        other.profile()


def test_profile_hash_is_stable(service):
    assert digest(service.profile()) == digest(service.profile().model_dump())


def test_cross_source_refresh_updates_canonical_job_and_invalidates_old_review(service):
    saved = service.save_packet(packet_for(service))
    service.approve("job-1", saved["packet_hash"], "Approved")
    newer = Job.model_validate(service.context("job-1")["job"])
    newer.id = "provider-alias"
    newer.source = "lever"
    newer.source_id = "board:other"
    newer.description = "Updated role now requires onsite Python operations"
    imported = service.import_jobs([newer])
    assert imported["aliases"] == {"provider-alias": "job-1"}
    assert len(service.list_jobs()) == 1
    context = service.context("job-1")
    assert context["job"]["description"] == newer.description
    assert context["application"]["approval"] is None
    assert context["application"]["assessment"] is None
    newer.apply_url += "?new-form=1"
    service.import_jobs([newer])
    assert len(service.list_jobs()) == 1
    assert service.context("job-1")["job"]["apply_url"] == newer.apply_url


def test_scoped_fact_cannot_be_reused_for_other_job(service):
    profile = service.profile()
    profile.facts[0].scope_job_ids = ["a-different-job"]
    service.save_profile(profile)
    service.assess("job-1")
    with pytest.raises(ValueError, match="Unsupported"):
        service.save_packet(packet_for(service))


def test_hundred_job_queue_reserves_high_fit_without_dropping_remainder(service):
    template = Job.model_validate(service.context("job-1")["job"])
    for index in range(1, 100):
        job = template.model_copy(
            update={
                "id": f"job-{index + 1}",
                "source_id": str(index),
                "apply_url": f"https://example.test/apply/{index + 1}",
            }
        )
        service.import_jobs([job])
        if index >= 90:
            service.assess(job.id)
    planned = service.plan(50)
    assert len(service.list_jobs()) == 100
    assert len(planned["jobs"]) == 50
    assert planned["remaining"] == 50
    assert len({item["job"]["id"] for item in planned["jobs"]}) == 50
    assert all((item["application"]["assessment"] or {}).get("fit") == "strong" for item in planned["jobs"][:10])
