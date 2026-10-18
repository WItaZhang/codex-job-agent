"""The form-fill local command (slice-1 plan sections 1 and 3).

    uv run python -m intent_job_agent.form_fill.cli report <run id>

checks the pages saved under data/local/form-fill/runs/<run id>/ against answers.yaml again and writes <job>.json and
summary.md next to them. It opens no browser, visits no site and never reads the agents' logs (log.jsonl). Only code
reads profile.json, for the redaction and for contact fields that are never masked (state, country).

Pages saved before redact.VERSION (no "redaction" in meta.json) were masked with one placeholder per kind and wrote
anyone's e-mail as [email]: they are checked against a sheet masked the same old way, and their contact fields are
not reviewed (decision 20).
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .. import applicant as applicant_files
from ..config import Settings
from ..domain import InvariantError
from . import runner
from .compare import MARK, placeholder
from .inputs import load_answers, profile_field_paths
from .models import AnswerEntry, ReadField
from .redact import VERSION, Redactor
from .report import job_report, write_reports

META_FILE, READBACK_FILE = runner.META_FILE, "readback.json"


def plain_contacts(answers: dict[str, AnswerEntry], profile: dict, redactor: Redactor) -> dict[str, AnswerEntry]:
    """Contact entries, as saved pages show them.
    - A profile value saved pages never mask (state, country, a city too short to mask) becomes the answer, compared
      as text; masked as the page would mask it, so a state that equals the city reads "[city]" on both sides.
    - A masked value whose profile value another field shares (portfolio URL = GitHub URL) also accepts that
      field's placeholder: the page shows whichever the redactor applied."""
    out = dict(answers)
    for key, entry in answers.items():
        if entry.category != "contact" or not entry.from_profile:
            continue
        value = (runner._profile_value(profile, entry.from_profile) or "").strip()
        if not value:
            continue
        masked = redactor.mask(value)
        mark = placeholder(entry.from_profile)
        if mark is None or masked == value:
            out[key] = entry.model_copy(update={"answer": masked, "kind": "text"})
        else:
            update = {}
            shared = tuple(m for m in MARK.findall(masked) if m != mark)
            if shared and masked.strip() in shared:
                update["accept"] = shared
            if entry.from_profile in ("personal.city", "personal.address"):  # only the applicant's own state, country
                places = (runner._profile_value(profile, f"personal.{f}") or "" for f in ("province_state", "country"))
                update["aliases"] = tuple(place.strip() for place in places)  # (state, country), "" when not given
            if update:
                out[key] = out[key].model_copy(update=update)
    return out


def _mask(redactor: Redactor, value):
    if isinstance(value, str):
        return redactor.mask(value)
    if isinstance(value, tuple):
        return tuple(redactor.mask(v) for v in value)
    if isinstance(value, dict):
        return {k: _mask(redactor, v) for k, v in value.items()}
    return value


def mask_sheet(answers: dict[str, AnswerEntry], redactor: Redactor) -> dict[str, AnswerEntry]:
    """The answer sheet masked as the saved pages are, so both sides compare alike: the applicant's city inside a
    school name ("University of California, [city]") or as a job's city ("[city]")."""
    fields = ("answer", "accept", "otherwise", "record", "record_accept", "record_otherwise", "aliases", "asks")
    return {
        key: entry.model_copy(update={name: _mask(redactor, getattr(entry, name)) for name in fields})
        for key, entry in answers.items()
    }


@dataclass(frozen=True)
class Sheet:
    """The answer sheet masked for pages saved now (`current`) and before redact.VERSION (`legacy`)."""

    current: dict[str, AnswerEntry]
    legacy: dict[str, AnswerEntry]
    redactor: Redactor

    def for_page(self, meta: dict) -> tuple[dict[str, AnswerEntry], bool]:
        """(sheet, whether contact fields can be checked) for a job's page."""
        if meta.get("redaction") == VERSION:
            return self.current, True
        return self.legacy, False


def load_sheet(data_dir: Path, settings: Settings) -> Sheet:
    """answers.yaml checked and resolved as for a run, masked like the saved pages, and the redactor built from
    profile.json. Errors are masked too: a contact value written into answers.yaml by mistake is never echoed."""
    applicant = applicant_files.load(Path(data_dir), settings.apply.applicant_dir)
    fields = profile_field_paths(applicant.profile)
    profile = json.loads(applicant.profile.read_text(encoding="utf-8"))
    redactor, old = Redactor.from_profile(profile), Redactor.from_profile(profile, legacy=True)
    values = runner.applicant_values(applicant)
    try:
        answers = load_answers(applicant.folder / settings.form_fill.answers_file, fields, set(values))
        answers = runner.resolve(answers, profile, values)
    except InvariantError as error:
        raise InvariantError(redactor.mask(str(error))) from None
    current = plain_contacts(mask_sheet(answers, redactor), profile, redactor)
    return Sheet(current, plain_contacts(mask_sheet(answers, old), profile, old), redactor)


def _broken(job_dir: Path, why: str) -> tuple[dict, tuple[ReadField, ...]]:
    return {"job_id": job_dir.name, "status": "system_error", "notes": [why]}, ()


def _job(job_dir: Path) -> tuple[dict, tuple[ReadField, ...]]:
    """A job folder's meta.json and read-back fields. A folder without a readable meta.json is still reported: the
    run may have stopped while this job was being filled."""
    if not (job_dir / META_FILE).is_file():
        return _broken(
            job_dir, f"没有 {META_FILE}：这个岗位可能还在填，或者运行中途断了。标签页可能停在任何一步，请你自己看。"
        )
    try:
        meta = json.loads((job_dir / META_FILE).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _broken(job_dir, f"{META_FILE} 读不出来。请你自己看标签页。")
    if not isinstance(meta, dict):
        return _broken(job_dir, f"{META_FILE} 读不出来。请你自己看标签页。")
    meta.setdefault("job_id", job_dir.name)
    readback = job_dir / READBACK_FILE
    if not readback.is_file():
        return meta, ()
    meta.setdefault("page_dir", str(job_dir))
    try:
        rows = json.loads(readback.read_text(encoding="utf-8"))
        return meta, tuple(ReadField.model_validate(row) for row in rows)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValidationError):
        return {**meta, "status": "readback_failed", "notes": [*meta.get("notes", []), f"{READBACK_FILE} 读不出来"]}, ()


def run_report(run_id: str, data_dir: Path, settings: Settings) -> Path:
    """Check every job saved under the run again and write the reports. Returns summary.md's path."""
    if not runner.SAFE_ID.match(run_id):
        raise InvariantError(f"not a run id: {run_id!r}")
    run_dir = Path(data_dir) / settings.form_fill.dir / "runs" / run_id
    if not run_dir.is_dir():
        raise InvariantError(f"no run {run_id} under {run_dir.parent}")
    jobs = [_job(job_dir) for job_dir in sorted(run_dir.iterdir()) if job_dir.is_dir()]
    if not jobs:
        raise InvariantError(f"run {run_id} has no job folders")
    jobs.sort(key=lambda job: str(job[0].get("started", "")))
    sheet = load_sheet(data_dir, settings)
    reports = []
    for meta, fields in jobs:
        answers, contacts = sheet.for_page(meta)
        reports.append(job_report(meta, fields, answers, contacts=contacts))
    return write_reports(run_dir, reports, sheet.redactor)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m intent_job_agent.form_fill.cli", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="check a saved run again and write its reports (no browser)")
    report.add_argument("run_id")
    report.add_argument("--config", default="configs/default.yaml")
    report.add_argument("--data-dir", help="defaults to storage.data_dir of the config")
    args = parser.parse_args(argv)
    settings = Settings.load(args.config)
    data_dir = Path(args.data_dir or settings.storage.data_dir)
    try:
        summary = run_report(args.run_id, data_dir, settings)
    except InvariantError as error:
        print(f"form-fill report: {error}", file=sys.stderr)
        return 2
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
