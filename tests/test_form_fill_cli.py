"""The form-fill local command (slice-1 plan sections 1 and 3): `report <run>` checks the pages saved under a run again
and writes the reports, without a browser and without reading the agents' logs. All data here is SYNTHETIC."""

import json
from pathlib import Path

import pytest

from intent_job_agent.form_fill import cli, readback
from intent_job_agent.form_fill.models import JobReport

from .conftest import settings
from .test_form_fill_runner import ANSWERS, CHECKLIST, RELOCATION
from .test_slice6 import write_applicant

RUN = "20261017-090000-abcd"
FIELDS = [
    {"id": "p1.1", "question": "First name", "origin": "control", "control": "text", "value": "Sam"},
    {"id": "p1.2", "question": "Email", "origin": "control", "control": "text", "value": "[email]"},
    {
        "id": "p1.3",
        "question": "Are you legally authorized to work in the U.S.?",
        "origin": "control",
        "control": "radio",
        "value": "No",
        "options": ["Yes", "No"],
    },
]


@pytest.fixture
def data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    write_applicant(data, relocation=RELOCATION)
    (data / "applicant/answers.yaml").write_text(ANSWERS, encoding="utf-8")
    (data / "applicant/checklist.yaml").write_text(CHECKLIST, encoding="utf-8")
    job = data / "form-fill/runs" / RUN / "acme-ml"
    job.mkdir(parents=True)
    meta = {
        "job_id": "acme-ml",
        "company": "Acme",
        "title": "ML Engineer",
        "status": "filled",
        "agent_result": "dry_run",
        "checks": {"not_submitted": True},
        "page": {"actions": ["Submit application"], "received": ""},
    }
    (job / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (job / "readback.json").write_text(json.dumps(FIELDS), encoding="utf-8")
    (job / "log.jsonl").write_text("not to be read\n", encoding="utf-8")

    def no_browser(*args, **kwargs):
        raise AssertionError("the local command must not touch the browser")

    monkeypatch.setattr(readback, "read_final_page", no_browser)
    monkeypatch.setattr(readback, "list_targets", no_browser)
    real_read_text = Path.read_text

    def guarded(self, *args, **kwargs):
        if self.name == "log.jsonl":
            raise AssertionError("the local command must not read the agent's log")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    return data


def test_report_rechecks_a_saved_run(data):
    summary = cli.run_report(RUN, data, settings())
    assert summary == data / "form-fill/runs" / RUN / "summary.md"
    report = JobReport.model_validate_json((summary.parent / "acme-ml.json").read_text(encoding="utf-8"))
    rows = {row.field_id: (row.key, row.result) for row in report.fields}
    assert rows == {
        "p1.1": ("name.first", "correct"),
        "p1.2": ("contact.email", "correct"),
        "p1.3": ("work_auth.authorized_us", "wrong"),
    }
    assert report.conclusion == "do_not_submit"
    assert "do_not_submit" not in summary.read_text(encoding="utf-8") and "不能提交" in summary.read_text("utf-8")


def test_main_prints_the_summary_path(data, capsys):
    assert cli.main(["report", RUN, "--data-dir", str(data)]) == 0
    assert str(data / "form-fill/runs" / RUN / "summary.md") in capsys.readouterr().out


@pytest.mark.parametrize("run", ["20990101-000000-none", "../applicant", "a/b"])
def test_an_unknown_or_unsafe_run_is_refused(data, capsys, run):
    assert cli.main(["report", run, "--data-dir", str(data)]) == 2
    assert "run" in capsys.readouterr().err


def test_a_job_folder_without_a_readback_is_reported_unreadable(data):
    job = data / "form-fill/runs" / RUN / "lost-tab"
    job.mkdir()
    (job / "meta.json").write_text(json.dumps({"job_id": "lost-tab", "status": "tab_lost"}), encoding="utf-8")
    summary = cli.run_report(RUN, data, settings())
    report = JobReport.model_validate_json((summary.parent / "lost-tab.json").read_text(encoding="utf-8"))
    assert report.conclusion == "unreadable" and report.fields == ()
