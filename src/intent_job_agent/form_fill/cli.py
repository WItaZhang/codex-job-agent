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


def plain_contacts(answers: dict[str, AnswerEntry], profile: dict) -> dict[str, AnswerEntry]:
    """Contact entries whose profile field is never masked get its value as their answer, compared as text."""
    out = dict(answers)
    for key, entry in answers.items():
        if entry.category == "contact" and entry.from_profile and placeholder(entry.from_profile) is None:
            value = runner._profile_value(profile, entry.from_profile)
            if value and value.strip():
                out[key] = entry.model_copy(update={"answer": value.strip(), "kind": "text"})
    return out


def load_sheet(data_dir: Path, settings: Settings) -> tuple[dict[str, AnswerEntry], Redactor]:
    """answers.yaml checked and resolved as for a run, and the redactor built from profile.json."""
    applicant = applicant_files.load(Path(data_dir), settings.apply.applicant_dir)
    fields = profile_field_paths(applicant.profile)
    profile = json.loads(applicant.profile.read_text(encoding="utf-8"))
    values = runner.applicant_values(applicant)
    answers = load_answers(applicant.folder / settings.form_fill.answers_file, fields, set(values))
    answers = runner.resolve(answers, profile, values)
    return plain_contacts(answers, profile), Redactor.from_profile(profile)


def _job(job_dir: Path) -> tuple[dict, tuple[ReadField, ...]]:
    meta = json.loads((job_dir / META_FILE).read_text(encoding="utf-8"))
    meta.setdefault("job_id", job_dir.name)
    meta.setdefault("page_dir", str(job_dir))
    readback = job_dir / READBACK_FILE
    if not readback.is_file():
        return meta, ()
    rows = json.loads(readback.read_text(encoding="utf-8"))
    return meta, tuple(ReadField.model_validate(row) for row in rows)


def run_report(run_id: str, data_dir: Path, settings: Settings) -> Path:
    """Check every job saved under the run again and write the reports. Returns summary.md's path."""
    if not runner.SAFE_ID.match(run_id):
        raise InvariantError(f"not a run id: {run_id!r}")
    run_dir = Path(data_dir) / settings.form_fill.dir / "runs" / run_id
    if not run_dir.is_dir():
        raise InvariantError(f"no run {run_id} under {run_dir.parent}")
    jobs = [_job(job_dir) for job_dir in sorted(run_dir.iterdir()) if (job_dir / META_FILE).is_file()]
    if not jobs:
        raise InvariantError(f"run {run_id} has no job folders with {META_FILE}")
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
