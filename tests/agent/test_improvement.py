"""RCA proposals preserve evidence and cannot silently become deployments."""

import json
from pathlib import Path

import pytest
import yaml

from applypilot_agent.config import Settings
from applypilot_agent.evaluation import run_evaluation
from applypilot_agent.improvement import ImprovementService
from applypilot_agent.service import AgentService


def prepare(tmp_path: Path):
    service = AgentService(Settings(data_dir=tmp_path / "state", logs_dir=tmp_path / "logs"))
    improvements = ImprovementService(service)
    alert = {"id": "synthetic-alert", "summary": "Synthetic reviewer found redundant wording", "severity": "minor"}
    proposal = {
        "source_alert_id": alert["id"],
        "author": "synthetic-developer",
        "root_cause": "materials",
        "diagnosis": "Duplicate fact selection in the synthetic template",
        "supporting_evidence": ["Repeated supported claim in the frozen artifact"],
        "corrective_action": "Deduplicate fact selection before rendering",
        "regression_cases": ["test_duplicate_selection"],
        "expected_effect": "Same factual coverage without duplicate statements",
    }
    saved = improvements.propose(proposal, alert)
    regression = tmp_path / "regression.xml"
    regression.write_text('<testsuite><testcase name="test_duplicate_selection"/></testsuite>', encoding="utf-8")
    source = Path(__file__).resolve().parents[2] / "configs" / "evaluation.yaml"
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    for key in ("cases_path", "candidate_predictions_path", "baseline_predictions_path", "quality_labels_path"):
        config[key] = str((source.parent / config[key]).resolve())
    config["log_dir"] = str(tmp_path / "evaluations")
    config_path = tmp_path / "evaluation.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    evaluation = Path(run_evaluation(config_path)["log_dir"]) / "metrics.json"
    validation = {
        "proposal_id": saved["id"],
        "reviewer": "synthetic-independent-reviewer",
        "baseline_revision": "synthetic-before",
        "candidate_revision": "synthetic-after",
        "regression_report": str(regression),
        "evaluation_report": str(evaluation),
        "decision": "accept",
        "rationale": "Synthetic regression and paired comparison meet the declared contract",
    }
    return service, improvements, proposal, alert, validation


def test_proposal_is_idempotent_and_freezes_source_alert(tmp_path):
    _, improvements, proposal, alert, _ = prepare(tmp_path)
    first = improvements.propose(proposal, alert)
    assert len(improvements.records()["proposals"]) == 1
    alert["summary"] = "Changed later"
    assert improvements.records()["proposals"][0]["source_alert"]["summary"] != alert["summary"]
    second = improvements.propose(proposal, alert)
    assert first["id"] != second["id"]
    assert first["automatic_deployment"] is False


def test_validation_archives_exact_bytes_and_preserves_synthetic_limit(tmp_path):
    service, improvements, _, _, validation = prepare(tmp_path)
    before = service.settings.model_dump(mode="json")
    result = improvements.validate(validation)
    Path(validation["evaluation_report"]).write_text("replaced later", encoding="utf-8")
    frozen = json.loads(Path(result["evidence"]["evaluation"]["path"]).read_text())
    assert frozen["versions"]["candidate"]["gates"]["passed"] is True
    assert result["evaluation_scope"]["production_quality_validated"] is False
    assert result["automatic_deployment"] is False
    assert service.settings.model_dump(mode="json") == before
    assert service.list_jobs() == []


@pytest.mark.parametrize(
    "problem", ["self_review", "same_revision", "failures", "all_skipped", "empty_tests", "unrelated_tests", "gates"]
)
def test_acceptance_requires_independent_review_and_passing_evidence(tmp_path, problem):
    _, improvements, _, _, value = prepare(tmp_path)
    if problem == "self_review":
        value["reviewer"] = "SYNTHETIC-DEVELOPER"
    elif problem == "same_revision":
        value["candidate_revision"] = value["baseline_revision"]
    elif problem in {"failures", "all_skipped", "empty_tests", "unrelated_tests"}:
        content = {
            "failures": '<testsuite><testcase name="broken"><failure/></testcase></testsuite>',
            "all_skipped": '<testsuite><testcase name="ignored"><skipped/></testcase></testsuite>',
            "empty_tests": "<testsuite/>",
            "unrelated_tests": '<testsuite><testcase name="test_unrelated"/></testsuite>',
        }[problem]
        Path(value["regression_report"]).write_text(content, encoding="utf-8")
    else:
        path = Path(value["evaluation_report"])
        report = json.loads(path.read_text())
        report["versions"]["candidate"]["gates"]["passed"] = False
        path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        improvements.validate(value)
    assert improvements.records()["validations"] == []


def test_unknown_cause_cannot_be_accepted_and_failed_review_can_be_recorded(tmp_path):
    _, improvements, proposal, alert, value = prepare(tmp_path)
    proposal["root_cause"] = "unknown"
    value["proposal_id"] = improvements.propose(proposal, alert)["id"]
    with pytest.raises(ValueError, match="diagnosed"):
        improvements.validate(value)
    value["decision"] = "reject"
    assert improvements.validate(value)["validation"]["decision"] == "reject"


def test_proposal_cannot_reference_an_unrelated_alert(tmp_path):
    _, improvements, proposal, alert, _ = prepare(tmp_path)
    proposal["source_alert_id"] = "another-alert"
    with pytest.raises(ValueError, match="actual quality alert"):
        improvements.propose(proposal, alert)


def test_acceptance_recomputes_metrics_and_verifies_archived_inputs(tmp_path):
    _, improvements, _, _, value = prepare(tmp_path)
    path = Path(value["evaluation_report"])
    report = json.loads(path.read_text())
    report["versions"]["candidate"]["overall"]["counts"]["tasks"] += 1
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="disagree"):
        improvements.validate(value)
    report["versions"]["candidate"]["overall"]["counts"]["tasks"] -= 1
    path.write_text(json.dumps(report), encoding="utf-8")
    Path(report["inputs"]["cases"]["snapshot_path"]).write_text("replaced", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        improvements.validate(value)


def test_acceptance_rejects_invented_human_validation_scope(tmp_path):
    _, improvements, _, _, value = prepare(tmp_path)
    path = Path(value["evaluation_report"])
    report = json.loads(path.read_text())
    report["evaluation_scope"].update(synthetic_cases=0, human_verified_cases=999, production_quality_validated=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="case provenance"):
        improvements.validate(value)
    assert improvements.records()["validations"] == []
