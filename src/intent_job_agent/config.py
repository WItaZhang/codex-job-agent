"""Settings loaded from configs/*.yaml. No parameter has a default in code."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Scoring(_Section):
    level_points: dict[Literal["strong_avoid", "avoid", "prefer", "strong_prefer"], float]
    recommend_threshold: float
    salary_below_floor_penalty: float
    salary_compare: Literal["max", "min"]


class Daily(_Section):
    recommended: int
    exploration: int
    exploration_max_from_avoid: int


class Analysis(_Section):
    max_causes: int
    max_flipped_shown: int


class Discovery(_Section):
    timeout_seconds: float
    boards_file: str
    untagged_batch: int


class HardReview(_Section):
    every_days: int


class LLM(_Section):
    provider: Literal["host", "fake", "anthropic", "openai_compatible"]
    model: str | None
    api_key_env: str | None


class Storage(_Section):
    db_path: str
    data_dir: str


class Settings(_Section):
    scoring: Scoring
    daily: Daily
    analysis: Analysis
    discovery: Discovery
    hard_review: HardReview
    llm: LLM
    storage: Storage

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
