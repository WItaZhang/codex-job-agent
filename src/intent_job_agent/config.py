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


class ApplyDone(_Section):
    """The done standard (spec section 9) that apply_stats checks a week against."""

    weekly_target: int = Field(gt=0)  # successful submissions a week
    success_rate: float = Field(gt=0, le=1)  # submitted / (submitted + not submitted)
    allowed: list[str]  # failure categories that are fine (apply_fill.CATEGORIES)


class ApplyWatchdog(_Section):
    """Every wait of an apply step has a limit (spec section 10 items 107, 108, 114)."""

    stall_seconds: float = Field(gt=0)  # an agent silent this long is stalled: it and its processes are ended
    drain_seconds: float = Field(gt=0)  # after the agent quits, how long its last output may still take
    read_seconds: float = Field(gt=0)  # one read of a tab by code (read back, the glance before a resume)
    interval_seconds: float = Field(gt=0)  # how often the agent's processes and the applying Chrome are looked at
    down_after: int = Field(gt=0)  # looks in a row without an answer before the applying Chrome counts as gone


class Apply(_Section):
    browser_lock_seconds: float = Field(default=5, gt=0)
    browser_start_seconds: float = Field(default=20, gt=0)
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
    done: ApplyDone
    notify: bool  # a macOS notification when a step ends and jobs wait for the user (spec 4.10 item 10)
    watchdog: ApplyWatchdog


class FormFill(_Section):
    """The form-fill module (specs/form-fill.md); its model and timeout are apply.*'s."""

    dir: str  # under storage.data_dir: runs/<run>/<job>/ and the run's feasibility.md
    recordings_dir: str  # under dir: recordings/<recording id>/ (spec 6.2)
    answers_file: str  # under apply.applicant_dir
    checklist_file: str  # under apply.applicant_dir
    lists_dir: str  # under dir: the job lists, /apply's one per step (spec section 10 item 87)
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
    execution_mode: Literal["legacy", "local_only"] = "legacy"

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
        from .workspace import MARKER, load_workspace

        original = Path(path).expanduser().absolute()
        parent = original.parent.resolve()
        if (parent / MARKER).exists() or (parent / MARKER).is_symlink():
            from .domain import InvariantError

            if original.name != "settings.yaml" or original.is_symlink():
                raise InvariantError("Marked workspace requires a regular canonical settings.yaml")
            return load_workspace(parent)
        path = original.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Configuration does not exist: {path}")
        if (path.parent / MARKER).exists():
            if path.name != "settings.yaml":
                from .domain import InvariantError

                raise InvariantError("Marked workspace requires its canonical settings.yaml")
            return load_workspace(path.parent)
        settings = cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if settings.execution_mode == "local_only":
            from .domain import InvariantError

            raise InvariantError("Local-only configuration requires an initialized workspace")
        settings.validate_storage_boundary()
        return settings

    def validate_storage_boundary(self, *extra_paths: str | Path) -> None:
        """Legacy settings cannot reopen marked workspace storage as unrestricted."""
        if self.execution_mode != "legacy":
            return
        from .domain import InvariantError
        from .workspace import MARKER

        for value in (self.storage.data_dir, self.storage.db_path, *extra_paths):
            path = Path(value).expanduser().resolve()
            for ancestor in (path, *path.parents):
                marker = ancestor / MARKER
                if marker.exists() or marker.is_symlink():
                    raise InvariantError(
                        f"Legacy configuration addresses a marked workspace at {ancestor}; use --workspace"
                    )

    def require_external(self, operation: str, *paths: str | Path) -> None:
        from .domain import InvariantError

        self.validate_storage_boundary(*paths)
        if self.execution_mode == "local_only":
            raise InvariantError(f"R1 local-only workspace: {operation} is disabled pending R4/R5")
