"""The form-fill local command (slice-1 plan sections 1 and 3): `report <run>` checks the pages saved under a run again
and writes the reports, without a browser and without reading the agents' logs. All data here is SYNTHETIC."""

import json
from pathlib import Path

import pytest

from intent_job_agent.form_fill import cli, readback
from intent_job_agent.form_fill.models import AnswerEntry, JobReport
from intent_job_agent.form_fill.redact import Redactor

from .conftest import ROOT, settings
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
        "redaction": 2,
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
    assert cli.main(["report", RUN, "--data-dir", str(data), "--config", str(ROOT / "configs/default.yaml")]) == 0
    assert str(data / "form-fill/runs" / RUN / "summary.md") in capsys.readouterr().out


@pytest.mark.parametrize("run", ["20990101-000000-none", "../applicant", "a/b"])
def test_an_unknown_or_unsafe_run_is_refused(data, capsys, run):
    assert cli.main(["report", run, "--data-dir", str(data), "--config", str(ROOT / "configs/default.yaml")]) == 2
    assert "run" in capsys.readouterr().err


def test_a_job_folder_without_a_readback_is_reported_unreadable(data):
    job = data / "form-fill/runs" / RUN / "lost-tab"
    job.mkdir()
    (job / "meta.json").write_text(json.dumps({"job_id": "lost-tab", "status": "tab_lost"}), encoding="utf-8")
    summary = cli.run_report(RUN, data, settings())
    report = JobReport.model_validate_json((summary.parent / "lost-tab.json").read_text(encoding="utf-8"))
    assert report.conclusion == "unreadable" and report.fields == ()


def test_a_job_folder_without_meta_json_is_still_reported(data):
    job = data / "form-fill/runs" / RUN / "half-done"
    job.mkdir()
    (job / "readback.json").write_text("[]", encoding="utf-8")
    broken = data / "form-fill/runs" / RUN / "broken"
    broken.mkdir()
    (broken / "meta.json").write_text("{not json", encoding="utf-8")
    summary = cli.run_report(RUN, data, settings())
    for name in ("half-done", "broken"):
        report = JobReport.model_validate_json((summary.parent / f"{name}.json").read_text(encoding="utf-8"))
        assert report.conclusion == "unreadable" and report.notes
    assert "- 岗位 3 个" in summary.read_text(encoding="utf-8")


def test_a_job_without_a_saved_page_has_no_page_folder(data):
    job = data / "form-fill/runs" / RUN / "lost-tab"
    job.mkdir()
    (job / "meta.json").write_text(json.dumps({"job_id": "lost-tab", "status": "tab_lost"}), encoding="utf-8")
    summary = cli.run_report(RUN, data, settings())
    report = JobReport.model_validate_json((summary.parent / "lost-tab.json").read_text(encoding="utf-8"))
    assert report.page_dir is None


def test_the_answer_sheet_is_masked_like_the_saved_page(data):
    """The synthetic applicant lives in Austin; the school's name holds the city, masked on the saved page."""
    answers = ANSWERS.replace(
        '    school: Example State University\n    aliases: ["ESU"]\n',
        '    school: University of Texas at Austin\n    aliases: ["UT Austin"]\n    city: Austin\n',
    )
    (data / "applicant/answers.yaml").write_text(answers, encoding="utf-8")
    rows = [
        {
            "id": "p1.1",
            "question": "School",
            "origin": "review_text",
            "control": "review",
            "value": "University of Texas at [city]",
        },
        {"id": "p1.2", "question": "City", "origin": "review_text", "control": "review", "value": "[city]"},
        {"id": "p1.3", "question": "Degree", "origin": "review_text", "control": "review", "value": "MS"},
    ]
    (data / "form-fill/runs" / RUN / "acme-ml/readback.json").write_text(json.dumps(rows), encoding="utf-8")
    summary = cli.run_report(RUN, data, settings())
    report = JobReport.model_validate_json((summary.parent / "acme-ml.json").read_text(encoding="utf-8"))
    assert [(r.field_id, r.key, r.result) for r in report.fields] == [
        ("p1.1", "education.ms", "correct"),
        ("p1.2", "education.ms", "correct"),
        ("p1.3", "education.ms", "correct"),
    ]


def test_a_contact_value_too_short_to_mask_is_compared_as_text():
    profile = {"personal": {"email": "sam.synthetic@example.test", "city": "Ely", "province_state": "TX"}}
    redactor = Redactor.from_profile(profile)
    entries = {
        "contact.city": AnswerEntry(key="contact.city", from_profile="personal.city"),
        "contact.email": AnswerEntry(key="contact.email", from_profile="personal.email"),
        "contact.state": AnswerEntry(key="contact.state", from_profile="personal.province_state"),
    }
    plain = cli.plain_contacts(entries, profile, redactor)
    assert plain["contact.city"].answer == "Ely" and plain["contact.state"].answer == "TX"
    assert plain["contact.email"].answer is None, "masked values stay references"


def test_a_page_saved_before_the_current_masking_is_checked_the_old_way(data):
    """Saved before redact.VERSION: the city was [address] and anyone's e-mail [email]."""
    job = data / "form-fill/runs" / RUN / "acme-ml"
    meta = json.loads((job / "meta.json").read_text(encoding="utf-8"))
    del meta["redaction"]
    (job / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    answers = ANSWERS.replace(
        '    school: Example State University\n    aliases: ["ESU"]\n',
        '    school: University of Texas at Austin\n    aliases: ["UT Austin"]\n',
    )
    (data / "applicant/answers.yaml").write_text(answers, encoding="utf-8")
    rows = [
        {"id": "p1.1", "question": "Email", "origin": "control", "control": "select", "value": "[email]"},
        {
            "id": "p1.2",
            "question": "School",
            "origin": "review_text",
            "control": "review",
            "value": "University of Texas at [address]",
        },
        {"id": "p1.3", "question": "Degree", "origin": "review_text", "control": "review", "value": "MS"},
    ]
    (job / "readback.json").write_text(json.dumps(rows), encoding="utf-8")
    summary = cli.run_report(RUN, data, settings())
    report = JobReport.model_validate_json((summary.parent / "acme-ml.json").read_text(encoding="utf-8"))
    got = {r.field_id: (r.key, r.result) for r in report.fields}
    assert got["p1.1"] == ("contact.email", "not_reviewed")
    assert got["p1.2"] == ("education.ms", "correct") and got["p1.3"] == ("education.ms", "correct")


def test_asks_are_masked_like_the_saved_questions(data):
    answers = (
        ANSWERS + '  location.onsite_home:\n    answer: "Yes"\n    kind: yes_no\n    asks: ["work onsite in austin"]\n'
    )
    (data / "applicant/answers.yaml").write_text(answers, encoding="utf-8")
    rows = [
        {
            "id": "p1.1",
            "question": "Can you work onsite in [city] three days a week?",
            "origin": "control",
            "control": "radio",
            "value": "No",
            "options": ["Yes", "No"],
        }
    ]
    (data / "form-fill/runs" / RUN / "acme-ml/readback.json").write_text(json.dumps(rows), encoding="utf-8")
    summary = cli.run_report(RUN, data, settings())
    report = JobReport.model_validate_json((summary.parent / "acme-ml.json").read_text(encoding="utf-8"))
    assert [(r.key, r.result) for r in report.fields] == [("location.onsite_home", "wrong")]


def test_answers_yaml_errors_are_masked(data, capsys):
    bad = ANSWERS + '  contact.mobile:\n    from_profile: "555-0100"\n'
    (data / "applicant/answers.yaml").write_text(bad, encoding="utf-8")
    assert cli.main(["report", RUN, "--data-dir", str(data), "--config", str(ROOT / "configs/default.yaml")]) == 2
    err = capsys.readouterr().err
    assert "555-0100" not in err and "[phone]" in err


def test_a_state_equal_to_the_city_is_compared_as_the_page_shows_it():
    profile = {"personal": {"email": "sam.synthetic@example.test", "city": "New York", "province_state": "New York"}}
    redactor = Redactor.from_profile(profile)
    entries = {"contact.state": AnswerEntry(key="contact.state", from_profile="personal.province_state")}
    assert cli.plain_contacts(entries, profile, redactor)["contact.state"].answer == "[city]"


def test_a_shared_profile_value_accepts_the_other_fields_placeholder():
    url = "https://github.com/sam-synthetic"
    profile = {"personal": {"email": "sam.synthetic@example.test", "github_url": url, "portfolio_url": url}}
    redactor = Redactor.from_profile(profile)
    entries = {"contact.portfolio": AnswerEntry(key="contact.portfolio", from_profile="personal.portfolio_url")}
    shared = cli.plain_contacts(entries, profile, redactor)["contact.portfolio"]
    assert shared.answer is None and shared.accept == ("[github]",)


def test_a_saved_page_drops_page_chrome_but_keeps_its_numbers(data):
    """Decision 23: step bars and empty widget text saved before the rule are left out when the run is checked
    again; the other fields keep the numbers the saved page and its screenshot use."""
    chrome = [
        {"id": "p1.4", "question": "step 1 of 6", "origin": "review_text", "control": "review", "value": "Me"},
        {"id": "p1.5", "question": "items selected", "origin": "control", "control": "select", "value": ""},
    ]
    readback_file = data / "form-fill/runs" / RUN / "acme-ml/readback.json"
    readback_file.write_text(json.dumps(chrome + FIELDS), encoding="utf-8")
    summary = cli.run_report(RUN, data, settings())
    report = JobReport.model_validate_json((summary.parent / "acme-ml.json").read_text(encoding="utf-8"))
    assert [row.field_id for row in report.fields] == ["p1.1", "p1.2", "p1.3"]


# --- slice 2a: a run recorded page by page (specs/form-fill.md 4.3, 5.3) ----------------------------------------


def recorded_run(data, **skips) -> Path:
    from .test_form_fill_recording import three_pages

    three_pages(
        data / "form-fill" / "recordings",
        Redactor.from_profile(json.loads((data / "applicant/profile.json").read_bytes())),
        **skips,
    )
    job = data / "form-fill/runs/run-1/acme-ml"
    job.mkdir(parents=True)
    meta = {
        "job_id": "acme-ml",
        "company": "Acme",
        "title": "ML Engineer",
        "url": "https://jobs.example.test/acme/apply",
        "status": "filled",
        "agent_result": "dry_run",
        "checks": {"not_submitted": True},
        "page": {"actions": ["Review"], "received": ""},
        "redaction": 2,
        "recording": "r",
        "started": "2026-10-20T10:00:00",
    }
    (job / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return job


def test_a_recorded_run_is_checked_page_by_page(data):
    recorded_run(data)
    summary = cli.run_report("run-1", data, settings())
    report = JobReport.model_validate_json((summary.parent / "acme-ml.json").read_text(encoding="utf-8"))
    assert report.recording_id == "r"
    assert [(p.page, p.read_from, p.title) for p in report.pages] == [
        (1, "filled", "Info"),
        (2, "filled", "Experience"),
        (3, "final", "Voluntary"),
    ]
    rows = {row.field_id: row for row in report.fields}
    assert (rows["p1.1"].key, rows["p1.1"].result, rows["p1.1"].page) == ("name.first", "correct", 1)
    assert (rows["p1.2"].result, rows["p1.2"].source) == ("correct", "prefilled")
    assert rows["p1.1"].source == "undeclared" and rows["p1.1"].read_at == "2026-10-20T10:01:00"
    assert (rows["p2.1"].key, rows["p2.1"].result, rows["p2.1"].read_from) == ("education.ms", "correct", "filled")
    assert rows["p3.1"].read_from == "final"
    assert report.conclusion == "check_then_submit"


def test_a_page_read_in_the_background_is_marked_as_such(data):
    recorded_run(data, skip_filled_2=True)
    summary = cli.run_report("run-1", data, settings())
    report = JobReport.model_validate_json((summary.parent / "acme-ml.json").read_text(encoding="utf-8"))
    assert {row.read_from for row in report.fields if row.page == 2} == {"auto"}
    assert "补录" in summary.read_text(encoding="utf-8")


def test_a_page_never_read_is_unreadable_but_the_rest_is_still_checked(data):
    recorded_run(data, skip_filled_2=True, skip_auto_2=True)
    summary = cli.run_report("run-1", data, settings())
    report = JobReport.model_validate_json((summary.parent / "acme-ml.json").read_text(encoding="utf-8"))
    page2 = [row for row in report.fields if row.page == 2]
    assert [row.result for row in page2] == ["unreadable", "unreadable"]
    assert all("未录到" in row.note for row in page2)
    assert report.conclusion == "check_then_submit"
    assert "未录到" in summary.read_text(encoding="utf-8")


def test_a_recorded_run_with_nothing_read_is_unreadable(data):
    job = recorded_run(data)
    import shutil

    shutil.rmtree(data / "form-fill/recordings/r/fills/run-1")
    summary = cli.run_report("run-1", data, settings())
    report = JobReport.model_validate_json((summary.parent / "acme-ml.json").read_text(encoding="utf-8"))
    assert report.conclusion == "unreadable"
    assert job.name == "acme-ml"


def test_the_recordings_last_conclusion_is_written_by_the_report(data):
    recorded_run(data)
    cli.run_report("run-1", data, settings())
    meta = json.loads((data / "form-fill/recordings/r/meta.json").read_text(encoding="utf-8"))
    assert meta["last"] == {"run": "run-1", "conclusion": "check_then_submit", "reason": None}
