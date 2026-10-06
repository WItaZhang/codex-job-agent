"""Explicit YAML settings; paths resolve relative to the configuration file."""

from pathlib import Path

import yaml
from pydantic import Field

from .models import Policy, Record


class BrowserSettings(Record):
    headless: bool = True
    timeout_ms: int = Field(default=15000, gt=0)
    allowed_origins: list[str] = Field(default_factory=list)
    storage_state: Path | None = None


class MatchingSettings(Record):
    strong_threshold: float = Field(default=0.75, ge=0, le=1)
    minimum_token_overlap: int = Field(default=2, ge=1)
    avoided_term_penalty: float = Field(default=1, ge=0)


class QualitySettings(Record):
    enabled: bool = True
    # Relative to data_dir; never shared with source or experiment outputs.
    directory: Path = Path("quality")
    batch_size: int = Field(default=10, ge=1, le=1000)


class Settings(Record):
    data_dir: Path
    logs_dir: Path
    daily_job_limit: int = Field(default=100, ge=1, le=1000)
    high_fit_reserved: int = Field(default=20, ge=0)
    request_timeout_seconds: float = Field(default=20, gt=0)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    matching: MatchingSettings = Field(default_factory=MatchingSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    policy: Policy = Field(default_factory=Policy)


def load_settings(path: Path) -> Settings:
    path = path.resolve()
    with path.open(encoding="utf-8-sig") as handle:
        settings = Settings.model_validate(yaml.safe_load(handle))
    for key in ("data_dir", "logs_dir"):
        value = getattr(settings, key)
        setattr(settings, key, (path.parent / value).resolve())
    if settings.browser.storage_state is not None:
        settings.browser.storage_state = (path.parent / settings.browser.storage_state).resolve()
    if settings.high_fit_reserved > settings.daily_job_limit:
        raise ValueError("high_fit_reserved exceeds daily_job_limit")
    return settings
