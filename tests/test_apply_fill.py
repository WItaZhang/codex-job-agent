"""/apply and the form-fill module, the pure part (spec section 10 items 87, 89, 92, 95, 96): a batch job as a list
entry, a form-fill report as an apply-list status, profile.json against answers.yaml, and the week's numbers.
All data is SYNTHETIC (tests/fixtures/apply_form_fill/samples.yaml)."""

import datetime
import time
from pathlib import Path

import pytest
import yaml

from intent_job_agent import apply_fill
from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill.models import AnswerEntry, JobReport

from .conftest import settings

SAMPLES = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "apply_form_fill" / "samples.yaml").read_text(encoding="utf-8")
)


def report(**fields) -> JobReport:
    return JobReport(job_id="job_x1", company="Acme Robotics", title="ML Engineer", **fields)


@pytest.mark.parametrize("case", SAMPLES["mapping"], ids=lambda case: case["id"])
def test_a_form_fill_report_becomes_an_apply_list_status(case):
    result = apply_fill.application_result(report(**case["report"]))
    expect = case["expect"]
    assert (result.status, result.reason) == (expect["status"], expect["reason"])
    assert apply_fill.worked(result.status) is expect["worked"]


def test_every_conclusion_has_a_status():
    for conclusion in ("maybe_submitted", "not_eligible", "do_not_submit", "check_then_submit", "not_filled"):
        result = apply_fill.application_result(report(conclusion=conclusion, status="filled", agent_result="x"))
        assert result.status in ("waiting", "applied", "failed", "pending")


def test_the_note_starts_with_the_result_code():
    note = apply_fill.note("do_not_submit", "不能提交", "20261020-101500-ab12", ["Check the start date."])
    assert note == "do_not_submit；不能提交；填表运行 20261020-101500-ab12；Check the start date."
    assert apply_fill.note("captcha", "没填完：门槛", "r1", []) == "captcha；没填完：门槛；填表运行 r1"


def test_a_batch_job_becomes_a_list_entry(tmp_path):
    letter = tmp_path / "apply" / "letters" / "job_a1.txt"
    entry = apply_fill.list_entry(
        "job_a1", "https://job-boards.greenhouse.io/acme/jobs/111", "Acme Robotics", "ML Engineer", "main", letter
    )
    assert entry == {
        "id": "job_a1",
        "url": "https://job-boards.greenhouse.io/acme/jobs/111",
        "company": "Acme Robotics",
        "title": "ML Engineer",
        "resume": "main",
        "cover_letter": str(letter),
        "revisit": True,
    }
    unnamed = apply_fill.list_entry("job_b2", "https://jobs.example.test/2", "", "Data Scientist", "main", letter)
    assert unnamed["company"]  # the form-fill list needs a company name


def answers_of(raw: dict) -> dict[str, AnswerEntry]:
    return {
        key: AnswerEntry(key=key, answer=str(value["answer"]), accept=tuple(value.get("accept", ())))
        for key, value in raw.items()
    }


@pytest.mark.parametrize("case", SAMPLES["consistency"], ids=lambda case: case["id"])
def test_profile_and_answers_are_compared_on_the_one_to_one_fields(case):
    conflicts = apply_fill.profile_conflicts(case["profile"], answers_of(case["answers"]))
    assert conflicts == case["conflicts"]


def test_conflicts_never_name_a_value():
    conflicts = apply_fill.profile_conflicts(
        {"compensation": {"salary_expectation": "123456"}}, answers_of({"compensation.expected": {"answer": "99999"}})
    )
    assert conflicts and not any("123456" in c or "99999" in c for c in conflicts)


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("captcha", "captcha"),
        ("wrong_job_redirect", "wrong_job_redirect"),
        (None, None),
        ("", None),
        ("account exists for a@b.test", "other"),
        ("Login-Failed", "other"),
        ("x" * 80, "other"),
    ],
)
def test_a_result_code_is_kept_short(reason, code):
    assert apply_fill.short_code(reason) == code


def test_reasons_fall_into_the_done_standards_categories():
    assert apply_fill.category("captcha") == "captcha"
    assert apply_fill.category("login_issue") == apply_fill.category("sso_required") == "login"
    assert apply_fill.category("screening_requires_candidate_written_answers") == "no_ai"
    assert apply_fill.category("expired") == "expired" and apply_fill.category("timeout") == "timeout"
    assert apply_fill.category("wrong_job_redirect") is None and apply_fill.category(None) is None
    allowed = set(settings().apply.done.allowed)
    assert allowed == {"login", "captcha", "arbitration", "ssn", "payment", "no_ai", "expired", "timeout"}


def test_the_weeks_numbers():
    stats = SAMPLES["stats"]
    week = apply_fill.week_start(stats["week"])
    numbers = apply_fill.week_stats(stats["attempts"], week, datetime.UTC, settings().apply.done)
    expect = stats["expect"]
    assert numbers["week"] == "2026-10-26" and numbers["end"] == "2026-11-01"
    for key, value in expect.items():
        assert numbers[key] == value, key
    assert numbers["target"] == 200 and numbers["rate_target"] == 0.8
    assert "提交 3 份" in numbers["line"] and "50%" in numbers["line"] and "没达标" in numbers["line"]


def test_a_week_without_attempts_gives_no_rate():
    numbers = apply_fill.week_stats([], datetime.date(2026, 10, 26), datetime.UTC, settings().apply.done)
    assert numbers["submitted"] == numbers["failed"] == 0 and numbers["success_rate"] is None
    assert numbers["met"] is False and "还没有" in numbers["line"]


def test_a_week_counts_in_local_time():
    at = "2026-11-02T03:00:00+00:00"
    late = [{"job_id": "j", "via": "applypilot", "status": "applied", "reason": None, "at": at}]
    pacific = datetime.timezone(datetime.timedelta(hours=-7))
    inside = apply_fill.week_stats(late, datetime.date(2026, 10, 26), pacific, settings().apply.done)
    assert inside["submitted"] == 1  # still Sunday evening there
    assert (
        apply_fill.week_stats(late, datetime.date(2026, 10, 26), datetime.UTC, settings().apply.done)["submitted"] == 0
    )


def test_each_attempt_is_turned_into_local_time_on_its_own_day(monkeypatch):  # review #4
    monkeypatch.setenv("TZ", "America/Los_Angeles")  # daylight saving ends on 2026-11-01
    time.tzset()
    try:
        monday = [
            {"job_id": "j", "via": "applypilot", "status": "applied", "reason": None, "at": "2026-10-26T07:30:00+00:00"}
        ]
        numbers = apply_fill.week_stats(monday, datetime.date(2026, 10, 26), None, settings().apply.done)
        assert numbers["submitted"] == 1  # Monday 00:30 PDT, although the clock now (or later) may be on PST
    finally:
        monkeypatch.undo()
        time.tzset()


def test_apply_stats_reads_local_time_per_attempt(monkeypatch):
    seen = []
    monkeypatch.setattr(apply_fill, "week_stats", lambda attempts, week, tz, done: seen.append(tz) or {})
    from intent_job_agent.tools import Toolbox

    class Box:
        settings = settings()

        class store:
            @staticmethod
            def apply_attempts():
                return []

    Toolbox.apply_stats(Box(), week="2026-10-26")
    assert seen == [None]  # None: each attempt is turned into local time with the rules of its own day


@pytest.mark.parametrize("text", ["2026-10-27", "last week", "2026-13-01", ""])
def test_a_week_is_named_by_its_monday(text):
    with pytest.raises(InvariantError):
        apply_fill.week_start(text)
