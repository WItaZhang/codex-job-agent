"""/apply through the form-fill module (spec section 10 items 86-96; samples S1, S2, S4-S6, S8, S11, S12 of
specs/apply-form-fill.samples.md): the reviewed jobs and the careful companies are filled by the form-fill module and
stop before submit, the rest are submitted by ApplyPilot with the answer sheet; each result is mapped to the apply
list and logged as an attempt; apply_stats gives the week's numbers.
All data is SYNTHETIC; nothing here starts Chrome or Claude Code."""

import datetime
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from intent_job_agent import apply_bridge
from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill import report as form_fill_report
from intent_job_agent.form_fill.models import PageState
from intent_job_agent.form_fill.recording import RecordingStore, recording_id
from intent_job_agent.form_fill.redact import Redactor
from intent_job_agent.store import Store
from intent_job_agent.tools import SCHEDULED_TOOLS, TOOL_TIERS, Toolbox

from .apply_fakes import ANSWERS, FormFillFake
from .conftest import BASE_INTENT, BASE_VOCAB, settings
from .test_slice6 import DAY, PROFILE, SECRETS, FakeRunner, add_jobs, write_applicant, write_letters

COMPANIES = ["Co0", "Co1", "Co2", "Co3", "Co4", "Stripe, Inc.", "Acme", "Beta"]  # S2
RECEIVED = PageState(actions=(), received="Thank you, your application has been received")


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
    write_applicant(data)
    return box


def ready(box, companies=COMPANIES):
    jobs = add_jobs(box, companies)
    write_letters(box, jobs)
    return jobs


def run_step(box, summary=None, batch=None):
    batch = batch or box.prepare_apply_batch(DAY)
    result = box.start_applying(batch["batch_id"], summary or batch["summary"])
    return batch, result


def direct(box):
    """The specs ApplyPilot ran itself (not through the form-fill module)."""
    through_form_fill = {id(spec) for spec, _ in box.form_fill_runner.fills}
    return [spec for spec in box.apply_runner.calls if id(spec) not in through_form_fill]


# --- S2: which way each job goes ---------------------------------------------------------------------------------


def test_reviewed_and_careful_jobs_are_filled_by_the_form_fill_module_and_the_rest_submitted(box):
    jobs = ready(box)
    batch, _ = run_step(box)
    assert "先投 5 个" in batch["summary"] and "填表模块" in batch["summary"]
    assert [spec.job_id for spec, _ in box.form_fill_runner.fills] == jobs[:5]
    assert all(spec.mode == "stop" for spec, _ in box.form_fill_runner.fills)
    assert direct(box) == []

    rest = box.apply_status()["next"]["summary"]
    assert rest == (
        f"批次 {batch['batch_id']}：接着投 3 个；自动提交 2 个：Acme、Beta；"
        "停在提交前（填表模块逐栏核对）1 个：Stripe, Inc."
    )
    box.start_applying(batch["batch_id"], rest)
    assert [spec.job_id for spec, _ in box.form_fill_runner.fills] == [*jobs[:5], "j05"]
    assert [(spec.job_id, spec.mode) for spec in direct(box)] == [("j06", "submit"), ("j07", "submit")]
    applications = box.store.applications()
    assert {applications[j]["status"] for j in [*jobs[:5], "j05"]} == {"waiting"}
    assert applications["j06"]["status"] == applications["j07"]["status"] == "applied"


def test_auto_submitted_jobs_carry_the_answer_sheet_and_the_checklist(box):
    ready(box, COMPANIES[:6] + ["Acme"])
    batch, _ = run_step(box)
    box.start_applying(batch["batch_id"], box.apply_status()["next"]["summary"])
    (submitted,) = direct(box)
    assert "== ANSWER SHEET ==" in submitted.sheet and "work_auth.sponsorship_future" in submitted.sheet
    assert "websites_linkedin_github_only" in submitted.sheet
    for spec, options in box.form_fill_runner.fills:  # the form-fill module adds its own copy
        assert spec.sheet == "" and "== ANSWER SHEET ==" in options["extra_prompt"]


STOP_RULES = ("ARBITRATION", "SSN", "PAYMENT", "ALREADY APPLIED", "NO AI WRITING")


def test_the_sheet_never_overrides_a_rule_that_says_stop(box):  # review #2
    ready(box, COMPANIES[:6] + ["Acme"])
    batch, _ = run_step(box)
    box.start_applying(batch["batch_id"], box.apply_status()["next"]["summary"])
    (submitted,) = direct(box)
    _, options = box.form_fill_runner.fills[0]
    for sheet in (submitted.sheet, options["extra_prompt"]):
        rules = sheet[sheet.index("these win") :]
        assert "never override" in rules and all(name in rules for name in STOP_RULES)
        assert rules.index("never override") < rules.index("== CHECKLIST ==")


def test_the_chrome_runner_hands_the_sheet_to_the_run(box, monkeypatch):
    seen = []
    monkeypatch.setattr(apply_bridge, "configure", lambda *args, **kwargs: None)
    monkeypatch.setattr(apply_bridge, "cdp_alive", lambda port: True)

    def run(spec, *args, **kwargs):
        seen.append(kwargs)
        return apply_bridge.ApplyOutcome("applied", None)

    monkeypatch.setattr(apply_bridge, "run_application", run)
    (job,) = ready(box, ["Acme"])
    runner = apply_bridge.ChromeRunner(box.data_dir, box.settings, box._applicant().profile)
    runner.port = 9
    spec = box._apply_spec(job, "submit")
    runner(spec)
    runner(apply_bridge.ApplySpec(**{**spec.__dict__, "sheet": "== ANSWER SHEET ==\n- x -> y"}))
    assert seen[0].get("extra_prompt", "") == ""  # without a sheet the run is /apply's, unchanged
    assert seen[1]["extra_prompt"] == "== ANSWER SHEET ==\n- x -> y"


# --- S1: the list --------------------------------------------------------------------------------------------------


def test_each_step_writes_a_checked_form_fill_list(box):
    jobs = ready(box)
    batch, _ = run_step(box)
    path = box.data_dir / "form-fill" / "lists" / f"{batch['batch_id']}-review.yaml"
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    assert [e["id"] for e in entries] == jobs[:5]
    first = entries[0]
    assert first["url"] == "https://jobs.example.test/j00" and first["company"] == "Co0" and first["resume"] == "ml"
    assert first["revisit"] is True
    assert first["cover_letter"] == str((box.data_dir / "apply" / "letters" / "j00.txt").resolve())


def test_a_bad_input_starts_nothing(box):
    ready(box, COMPANIES[:2])
    (box.data_dir / "applicant" / "answers.yaml").write_text("version: 1\nentries: {name.first: {}}\n", "utf-8")
    batch = box.prepare_apply_batch(DAY)
    with pytest.raises(InvariantError, match="answers"):
        box.start_applying(batch["batch_id"], batch["summary"])
    assert box.apply_runner.calls == [] and box.form_fill_runner.fills == []
    assert not [d for d in box.store.decisions() if d["kind"] == "apply"]
    assert box.store.applications()["j00"]["status"] == "pending"


def test_a_link_that_is_not_https_is_listed_before_the_step(box):
    jobs = ready(box, ["Acme", "Beta"])
    box.store.db.execute(
        "UPDATE jobs SET data = json_set(data, '$.url', 'http://jobs.example.test/j01') WHERE id = 'j01'"
    )
    box.store.db.commit()
    batch = box.prepare_apply_batch(DAY)
    assert [p["job_id"] for p in batch["problems"]] == ["j01"]
    with pytest.raises(InvariantError, match="https"):
        box.start_applying(batch["batch_id"], batch["summary"])
    fewer = box.prepare_apply_batch(DAY, exclude=["j01"])
    assert fewer["problems"] == [] and [j["job_id"] for j in fewer["jobs"]] == jobs[:1]


# --- S3-S5: results ------------------------------------------------------------------------------------------------


def test_results_reach_the_apply_list_with_the_code_first_in_the_note(box):
    jobs = ready(box, COMPANIES[:5])
    box.apply_runner = FakeRunner(
        {
            "j01": ("failed", "captcha", ["Cloudflare check on the first page."]),
            "j02": ("applied", "already_applied", ["The site says you applied in 2026-09."]),
            "j03": ("pending", "system_error", ["Error: cannot start"]),
            "j04": ("failed", "timeout", []),
        }
    )
    _, result = run_step(box)
    run_id = box.apply_status()["run_id"]
    applications = box.store.applications()
    assert [applications[j]["status"] for j in jobs] == ["waiting", "held", "applied", "pending", "failed"]
    assert applications["j00"]["note"].startswith(f"check_then_submit；检查后可提交；填表运行 {run_id}")
    assert applications["j01"]["note"].startswith("captcha；没填完：门槛；")
    assert "Cloudflare check on the first page." in applications["j01"]["note"]
    assert applications["j02"]["note"].startswith("already_applied；")
    assert applications["j03"]["note"].startswith("system_error；")
    assert applications["j04"]["note"].startswith("timeout；没填完：卡住；")
    assert result["jobs"] == 5


def test_nothing_worked_in_the_first_step_voids_the_batch(box):  # S4
    ready(box)
    box.apply_runner = FakeRunner(
        {
            "j00": ("failed", "arbitration", []),  # not a gate: a gate would wait for the user (decision 22)
            "j01": ("failed", "payment_required", []),
            "j02": ("failed", "expired", []),
            "j03": ("pending", "system_error", ["Error: cannot start"]),
        }
    )
    box.form_fill_runner = FormFillFake(box, no_tab={"j04"})
    run_step(box)
    status = box.apply_status()
    assert status["void"] is True and status["next"] is None
    applications = box.store.applications()
    assert applications["j03"]["status"] == "pending" and applications["j04"]["status"] == "failed"
    assert applications["j04"]["note"].startswith("tab_lost；")


def test_a_failed_agent_whose_page_cannot_be_read_back_is_a_failure(box):  # review #3
    jobs = ready(box)
    box.apply_runner = FakeRunner({job: ("failed", "wrong_job_redirect", []) for job in jobs[:5]})
    box.form_fill_runner = FormFillFake(box, unreadable=set(jobs[:5]))
    run_step(box)
    assert {box.store.applications()[j]["status"] for j in jobs[:5]} == {"failed"}
    assert box.apply_status()["void"] is True  # nothing worked in the first step
    assert [a["reason"] for a in box.store.apply_attempts()] == ["wrong_job_redirect"] * 5


def test_a_maybe_submitted_form_stops_the_step_and_voids_the_batch(box):  # S5
    jobs = ready(box)
    box.form_fill_runner = FormFillFake(box, states={"j02": RECEIVED})
    run_step(box)
    assert [spec.job_id for spec, _ in box.form_fill_runner.fills] == jobs[:3]
    applications = box.store.applications()
    assert applications["j02"]["status"] == "applied" and applications["j02"]["note"].startswith("maybe_submitted；")
    assert applications["j03"]["status"] == applications["j04"]["status"] == "pending"
    status = box.apply_status()
    assert status["void"] is True and status["next"] is None and "可能" in status["notice"]
    states = {item["job_id"]: item["state"] for item in status["jobs"]}
    assert states["j03"] == states["j04"] == "stopped"


class ReceivedThenBroken(FormFillFake):
    """The page said the application was received, then the filling agent's run raised (as apply_bridge does when
    Claude Code does not exit in time): the job's own report is "maybe submitted" (review #1 path a)."""

    def fill(self, spec, **options):
        if spec.job_id != "j01":
            return super().fill(spec, **options)
        run_dir = Path(options["log_path"]).parent.parent
        root = run_dir.parent.parent / self.box.settings.form_fill.recordings_dir
        store = RecordingStore(root, recording_id(spec.url), run_dir.name, Redactor.from_profile(PROFILE))
        store.note_received("Thank you, your application has been received", spec.url + "/done")
        raise subprocess.TimeoutExpired(cmd="claude", timeout=60)


def test_a_run_that_broke_after_the_page_said_received_still_stops_and_voids(box):
    jobs = ready(box, COMPANIES[:6])
    box.form_fill_runner = ReceivedThenBroken(box)
    run_step(box)
    application = box.store.applications()["j01"]
    assert application["status"] == "applied" and application["note"].startswith("maybe_submitted；")
    status = box.apply_status()
    assert status["void"] is True and status["next"] is None
    assert [spec.job_id for spec, _ in box.form_fill_runner.fills] == jobs[:1]  # j01 raised; j02-j04 never ran
    assert {item["state"] for item in status["jobs"][2:]} == {"stopped"}
    assert "j01" not in [j["job_id"] for j in box.prepare_apply_batch(DAY)["jobs"]]  # never applied to again


class SheetBrokenAfter(FormFillFake):
    """answers.yaml is broken while the step runs, right after `job`'s form is filled, so its report fails."""

    def __init__(self, box, job, **kwargs):
        super().__init__(box, **kwargs)
        self.job = job

    def fill(self, spec, **options):
        outcome = super().fill(spec, **options)
        if spec.job_id == self.job:
            (self.box.data_dir / "applicant" / "answers.yaml").write_text("version: 1\nentries: [oops\n", "utf-8")
        return outcome


def test_without_a_report_a_received_page_still_stops_the_step(box):  # review #1 path b
    ready(box, COMPANIES)
    batch, _ = run_step(box)
    box.form_fill_runner = SheetBrokenAfter(box, "j05", states={"j05": RECEIVED})
    box.start_applying(batch["batch_id"], box.apply_status()["next"]["summary"])
    applications = box.store.applications()
    assert applications["j05"]["status"] == "applied" and applications["j05"]["note"].startswith("maybe_submitted；")
    assert "报告没出来" in applications["j05"]["note"]
    assert applications["j06"]["status"] == applications["j07"]["status"] == "pending"  # nothing auto-submitted
    assert [s for s in box.apply_runner.calls if s.mode == "submit"] == [] and box.apply_status()["void"] is True


def test_without_a_report_a_filled_form_waits_and_a_failure_fails(box):
    ready(box, COMPANIES[:2])
    box.apply_runner = FakeRunner({"j01": ("failed", "wrong_job_redirect", [])})
    box.form_fill_runner = SheetBrokenAfter(box, "j00")
    run_step(box)
    applications = box.store.applications()
    assert applications["j00"]["status"] == "waiting" and applications["j00"]["note"].startswith("unreadable；")
    assert applications["j01"]["status"] == "failed" and applications["j01"]["note"].startswith("wrong_job_redirect；")


# --- S6: rerun ---------------------------------------------------------------------------------------------------


def test_a_rerun_batch_holds_only_the_named_jobs_and_never_submits(box):
    jobs = ready(box)
    box.apply_runner = FakeRunner({"j03": ("failed", "captcha", [])})
    run_step(box)
    with pytest.raises(InvariantError, match="pending"):  # still failed: the agent marks it pending first
        box.prepare_apply_batch(DAY, only=["j03"], stop_before_submit=True)
    box.mark_applications(["j03"], "pending")
    box.apply_runner = FakeRunner()
    again = box.prepare_apply_batch(DAY, only=["j03"], stop_before_submit=True)
    assert [(j["job_id"], j["mode"]) for j in again["jobs"]] == [("j03", "review")]
    run_step(box, batch=again)
    assert box.store.applications()["j03"]["status"] == "waiting"

    many = box.prepare_apply_batch(
        DAY, only=jobs[5:], stop_before_submit=True
    )  # more than review_first: still all reviewed
    assert {j["mode"] for j in many["jobs"]} == {"review"} and len(many["jobs"]) == 3
    with pytest.raises(InvariantError):
        box.prepare_apply_batch(DAY, only=["j99"])


# --- S7: profile.json against answers.yaml -------------------------------------------------------------------------


def test_profile_and_answers_that_disagree_stop_both_steps(box):
    ready(box, COMPANIES[:6] + ["Acme"])
    sheet = ANSWERS + "  compensation.expected:\n    answer: 90000\n    kind: number_min\n"
    (box.data_dir / "applicant" / "answers.yaml").write_text(sheet, encoding="utf-8")
    setup = box.get_apply_setup()
    assert setup["profile_conflicts"] == ["compensation.salary_expectation / compensation.expected"]
    batch = box.prepare_apply_batch(DAY)
    with pytest.raises(InvariantError) as error:
        box.start_applying(batch["batch_id"], batch["summary"])
    assert "compensation.expected" in str(error.value) and "90000" not in str(error.value)
    assert "150000" not in str(error.value)
    assert box.apply_runner.calls == []


# --- S8: what apply_status shows -----------------------------------------------------------------------------------


def test_apply_status_shows_the_form_fill_report_of_each_job(box):
    ready(box, COMPANIES[:6] + ["Acme"])
    batch, _ = run_step(box)
    status = box.apply_status()
    first = status["jobs"][0]
    assert first["via"] == "form_fill" and first["conclusion"] == "检查后可提交"
    assert first["todo"] and first["todo"][0].startswith("检查后可提交")
    assert first["run_id"] == status["run_id"] and first["recording_id"]
    report = json.loads(open(first["report"], encoding="utf-8").read())
    assert report["job_id"] == "j00" and report["conclusion"] == "check_then_submit"
    assert status["summary"].endswith("summary.md") and open(status["summary"], encoding="utf-8").read()
    box.start_applying(batch["batch_id"], status["next"]["summary"])
    by_id = {item["job_id"]: item for item in box.apply_status()["jobs"]}
    assert by_id["j06"]["via"] == "applypilot" and by_id["j05"]["via"] == "form_fill"


def test_personal_tabs_open_in_the_applying_chrome_are_named_at_the_start(box):
    ready(box, COMPANIES[:2])
    box.form_fill_runner = FormFillFake(box, personal=["https://mail.google.com/mail/u/0/#inbox"])
    _, result = run_step(box)
    assert result["personal_tabs"] == ["https://mail.google.com"] and "mail.google.com" in result["warning"]


def test_the_checklist_lists_other_sites_of_form_fill_jobs(box):
    ready(box, ["Acme"])

    def runner(spec):
        return apply_bridge.ApplyOutcome("waiting", None, [], ["sso.example.test"], [], None)

    box.apply_runner = runner
    run_step(box)
    assert box.apply_status()["checklist"] == [{"job_id": "j00", "other_sites": ["sso.example.test"], "emails": []}]


def test_no_contact_detail_reaches_the_database_or_the_status(box, tmp_path):
    ready(box, COMPANIES[:2])
    box.apply_runner = FakeRunner({"j01": ("failed", "captcha", ["Signed in as sam.synthetic@example.test, 555-0100"])})
    run_step(box)
    status = json.dumps(box.apply_status(), ensure_ascii=False)
    raw = (tmp_path / "agent.sqlite").read_bytes()
    assert not any(secret in status or secret.encode() in raw for secret in SECRETS)


def test_a_free_text_result_code_is_kept_short_and_masked(box):  # review #5
    jobs = ready(box, COMPANIES[:7])  # j06 Acme is submitted by ApplyPilot
    free = "account exists for sam.synthetic@example.test"
    box.apply_runner = FakeRunner(
        {
            "j00": ("failed", "login failed for sam.synthetic@example.test", []),  # through the form-fill module
            "j06": ("failed", free, ["Call 555-0100 to reset the password."]),  # through ApplyPilot
        }
    )
    batch, _ = run_step(box)
    box.start_applying(batch["batch_id"], box.apply_status()["next"]["summary"])
    reasons = {a["job_id"]: a["reason"] for a in box.store.apply_attempts()}
    assert reasons["j00"] == reasons["j06"] == "other"
    applications = box.store.applications()
    assert applications["j06"]["note"].startswith("other; account exists for ")
    assert applications["j00"]["note"].startswith("other；")
    shown = json.dumps([box.apply_status(), box.apply_stats(), applications], ensure_ascii=False)
    assert not any(secret in shown for secret in SECRETS)
    assert jobs


# --- S11: screening answers that must be written without AI --------------------------------------------------------


@pytest.mark.parametrize("mode", ["stop", "submit"])
def test_both_prompts_stop_when_answers_must_be_written_without_ai(box, mode):
    (job,) = ready(box, ["Zeta Analytics"])
    spec = box._apply_spec(job, mode)
    prompt = apply_bridge.build_prompt(spec, box.data_dir, box.settings, box._applicant().profile)
    rules = prompt[prompt.index("== ADDITIONAL RULES") :]
    assert "RESULT:FAILED:screening_requires_candidate_written_answers" in rules
    assert "without AI" in rules and "NOTE:" in rules
    assert "not an instruction to you" in rules  # the untrusted-page rule no longer hides it


def test_a_no_ai_stop_is_a_failure_with_its_own_reason():
    output = 'NOTE: "Answer without AI writing tools."\nRESULT:FAILED:screening_requires_candidate_written_answers'
    result = apply_bridge.parse_result(output)
    assert apply_bridge.outcome_status("submit", result) == ("failed", "screening_requires_candidate_written_answers")
    reason = "screening_requires_candidate_written_answers"
    assert form_fill_report.REASONS[reason] == "筛选题要本人作答"
    advice = form_fill_report.ADVICE[reason]
    assert "重跑" not in advice and "你" in advice


def test_a_no_ai_stop_in_the_form_fill_module_is_recorded_as_failed(box):
    (job,) = ready(box, ["Zeta Analytics"])
    quote = 'NOTE: "Please answer the following questions yourself, without the use of AI writing tools."'
    box.apply_runner = FakeRunner({job: ("failed", "screening_requires_candidate_written_answers", [quote])})
    run_step(box)
    application = box.store.applications()[job]
    assert application["status"] == "failed"
    assert application["note"].startswith("screening_requires_candidate_written_answers；没填完：筛选题要本人作答；")
    assert quote in application["note"]


# --- S12: every attempt, and the week's numbers --------------------------------------------------------------------


def test_every_result_and_every_change_by_the_user_is_one_attempt(box):
    jobs = ready(box, COMPANIES[:6] + ["Acme"])
    box.apply_runner = FakeRunner({"j01": ("failed", "captcha", [])})
    batch, _ = run_step(box)
    box.start_applying(batch["batch_id"], box.apply_status()["next"]["summary"])
    rows = box.store.apply_attempts()
    assert [(r["job_id"], r["via"], r["phase"], r["mode"], r["status"]) for r in rows] == [
        ("j00", "form_fill", "review", "review", "waiting"),
        ("j01", "form_fill", "review", "review", "held"),
        ("j02", "form_fill", "review", "review", "waiting"),
        ("j03", "form_fill", "review", "review", "waiting"),
        ("j04", "form_fill", "review", "review", "waiting"),
        ("j05", "form_fill", "rest", "careful", "waiting"),
        ("j06", "applypilot", "rest", "submit", "applied"),
    ]
    assert rows[1]["reason"] == "captcha" and rows[0]["run_id"] and rows[6]["run_id"] is None
    assert {r["batch_id"] for r in rows} == {batch["batch_id"]}

    box.mark_applications(["j00", "j01"], "applied")  # the user submitted the first, then the second by hand
    box.mark_applications(["j00"], "applied")  # no change: no new attempt
    users = [r for r in box.store.apply_attempts() if r["via"] == "user"]
    assert [(r["job_id"], r["status"], r["batch_id"], r["phase"]) for r in users] == [
        ("j00", "applied", None, None),
        ("j01", "applied", batch["batch_id"], None),  # j01 was held: its batch decides the first step (item 101)
    ]
    assert box.store.holds() == []
    assert jobs  # all seven were tried


def test_attempts_are_append_only(box):
    ready(box, ["Acme"])
    run_step(box)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        box.store.db.execute("UPDATE apply_attempts SET status = 'applied'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        box.store.db.execute("DELETE FROM apply_attempts")
    assert len(box.store.apply_attempts()) == 1


def test_apply_stats_gives_this_week_and_last_week(box):
    jobs = ready(box, COMPANIES[:6] + ["Acme"])
    box.apply_runner = FakeRunner({"j01": ("failed", "expired", []), "j06": ("failed", "wrong_job_redirect", [])})
    batch, _ = run_step(box)
    box.start_applying(batch["batch_id"], box.apply_status()["next"]["summary"])
    box.mark_applications([jobs[0]], "applied")
    stats = box.apply_stats()
    this, last = stats["weeks"]
    today = datetime.date.today()
    assert this["week"] == (today - datetime.timedelta(days=today.weekday())).isoformat()
    assert last["week"] == (datetime.date.fromisoformat(this["week"]) - datetime.timedelta(days=7)).isoformat()
    assert (this["submitted"], this["failed"]) == (1, 2)
    assert this["allowed"] == {"expired": 1} and this["to_fix"] == {"wrong_job_redirect": 1}
    assert this["not_counted"]["waiting"] == 5 and this["met"] is False
    assert last["submitted"] == last["failed"] == 0
    assert box.apply_stats(week=this["week"])["weeks"] == [this]
    with pytest.raises(InvariantError):
        box.apply_stats(week="yesterday")


def test_apply_stats_is_a_read_tool_scheduled_runs_may_call():
    assert TOOL_TIERS["apply_stats"] == "read" and "apply_stats" in SCHEDULED_TOOLS
