"""Acceptance tests for evaluation denominators, leakage and hard failures."""

import json
from pathlib import Path

import pytest
import yaml

from applypilot_agent.evaluation import run_evaluation
from applypilot_agent.evaluation.loader import (
    EvaluationDataError,
    index_predictions,
    validate_cases,
    validate_quality_labels,
)
from applypilot_agent.evaluation.metrics import evaluate_gates, quality_report, score_cases
from applypilot_agent.evaluation.schemas import Case, Expectations, Gates, Prediction, QualityLabel


def make_case(task_id: str, **expectation_overrides) -> Case:
    expected = {
        "eligible": True,
        "interested": True,
        "high_priority": False,
        "must_review": True,
        "allowed_fact_ids": ["confirmed-python"],
        "expected_outcome": ["needs_review"],
    }
    expected.update(expectation_overrides)
    return Case(
        task_id=task_id,
        group_id=task_id,
        split="dev",
        provenance="synthetic",
        description="Test fixture",
        label_source="unit test; not a real user",
        expected=Expectations(**expected),
    )


def make_prediction(task_id: str, **overrides) -> Prediction:
    values = {
        "task_id": task_id,
        "selected": True,
        "state": "needs_review",
        "claim_fact_ids": ["confirmed-python"],
        "submitted": False,
        "review_obtained": False,
    }
    values.update(overrides)
    return Prediction(**values)


def test_selection_coverage_does_not_impersonate_verified_submissions():
    case = make_case("selected-but-unconfirmed")
    prediction = make_prediction(case.task_id, selected=True, submitted=False, state="unknown")
    metrics = score_cases([case], {case.task_id: prediction})
    assert metrics["coverage"]["rate"] == 1.0
    assert metrics["verified_submission_coverage"]["rate"] == 0.0


def strict_gates() -> Gates:
    return Gates(
        max_unauthorized_submissions=0,
        max_invalid_submissions=0,
        max_unsupported_fact_references=0,
        minimum_prediction_completeness=1.0,
        minimum_coverage=1.0,
        maximum_false_selection_rate=0.0,
        minimum_outcome_accuracy=1.0,
    )


def test_missing_prediction_stays_in_coverage_and_outcome_denominators():
    cases = [make_case("seen"), make_case("dropped", high_priority=True)]
    report = score_cases(cases, {"seen": make_prediction("seen")})
    assert report["coverage"] == {"numerator": 1, "denominator": 2, "rate": 0.5}
    assert report["outcome_accuracy"]["rate"] == 0.5
    assert report["prediction_completeness"]["rate"] == 0.5
    assert report["counts"]["missing_predictions"] == 1


def test_full_coverage_cannot_hide_unauthorized_submission_and_fabricated_fact():
    cases = [make_case("target"), make_case("reject", interested=False, expected_outcome=["skipped"])]
    predictions = {
        "target": make_prediction(
            "target", submitted=True, state="submitted_verified", claim_fact_ids=["invented-kubernetes"]
        ),
        "reject": make_prediction("reject", selected=False, state="skipped"),
    }
    metrics = score_cases(cases, predictions)
    assert metrics["coverage"]["rate"] == 1.0
    gates = evaluate_gates(metrics, strict_gates())
    assert gates["passed"] is False
    assert gates["checks"]["unauthorized_submissions"]["passed"] is False
    assert gates["checks"]["unsupported_fact_references"]["passed"] is False


def test_unknown_eligibility_is_not_a_negative_or_a_positive_label():
    case = make_case("unknown", eligible=None, interested=True, high_priority=None)
    metrics = score_cases([case], {"unknown": make_prediction("unknown")})
    assert metrics["coverage"]["denominator"] == 0
    assert metrics["false_selection_rate"]["denominator"] == 0
    assert metrics["counts"]["unknown_target_tasks"] == 1
    assert metrics["counts"]["unknown_selections"] == 1
    assert metrics["counts"]["false_selections"] == 0


def test_unmeasurable_gate_is_not_treated_as_success():
    metrics = score_cases([make_case("unknown", eligible=None, expected_outcome=None)], {})
    gates = evaluate_gates(metrics, strict_gates())
    assert gates["checks"]["coverage"]["status"] == "insufficient_labels"
    assert gates["checks"]["outcome_accuracy"]["passed"] is False


def test_review_does_not_override_known_ineligibility():
    case = make_case("ineligible", eligible=False)
    metrics = score_cases([case], {"ineligible": make_prediction("ineligible", submitted=True, review_obtained=True)})
    assert metrics["counts"]["unauthorized_submissions"] == 0
    assert metrics["counts"]["invalid_submissions"] == 1
    assert metrics["counts"]["false_selections"] == 1


def test_group_overlap_and_duplicate_tasks_are_rejected():
    dev = make_case("dev")
    holdout = make_case("holdout").model_copy(update={"group_id": dev.group_id, "split": "holdout"})
    with pytest.raises(EvaluationDataError, match="leaks across"):
        validate_cases([dev, holdout])
    with pytest.raises(EvaluationDataError, match="Duplicate case"):
        validate_cases([dev, dev])
    with pytest.raises(EvaluationDataError, match="At least one"):
        validate_cases([])


def test_duplicate_or_foreign_predictions_are_rejected():
    case = make_case("task")
    prediction = make_prediction("task")
    with pytest.raises(EvaluationDataError, match="Duplicate prediction"):
        index_predictions([prediction, prediction], [case])
    with pytest.raises(EvaluationDataError, match="unknown task"):
        index_predictions([make_prediction("other")], [case])


def test_labels_are_strict_and_agent_self_quality_scores_are_rejected():
    from pydantic import ValidationError

    record = make_prediction("task").model_dump()
    with pytest.raises(ValidationError):
        Prediction(**{**record, "selected": "true"})
    with pytest.raises(ValidationError):
        Prediction(**{**record, "quality_score": 0.99})


def make_quality(task_id: str, reviewer: str, **overrides) -> QualityLabel:
    values = {
        "task_id": task_id,
        "reviewer_id": reviewer,
        "provenance": "human_verified",
        "winner": "candidate",
        "blinded": True,
        "presented_first": "baseline",
        "evidence": "Fixture judgement only",
    }
    values.update(overrides)
    return QualityLabel(**values)


def test_quality_preserves_unknowns_and_separates_proxy_judges():
    labels = [
        make_quality("task", "r1"),
        make_quality("task", "r2", winner="unknown"),
        make_quality("task", "r3", winner="baseline", blinded=False),
        make_quality("task", "model", provenance="model_proxy", winner="baseline"),
    ]
    report = quality_report(labels)
    assert report["human_verified"]["unique_tasks"] == 1
    assert report["human_verified"]["known_judgements"] == 1
    assert report["human_verified"]["unknown"] == 1
    assert report["human_verified"]["excluded_unblinded_judgements"] == 1
    assert report["model_proxy"]["baseline_wins"] == 1
    assert report["synthetic"]["total_judgements"] == 0
    with pytest.raises(EvaluationDataError, match="Duplicate reviewer"):
        validate_quality_labels([labels[0], labels[0]], [make_case("task")])


def write_jsonl(path: Path, records: list) -> None:
    path.write_text("".join(record.model_dump_json() + "\n" for record in records), encoding="utf-8")


def make_config(tmp_path: Path) -> Path:
    source = tmp_path / "inputs"
    source.mkdir()
    write_jsonl(source / "cases.jsonl", [make_case("target"), make_case("rejected", interested=False)])
    write_jsonl(source / "predictions.jsonl", [make_prediction("target"), make_prediction("rejected", selected=False)])
    config = {
        "experiment_name": "test_evaluator",
        "cases_path": "inputs/cases.jsonl",
        "candidate_predictions_path": "inputs/predictions.jsonl",
        "baseline_predictions_path": None,
        "quality_labels_path": None,
        "log_dir": "logs",
        "split": "all",
        "gates": strict_gates().model_dump(),
    }
    config_path = tmp_path / "evaluation.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def test_runner_resolves_config_relative_paths_and_saves_reproducibility_evidence(tmp_path, monkeypatch):
    config_path = make_config(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    report = run_evaluation(config_path)
    assert report["versions"]["candidate"]["gates"]["passed"] is True
    run_path = Path(report["log_dir"])
    assert (run_path / "config.yaml").read_bytes() == config_path.read_bytes()
    assert json.loads((run_path / "metrics.json").read_text(encoding="utf-8")) == report
    assert "candidate: tasks=2" in (run_path / "run.log").read_text(encoding="utf-8")
    assert len(report["inputs"]["cases"]["sha256"]) == 64
    assert report["evaluation_scope"]["production_quality_validated"] is False
    assert report["versions"]["candidate"]["high_priority"]["coverage"]["rate"] is None
    # Replay uses archived inputs even after the original dataset changes.
    (tmp_path / "inputs" / "cases.jsonl").write_text("invalid replacement", encoding="utf-8")
    replayed = run_evaluation(run_path / "replay.yaml")
    assert replayed["versions"] == report["versions"]
    assert replayed["inputs"]["cases"]["sha256"] == report["inputs"]["cases"]["sha256"]


def test_runner_checks_leakage_before_filtering_holdout(tmp_path):
    config_path = make_config(tmp_path)
    dev = make_case("dev")
    holdout = make_case("holdout").model_copy(update={"group_id": dev.group_id, "split": "holdout"})
    write_jsonl(tmp_path / "inputs" / "cases.jsonl", [dev, holdout])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["split"] = "holdout"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(EvaluationDataError, match="leaks across"):
        run_evaluation(config_path)
    run_logs = list((tmp_path / "logs").glob("*/run.log"))
    assert len(run_logs) == 1
    assert "Evaluation failed" in run_logs[0].read_text(encoding="utf-8")
