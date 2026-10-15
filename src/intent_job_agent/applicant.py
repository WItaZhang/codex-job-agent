"""The applicant's own files for applying (spec 4.10), read from `data/local/<applicant_dir>/applicant.yaml`.

Contact details live only in the ApplyPilot-format profile it names; this module never reads that profile, and
nothing here is stored in the database. Paths are relative to the applicant directory or absolute (a personal
knowledge base elsewhere on the machine).
"""

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .domain import InvariantError

FILE = "applicant.yaml"


# One line of English that goes into the apply agent's instructions as it is.
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\r\n]+$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Resume(_Model):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    pdf: Path
    text: Path  # plain text of the same resume: cover letters are checked against it
    use_for: str = ""  # when to choose this one, for the agent


class Applicant(_Model):
    folder: Path
    profile: Path
    resumes: list[Resume] = Field(min_length=1)
    resume_selection: Path | None = None  # optional rules file for choosing a resume
    cover_letter_skill: Path | None = None  # optional own skill directory (with SKILL.md)
    cover_letter_context: list[Path] = []  # optional extra files that skill needs
    careful_companies: list[str] = []  # applications to these always stop before submit
    apply_agent_read: list[Path] = []  # folders the apply agent may read (read-only), e.g. a knowledge base
    onsite_ok: list[Line] = []  # where on-site or hybrid jobs are fine; empty: ApplyPilot's single profile city
    relocation: Line | None = None  # the answer to relocation questions; None: ApplyPilot's "cannot relocate"

    def resume(self, resume_id: str) -> Resume:
        for resume in self.resumes:
            if resume.id == resume_id:
                return resume
        raise InvariantError(f"Unknown resume {resume_id!r}; choose from {[r.id for r in self.resumes]}")

    def missing_files(self) -> list[str]:
        paths = [self.profile, *(p for r in self.resumes for p in (r.pdf, r.text)), *self.cover_letter_context]
        if self.resume_selection:
            paths.append(self.resume_selection)
        if self.cover_letter_skill:
            paths.append(self.cover_letter_skill / "SKILL.md")
        return [str(path) for path in paths if not path.is_file()]


def folder(data_dir: Path, applicant_dir: str) -> Path:
    return Path(data_dir) / applicant_dir


def load(data_dir: Path, applicant_dir: str) -> Applicant:
    base = folder(data_dir, applicant_dir)
    path = base / FILE
    if not path.is_file():
        raise InvariantError(f"{path} is missing; see docs/examples/applicant/{FILE}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = {**raw, "folder": base}
        applicant = Applicant.model_validate(raw)
    except (ValidationError, yaml.YAMLError) as error:
        raise InvariantError(f"Invalid {path}: {error}") from error

    def resolve(value: Path | None) -> Path | None:
        if value is None:
            return None
        value = Path(value).expanduser()
        return value if value.is_absolute() else base / value

    ids = [resume.id for resume in applicant.resumes]
    if len(ids) != len(set(ids)):
        raise InvariantError(f"Resume ids in {path} must be unique")
    return applicant.model_copy(
        update={
            "profile": resolve(applicant.profile),
            "resumes": [
                r.model_copy(update={"pdf": resolve(r.pdf), "text": resolve(r.text)}) for r in applicant.resumes
            ],
            "resume_selection": resolve(applicant.resume_selection),
            "cover_letter_skill": resolve(applicant.cover_letter_skill),
            "cover_letter_context": [resolve(p) for p in applicant.cover_letter_context],
            "apply_agent_read": [resolve(p) for p in applicant.apply_agent_read],
        }
    )


def read_dir_problems(applicant: Applicant, data_dir: Path, home: Path | None = None) -> list[str]:
    """Why a folder may not be opened to the apply agent (spec 4.10 item 6): it must exist and must not be the
    root or the home folder, contain or sit inside the data directory, or contain the applicant profile."""
    home = (home or Path.home()).resolve()
    data = Path(data_dir).resolve()
    profile = applicant.profile.resolve()
    problems = []
    for folder in applicant.apply_agent_read:
        path = folder.resolve()
        if not path.is_dir():
            problems.append(f"{folder} is not a folder")
        elif path == Path(path.anchor) or path == home:
            problems.append(f"{folder} is the root or home folder")
        elif path == data or path in data.parents or data in path.parents:
            problems.append(f"{folder} contains or is inside the data directory {data}")
        elif path in profile.parents:
            problems.append(f"{folder} contains the applicant profile {profile}")
    return problems


def careful(company: str, names: list[str]) -> bool:
    """Whether a company is on the stop-before-submit list (case and punctuation ignored; "Stripe" matches
    "Stripe, Inc."). A loose match only ever stops more applications, never fewer."""
    simple = "".join(ch for ch in company.casefold() if ch.isalnum())
    return any(key and key in simple for key in ("".join(ch for ch in n.casefold() if ch.isalnum()) for n in names))
