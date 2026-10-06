"""Recompute paired evaluation evidence before recording development acceptance."""

import hashlib
import json
from pathlib import Path

from .evaluation.loader import index_predictions, validate_cases
from .evaluation.metrics import evaluate_gates, score_cases
from .evaluation.schemas import Case, EvaluationConfig, Prediction


def verify_comparison(report: dict, report_path: Path) -> dict[str, bytes]:
    """Check the archived bytes, schemas and metrics, not an asserted pass flag.

    Labels retain their declared provenance; this is not reviewer authentication
    or proof that synthetic cases represent real users.
    """
    config = EvaluationConfig.model_validate(report.get("config", {}))
    folder = report_path.resolve().parent / "inputs"
    contents = {}
    for name in ("cases", "candidate_predictions", "baseline_predictions"):
        evidence = report.get("inputs", {}).get(name)
        if not isinstance(evidence, dict) or not evidence.get("snapshot_path"):
            raise ValueError("Paired evaluation needs archived cases and both prediction files")
        source = Path(evidence["snapshot_path"]).resolve()
        if source.parent != folder:
            raise ValueError("Evaluation snapshots must belong to the supplied report's inputs directory")
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != evidence.get("sha256"):
            raise ValueError("Evaluation input snapshot hash mismatch")
        contents[name] = data
    all_cases = [
        Case.model_validate(json.loads(line)) for line in contents["cases"].decode("utf-8").splitlines() if line
    ]
    validate_cases(all_cases)
    selected = [case for case in all_cases if config.split == "all" or case.split == config.split]
    if not selected:
        raise ValueError("Evaluation has no cases for the selected split")
    scope = {
        "split": config.split,
        "synthetic_cases": sum(case.provenance == "synthetic" for case in selected),
        "human_verified_cases": sum(case.provenance == "human_verified" for case in selected),
        "production_quality_validated": False,
    }
    if report.get("evaluation_scope") != scope:
        raise ValueError("Evaluation scope disagrees with archived case provenance")
    for version in ("baseline", "candidate"):
        values = [
            Prediction.model_validate(json.loads(line))
            for line in contents[f"{version}_predictions"].decode("utf-8").splitlines()
            if line
        ]
        predictions = index_predictions(values, all_cases)
        metrics = score_cases(selected, predictions)
        declared = report.get("versions", {}).get(version, {})
        if metrics != declared.get("overall") or evaluate_gates(metrics, config.gates) != declared.get("gates"):
            raise ValueError("Evaluation metrics disagree with archived inputs and configured gates")
    return contents
