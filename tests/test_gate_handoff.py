"""Handing gates to the user (spec 4.10 item 10, section 10 items 97-106; decisions 22 and 23; samples G1-G18 of
specs/gate-handoff.samples.md): a login, single sign-on, CAPTCHA or missing e-mail code stops the job as held with its
tab kept, the user is notified, and after the user handled it and approved, the job is resumed.
All data is SYNTHETIC (tests/fixtures/gate_handoff/samples.yaml); nothing here starts Chrome or Claude Code."""

import datetime
import subprocess
from pathlib import Path

import pytest
import yaml

from intent_job_agent import apply_bridge, apply_fill, notify
from intent_job_agent.form_fill.models import JobReport

from .conftest import settings

SAMPLES = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "gate_handoff" / "samples.yaml").read_text(encoding="utf-8")
)


def report(**fields) -> JobReport:
    return JobReport(job_id="g_beta", company="Beta Health", title="Data Scientist", **fields)


# --- G1, G2: which stops are gates -------------------------------------------------------------------------------


@pytest.mark.parametrize("case", SAMPLES["mapping"], ids=lambda case: case["id"])
def test_a_form_fill_job_at_a_gate_is_held_and_other_stops_still_fail(case):
    result = apply_fill.application_result(report(**case["report"]))
    assert (result.status, result.reason) == (case["expect"]["status"], case["expect"]["reason"])


@pytest.mark.parametrize("mode", ["stop", "submit"])
@pytest.mark.parametrize("case", SAMPLES["applypilot"], ids=lambda case: case["result"])
def test_an_applypilot_job_at_a_gate_is_held_and_other_stops_still_fail(case, mode):
    assert apply_bridge.outcome_status(mode, case["result"]) == tuple(case["expect"])


def test_the_four_gates_and_a_held_job_is_not_something_that_worked():
    assert apply_bridge.GATES == ("captcha", "login_issue", "sso_required", "email_verification")
    assert apply_fill.worked("held") is False


def test_an_email_code_that_never_came_counts_as_a_login_failure():  # decision 22 item 10
    assert apply_fill.category("email_verification") == "login"


# --- G18: how a week counts ----------------------------------------------------------------------------------------


def test_a_week_with_held_and_resumed_jobs():
    stats = SAMPLES["stats"]
    week = apply_fill.week_start(stats["week"])
    numbers = apply_fill.week_stats(stats["attempts"], week, datetime.UTC, settings().apply.done)
    for key, value in stats["expect"].items():
        assert numbers[key] == value, key


def test_giving_up_a_held_job_is_an_allowed_failure_and_handing_it_back_is_not_counted():
    at = "2026-10-21T10:00:00+00:00"
    attempts = [
        {"job_id": "a", "via": "user", "status": "failed", "reason": "captcha", "at": at},
        {"job_id": "b", "via": "user", "status": "failed", "reason": "sso_required", "at": at},
        {"job_id": "c", "via": "user", "status": "pending", "reason": None, "at": at},
        {"job_id": "d", "via": "form_fill", "status": "applied", "reason": "submitted_by_user", "at": at},
    ]
    numbers = apply_fill.week_stats(attempts, datetime.date(2026, 10, 19), datetime.UTC, settings().apply.done)
    assert numbers["allowed"] == {"captcha": 1, "login": 1} and numbers["to_fix"] == {}
    assert numbers["submitted"] == 1 and numbers["not_counted"]["user_pending"] == 1


# --- G6, G7: the notification --------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", SAMPLES["notices"], ids=lambda case: case["id"])
def test_what_the_notification_says(case):
    held = [(n, company, reason) for n, company, reason in case["held"]]
    expect = tuple(case["expect"]) if case["expect"] else None
    assert notify.notice(held, case["waiting"], case["step"]) == expect


def test_a_company_name_is_plain_text_and_short():
    name = 'Acme"; do shell script "say hi\n' + "x" * 50
    assert notify.clean(name) == 'Acme"; do shell scri…'
    assert notify.clean("Beta\tHealth\r\n") == "Beta Health"
    title, body = notify.notice([(3, name, "captcha")], 0, "第二步")
    assert body == '1 个验证码：3 Acme"; do shell scri…' and "\n" not in body


def test_the_mac_notifier_passes_the_text_as_arguments_never_as_script(monkeypatch):
    seen = []
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.subprocess, "run", lambda argv, **kw: seen.append((argv, kw)))
    body = '1 个验证码：3 Acme"; do shell script "say hi'
    assert notify.mac("投递：1 个等你处理", body) is True
    ((argv, options),) = seen
    assert argv[0] == "osascript" and argv[-2:] == ["投递：1 个等你处理", body]
    script = [argv[i + 1] for i, part in enumerate(argv[:-2]) if part == "-e"]
    assert script == list(notify.SCRIPT) and not any("Acme" in line for line in script)
    assert options["timeout"] == 10


def test_the_mac_notifier_does_nothing_elsewhere_and_never_raises(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    assert notify.mac("t", "b") is False
    monkeypatch.setattr(notify.sys, "platform", "darwin")

    def broken(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(notify.subprocess, "run", broken)
    assert notify.mac("t", "b") is False
