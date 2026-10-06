"""Deterministic offline evaluations with versioned inputs and auditable logs."""

import hashlib
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .loader import (
    EvaluationDataError,
    index_predictions,
    load_config,
    load_jsonl,
    resolve_input,
    validate_cases,
    validate_quality_labels,
)
from .metrics import evaluate_gates, quality_report, score_cases
from .schemas import Case, Gates, Prediction, QualityLabel


def _version_report(cases: list[Case], predictions: dict[str, Prediction], gates: Gates) -> dict:
    overall = score_cases(cases, predictions)
    return {
        "overall": overall,
        "gates": evaluate_gates(overall, gates),
        "high_priority": score_cases([case for case in cases if case.expected.high_priority is True], predictions),
        "by_split": {
            split: score_cases([case for case in cases if case.split == split], predictions)
            for split in sorted({case.split for case in cases})
        },
        "by_provenance": {
            source: score_cases([case for case in cases if case.provenance == source], predictions)
            for source in sorted({case.provenance for case in cases})
        },
    }


def _comparison(baseline: dict, candidate: dict) -> dict:
    deltas = {}
    for name in ("prediction_completeness", "coverage", "false_selection_rate", "outcome_accuracy"):
        before, after = baseline["overall"][name]["rate"], candidate["overall"][name]["rate"]
        deltas[name] = after - before if before is not None and after is not None else None
    return {
        "rate_deltas_candidate_minus_baseline": deltas,
        "count_deltas_candidate_minus_baseline": {
            name: candidate["overall"]["counts"][name] - baseline["overall"]["counts"][name]
            for name in ("unauthorized_submissions", "invalid_submissions", "unsupported_fact_references")
        },
        "interpretation": "Descriptive paired-task comparison; no statistical significance or real-user claim.",
    }


def _file_evidence(path: Path) -> dict:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _snapshot_inputs(paths: dict[str, Path], log_path: Path, config: dict) -> tuple[dict[str, Path], dict]:
    """Freeze precisely the bytes evaluated and provide a runnable replay config."""
    snapshot_dir = log_path / "inputs"
    snapshot_dir.mkdir()
    snapshots = {}
    evidence = {}
    replay = dict(config)
    for name, source in paths.items():
        snapshot = snapshot_dir / f"{name}.jsonl"
        contents = source.read_bytes()
        snapshot.write_bytes(contents)
        snapshots[name] = snapshot
        evidence[name] = {
            "path": str(source),
            "sha256": hashlib.sha256(contents).hexdigest(),
            "snapshot_path": str(snapshot),
        }
        replay[f"{name}_path"] = f"inputs/{snapshot.name}"
    replay["log_dir"] = str(log_path.parent)
    (log_path / "replay.yaml").write_text(yaml.safe_dump(replay, sort_keys=False), encoding="utf-8")
    return snapshots, evidence


def run_evaluation(config_path: str | Path) -> dict:
    """Evaluate observations against external labels; never invoke models or browsers.

    Paths resolve relative to the configuration file. The entire cases file is
    validated before split filtering, so requesting holdout cannot hide leakage.
    The returned report is also written as ``metrics.json`` in a fresh log folder.
    """
    config_path = Path(config_path).resolve()
    config = load_config(config_path)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    log_path = resolve_input(config_path, config.log_dir) / f"{timestamp}_{config.experiment_name}"
    log_path.mkdir(parents=True, exist_ok=False)
    (log_path / "config.yaml").write_bytes(config_path.read_bytes())
    logger = logging.getLogger(f"applypilot.evaluation.{timestamp}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    file_handler = logging.FileHandler(log_path / "run.log", encoding="utf-8")
    stream_handler = logging.StreamHandler(sys.stdout)
    for handler in (file_handler, stream_handler):
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)

    try:
        logger.info("Loading evaluation %s", config.experiment_name)
        paths = {
            "cases": resolve_input(config_path, config.cases_path),
            "candidate_predictions": resolve_input(config_path, config.candidate_predictions_path),
        }
        if config.baseline_predictions_path:
            paths["baseline_predictions"] = resolve_input(config_path, config.baseline_predictions_path)
        if config.quality_labels_path:
            paths["quality_labels"] = resolve_input(config_path, config.quality_labels_path)
        paths, input_evidence = _snapshot_inputs(paths, log_path, config.model_dump())
        all_cases = load_jsonl(paths["cases"], Case)
        validate_cases(all_cases)
        cases = [case for case in all_cases if config.split == "all" or case.split == config.split]
        if not cases:
            raise EvaluationDataError(f"No cases in selected split: {config.split}")
        selected_ids = {case.task_id for case in cases}
        versions = {}
        for version in ("candidate", "baseline"):
            path = paths.get(f"{version}_predictions")
            if path is not None:
                predictions = index_predictions(load_jsonl(path, Prediction), all_cases)
                versions[version] = _version_report(cases, predictions, config.gates)
                logger.info(
                    "%s: tasks=%s missing=%s gates_passed=%s",
                    version,
                    versions[version]["overall"]["counts"]["tasks"],
                    versions[version]["overall"]["counts"]["missing_predictions"],
                    versions[version]["gates"]["passed"],
                )
        quality_labels = []
        if "quality_labels" in paths:
            if "baseline" not in versions:
                raise EvaluationDataError("Blind comparison labels require baseline predictions")
            quality_labels = load_jsonl(paths["quality_labels"], QualityLabel)
            validate_quality_labels(quality_labels, all_cases)
            quality_labels = [label for label in quality_labels if label.task_id in selected_ids]
        report = {
            "schema_version": 1,
            "experiment_name": config.experiment_name,
            "created_at": datetime.now(UTC).isoformat(),
            "log_dir": str(log_path),
            "config": config.model_dump(),
            "config_source": _file_evidence(config_path),
            "inputs": input_evidence,
            "evaluation_scope": {
                "split": config.split,
                "synthetic_cases": sum(case.provenance == "synthetic" for case in cases),
                "human_verified_cases": sum(case.provenance == "human_verified" for case in cases),
                "production_quality_validated": False,
            },
            "versions": versions,
            "quality": quality_report(quality_labels),
            "comparison": _comparison(versions["baseline"], versions["candidate"]) if "baseline" in versions else None,
            "limitations": [
                "Fixture observations are not live agent runs or evidence of production reliability.",
                "Fact reference checks validate membership, not whether wording faithfully represents the fact.",
                "submitted/review_obtained must originate from trusted execution evidence, not agent self-report.",
                "Human labels and blind comparisons require independently verified provenance.",
                "Counts and rates are descriptive; repeated reviewers are not independent task samples.",
                "Repository demo holdout is public and is not a protected release acceptance set.",
            ],
        }
        (log_path / "metrics.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        logger.info("Report saved to %s", log_path / "metrics.json")
        return report
    except Exception:
        logger.exception("Evaluation failed")
        raise
    finally:
        for handler in (file_handler, stream_handler):
            logger.removeHandler(handler)
            handler.close()
