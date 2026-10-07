"""Production profile contracts, using synthetic records only."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from applypilot_agent.cli import app
from applypilot_agent.config import Settings
from applypilot_agent.materials import render_resume, validate_evidence
from applypilot_agent.models import Answer, Packet
from applypilot_agent.profile_evidence import confirmed_evidence
from applypilot_agent.profile_models import (
    Education,
    PersonalInfo,
    Profile,
    Project,
    Provenance,
    Publication,
    Skill,
    WorkAuthorization,
)
from applypilot_agent.quality.archive import archived_evidence
from applypilot_agent.serialization import digest
from applypilot_agent.service import AgentService


def evidence(confirmed=True, **kwargs):
    return Provenance(source="Synthetic user statement", confirmed=confirmed, **kwargs)


def profile():
    return Profile(
        personal=PersonalInfo(id="contact", full_name="Synthetic Candidate", evidence=evidence()),
        projects=[
            Project(id="pipeline", name="Example pipeline", summary="Built Python pipelines", evidence=evidence())
        ],
        publications=[Publication(id="paper", title="Synthetic study", status="under_review", evidence=evidence())],
    )


def test_typed_roundtrip_has_one_source_of_truth():
    value = profile()
    payload = value.model_dump(mode="json")
    assert "facts" not in payload and "name" not in payload
    assert Profile.model_validate_json(value.model_dump_json()) == value
    assert confirmed_evidence(value)["pipeline.summary"].text == payload["projects"][0]["summary"]


def test_partial_confirmation_does_not_promote_sibling_fields(tmp_path):
    value = profile()
    value.personal.email = "unconfirmed@example.test"
    value.personal.field_evidence["email"] = evidence(False)
    available = confirmed_evidence(value)
    assert "contact.full_name" in available
    assert "contact.email" not in available and "contact" not in available
    result = render_resume(value, ["contact.full_name", "pipeline"], tmp_path, pdf=False)
    text = Path(result["markdown_path"]).read_text(encoding="utf-8")
    assert "## Projects" in text and "Example pipeline" in text
    assert "unconfirmed@example.test" not in text
    with pytest.raises(ValueError, match="confirmed"):
        render_resume(value, ["contact"], tmp_path, pdf=False)


def test_scopes_intersect_for_record_and_remain_per_field():
    value = profile()
    value.personal.evidence = evidence(scope_job_ids=["job-a"])
    value.personal.email = "candidate@example.test"
    value.personal.field_evidence["email"] = evidence(scope_job_ids=["job-b"])
    assert "contact" not in confirmed_evidence(value, "job-a")
    assert "contact" not in confirmed_evidence(value, "job-b")
    assert "contact.full_name" in confirmed_evidence(value, "job-a")
    assert "contact.email" not in confirmed_evidence(value, "job-a")
    assert "contact.full_name" not in confirmed_evidence(value)


def test_boolean_false_and_unknown_authorization_are_distinct():
    value = profile()
    value.work_authorization = [
        WorkAuthorization(id="us-work", country="US", requires_sponsorship_now=False, evidence=evidence())
    ]
    available = confirmed_evidence(value)
    assert available["us-work.requires_sponsorship_now"].value is False
    assert "us-work.authorized_to_work" not in available
    with pytest.raises(ValidationError):
        WorkAuthorization(id="us-work", country="US", requires_sponsorship_now="false", evidence=evidence())


@pytest.mark.parametrize(
    "start,end,current",
    [
        ("2025-13", None, None),
        ("2025-02-30", None, None),
        ("2025", "2024", False),
        ("2020", "2024", True),
    ],
)
def test_bad_dates_rejected(start, end, current):
    with pytest.raises(ValidationError):
        Education(
            id="school",
            institution="Synthetic University",
            start_date=start,
            end_date=end,
            current=current,
            evidence=evidence(),
        )


def test_partial_date_precision_preserved():
    item = Education(
        id="school", institution="Synthetic University", start_date="2024", end_date="2024-06", evidence=evidence()
    )
    assert item.start_date == "2024" and item.end_date == "2024-06"


def test_duplicate_ids_and_dangling_references_rejected():
    value = profile().model_dump()
    value["projects"][0]["id"] = "contact"
    with pytest.raises(ValidationError, match="globally unique"):
        Profile.model_validate(value)
    value = profile().model_dump()
    value["projects"][0]["work_experience_id"] = "missing"
    with pytest.raises(ValidationError, match="missing work"):
        Profile.model_validate(value)
    value = profile()
    value.skills = [Skill(id="python", name="Python", supporting_record_ids=["missing"], evidence=evidence())]
    with pytest.raises(ValidationError, match="missing background"):
        Profile.model_validate(value.model_dump())


def test_metadata_cannot_point_to_missing_field_or_use_non_boolean_confirmation():
    with pytest.raises(ValidationError, match="populated"):
        PersonalInfo(id="contact", evidence=evidence(), field_evidence={"email": evidence()})
    with pytest.raises(ValidationError):
        Provenance(source="Synthetic source", confirmed="false")


def test_save_revalidates_nested_mutations_and_keeps_previous_record(tmp_path):
    service = AgentService(Settings(data_dir=tmp_path, logs_dir=tmp_path / "logs"))
    original = profile()
    service.save_profile(original)
    changed = service.profile()
    changed.projects[0].id = "contact"
    with pytest.raises(ValidationError):
        service.save_profile(changed)
    assert service.profile() == original


def test_edit_replaces_same_entity_and_stale_writer_cannot_restore_old_value(tmp_path):
    service = AgentService(Settings(data_dir=tmp_path, logs_dir=tmp_path / "logs"))
    initial = profile()
    saved = service.save_profile(initial)
    changed = service.profile()
    changed.publications[0].status = "accepted"
    service.save_profile(changed, expected_hash=saved["profile_hash"])
    current = service.profile()
    assert len(current.publications) == 1
    assert current.publications[0].id == "paper" and current.publications[0].status == "accepted"
    assert current.projects == initial.projects and current.personal == initial.personal
    assert digest(current) != saved["profile_hash"]
    with pytest.raises(ValueError, match="changed since"):
        service.save_profile(initial, expected_hash=saved["profile_hash"])
    with service.store.transaction() as db:
        assert len(service.store.records(db, "profile")) == 1
        assert len(service.store.records(db, "profile_version")) == 2


def test_legacy_live_profile_rejected_but_frozen_archive_remains_readable():
    legacy = {
        "name": "Synthetic",
        "facts": [
            {"id": "old", "text": "Old confirmed evidence", "source": "Fixture", "confirmed": True, "scope_job_ids": []}
        ],
    }
    with pytest.raises(ValidationError, match="Legacy profile"):
        Profile.model_validate(legacy)
    assert archived_evidence(legacy, "job")[0]["id"] == "old"


def test_field_citation_enforces_confirmation_and_scope():
    value = profile()
    value.personal.evidence = evidence(scope_job_ids=["job-a"])
    packet = Packet(
        job_id="job-b",
        job_hash="j",
        profile_hash="p",
        answers={"name": Answer(value="Synthetic Candidate", fact_ids=["contact.full_name"])},
    )
    with pytest.raises(ValueError, match="Unsupported"):
        validate_evidence(value, packet)
    packet.job_id = "job-a"
    validate_evidence(value, packet)


def test_export_is_plain_typed_snapshot_and_expected_hash_rejects_stale_import(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text(
        f"data_dir: {tmp_path.as_posix()}/state\nlogs_dir: {tmp_path.as_posix()}/logs\n", encoding="utf-8"
    )
    service = AgentService(Settings(data_dir=tmp_path / "state", logs_dir=tmp_path / "logs"))
    service.save_profile(profile())
    output = tmp_path / "visible-profile.json"
    runner = CliRunner()
    result = runner.invoke(app, ["--config", str(config), "profile-export", str(output)])
    assert result.exit_code == 0, result.output
    assert Profile.model_validate_json(output.read_text(encoding="utf-8")) == service.profile()
    exported_hash = json.loads(result.output)["profile_hash"]
    changed = service.profile()
    changed.publications[0].status = "published"
    service.save_profile(changed)
    result = runner.invoke(app, ["--config", str(config), "profile-set", str(output), "--expected-hash", exported_hash])
    assert result.exit_code == 1 and "changed since" in result.output
    assert service.profile().publications[0].status == "published"


def test_committed_template_and_example_are_valid_and_unconfirmed():
    root = Path(__file__).resolve().parents[2]
    for name in ("profile-template.json", "profile-example.json"):
        path = root / ".agents/skills/applypilot-onboard/references" / name
        value = Profile.model_validate_json(path.read_text(encoding="utf-8"))
        assert confirmed_evidence(value) == {}
