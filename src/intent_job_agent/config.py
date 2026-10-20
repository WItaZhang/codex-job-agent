"""Settings loaded from configs/*.yaml. No parameter has a default in code."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


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


class AvatureDiscovery(_Section):
    request_delay_seconds: float
    max_pages: int


class Discovery(_Section):
    timeout_seconds: float
    boards_file: str
    untagged_batch: int
    full_text_chars: int
    applypilot_dir: str
    enrich_limit: int
    proxy_env: str
    avature: AvatureDiscovery


class Tagging(_Section):
    daily_budget: int
    backfill_dimensions: list[str]


class HardReview(_Section):
    every_days: int


class ApplyList(_Section):
    agreement_window_days: int = Field(gt=0)
    shown: int = Field(gt=0)
    dir: str


class Duplicates(_Section):
    shingle_words: int = Field(gt=0)
    overlap: float = Field(gt=0, le=1)


class ApplyMCP(_Section):
    playwright: str  # npm package with a pinned version (ApplyPilot runs @latest)
    gmail: str


class CoverLetterRules(_Section):
    min_words: int = Field(gt=0)
    max_words: int = Field(gt=0)
    max_attempts: int = Field(gt=0)
    read_chars: int = Field(gt=0)


class Apply(_Section):
    applicant_dir: str
    batch_size: int = Field(gt=0)
    review_first: int = Field(ge=0)
    model: str
    job_timeout_seconds: int = Field(gt=0)
    letters_dir: str
    logs_dir: str
    cover_letter: CoverLetterRules
    mcp: ApplyMCP
    blocked_origins: list[str]
    builtin_tools: str
    file_tools: str
    permission_mode: str
    allowed_tools: list[str]
    disallowed_tools: list[str]
    chrome_profile_strip: list[str]


class FormFill(_Section):
    """The form-fill module (specs/form-fill.md); its model and timeout are apply.*'s."""

    dir: str  # under storage.data_dir: runs/<run>/<job>/ and the run's feasibility.md
    recordings_dir: str  # under dir: recordings/<recording id>/ (spec 6.2)
    answers_file: str  # under apply.applicant_dir
    checklist_file: str  # under apply.applicant_dir
    disallowed_tools: list[str]  # refused on top of apply.disallowed_tools
    checkpoint_timeout_seconds: float  # a checkpoint call answers within this (spec 6.1 item 3)
    watch_interval_seconds: float  # the background read of the job's tabs (spec 4.2)
    stuck_after: int  # filled checkpoints in a row on one page before it says stop (spec 4.2)
    network_max_mb: int  # the first page's network record stops growing here (spec 6.2)


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
    tagging: Tagging
    hard_review: HardReview
    apply_list: ApplyList
    duplicates: Duplicates
    apply: Apply
    form_fill: FormFill
    llm: LLM
    storage: Storage

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
