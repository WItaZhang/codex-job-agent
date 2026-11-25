"""The applicant's resume_tailor.yaml (specs/resume-tailor.md section 2). Paths are relative to that file's folder,
or absolute (for example a personal knowledge base)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..domain import InvariantError


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Checks(_Model):
    """The code checks a version must pass (section 4). Everything personal is configured here."""

    must_keep: list[str] = []  # entries that must stay, matched by name in an entry macro
    approved_only_sections: list[str] = []  # entries whose items may only use approved numbers
    no_arrows: bool = False
    no_doubled_verbs: bool = False
    max_bold_per_item: int | None = Field(default=None, gt=0)
    no_full_bold: bool = False


class Template(_Model):
    """How entries and items are written in the applicant's LaTeX template."""

    entry_macros: list[str] = Field(default=["roleentry", "projectentry", "publicationentry"], min_length=1)
    items_env: str = "cvitems"


class TailorFile(_Model):
    roles: dict[str, Path] = Field(min_length=1)  # direction -> what that role needs
    role_sections: dict[str, dict[str, str]] = {}  # direction -> {entry name: direction it is judged against}
    style: Path | None = None  # the applicant's writing rules: for the writing agent only, never the evaluator
    approved_numbers: Path | None = None  # numbers the applicant confirmed, one per line
    rubric: Path | None = None  # None: the project's general rubric
    evidence_read: list[Path] = []  # folders the writing agent may read for evidence

    checks: Checks = Checks()
    template: Template = Template()

    @model_validator(mode="after")
    def known_directions(self):
        for direction, sections in self.role_sections.items():
            unknown = {direction, *sections.values()} - self.roles.keys()
            if unknown:
                raise ValueError(f"role_sections names directions missing from roles: {sorted(unknown)}")
        return self

    @classmethod
    def load(cls, path: Path, check_files: bool = True) -> "TailorFile":
        path = Path(path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            cfg = cls.model_validate(raw)
        except (OSError, ValidationError, yaml.YAMLError, TypeError) as error:
            raise InvariantError(f"Invalid resume_tailor settings {path}: {error}") from error
        base = path.parent

        def resolve(value: Path | None) -> Path | None:
            if value is None:
                return None
            value = Path(value).expanduser()
            return value if value.is_absolute() else base / value

        cfg = cfg.model_copy(
            update={
                "roles": {d: resolve(p) for d, p in cfg.roles.items()},
                "style": resolve(cfg.style),
                "approved_numbers": resolve(cfg.approved_numbers),
                "rubric": resolve(cfg.rubric),
                "evidence_read": [resolve(p) for p in cfg.evidence_read],
            }
        )
        if check_files:
            missing = [str(p) for p in cfg.files() if not p.is_file()]
            missing += [str(p) for p in cfg.evidence_read if not p.is_dir()]
            if missing:
                raise InvariantError(f"resume_tailor settings name missing files or folders: {missing}")
        return cfg

    def files(self) -> list[Path]:
        return [*self.roles.values(), *(p for p in (self.style, self.approved_numbers, self.rubric) if p)]

    def approved(self) -> set[str]:
        from .checks import NUMBER

        if not self.approved_numbers or not self.approved_numbers.is_file():
            return set()
        return set(NUMBER.findall(self.approved_numbers.read_text(encoding="utf-8")))
