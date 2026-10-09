"""The user's list of company boards (data/local/boards.yaml) and the source-scope title filter.

`title_include` narrows which postings are worth tagging at all. It is a source setting, not part of
the intent model, and is never changed by calibration.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from .domain import InvariantError, Job


class BoardSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: Literal["greenhouse", "lever", "ashby"]
    board: str
    company: str = ""
    title_include: tuple[str, ...] = ()

    def keeps(self, job: Job) -> bool:
        title = job.title.casefold()
        return not self.title_include or any(word.casefold() in title for word in self.title_include)


def load_boards(path: Path) -> list[BoardSpec]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return [BoardSpec.model_validate(item) for item in data.get("boards", [])]
    except (OSError, yaml.YAMLError, ValidationError, AttributeError) as error:
        raise InvariantError(f"Invalid boards file {path}: {error}") from error
