"""Load frozen cases, observations and external judgements with leakage checks."""

import json
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from .schemas import Case, EvaluationConfig, Prediction, QualityLabel

Record = TypeVar("Record", bound=BaseModel)


class EvaluationDataError(ValueError):
    """Malformed or contradictory evaluation inputs."""


def load_config(path: Path) -> EvaluationConfig:
    try:
        return EvaluationConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (ValidationError, yaml.YAMLError) as exc:
        raise EvaluationDataError(f"Invalid evaluation config {path}: {exc}") from exc


def load_jsonl(path: Path, record_type: type[Record]) -> list[Record]:
    records: list[Record] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(record_type.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise EvaluationDataError(f"{path}:{line_number}: {exc}") from exc
    return records


def validate_cases(cases: list[Case]) -> None:
    if not cases:
        raise EvaluationDataError("At least one evaluation case is required")
    seen_tasks: set[str] = set()
    group_splits: dict[str, str] = {}
    for case in cases:
        if case.task_id in seen_tasks:
            raise EvaluationDataError(f"Duplicate case task_id: {case.task_id}")
        seen_tasks.add(case.task_id)
        previous_split = group_splits.setdefault(case.group_id, case.split)
        if previous_split != case.split:
            raise EvaluationDataError(f"Group {case.group_id} leaks across dev and holdout")


def index_predictions(predictions: list[Prediction], cases: list[Case]) -> dict[str, Prediction]:
    known_tasks = {case.task_id for case in cases}
    indexed: dict[str, Prediction] = {}
    for prediction in predictions:
        if prediction.task_id not in known_tasks:
            raise EvaluationDataError(f"Prediction references unknown task: {prediction.task_id}")
        if prediction.task_id in indexed:
            raise EvaluationDataError(f"Duplicate prediction task_id: {prediction.task_id}")
        indexed[prediction.task_id] = prediction
    return indexed


def validate_quality_labels(labels: list[QualityLabel], cases: list[Case]) -> None:
    known_tasks = {case.task_id for case in cases}
    seen: set[tuple[str, str]] = set()
    for label in labels:
        if label.task_id not in known_tasks:
            raise EvaluationDataError(f"Quality label references unknown task: {label.task_id}")
        key = (label.task_id, label.reviewer_id)
        if key in seen:
            raise EvaluationDataError(f"Duplicate reviewer judgement: {key}")
        seen.add(key)


def resolve_input(config_path: Path, configured_path: str) -> Path:
    """All paths are relative to the YAML file, never the shell's working directory."""
    path = Path(configured_path)
    return path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()
