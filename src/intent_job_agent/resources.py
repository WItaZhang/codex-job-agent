"""Read installed build-mapped resources; never search a source checkout."""

from importlib.resources import files
from pathlib import Path


def default_config() -> Path:
    return Path(str(files("intent_job_agent_data") / "default.yaml"))


def letter_skill() -> Path:
    return Path(str(files("intent_job_agent_data") / "cover-letter"))
