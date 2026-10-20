"""Form-fill reports (specs/form-fill.md 3.2, 5.2; slice-1 plan sections 1 and 5): the conclusion of each job, the
numbered list of what the user has to do, the per-job JSON and summary.md. All data here is SYNTHETIC."""

import json

import pytest

from intent_job_agent.form_fill.models import CheckResult, JobReport, ReadField
from intent_job_agent.form_fill.redact import Redactor
from intent_job_agent.form_fill.report import conclude, job_report, summary_md, write_reports

from .test_form_fill_compare import ERRORS, load, page

PROFILE = {"personal": {"full_name": "Sam Synthetic", "email": "sam.synthetic@example.test", "phone": "555-010-2468"}}


def result(severity="none", outcome=None, n=1, key="screening.x", **kw) -> CheckResult:
    outcome = outcome or ("correct" if severity == "none" else "wrong")
    return CheckResult(
        field_id=f"p1.{n}", question=f"Question {n}", value="v", key=key, result=outcome, severity=severity, **kw
    )


def meta(**kw) -> dict:
    base = {
        "job_id": "acme-ml",
        "company": "Acme",
        "title": "ML Engineer",
        "status": "filled",
        "agent_result": "dry_run",
        "checks": {"tab_found": True, "not_submitted": True, "other_tabs_unchanged": True, "redaction_hits": 0},
        "page": {"actions": ["Submit application"], "received": ""},
    }
    return {**base, **kw}


@pytest.mark.parametrize(
    ("job", "results", "want"),
    [
        (meta(), [result(), result("fact", n=2)], ("do_not_submit", None)),
        (meta(), [result(), result("guess", outcome="no_answer", n=2)], ("do_not_submit", None)),
        (meta(), [result("missing"), result("format", n=2)], ("check_then_submit", None)),
        (meta(), [result(outcome="not_reviewed")], ("check_then_submit", None)),
        (meta(), [result()], ("check_then_submit", None)),
        (meta(), [], ("unreadable", None)),
        (meta(status="gate", agent_result="captcha"), [result()], ("not_filled", "gate")),
        (meta(agent_result="timeout"), [result("fact")], ("not_filled", "stuck")),
        (meta(agent_result="needs_answer"), [result()], ("not_filled", "needs_answer")),
        (meta(agent_result="page_error"), [], ("not_filled", "page_error")),
        (meta(status="tab_lost", agent_result="dry_run"), [], ("unreadable", None)),
        (meta(status="readback_failed"), [], ("unreadable", None)),
        (meta(checks={"not_submitted": False}), [result()], ("maybe_submitted", None)),
        (
            meta(status="gate", page={"actions": [], "received": "Thank you for applying!"}),
            [],
            ("maybe_submitted", None),
        ),
    ],
    ids=[
        "fact",
        "guess",
        "missing-and-format",
        "not-reviewed",
        "all-correct-is-still-check-first",
        "nothing-read",
        "gate",
        "timeout",
        "needs-answer",
        "page-error",
        "tab-lost",
        "readback-failed",
        "check-4-failed",
        "received-text-first",
    ],
)
def test_conclusion(job, results, want):
    assert conclude(job, results) == want


def test_a_meta_without_status_counts_as_filled():
    job = {"job_id": "x", "page": {"actions": ["Submit"], "received": ""}}
    assert conclude(job, [result()]) == ("check_then_submit", None)


@pytest.fixture
def linkedin_report(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    return job_report(meta(job_id="li-review"), page(ERRORS["pages"][0]["fields"]), answers)


def test_job_report_lists_what_to_do(linkedin_report):
    report = linkedin_report
    assert isinstance(report, JobReport) and report.conclusion == "do_not_submit"
    todo = "\n".join(report.todo)
    assert "p1.17" in todo and "Lumen Search (China) Co., Ltd." in todo, "a wrong fact names the right value"
    assert "p1.11–p1.15" in todo and "Research Assistant" in todo, "an extra segment is one item"
    assert "p1.21–p1.24" in todo and "Orbit Tech" in todo
    assert "p1.26" in todo and "p1.35" in todo, "empty fields with an answer"
    assert "未审查" in todo and "p1.36" in todo, "fields nobody checked are listed together"
    assert report.todo[0].startswith("不能提交")


def test_not_filled_says_what_happened_first(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    report = job_report(meta(agent_result="timeout"), page(ERRORS["pages"][2]["fields"]), answers)
    assert report.conclusion == "not_filled" and report.not_filled_reason == "stuck"
    assert report.todo[0].startswith("没填完：卡住（填表 agent 的结果：timeout）")


def test_summary_md(tmp_path, linkedin_report):
    answers = load(tmp_path, ERRORS["answers"])
    ashby = job_report(meta(job_id="ashby-one"), page(ERRORS["pages"][2]["fields"]), answers)
    text = summary_md("20261017-000000-abcd", [linkedin_report, ashby])
    lines = text.splitlines()
    assert lines[0] == "# 填表报告 20261017-000000-abcd"
    assert "- 岗位 2 个：不能提交 1、检查后可提交 1" in text
    assert "## li-review · Acme · ML Engineer" in text
    assert "**结论：不能提交**" in text and "**结论：检查后可提交**" in text
    assert "1) " in text and "2) " in text, "numbered items"
    assert "## 在两个以上岗位出现的问题" in text
    assert "screening.contact_consent" in text.split("## 在两个以上岗位出现的问题")[1], "unticked consent box, twice"


def test_maybe_submitted_is_the_first_line_of_its_section(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    report = job_report(meta(checks={"not_submitted": False}), page(ERRORS["pages"][2]["fields"]), answers)
    section = summary_md("r", [report]).split("## acme-ml · Acme · ML Engineer")[1]
    assert section.strip().startswith("**结论：可能已提交**")


def test_write_reports_masks_and_validates(tmp_path, linkedin_report):
    redactor = Redactor.from_profile(PROFILE)
    leaky = linkedin_report.model_copy(update={"notes": ("the agent wrote sam.synthetic@example.test",)})
    summary = write_reports(tmp_path, [leaky], redactor)
    assert summary == tmp_path / "summary.md"
    data = json.loads((tmp_path / "li-review.json").read_text(encoding="utf-8"))
    assert JobReport.model_validate(data).conclusion == "do_not_submit"
    for path in (tmp_path / "li-review.json", summary):
        text = path.read_text(encoding="utf-8")
        assert "sam.synthetic@example.test" not in text and "[email]" in text
    assert redactor.scan([tmp_path]) and all(
        sum(hits.values()) == 0 for name, hits in redactor.scan([tmp_path]).items() if name != "_skipped"
    )


def test_reports_carry_values_as_read(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    fields = (ReadField(id="p1.1", question="Email", value="[other email]", control="select", origin="control"),)
    report = job_report(meta(), fields, answers)
    (row,) = report.fields
    assert (row.question, row.value, row.expected, row.result) == ("Email", "[other email]", "[email]", "wrong")


# --- review round 1 (decision 19) -------------------------------------------------------------------------------


def test_login_issue_is_a_login_failure_not_a_gate():
    job = meta(status="gate", agent_result="login_issue")
    assert conclude(job, [result()]) == ("not_filled", "login_failed")


def test_already_applied_is_not_reported_as_maybe_submitted():
    job = meta(agent_result="already_applied", checks={"not_submitted": False})
    assert conclude(job, [result()]) == ("not_filled", "already_applied")


@pytest.mark.parametrize("agent", ["expired", "already_applied", "wrong_job", "email_only"])
def test_jobs_that_should_not_be_filled_are_not_told_to_keep_filling(tmp_path, agent):
    answers = load(tmp_path, ERRORS["answers"])
    report = job_report(meta(agent_result=agent), (), answers)
    assert report.conclusion == "not_filled" and "接着填" not in report.todo[0] and "重跑" not in report.todo[0]


def test_a_filled_job_with_nothing_read_says_so(tmp_path):
    report = job_report(meta(), (), load(tmp_path, ERRORS["answers"]))
    assert report.todo[0].startswith("读不出：一栏都没读到")


def test_extra_segments_are_one_item_each_and_empty_ones_are_not_called_wrong(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    rows = [
        ["Your title", "Intern", "review"],
        ["Company", "Beta Inc", "review"],
        ["Your title", "Machine Learning Engineer Intern", "review"],
        ["Company", "Lumen Search (China) Co., Ltd.", "review"],
        ["Your title", "Intern", "review"],
        ["Company", "Beta Inc", "review"],
        ["School", "", "review"],
        ["Degree", "", "review"],
    ]
    todo = job_report(meta(), page(rows), answers).todo
    deletes = [item for item in todo if "请在标签页里删掉这一段" in item]
    assert [item.split(" ", 1)[0] for item in deletes] == ["p1.1–p1.2", "p1.5–p1.6"]
    assert any(item.startswith("p1.7–p1.8 是空着的一段") for item in todo)


def test_a_tied_segment_is_one_item_without_asks_advice(tmp_path):
    twin = {k: v for k, v in ERRORS["answers"]["education.beng_nru"].items() if k not in ("accept", "otherwise")}
    answers = load(tmp_path, {"education.a": twin, "education.b": {**twin, "start": "2019-09"}})
    rows = [["School", "Northern River University", "review"], ["Degree", "Bachelor of Engineering", "review"]]
    todo = job_report(meta(), page(rows), answers).todo
    tied = [item for item in todo if "分不出" in item]
    assert len(tied) == 1 and tied[0].startswith("p1.1–p1.2") and "asks" not in tied[0]


def test_a_missing_option_is_listed_with_the_choices(tmp_path):
    answers = load(tmp_path, {"work_auth.visa": {"answer": "F-1 OPT", "kind": "choice", "asks": ["visa status"]}})
    fields = (
        ReadField(
            id="p1.1",
            question="What is your visa status?",
            value="",
            control="select",
            origin="control",
            options=("H-1B", "Green card", "Citizen"),
        ),
    )
    todo = "\n".join(job_report(meta(), fields, answers).todo)
    assert "p1.1 What is your visa status?" in todo and "H-1B、Green card、Citizen" in todo and "otherwise" in todo


def test_ambiguous_questions_count_toward_problems_seen_in_two_jobs(tmp_path):
    answers = load(
        tmp_path,
        {
            "work_auth.now": {"answer": "No", "kind": "yes_no", "asks": ["require sponsorship"]},
            "work_auth.later": {"answer": "Yes", "kind": "yes_no", "asks": ["require sponsorship"]},
        },
    )
    rows = [["Do you require sponsorship?", "No", "radio"]]
    reports = [job_report(meta(job_id=j), page(rows), answers) for j in ("j1", "j2")]
    repeated = summary_md("r", reports).split("## 在两个以上岗位出现的问题")[1]
    assert "work_auth.now：j1、j2" in repeated and "work_auth.later：j1、j2" in repeated


# --- review round 2 (decision 20) -------------------------------------------------------------------------------


def test_an_agent_that_never_started_is_not_a_page_error(tmp_path):
    report = job_report(meta(status="system_error", agent_result="system_error"), (), load(tmp_path, ERRORS["answers"]))
    assert report.not_filled_reason == "system_error" and "程序没跑起来" in report.todo[0]
    assert "标签页" not in report.todo[0]


def test_email_only_does_not_point_to_a_masked_address(tmp_path):
    report = job_report(meta(agent_result="email_only"), (), load(tmp_path, ERRORS["answers"]))
    assert "已经打码" in report.todo[0]


def test_the_headline_counts_an_extra_segment_once(tmp_path):
    report = job_report(meta(), page(ERRORS["pages"][0]["fields"]), load(tmp_path, ERRORS["answers"]))
    assert report.todo[0].startswith("不能提交：6 个错的事实"), "4 fields and 2 extra segments"


def test_answer_sheet_amendments_count_toward_repeated_problems(tmp_path):
    answers = load(tmp_path, {"eeo.veteran": {"answer": "Decline", "kind": "choice", "asks": ["protected veteran"]}})
    fields = (
        ReadField(
            id="p1.1",
            question="Are you a protected veteran?",
            value="",
            control="select",
            origin="control",
            options=("I am a protected veteran", "I am not a protected veteran"),
        ),
    )
    reports = [job_report(meta(job_id=j), fields, answers) for j in ("j1", "j2")]
    assert "eeo.veteran：j1、j2" in summary_md("r", reports)


def test_required_empty_fields_get_their_own_item(tmp_path):
    f = ReadField(
        id="p1.1",
        question="Have you worked for Acme?",
        value="",
        control="radio",
        origin="control",
        options=("Yes", "No"),
        required=True,
    )
    todo = job_report(meta(), (f,), load(tmp_path, {})).todo
    assert any(item.startswith("必填、空着") and "p1.1" in item for item in todo)


# --- review round 3 (decision 21) -------------------------------------------------------------------------------


def test_old_page_contacts_get_their_own_item(tmp_path):
    answers = load(tmp_path, ERRORS["answers"])
    report = job_report(
        meta(), page([("Email", "[email]", "text"), ("First name", "Sam", "text")]), answers, contacts=False
    )
    item = next(i for i in report.todo if i.startswith("联系方式 1 栏没有核对"))
    assert "旧的打码格式" in item and "p1.1" in item


def test_a_filled_no_option_field_is_not_called_empty(tmp_path):
    answers = load(tmp_path, {"work_auth.x": {"answer": "No", "kind": "yes_no", "asks": ["require sponsorship"]}})
    options = ("I will require sponsorship", "I will not require sponsorship")
    f = ReadField(
        id="p1.1",
        question="Will you require sponsorship?",
        value=options[0],
        control="select",
        origin="control",
        options=options,
    )
    report = job_report(meta(), (f,), answers)
    assert report.conclusion == "do_not_submit"
    assert "空着" not in "".join(report.todo[1:]) and "选了一项「I will require sponsorship」" in "".join(report.todo)


# --- decision 23 ------------------------------------------------------------------------------------------------


def test_not_eligible_comes_right_after_maybe_submitted():
    assert conclude(meta(agent_result="not_eligible"), [result("missing")]) == ("not_eligible", None)
    # the result ApplyPilot's own prompt offers for a work-authorization bar means the same here
    assert conclude(meta(agent_result="not_eligible_work_auth"), [result("missing")]) == ("not_eligible", None)
    received = meta(agent_result="not_eligible", page={"actions": [], "received": "Thank you for applying!"})
    assert conclude(received, [])[0] == "maybe_submitted"


def test_not_eligible_asks_for_no_answers(tmp_path):
    note = 'The page says "This position is not open to current students." The true answer is auto-rejected.'
    rows = page([("Name", "", "text"), ("Location", "", "text"), ("Current education status", "", "select")])
    report = job_report(meta(agent_result="not_eligible", notes=[note]), rows, load(tmp_path, ERRORS["answers"]))
    assert report.conclusion == "not_eligible"
    assert report.todo == (report.todo[0],) and report.todo[0].startswith("不符合申请资格")
    text = summary_md("r", [report])
    assert "**结论：不符合申请资格**" in text and note in text and "不符合申请资格 1" in text


def test_a_check_first_job_with_nothing_confirmed_says_to_check_every_field(tmp_path):
    rows = page([("What drew you to Acme?", "The product", "textarea")])
    report = job_report(meta(), rows, load(tmp_path, {}))
    assert report.conclusion == "check_then_submit" and "没有一栏由代码核对为对" in report.todo[0]


def test_a_check_first_job_with_a_confirmed_field_has_no_such_reminder(tmp_path):
    rows = page([("First name", "Sam", "text"), ("What drew you to Acme?", "The product", "textarea")])
    report = job_report(meta(), rows, load(tmp_path, {}))
    assert report.conclusion == "check_then_submit" and "没有一栏由代码核对为对" not in report.todo[0]


def test_the_report_knows_the_jobs_company(tmp_path):
    answers = load(
        tmp_path,
        {"screening.how_heard": {"answer": "Company career site", "kind": "choice", "asks": ["how did you hear"]}},
    )
    rows = page([("How did you hear about us?", "Acme Careers Website", "select")])
    assert job_report(meta(company="Acme"), rows, answers).fields[0].result == "correct"


# --- the merge review of 2026-10-20 (decision 32) ----------------------------------------------------------------


def _pages(tmp_path):
    from intent_job_agent.form_fill.recording import PageRead

    answers = load(
        tmp_path,
        {
            "education.ms": {
                "kind": "record",
                "school": "Example State University",
                "degree": "Master of Science",
                "major": "Data Science",
                "start": "2025-08",
                "end": "2027-05",
            },
            "experience.acme": {
                "kind": "record",
                "company": "Acme Synthetic",
                "title": "Intern",
                "start": "2024-06",
                "end": "2024-08",
            },
        },
    )
    education = page(
        [
            ("School", "Example State University", "text"),
            ("Degree", "Master of Science", "text"),
            ("School", "Fake Synthetic College", "text"),
            ("Degree", "Bachelor of Arts", "text"),
        ]
    )
    work = tuple(
        f.model_copy(update={"id": f.id.replace("p1.", "p2.")})
        for f in page(
            [
                ("Company", "Acme Synthetic", "text"),
                ("Job Title", "Intern", "text"),
                ("Company", "Fake Synthetic Corp", "text"),
                ("Job Title", "Research Assistant", "text"),
            ]
        )
    )
    pages = [PageRead(1, "Education", "filled", "", education, {}), PageRead(2, "Work", "filled", "", work, {})]
    return pages, answers


def test_extra_segments_on_two_pages_are_both_listed(tmp_path):
    """Review #6: segment numbers are per page; the report keeps the two pages' segments apart."""
    pages, answers = _pages(tmp_path)
    report = job_report(meta(), (), answers, pages=pages)
    text = "\n".join(report.todo)
    assert "Fake Synthetic College" in text and "Fake Synthetic Corp" in text
    assert report.todo[0].startswith("不能提交：2 个错的事实")


def test_a_received_page_seen_while_recording_makes_it_maybe_submitted():
    """Review #10."""
    assert conclude(meta(received_seen="Your application was sent"), [result()]) == ("maybe_submitted", None)


def test_fields_without_a_recorded_source_are_listed_for_the_user(tmp_path):
    """Review #29, #30."""
    from intent_job_agent.form_fill.recording import PageRead

    answers = load(tmp_path, {"work_auth.us": {"answer": "Yes", "kind": "yes_no", "asks": ["authorized to work"]}})
    fields = page([("Are you authorized to work in the US?", "Yes", "text")])
    report = job_report(
        meta(), (), answers, pages=[PageRead(1, "Questions", "filled", "", fields, {"p1.1": "unrecorded"})]
    )
    assert report.conclusion == "check_then_submit"
    assert any("来源未录到" in item and "p1.1" in item for item in report.todo[1:])
