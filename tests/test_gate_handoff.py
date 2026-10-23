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
from intent_job_agent.form_fill.models import JobReport, PageState
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox

from .apply_fakes import FormFillFake, synthetic_binding
from .conftest import BASE_INTENT, BASE_VOCAB, settings
from .test_slice6 import SECRETS, FakeRunner, add_jobs, fake_claude, write_applicant, write_letters

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
        box._apply_spec(job, "submit"), box.data_dir, box.settings, box._applicant().profile, synthetic_binding()
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
        synthetic_binding(),
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
    monkeypatch.setattr(apply_bridge.readback, "list_targets", lambda binding, **kwargs: next(lists))
    monkeypatch.setattr(apply_bridge, "cdp_alive", lambda binding, **kwargs: True)
    held_run = apply_bridge.ApplyOutcome("held", "login_issue", ["sign in"], last_url="https://careers.zetapay.test/")
    monkeypatch.setattr(apply_bridge, "run_application", lambda *a, **k: held_run)
    runner = apply_bridge.ChromeRunner(box.data_dir, box.settings, box._applicant().profile)
    runner.binding = synthetic_binding()
    outcome = runner(box._apply_spec(job, "submit"))
    assert outcome.tab == {"id": "N7", "url": "https://careers.zetapay.test/login", "generation": "synthetic"}


def test_the_chrome_runner_finds_a_resumed_tab_by_its_id(box, monkeypatch):  # G5, G14
    (job,) = add_jobs(box, ["Acme"])
    write_letters(box, [job])
    tabs = [{"id": "N7", "type": "page", "url": "https://careers.zetapay.test/apply/review"}]
    monkeypatch.setattr(apply_bridge.readback, "list_targets", lambda binding, **kwargs: tabs)
    monkeypatch.setattr(apply_bridge, "cdp_alive", lambda binding, **kwargs: True)
    monkeypatch.setattr(apply_bridge, "run_application", lambda *a, **k: apply_bridge.ApplyOutcome("held", "captcha"))
    runner = apply_bridge.ChromeRunner(box.data_dir, box.settings, box._applicant().profile)
    runner.binding = synthetic_binding()
    spec = dataclasses.replace(box._apply_spec(job, "submit"), resume=True, resume_tab="N7", resume_url=tabs[0]["url"])
    assert runner(spec).tab == {
        "id": "N7",
        "url": "https://careers.zetapay.test/apply/review",
        "generation": "synthetic",
    }


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


# --- G1, G2, G6, G8, G9, G17: a step with gates --------------------------------------------------------------------

DAY = "2026-10-14"
COMPANIES = ["Co0", "Co1", "Co2", "Co3", "Co4", "Stripe, Inc.", "Acme", "Beta"]  # 1-5 review, 6 careful, 7-8 submit
ACME_TAB = {"id": "N7", "url": "https://careers.acme.test/login", "generation": "synthetic"}


class GateRunner(FakeRunner):
    """FakeRunner whose auto-submitted runs also say which tab a held job stopped in, as ChromeRunner does."""

    def __init__(self, outcomes=None, tabs=None):
        super().__init__(outcomes)
        self.tabs = tabs or {}

    def __call__(self, spec):
        outcome = super().__call__(spec)
        return dataclasses.replace(outcome, tab=self.tabs.get(spec.job_id)) if outcome.status == "held" else outcome


def step(box, batch=None, summary=None):
    batch = batch or box.prepare_apply_batch(DAY)
    box.start_applying(batch["batch_id"], summary or batch["summary"])
    return batch


def both_steps(box):
    batch = step(box)
    return step(box, batch, box.apply_status()["next"]["summary"])


def test_a_held_job_waits_with_its_tab_and_the_step_goes_on(box):  # G1
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    box.apply_runner = GateRunner(
        {
            "j01": ("failed", "captcha", ["jobs.example.test: tick the human check on the last page"]),
            "j06": ("held", "login_issue", ["careers.acme.test: sign in as sam.synthetic@example.test"]),
        },
        tabs={"j06": ACME_TAB},
    )
    batch = both_steps(box)
    applications = box.store.applications()
    assert [applications[j]["status"] for j in jobs] == [
        "waiting",
        "held",
        "waiting",
        "waiting",
        "waiting",
        "waiting",
        "held",
        "applied",
    ]
    holds = {row["job_id"]: row for row in box.store.holds()}
    assert set(holds) == {"j01", "j06"}
    assert (holds["j01"]["via"], holds["j01"]["mode"], holds["j01"]["phase"], holds["j01"]["n"]) == (
        "form_fill",
        "review",
        "review",
        2,
    )
    assert holds["j01"]["tab_id"] == "N2-j01" and holds["j01"]["reason"] == "captcha"
    acme = holds["j06"]
    assert (acme["via"], acme["mode"], acme["phase"], acme["n"], acme["times"]) == (
        "applypilot",
        "submit",
        "rest",
        7,
        1,
    )
    assert (acme["tab_id"], acme["tab_url"]) == (ACME_TAB["id"], ACME_TAB["url"])
    assert "sam.synthetic" not in acme["note"] and "sign in" in acme["note"]  # masked like every note
    assert {row["batch_id"] for row in holds.values()} == {batch["batch_id"]}
    listed = {item["job_id"] for item in box.build_apply_list(DAY, limit=10**9)["jobs"]}
    assert not {"j01", "j06"} & listed  # held is not pending: no other batch picks it up
    with pytest.raises(InvariantError, match="not pending"):
        box.prepare_apply_batch(DAY, only=["j06"])


@pytest.mark.parametrize(
    "code",
    [
        "arbitration",
        "ssn_required",
        "payment_required",
        "screening_requires_candidate_written_answers",
        "unsafe_verification",
        "expired",
    ],
)
def test_stops_that_are_not_gates_still_fail(box, code):  # G2
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    box.apply_runner = GateRunner({"j01": ("failed", code, []), "j06": ("failed", code, [])})
    both_steps(box)
    applications = box.store.applications()
    assert applications["j01"]["status"] == applications["j06"]["status"] == "failed"
    assert box.store.holds() == [] and box.notices[-1] == ("投递：1 个填好等你检查", "第二步做完")


def test_each_step_ends_with_one_notification_of_what_waits_for_the_user(box):  # G6
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    box.apply_runner = GateRunner({"j01": ("failed", "captcha", []), "j06": ("held", "login_issue", [])})
    both_steps(box)
    assert box.notices == [
        ("投递：1 个等你处理，4 个填好等你检查", "1 个验证码：2 Co1"),
        ("投递：1 个等你处理，1 个填好等你检查", "1 个要登录：7 Acme"),
    ]


def test_a_step_with_nothing_to_do_for_the_user_sends_nothing(box):  # G6 c
    jobs = add_jobs(box, ["Co0", "Acme", "Beta"])
    write_letters(box, jobs)
    box.settings = box.settings.model_copy(update={"apply": box.settings.apply.model_copy(update={"review_first": 0})})
    step(box)  # both auto-submitted
    assert box.notices == []


def test_the_shipped_setting_uses_the_mac_notifier(box, monkeypatch):
    from intent_job_agent.config import Settings

    assert Settings.load(Path(__file__).parent.parent / "configs/default.yaml").apply.notify is True
    seen = []
    monkeypatch.setattr(notify, "mac", lambda title, body: seen.append(title))
    box.notifier = None
    box.settings = box.settings.model_copy(update={"apply": box.settings.apply.model_copy(update={"notify": True})})
    jobs = add_jobs(box, ["Co0"])
    write_letters(box, jobs)
    step(box)
    assert seen == ["投递：1 个填好等你检查"]
    seen.clear()
    box.settings = box.settings.model_copy(update={"apply": box.settings.apply.model_copy(update={"notify": False})})
    box.mark_applications(jobs, "pending")
    step(box)
    assert seen == []


def test_held_jobs_are_listed_from_the_database_after_a_restart(box, tmp_path):  # G8
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    box.apply_runner = GateRunner(
        {
            "j01": ("failed", "captcha", ["jobs.example.test: tick the human check"]),
            "j06": ("held", "login_issue", ["careers.acme.test: sign in to continue"]),
        },
        tabs={"j06": ACME_TAB},
    )
    batch = both_steps(box)
    fresh = Toolbox(box.settings, Store(box.store.path, box.settings), data_dir=box.data_dir)
    status = fresh.apply_status()
    assert status["status"] == "idle"
    assert [(row["n"], row["company"], row["gate"], row["site"], row["then"]) for row in status["held"]] == [
        (2, "Co1", "验证码", "jobs.example.test", "停在提交前（填表模块从岗位地址重填）"),
        (7, "Acme", "登录和注册都不成", "careers.acme.test", "自动提交"),
    ]
    row = status["held"][1]
    assert row["batch_id"] == batch["batch_id"] and row["job_id"] == "j06" and row["title"] == "ML Engineer 6"
    assert row["todo"] == "careers.acme.test: sign in to continue" and row["times"] == 1
    assert row["interrupted"] is False
    assert box.apply_status()["held"] == status["held"]  # the same list next to a step's own results


def test_the_user_decides_held_jobs_with_mark_applications(box):  # G9
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    box.apply_runner = GateRunner({"j01": ("failed", "captcha", []), "j06": ("held", "sso_required", [])})
    both_steps(box)
    box.mark_applications(["j06"], "skipped")  # "7 不处理了"
    box.mark_applications(["j01"], "applied")  # "2 已投"
    assert box.store.holds() == [] and box.apply_status()["held"] == []
    users = [(r["job_id"], r["status"], r["reason"]) for r in box.store.apply_attempts() if r["via"] == "user"]
    assert users == [("j06", "failed", "sso_required"), ("j01", "applied", None)]
    this = box.apply_stats()["weeks"][0]
    assert this["allowed"] == {"login": 1} and this["not_counted"]["held"] == 2


def first_step_held(box):
    """G17: the first step's five - three held at gates, two already gone."""
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    box.apply_runner = GateRunner(
        {
            "j00": ("failed", "login_issue", []),
            "j01": ("failed", "captcha", []),
            "j02": ("failed", "sso_required", []),
            "j03": ("failed", "expired", []),
            "j04": ("failed", "expired", []),
        }
    )
    return step(box)


def test_a_first_step_held_at_gates_waits_instead_of_voiding(box):  # G17
    batch = first_step_held(box)
    status = box.apply_status()
    assert status["void"] is False and status["next"] is None
    assert "第一步还在等 1、2、3 处理完" in status["notice"]
    rest = box._phase_summary(batch["batch_id"], box.store.apply_batch(batch["batch_id"]), "rest")
    with pytest.raises(InvariantError, match="1、2、3"):
        box.start_applying(batch["batch_id"], rest)


def test_one_held_job_that_worked_after_all_lets_the_second_step_start(box):  # G17, G-d
    batch = first_step_held(box)
    attempt = {"batch_id": batch["batch_id"], "phase": "resume", "mode": "review", "via": "form_fill"}
    box.store.record_application_result(
        "j01", "waiting", "check_then_submit", {**attempt, "reason": "check_then_submit"}
    )
    status = box.apply_status()
    assert status["void"] is False and status["next"]["phase"] == "rest"
    box.mark_applications(["j01"], "skipped")  # the user later skipped that form: it still worked
    box.start_applying(batch["batch_id"], box.apply_status()["next"]["summary"])
    assert box.store.applications()["j05"]["status"] == "waiting"


def test_a_first_step_whose_held_jobs_all_came_to_nothing_is_void(box):  # G17
    batch = first_step_held(box)
    box.mark_applications(["j00", "j01"], "skipped")
    assert box.apply_status()["void"] is False  # 3 still waits
    box.mark_applications(["j02"], "pending")
    status = box.apply_status()
    assert status["void"] is True and status["next"] is None
    assert box.store.apply_batch(batch["batch_id"])["phases"]["rest"] == "void"


# --- G10-G16: resuming held jobs -----------------------------------------------------------------------------------

STEP2 = "https://careers.acme.test/apply/step2"  # where the user's sign-in left Acme's tab


def held_three(box):
    """2 Co1 (reviewed, CAPTCHA, its tab N2-j01), 7 Acme (auto-submit, login, tab N7 still open) and 8 Beta
    (auto-submit, CAPTCHA, its tab closed by the user)."""
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    box.apply_runner = GateRunner(
        {
            "j01": ("failed", "captcha", ["jobs.example.test: tick the human check"]),
            "j06": ("held", "login_issue", ["careers.acme.test: sign in to continue"]),
            "j07": ("held", "captcha", ["careers.beta.test: tick the human check"]),
        },
        tabs={"j06": ACME_TAB, "j07": {"id": "N8", "url": "https://careers.beta.test/apply"}},
    )
    batch = both_steps(box)
    box.form_fill_runner.tabs.append({"id": "N7", "type": "page", "url": STEP2})  # the user signed in there
    box.apply_runner.outcomes = {}  # from now on every run goes through
    box.notices.clear()
    return batch


SUMMARY = (
    "接着做 3 个：自动提交 2 个：7 Acme（回原标签页）、8 Beta（从岗位地址重开）；"
    "停在提交前 1 个（填表模块从岗位地址重填）：2 Co1"
)
HELD = ["j01", "j06", "j07"]


def resumed(box, job_ids=HELD):
    plan = box.prepare_resume(job_ids)
    box.resume_applying(plan["job_ids"], plan["summary"])
    return plan


def test_the_summary_says_how_each_held_job_goes_on(box):  # G10
    held_three(box)
    plan = box.prepare_resume(HELD)
    assert plan["summary"] == SUMMARY and plan["job_ids"] == HELD and plan["problems"] == []
    assert box.store.holds()[0]["resuming"] is False  # preparing changes nothing


def test_resuming_takes_over_the_tab_submits_and_refills(box):  # G11 a, c; G13
    batch = held_three(box)
    calls = len(box.apply_runner.calls)
    resumed(box)
    submitted = [spec for spec in box.apply_runner.calls[calls:] if spec.mode == "submit"]
    acme, beta = submitted
    assert (acme.job_id, acme.resume, acme.resume_url, acme.resume_tab) == ("j06", True, STEP2, "N7")
    assert (beta.job_id, beta.resume, beta.resume_url, beta.resume_tab) == ("j07", True, None, None)
    assert "== ANSWER SHEET ==" in acme.sheet and "== ANSWER SHEET ==" in beta.sheet
    co1, _ = box.form_fill_runner.fills[-1]
    assert (co1.job_id, co1.mode) == ("j01", "stop")
    applications = box.store.applications()
    assert [applications[j]["status"] for j in HELD] == ["waiting", "applied", "applied"]
    assert box.store.holds() == []
    rows = box.store.apply_attempts()[-3:]
    assert [(r["job_id"], r["phase"], r["mode"], r["via"], r["batch_id"]) for r in rows] == [
        ("j01", "resume", "review", "form_fill", batch["batch_id"]),
        ("j06", "resume", "submit", "applypilot", batch["batch_id"]),
        ("j07", "resume", "submit", "applypilot", batch["batch_id"]),
    ]
    status = box.apply_status()
    assert status["phase"] == "resume" and status["next"] is None and status["held"] == []
    (refill,) = [row for row in status["jobs"] if row["job_id"] == "j01"]
    assert any("原来等你处理的那个标签页可以关掉" in item for item in refill["todo"])
    assert box.notices == [("投递：1 个填好等你检查", "接着做做完")]
    (decision,) = [d for d in box.store.decisions() if d["kind"] == "resume"]
    assert json.loads(decision["payload"]) == {"job_ids": HELD, "approved_via": "delegated_coordinator"}


def test_two_tabs_on_the_same_address_keep_that_job_waiting(box):  # G11 d
    held_three(box)
    box.form_fill_runner.tabs.append({"id": "X9", "type": "page", "url": STEP2})
    plan = box.prepare_resume(HELD)
    assert plan["job_ids"] == ["j01", "j07"]
    assert plan["problems"] == [{"job_id": "j06", "problem": "7 Acme：同一个地址开着 2 个标签页，关掉多余的再恢复"}]
    with pytest.raises(InvariantError, match="summary"):
        box.resume_applying(HELD, plan["summary"])
    box.resume_applying(plan["job_ids"], plan["summary"])
    assert box.store.hold("j06")["resuming"] is False and box.store.applications()["j06"]["status"] == "held"


def test_a_tab_the_user_already_submitted_is_counted_without_an_agent(box):  # G12
    held_three(box)
    box.form_fill_runner.tab_states["N7"] = PageState(actions=(), received="Thank you for applying!")
    calls = len(box.apply_runner.calls)
    resumed(box, ["j06"])
    assert box.apply_runner.calls[calls:] == [] and box.form_fill_runner.glances[-1] == "N7"
    application = box.store.applications()["j06"]
    assert application["status"] == "applied" and application["note"].startswith("submitted_by_user；")
    audit = [json.loads(d["payload"]) for d in box.store.decisions() if d["kind"] == "observed_submission"]
    assert audit[-1]["actor"] == "unknown" and audit[-1]["evidence"] == "held_tab_received"
    row = box.store.apply_attempts()[-1]
    assert (row["status"], row["reason"], row["phase"]) == ("applied", "submitted_by_user", "resume")
    assert box.apply_stats()["weeks"][0]["submitted"] == 1  # the user's own submission (G-e)


def test_a_job_held_a_third_time_fails_at_its_gate(box):  # G14
    held_three(box)
    box.apply_runner.outcomes = {"j07": ("held", "captcha", ["careers.beta.test: a CAPTCHA on the review page"])}
    resumed(box, ["j07"])
    hold = box.store.hold("j07")
    assert (hold["times"], hold["reason"]) == (2, "captcha") and box.store.applications()["j07"]["status"] == "held"
    assert box.notices == [("投递：1 个等你处理", "1 个验证码：8 Beta")]
    resumed(box, ["j07"])
    application = box.store.applications()["j07"]
    assert application["status"] == "failed" and application["note"].startswith("captcha；第三次卡在门槛")
    assert box.store.hold("j07") is None
    assert box.apply_stats()["weeks"][0]["allowed"] == {"captcha": 1}


def test_resuming_refuses_what_it_cannot_do_safely(box):  # G15
    held_three(box)
    with pytest.raises(InvariantError, match="summary"):
        box.resume_applying(HELD, SUMMARY.replace("8 Beta", "8 Gamma"))
    with pytest.raises(InvariantError, match="held"):
        box.prepare_resume(["j00"])  # waiting, not held
    with pytest.raises(InvariantError):
        box.prepare_resume([])
    box._apply_run = {"status": "running", "phase": "rest", "jobs": []}
    with pytest.raises(InvariantError, match="running"):
        box.resume_applying(HELD, SUMMARY)
    box._apply_run = {"status": "running", "phase": "resume", "jobs": [{"job_id": "j06"}]}
    with pytest.raises(InvariantError):
        box.start_applying("batch-x", "x")
    with pytest.raises(InvariantError, match="j06"):
        box.mark_applications(["j06"], "applied")


def test_a_resume_cut_short_is_flagged_next_time(box):  # G16
    held_three(box)
    box.store.set_resuming(["j06"], True)  # the server stopped while j06 was being resumed
    fresh = Toolbox(box.settings, Store(box.store.path, box.settings), data_dir=box.data_dir)
    fresh.form_fill_runner = box.form_fill_runner
    (acme,) = [row for row in fresh.apply_status()["held"] if row["job_id"] == "j06"]
    assert acme["interrupted"] is True
    plan = fresh.prepare_resume(["j06"])
    assert (
        plan["summary"]
        == "接着做 1 个：自动提交 1 个：7 Acme（上次接着做时中断，可能已经交过，请先看标签页；回原标签页）"
    )


def test_resuming_is_the_users_own_approval_and_never_scheduled():  # spec 5.1, 5.2, section 10 item 100
    from intent_job_agent.tools import COMMIT_TOOLS, SCHEDULED_TOOLS, TOOL_TIERS

    assert TOOL_TIERS["resume_applying"] == "commit" and TOOL_TIERS["prepare_resume"] == "prepare"
    assert "resume_applying" in COMMIT_TOOLS and not {"resume_applying", "prepare_resume"} & SCHEDULED_TOOLS


def test_a_held_tabs_address_is_masked_before_it_is_kept(box):  # privacy: CLAUDE.md, spec 4.10 item 8
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    leaky = {"id": "N7", "url": "https://careers.acme.test/verify?email=sam.synthetic@example.test"}
    box.apply_runner = GateRunner({"j06": ("held", "email_verification", [])}, tabs={"j06": leaky})
    both_steps(box)
    kept = box.store.hold("j06")
    assert "sam.synthetic" not in kept["tab_url"] and kept["tab_url"].startswith("https://careers.acme.test/verify")
    assert not any(secret in json.dumps(box.apply_status(), ensure_ascii=False) for secret in SECRETS)
    assert not any(secret in json.dumps(box.store.holds(), ensure_ascii=False) for secret in SECRETS)


# --- review fixes (gate-handoff-review.md, 2026-10-21) -------------------------------------------------------------

RECEIVED = PageState(actions=(), received="Thank you, your application has been received")


def test_a_resume_that_may_have_submitted_voids_the_batch(box):  # review 1: spec section 10 item 90
    batch = first_step_held(box)
    box.apply_runner.outcomes = {}  # j01 goes through
    box.form_fill_runner.states["j01"] = RECEIVED  # the refilled form says it was received
    resumed(box, ["j01"])
    assert box.store.applications()["j01"]["status"] == "applied"
    data = box.store.apply_batch(batch["batch_id"])
    box._settle(batch["batch_id"])
    data = box.store.apply_batch(batch["batch_id"])
    assert data["void"] is True and data["phases"]["rest"] == "void"
    assert data["void_reason"] == {"why": "maybe_submitted", "job_id": "j01"}
    rest = box._phase_summary(batch["batch_id"], data, "rest")
    with pytest.raises(InvariantError):
        box.start_applying(batch["batch_id"], rest)
    assert not [spec for spec in box.apply_runner.calls if spec.mode == "submit"]
    assert "可能已提交" in box.apply_status()["notice"]


def test_after_a_resume_that_worked_the_second_step_is_offered(box):  # review 2: decision 23 G-d, item 101
    batch = first_step_held(box)
    box.apply_runner.outcomes = {}  # j01 goes through this time
    resumed(box, ["j01"])
    assert box.store.applications()["j01"]["status"] == "waiting"
    upcoming = box.apply_status()["next"]
    assert (upcoming["batch_id"], upcoming["phase"]) == (batch["batch_id"], "rest")
    box.start_applying(upcoming["batch_id"], upcoming["summary"])  # the user said "继续"
    assert [spec.job_id for spec in box.apply_runner.calls if spec.mode == "submit"] == ["j06", "j07"]


def test_a_resume_that_did_not_settle_the_first_step_says_what_it_waits_for(box):  # review 2
    first_step_held(box)
    box.apply_runner.outcomes = {"j01": ("failed", "captcha", [])}  # held again
    resumed(box, ["j01"])
    status = box.apply_status()
    assert status["next"] is None and "第一步还在等 1、2、3 处理完" in status["notice"]


def test_a_step_offers_its_next_step_with_the_batch(box):
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    batch = step(box)
    assert box.apply_status()["next"]["batch_id"] == batch["batch_id"]


def test_a_held_form_says_to_resume_without_requiring_a_user_phrase(box):  # review 3: spec section 10 items 91, 97
    first_step_held(box)
    rows = {row["job_id"]: row for row in box.apply_status()["jobs"]}
    for job_id, gate in (("j00", "登录和注册都不成"), ("j01", "验证码"), ("j02", "单点登录")):
        todo = rows[job_id]["todo"]
        assert todo and gate in todo[0] and "恢复" in todo[0] and "好了" not in todo[0], job_id
        assert not any("重跑" in item for item in todo), job_id
    assert any("重跑" in item for item in rows["j03"]["todo"]) or rows["j03"]["result"] == "failed"


def test_a_resumed_job_the_agent_finds_already_submitted_counts_as_the_users(box):  # review 4: decision 23 G-e
    held_three(box)
    said = ["The page says the application was submitted on 2026-10-21"]
    box.apply_runner.outcomes = {
        "j06": ("applied", "already_applied", said),
        "j01": ("applied", "already_applied", said),
    }
    resumed(box, ["j06", "j01"])  # the glance at the tab missed it (wording not recognised): the agents saw it
    rows = box.store.apply_attempts()[-2:]
    assert [(r["job_id"], r["status"], r["reason"]) for r in rows] == [
        ("j06", "applied", "submitted_by_user"),
        ("j01", "applied", "submitted_by_user"),
    ]
    assert box.store.applications()["j06"]["note"].startswith("submitted_by_user")
    assert box.apply_stats()["weeks"][0]["submitted"] == 2


def test_already_applied_outside_a_resume_is_still_not_a_new_submission(box):  # spec section 10 item 63
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    box.apply_runner = GateRunner({"j06": ("applied", "already_applied", [])})
    both_steps(box)
    assert box.store.apply_attempts()[-2]["reason"] == "already_applied"
    assert box.apply_stats()["weeks"][0]["not_counted"]["already_applied"] == 1


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("Acme‮etaN olleH", "AcmeetaN olleH"),  # right-to-left override: would show the text reversed
        ("Ac​me⁦x⁩", "Acmex"),  # zero-width space, isolates
        ("Beta﻿‍Health", "BetaHealth"),  # byte-order mark, zero-width joiner
        ("Line two three", "Line two three"),  # line and paragraph separators
        ("Tab\there\x85next", "Tab here next"),  # controls become spaces
    ],
)
def test_a_company_name_cannot_disguise_the_notification(name, shown):  # review 5: spec section 10 item 99
    assert notify.clean(name) == shown


class WatchingRunner(GateRunner):
    """Notes, when each job is run, which held jobs are marked as being resumed."""

    def __init__(self, box, outcomes=None):
        super().__init__(outcomes)
        self.box, self.marked = box, {}

    def __call__(self, spec):
        self.marked[spec.job_id] = sorted(h["job_id"] for h in self.box.store.holds() if h["resuming"])
        return super().__call__(spec)


def test_each_job_is_marked_as_being_resumed_only_when_its_turn_comes(box):  # review 6: spec section 10 item 102
    held_three(box)
    box.apply_runner = WatchingRunner(box)
    resumed(box, ["j06", "j07"])
    assert box.apply_runner.marked == {"j06": ["j06"], "j07": ["j07"]}
    assert [(h["job_id"], h["resuming"]) for h in box.store.holds()] == [("j01", False)]  # not resumed: untouched
