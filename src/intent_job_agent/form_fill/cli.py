"""The form-fill local command (slice-1 plan sections 1 and 3).

    uv run python -m intent_job_agent.form_fill.cli report <run id>

checks the pages saved under data/local/form-fill/runs/<run id>/ against answers.yaml again and writes <job>.json and
summary.md next to them. It opens no browser, visits no site and never reads the agents' logs (log.jsonl). Only code
reads profile.json, for the redaction and for contact fields that are never masked (state, country).
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .. import applicant as applicant_files
from ..config import Settings
from ..domain import InvariantError
from . import runner
from .compare import placeholder
from .inputs import load_answers, profile_field_paths
from .models import AnswerEntry, ReadField
from .redact import Redactor
from .report import job_report, write_reports

META_FILE, READBACK_FILE = runner.META_FILE, "readback.json"


def plain_contacts(answers: dict[str, AnswerEntry], profile: dict, redactor: Redactor) -> dict[str, AnswerEntry]:
    """Contact entries whose profile value saved pages never mask (state, country, a city too short to mask) get the
    value as their answer, compared as text."""
    out = dict(answers)
    for key, entry in answers.items():
        if entry.category != "contact" or not entry.from_profile:
            continue
        value = runner._profile_value(profile, entry.from_profile)
        if value and value.strip() and (placeholder(entry.from_profile) is None or redactor.mask(value) == value):
            out[key] = entry.model_copy(update={"answer": value.strip(), "kind": "text"})
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
    fields = ("answer", "accept", "otherwise", "record", "record_accept", "record_otherwise", "aliases")
    return {
        key: entry.model_copy(update={name: _mask(redactor, getattr(entry, name)) for name in fields})
        for key, entry in answers.items()
    }


def load_sheet(data_dir: Path, settings: Settings) -> tuple[dict[str, AnswerEntry], Redactor]:
    """answers.yaml checked and resolved as for a run, masked like the saved pages, and the redactor built from
    profile.json."""
    applicant = applicant_files.load(Path(data_dir), settings.apply.applicant_dir)
    fields = profile_field_paths(applicant.profile)
    profile = json.loads(applicant.profile.read_text(encoding="utf-8"))
    redactor = Redactor.from_profile(profile)
    values = runner.applicant_values(applicant)
    answers = load_answers(applicant.folder / settings.form_fill.answers_file, fields, set(values))
    answers = mask_sheet(runner.resolve(answers, profile, values), redactor)
    return plain_contacts(answers, profile, redactor), redactor


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
    answers, redactor = load_sheet(data_dir, settings)
    return write_reports(run_dir, [job_report(meta, fields, answers) for meta, fields in jobs], redactor)


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
