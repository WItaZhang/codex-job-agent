"""Configuration for an isolated, synthetic profile-update experiment."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class CodexSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executable: str
    model: str
    reasoning_effort: str
    timeout_seconds: float = Field(gt=0)
    config_overrides: dict[str, Any]


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    cases_path: Path
    logs_dir: Path
    seed: int
    repetitions: int = Field(ge=1)
    case_limit: int = Field(ge=1)
    turn_limit: int = Field(ge=1)
    concurrency: int = Field(ge=1, le=4)
    maximum_calls: int = Field(ge=1)
    max_calls_per_turn: int = Field(ge=1)
    langmem_max_steps: int = Field(ge=1)
    codex: CodexSettings
    profile_instructions: str = Field(min_length=1)


def load_config(path: Path) -> ExperimentConfig:
    path = path.resolve()
    result = ExperimentConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8-sig")))
    result.cases_path = (path.parent / result.cases_path).resolve()
    result.logs_dir = (path.parent / result.logs_dir).resolve()
    return result
