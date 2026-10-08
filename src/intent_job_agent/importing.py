"""Reading user-provided files and validating agent-submitted job tags."""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from .domain import InvariantError, Job, Salary, Vocabulary, parse_ref


def inside(data_dir: Path, path: str) -> Path:
    """Resolve `path` and refuse anything outside the configured data directory."""
    resolved, root = Path(path).resolve(), data_dir.resolve()
    if root != resolved and root not in resolved.parents:
        raise InvariantError(f"{path} is outside the data directory {data_dir}")
    if not resolved.is_file():
        raise InvariantError(f"{path} is not a file")
    return resolved


class RawJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    title: str
    company: str = ""
    url: str = ""
    description: str = ""
    salary: Salary | None = None


def parse_jobs(path: Path) -> list[Job]:
    """A JSON array of postings. Jobs start untagged; ids are stable for the same posting."""
    try:
        raw = [RawJob.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    except (ValueError, ValidationError) as error:
        raise InvariantError(f"Invalid jobs file {path}: {error}") from error
    jobs = []
    for item in raw:
        identity = item.url or f"{item.company}\n{item.title}\n{item.description}"
        job_id = item.id or "job-" + hashlib.sha256(identity.encode()).hexdigest()[:12]
        jobs.append(Job(id=job_id, tags=[], **item.model_dump(exclude={"id"})))
    return jobs


def normalize_tags(vocab: Vocabulary, tags: list[str]) -> tuple[list[str], list[str]]:
    """Canonical tags (aliases resolved) and the ones not yet in the vocabulary."""
    canonical = list(dict.fromkeys(vocab.canonical(parse_ref(tag).ref) for tag in tags))
    return canonical, [tag for tag in canonical if not vocab.has(tag)]
