"""The applicant's own files for applying (spec 4.10), read from `data/local/<applicant_dir>/applicant.yaml`.

Contact details live only in the ApplyPilot-format profile it names; this module never reads that profile, and
nothing here is stored in the database. Paths are relative to the applicant directory or absolute (a personal
knowledge base elsewhere on the machine).
"""

import hashlib
import re
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from .domain import InvariantError

FILE = "applicant.yaml"
# Written only by adopt_tailored_resume (specs/resume-tailor.md 3): one resume per job, kept next to FILE.
TAILORED_FILE = "tailored_resumes.yaml"


# One line of English that goes into the apply agent's instructions as it is.
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\r\n]+$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Resume(_Model):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    pdf: Path
    text: Path  # plain text of the same resume: cover letters are checked against it
    use_for: str = ""  # when to choose this one, for the agent
    tex: Path | None = None  # LaTeX source of the same resume: only these can be tailored (specs/resume-tailor.md)
    job_id: str | None = None  # set on a tailored resume: it is used for this job only
    sha256: str | None = None  # set on a tailored resume: the adopted PDF, checked before every use


class Applicant(_Model):
    folder: Path
    profile: Path
    resumes: list[Resume] = Field(min_length=1)
    resume_selection: Path | None = None  # optional rules file for choosing a resume
    cover_letter_skill: Path | None = None  # optional own skill directory (with SKILL.md)
    cover_letter_context: list[Path] = []  # optional extra files that skill needs
    careful_companies: list[Line]  # applications to these always stop before submit
    apply_agent_read: list[Path] = []  # folders the apply agent may read (read-only), e.g. a knowledge base
    onsite_ok: list[Line] = []  # where on-site or hybrid jobs are fine; empty: ApplyPilot's single profile city
    relocation: Line | None = None  # the answer to relocation questions; None: ApplyPilot's "cannot relocate"
    chrome_user_data_dir: Path | None = None  # the applicant's own Chrome instance (decision 33); None: dedicated
    open_answers: Path | None = None  # the open-answer bank (specs/open-answers.md, decision 35); None: no bank
    resume_tailor: Path | None = None  # resume tailoring settings (specs/resume-tailor.md); None: not set up

    @field_validator("careful_companies")
    @classmethod
    def meaningful_companies(cls, names):
        if any(unknown_company(name) for name in names):
            raise ValueError("careful_companies entries must identify a company, not only punctuation/legal suffixes")
        return names

    def resume(self, resume_id: str) -> Resume:
        for resume in self.resumes:
            if resume.id == resume_id:
                return resume
        raise InvariantError(f"Unknown resume {resume_id!r}; choose from {[r.id for r in self.resumes]}")

    def tailored_for(self, job_id: str) -> Resume | None:
        return next((r for r in self.resumes if r.job_id == job_id), None)

    def resume_for(self, job_id: str, resume_id: str) -> Resume:
        """The resume to use for this job. A tailored one belongs to one job and must be the PDF that was adopted."""
        resume = self.resume(resume_id)
        if resume.job_id is not None and resume.job_id != job_id:
            raise InvariantError(f"{resume_id} is tailored for job {resume.job_id}, not {job_id}")
        if resume.sha256 is not None and (
            not resume.pdf.is_file()
            or not resume.text.is_file()
            or hashlib.sha256(resume.pdf.read_bytes()).hexdigest() != resume.sha256
        ):
            raise InvariantError(
                f"The tailored resume for {job_id} changed or is gone since it was adopted. For a job not tried "
                "yet: adopt it again (adopt_tailored_resume) and save the cover letter again. For a job already "
                "held: restore the adopted PDF, or leave the job out (mark_applications)"
            )
        return resume

    def missing_files(self) -> list[str]:
        own = [r for r in self.resumes if r.job_id is None]  # a tailored one is checked where it is used
        paths = [self.profile, *(p for r in own for p in (r.pdf, r.text)), *self.cover_letter_context]
        paths += [r.tex for r in self.resumes if r.tex]
        if self.resume_selection:
            paths.append(self.resume_selection)
        if self.cover_letter_skill:
            paths.append(self.cover_letter_skill / "SKILL.md")
        if self.open_answers:
            paths.append(self.open_answers)
        if self.resume_tailor:
            paths.append(self.resume_tailor)
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
    except (ValidationError, yaml.YAMLError, TypeError) as error:
        raise InvariantError(f"Invalid {path}: {error}") from error

    def resolve(value: Path | None) -> Path | None:
        if value is None:
            return None
        value = Path(value).expanduser()
        return value if value.is_absolute() else base / value

    if any(r.job_id is not None or r.sha256 is not None for r in applicant.resumes):
        raise InvariantError(f"Invalid {path}: job_id and sha256 are set only by adopt_tailored_resume")
    applicant = applicant.model_copy(update={"resumes": [*applicant.resumes, *_tailored(base)]})
    ids = [resume.id for resume in applicant.resumes]
    if len(ids) != len(set(ids)):
        raise InvariantError(f"Resume ids in {path} must be unique")
    return applicant.model_copy(
        update={
            "profile": resolve(applicant.profile),
            "resumes": [
                r.model_copy(update={"pdf": resolve(r.pdf), "text": resolve(r.text), "tex": resolve(r.tex)})
                for r in applicant.resumes
            ],
            "resume_selection": resolve(applicant.resume_selection),
            "cover_letter_skill": resolve(applicant.cover_letter_skill),
            "cover_letter_context": [resolve(p) for p in applicant.cover_letter_context],
            "apply_agent_read": [resolve(p) for p in applicant.apply_agent_read],
            "chrome_user_data_dir": applicant.chrome_user_data_dir and applicant.chrome_user_data_dir.expanduser(),
            "open_answers": resolve(applicant.open_answers),
            "resume_tailor": resolve(applicant.resume_tailor),
        }
    )


TAILORED_RECOVERY = "delete it to withdraw every adoption (those jobs go back to your own resumes)"


def read_tailored(base: Path) -> list[dict]:
    """The raw entries of the tailored-resume file; a broken file says how to recover."""
    path = base / TAILORED_FILE
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(raw, list):
            raise TypeError("not a list")
        resumes = [Resume.model_validate(item) for item in raw]
    except (ValidationError, yaml.YAMLError, TypeError) as error:
        raise InvariantError(
            f"Invalid {path} (written by adopt_tailored_resume): {error}; {TAILORED_RECOVERY}"
        ) from error
    if any(r.job_id is None or r.sha256 is None or not r.id.startswith("tailored-") for r in resumes):
        raise InvariantError(
            f"Invalid {path}: every entry needs a tailored- id, a job_id and a sha256; {TAILORED_RECOVERY}"
        )
    return raw


def _tailored(base: Path) -> list[Resume]:
    """Adopted tailored resumes (absolute paths, one per job). Ids start with tailored- and never clash."""
    return [Resume.model_validate(item) for item in read_tailored(base)]


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


# General legal suffixes, not a company alias database. Strip only trailing whole tokens.
_LEGAL_SUFFIX = re.compile(r"(?:\s|^)(?:incorporated|inc|corporation|corp|limited|ltd|llc|llp|plc|gmbh|co|company)$")
_UNKNOWN_COMPANIES = {
    "",
    "unknown",
    "na",
    "none",
    "null",
    "undisclosed",
    "confidential",
    "notavailable",
    "nan",
    "undefined",
    "notdisclosed",
    "notprovided",
    "unspecified",
    "未知",
    "保密",
    "未公开",
    "某公司",
    "stealth",
    "private",
    "anonymous",
    "companyconfidential",
    "hiringcompany",
    "hiring",
    "stealthstartup",
    "confidentialemployer",
}


def company_key(company: str) -> str:
    text = re.sub(r"[^\w\s]", " ", (company or "").casefold())
    text = " ".join(text.split())
    # Dotted legal abbreviations have the same meaning as their undotted forms.
    text = re.sub(r"\bl l c$", "llc", text)
    text = re.sub(r"\bl l p$", "llp", text)
    while (shorter := _LEGAL_SUFFIX.sub("", text).strip()) != text:
        text = shorter
    return "".join(ch for ch in text if ch.isalnum())


def unknown_company(company: str) -> bool:
    """Generic unidentified-employer markers; no guesses about corporate relationships."""
    words = re.sub(r"[^\w]", " ", (company or "").casefold()).split()
    return (
        company_key(company) in _UNKNOWN_COMPANIES
        or bool({"confidential", "stealth", "undisclosed", "anonymous"}.intersection(words))
        or "not disclosed" in " ".join(words)
    )


def careful(company: str, names: list[str]) -> bool:
    """Fail closed for unknown companies; retain conservative containment after legal-suffix normalization.
    No inferred corporate relationships. False positives stop more applications, never fewer."""
    key = company_key(company)
    if unknown_company(company):
        return True
    return any(not (other := company_key(name)) or other in key or key in other for name in names)
