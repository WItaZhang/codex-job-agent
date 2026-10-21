"""Handing gates to the user (spec 4.10 item 10, section 10 items 97-106; decisions 22 and 23; samples G1-G18 of
specs/gate-handoff.samples.md): a login, single sign-on, CAPTCHA or missing e-mail code stops the job as held with its
tab kept, the user is notified, and after the user handled it and approved, the job is resumed.
All data is SYNTHETIC (tests/fixtures/gate_handoff/samples.yaml); nothing here starts Chrome or Claude Code."""

import dataclasses
import datetime
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from intent_job_agent import apply_bridge, apply_fill, notify
from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill import runner as form_fill_runner
from intent_job_agent.form_fill.models import JobReport
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox

from .apply_fakes import FormFillFake
from .conftest import BASE_INTENT, BASE_VOCAB, settings
from .test_slice6 import FakeRunner, add_jobs, fake_claude, write_applicant, write_letters

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


# --- G1, G8, G9, G16: what the database keeps ----------------------------------------------------------------------

HOLD = {
    "batch_id": "batch-gate01",
    "phase": "rest",
    "n": 7,
    "mode": "submit",
    "via": "applypilot",
    "reason": "login_issue",
    "note": "careers.zetapay.test: sign in to continue",
    "tab_id": "N7",
    "tab_url": "https://careers.zetapay.test/login",
    "times": 1,
}
ATTEMPT = {"batch_id": "batch-gate01", "phase": "rest", "mode": "submit", "via": "applypilot"}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "agent.sqlite", settings())
    s.list_applications(["g_zeta", "g_eta"], "2026-10-20")
    return s


def held(store, job_id="g_zeta", **hold):
    store.record_application_result(
        job_id,
        "held",
        "login_issue; careers.zetapay.test: sign in to continue",
        attempt={**ATTEMPT, "reason": "login_issue"},
        hold={**HOLD, **hold},
    )


def test_a_held_job_is_kept_with_its_tab_in_the_same_write(store):
    held(store)
    assert store.applications()["g_zeta"]["status"] == "held"
    (row,) = store.holds()
    assert {key: row[key] for key in HOLD} == HOLD and row["job_id"] == "g_zeta" and row["resuming"] is False
    assert row["held_at"] and store.hold("g_zeta") == row and store.hold("g_eta") is None
    (attempt,) = store.apply_attempts()
    assert (attempt["status"], attempt["reason"]) == ("held", "login_issue")


def test_a_held_result_needs_its_hold_and_any_other_result_ends_it(store):
    with pytest.raises(InvariantError):
        store.record_application_result("g_zeta", "held", None, attempt={**ATTEMPT, "reason": "captcha"})
    held(store)
    store.record_application_result("g_zeta", "applied", None, attempt={**ATTEMPT, "phase": "resume", "reason": None})
    assert store.holds() == [] and store.applications()["g_zeta"]["status"] == "applied"


def test_holding_again_replaces_the_row(store):
    held(store)
    held(store, reason="captcha", times=2, tab_url="https://careers.zetapay.test/apply/review")
    (row,) = store.holds()
    assert (row["reason"], row["times"], row["tab_url"]) == ("captcha", 2, "https://careers.zetapay.test/apply/review")


def test_holds_survive_a_restart_with_the_resuming_mark(store, tmp_path):
    held(store)
    store.set_resuming(["g_zeta"], True)
    again = Store(tmp_path / "agent.sqlite", settings())
    (row,) = again.holds()
    assert row["job_id"] == "g_zeta" and row["resuming"] is True
    again.set_resuming(["g_zeta"], False)
    assert store.hold("g_zeta")["resuming"] is False


@pytest.mark.parametrize(
    ("status", "attempt"),
    [
        ("applied", ("applied", None)),  # "7 已投": the user submitted it in the tab
        ("skipped", ("failed", "login_issue")),  # "7 不处理了": an allowed failure, by its gate
        ("pending", ("pending", None)),  # back to the list: not counted
    ],
)
def test_what_the_user_does_with_a_held_job(store, status, attempt):  # G9
    held(store)
    store.set_application_status(["g_zeta"], status, by_user=True)
    assert store.applications()["g_zeta"]["status"] == status and store.holds() == []
    user = store.apply_attempts()[-1]
    assert (user["via"], user["status"], user["reason"]) == ("user", *attempt)
    assert user["batch_id"] == "batch-gate01"  # the first step's verdict reads it (spec section 10 item 101)


def test_a_job_that_was_not_held_is_marked_as_before(store):
    store.set_application_status(["g_eta"], "skipped", by_user=True)
    user = store.apply_attempts()[-1]
    assert (user["status"], user["reason"], user["batch_id"]) == ("skipped", None, None)


# --- G3, G4, G5: the instructions and the run ----------------------------------------------------------------------


@pytest.fixture
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": {**BASE_INTENT, "location": {"us": {"level": "require"}}}, "vocabulary": BASE_VOCAB}),
        encoding="utf-8",
    )
    box.initialize_intent(str(data / "intent.yaml"))
    box.apply_runner = FakeRunner()
    box.form_fill_runner = FormFillFake(box)
    box.letter_renderer = lambda text, pdf_path, name, day: pdf_path.write_bytes(b"%PDF-1.4 letter")
    box.apply_in_background = False
    box.notifier = lambda title, body: box.notices.append((title, body))
    box.notices = []
    write_applicant(box.data_dir)
    return box


def prompt(box, mode, **resume):
    """The whole prompt with its whitespace collapsed: rules wrap across lines, phrases are checked as read."""
    (job,) = add_jobs(box, ["Acme"])
    write_letters(box, [job])
    spec = dataclasses.replace(box._apply_spec(job, mode), **resume)
    return flat(apply_bridge.build_prompt(spec, box.data_dir, box.settings, box._applicant().profile))


def flat(text: str) -> str:
    return " ".join(text.split())


def vendor_prompt(box) -> str:
    """ApplyPilot's own prompt for one job, before this project's replacements."""
    from intent_job_agent.vendor.applypilot.apply import prompt as vendor

    (job,) = add_jobs(box, ["Acme"])
    write_letters(box, [job])
    spec = box._apply_spec(job, "submit")
    apply_bridge.configure(box.data_dir, box.settings, box._applicant().profile)
    posting = {
        "url": spec.url,
        "application_url": spec.url,
        "title": spec.title,
        "site": spec.company,
        "tailored_resume_path": str(spec.resume_pdf),
        "cover_letter_path": str(spec.letter_txt),
    }
    return vendor.build_prompt(job=posting, tailored_resume=spec.resume_text.read_text(encoding="utf-8"))


SOLVING = ("CapSolver", "capsolver", "CAP-SYNTHETIC-123", "MANUAL FALLBACK", "Solve them yourself", "createTask")
SOLVING += ("If found, solve", "solve it before continuing", "solve it then retry", "Audio challenge")


@pytest.mark.parametrize("mode", ["stop", "submit"])
def test_no_prompt_solves_a_captcha_even_with_a_key_set(box, mode, monkeypatch):  # G4
    monkeypatch.setenv("CAPSOLVER_API_KEY", "CAP-SYNTHETIC-123")
    text = prompt(box, mode)
    assert not [phrase for phrase in SOLVING if phrase in text]
    gates = text[text.index("- GATES:") :]
    for code in ("RESULT:CAPTCHA", "sso_required", "login_issue", "email_verification"):
        assert code in gates
    assert "leave its tab open" in gates and "never an email address or a password" in gates.lower()
    assert "RESULT:CAPTCHA -- blocked by unsolvable captcha" not in text


@pytest.mark.parametrize("index", range(len(apply_bridge.NO_SOLVING)))
def test_a_reworded_captcha_passage_stops_the_run(box, index):  # G4: never silently keep ApplyPilot's solving
    text = vendor_prompt(box)
    pattern, _ = apply_bridge.NO_SOLVING[index]
    assert len(pattern.findall(text)) == 1
    with pytest.raises(InvariantError, match="ApplyPilot"):
        apply_bridge.no_solving(pattern.sub("== REWORDED ==", text))


def test_a_vendor_prompt_with_a_reworded_captcha_section_starts_nothing(box, monkeypatch):
    from intent_job_agent.vendor.applypilot.apply import prompt as vendor

    monkeypatch.setattr(vendor, "_build_captcha_section", lambda: "== CAPTCHA ==\nSomething else.")
    with pytest.raises(InvariantError, match="ApplyPilot"):
        prompt(box, "submit")


def test_the_solving_services_are_blocked_in_the_applying_browser():
    origins = settings().apply.blocked_origins
    assert "https://api.capsolver.com" in origins and "https://2captcha.com" in origins


@pytest.mark.parametrize("mode", ["stop", "submit"])
def test_terms_are_ticked_optional_boxes_left_and_arbitration_still_stops(box, mode):  # G3
    text = prompt(box, mode)
    terms = text[text.index("- TERMS AND CONSENT:") :]
    assert "terms of use" in terms and "background check" in terms and "Do not stop for these" in terms
    assert "marketing" in terms and "unticked" in terms and "ARBITRATION" in terms
    assert "Consent, declaration and signature checkboxes and dates may be completed." not in text


def test_an_auto_submitted_tab_is_closed_except_at_a_gate(box):  # G5
    text = prompt(box, "submit")
    assert "close this tab, except at a GATE" in text
    assert "except at a GATE" not in prompt(box, "stop")  # a stopped form keeps its tab anyway


def test_a_resumed_job_takes_over_its_tab_and_checks_it_was_not_submitted(box):  # G11 a
    url = "https://careers.zetapay.test/apply/step2"
    text = prompt(box, "submit", resume=True, resume_url=url, resume_tab="N7")
    tabs = text[text.index("- TABS:") :]
    assert f"select the tab showing {url}" in tabs and 'browser_tabs action "new"' not in tabs
    assert "skip steps 1 and 2" in tabs and "RESULT:FAILED:already_applied" in tabs


def test_a_resumed_job_without_its_tab_starts_from_the_job_address(box):  # G11 c
    text = prompt(box, "submit", resume=True)
    tabs = text[text.index("- TABS:") :]
    assert 'browser_tabs action "new"' in tabs and "already applied" in tabs and "signed in" in tabs


def test_the_apply_agent_runs_without_the_solving_key(box, tmp_path, monkeypatch):  # G4
    monkeypatch.setenv("CAPSOLVER_API_KEY", "CAP-SYNTHETIC-123")
    (job,) = add_jobs(box, ["Acme"])
    write_letters(box, [job])
    fake_claude(tmp_path, monkeypatch, FAKE_CLAUDE_ENV)
    apply_bridge.run_application(
        box._apply_spec(job, "submit"), box.data_dir, box.settings, box._applicant().profile, 9
    )
    seen = json.loads((tmp_path / "args.json").read_text(encoding="utf-8"))
    assert seen["key"] is None


def test_the_form_fill_agent_reads_gmail_but_can_never_send(box):  # spec section 10 item 105
    s = box.settings
    assert "mcp__gmail__send_email" in s.form_fill.disallowed_tools
    command = apply_bridge.claude_command(s, Path("/x/mcp.json"), extra_disallowed=tuple(s.form_fill.disallowed_tools))
    allowed = command[command.index("--allowedTools") + 1].split(",")
    assert "mcp__gmail__read_email" in allowed and "mcp__gmail__search_emails" in allowed
    assert "mcp__gmail__send_email" not in allowed
    assert "mcp__gmail__send_email" in command[command.index("--disallowedTools") + 1].split(",")
    rules = flat(form_fill_runner.RUN_RULES)
    assert "Gmail" in rules and "never send" in rules.lower() and "no email tools" not in rules


def test_the_form_fill_run_starts_gmail_in_its_own_config(box, tmp_path, monkeypatch):
    (job,) = add_jobs(box, ["Acme"])
    write_letters(box, [job])
    fake_claude(tmp_path, monkeypatch, FAKE_CLAUDE_ENV)
    entry = {"command": "/venv/bin/python", "args": ["-m", "intent_job_agent.form_fill.checkpoint", "/x/c.json"]}
    apply_bridge.run_application(
        box._apply_spec(job, "stop"),
        box.data_dir,
        box.settings,
        box._applicant().profile,
        9,
        log_path=tmp_path / "runs" / "r1" / job / "log.jsonl",
        extra_servers={"checkpoint": entry},
    )
    seen = json.loads((tmp_path / "args.json").read_text(encoding="utf-8"))
    argv = seen["argv"]
    assert Path(argv[argv.index("--mcp-config") + 1]).name == ".mcp-form-fill-0.json"
    assert list(json.loads(seen["mcp"])["mcpServers"]) == ["playwright", "gmail", "checkpoint"]


def test_the_chrome_runner_keeps_the_tab_of_a_held_job(box, monkeypatch):  # G5
    (job,) = add_jobs(box, ["Acme"])
    write_letters(box, [job])
    before = [{"id": "T1", "type": "page", "url": "https://news.example.test/"}]
    after = [*before, {"id": "N7", "type": "page", "url": "https://careers.zetapay.test/login"}]
    lists = iter([before, after])
    monkeypatch.setattr(apply_bridge.readback, "list_targets", lambda port: next(lists))
    monkeypatch.setattr(apply_bridge, "cdp_alive", lambda port: True)
    held_run = apply_bridge.ApplyOutcome("held", "login_issue", ["sign in"], last_url="https://careers.zetapay.test/")
    monkeypatch.setattr(apply_bridge, "run_application", lambda *a, **k: held_run)
    runner = apply_bridge.ChromeRunner(box.data_dir, box.settings, box._applicant().profile)
    runner.port = 9222
    outcome = runner(box._apply_spec(job, "submit"))
    assert outcome.tab == {"id": "N7", "url": "https://careers.zetapay.test/login"}


def test_the_chrome_runner_finds_a_resumed_tab_by_its_id(box, monkeypatch):  # G5, G14
    (job,) = add_jobs(box, ["Acme"])
    write_letters(box, [job])
    tabs = [{"id": "N7", "type": "page", "url": "https://careers.zetapay.test/apply/review"}]
    monkeypatch.setattr(apply_bridge.readback, "list_targets", lambda port: tabs)
    monkeypatch.setattr(apply_bridge, "cdp_alive", lambda port: True)
    monkeypatch.setattr(apply_bridge, "run_application", lambda *a, **k: apply_bridge.ApplyOutcome("held", "captcha"))
    runner = apply_bridge.ChromeRunner(box.data_dir, box.settings, box._applicant().profile)
    runner.port = 9222
    spec = dataclasses.replace(box._apply_spec(job, "submit"), resume=True, resume_tab="N7", resume_url=tabs[0]["url"])
    assert runner(spec).tab == {"id": "N7", "url": "https://careers.zetapay.test/apply/review"}


def test_the_last_address_the_agent_opened_is_kept():
    events = [
        {"name": "mcp__playwright__browser_navigate", "input": {"url": "https://careers.zetapay.test/"}},
        {"name": "mcp__playwright__browser_click", "input": {"element": "Sign in"}},
        {"name": "mcp__playwright__browser_tabs", "input": {"action": "new", "url": "https://sso.zetapay.test/"}},
    ]
    assert apply_bridge.last_url(events) == "https://sso.zetapay.test/"
    assert apply_bridge.last_url([]) is None


FAKE_CLAUDE_ENV = """#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
argv = sys.argv[1:]
mcp = argv[argv.index("--mcp-config") + 1]
open(os.environ["FAKE_ARGS"], "w").write(json.dumps(
    {"argv": argv, "prompt": prompt, "mcp": open(mcp).read(), "key": os.environ.get("CAPSOLVER_API_KEY")}))
print(json.dumps({"type": "result", "result": "RESULT:APPLIED"}), flush=True)
"""


def test_a_form_fill_job_hands_over_the_tab_it_was_read_in(box):  # G5, form-fill path
    (job,) = add_jobs(box, ["Acme"])
    write_letters(box, [job])
    box.apply_runner = FakeRunner({job: ("failed", "captcha", ["hcaptcha on the last page"])})
    batch = box.prepare_apply_batch("2026-10-14")
    box.start_applying(batch["batch_id"], batch["summary"])
    (row,) = box.store.holds()
    assert row["tab_id"] == f"N1-{job}" and row["tab_url"] == f"https://jobs.example.test/{job}/apply"
    run = box.apply_status()["run_id"]
    meta = json.loads((box.data_dir / "form-fill" / "runs" / run / job / "meta.json").read_text(encoding="utf-8"))
    assert meta["tab_id"] == f"N1-{job}"
