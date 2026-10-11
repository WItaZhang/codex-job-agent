"""The user's list of company boards (data/local/boards.yaml) and the source-scope filters.

`title_include` and `country_include` narrow which postings are worth tagging at all. They are source
settings, not part of the intent model, and are never changed by calibration.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from . import avature
from .domain import InvariantError, Job


class BoardSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["greenhouse", "lever", "ashby", "avature"]
    board: str  # a board token; for avature, the site's HTTPS address
    company: str = ""
    title_include: tuple[str, ...] = ()
    country_include: tuple[str, ...] = ()  # avature only: whole comma-separated parts of the location

    @field_validator("board")
    @classmethod
    def _site_address(cls, board: str, info) -> str:
        return avature.site_url(board) if info.data.get("provider") == "avature" else board

    @model_validator(mode="after")
    def _country_filter_only_on_avature(self) -> "BoardSpec":
        if self.country_include and self.provider != "avature":
            raise ValueError("country_include is only available for avature boards")
        return self

    def keeps(self, job: Job) -> bool:
        title = job.title.casefold()
        if self.title_include and not any(word.casefold() in title for word in self.title_include):
            return False
        if not self.country_include or not job.location.strip():
            return True  # an unknown location is kept rather than lost
        parts = {part.strip().casefold() for part in job.location.split(",")}
        return any(country.strip().casefold() in parts for country in self.country_include)


def load_boards(path: Path) -> list[BoardSpec]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return [BoardSpec.model_validate(item) for item in data.get("boards", [])]
    except (OSError, yaml.YAMLError, ValidationError, AttributeError) as error:
        raise InvariantError(f"Invalid boards file {path}: {error}") from error
