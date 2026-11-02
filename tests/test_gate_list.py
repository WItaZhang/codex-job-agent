"""The gate list (spec 4.10 item 10, section 9, section 10 items 116-121; decision 35; samples L1-L15 of
specs/gate-list.plan.md): an unattended step records a job held at a gate on the list and closes its tab; when the user
has time, the list is re-run live, one job at a time, the user clearing each gate in its tab, and each job's outcome is
recorded again. All data is SYNTHETIC; nothing here starts Chrome or Claude Code, and the clock and the waits are
fakes."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from intent_job_agent import apply_bridge, notify
from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill import readback
from intent_job_agent.form_fill.models import PageState
from intent_job_agent.store import Store
from intent_job_agent.tools import COMMIT_TOOLS, SCHEDULED_TOOLS, TOOL_TIERS, Toolbox

from .apply_fakes import FormFillFake, synthetic_binding
from .conftest import BASE_INTENT, BASE_VOCAB, settings
from .test_slice6 import SECRETS, add_jobs, write_applicant, write_letters

DAY = "2026-10-14"
COMPANIES = ["Stripe, Inc.", "Acme", "Beta"]  # 1 careful (form-fill module), 2 and 3 auto-submitted
CAPTCHA_NOTE = "careers.acme.test: tick the human check"
RECEIVED = PageState(actions=(), received="Thank you for applying!")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Browser(FormFillFake):
    """The applying Chrome as the form-fill fake has it, plus closing a tab by its id (spec section 10 item 116)."""

    def __init__(self, box):
        super().__init__(box)
        self.closed, self.close_fails = [], False

    def close_tab(self, target) -> bool:
        if self.close_fails:
            raise readback.ReadbackError("the page did not answer")
        self.closed.append(target["id"])
        before = len(self.tabs)
        self.tabs = [tab for tab in self.tabs if tab["id"] != target["id"]]
        return len(self.tabs) < before


class SeqRunner:
    """Stands in for ChromeRunner: each job's outcomes in turn (then it goes through); a held job's tab opens in the
    fake Chrome, as the agent's own new tab does."""

    def __init__(self, box, plan=None, prefix="A"):
        self.box, self.plan, self.prefix = box, {k: list(v) for k, v in (plan or {}).items()}, prefix
        self.calls, self.opened = [], {}

    def __call__(self, spec):
        self.calls.append(spec)
        seq = self.plan.get(spec.job_id)
        status, reason, notes = seq.pop(0) if seq else ("waiting" if spec.mode == "stop" else "applied", None, [])
        tab = None
        if status == "held":
            if spec.resume_tab:
                tab = next(t for t in self.box.form_fill_runner.tabs if t["id"] == spec.resume_tab)
            else:
                self.opened[spec.job_id] = self.opened.get(spec.job_id, 0) + 1
                tab = {"id": f"{self.prefix}{self.opened[spec.job_id]}-{spec.job_id}", "type": "page"}
                tab["url"] = f"https://jobs.example.test/{spec.job_id}/apply"
                self.box.form_fill_runner.tabs.append(tab)
            tab = {"id": tab["id"], "url": tab["url"], "generation": "synthetic"}
        return apply_bridge.ApplyOutcome(status, reason, list(notes), tab=tab)


class User:
    """Stands in for the waits of a live re-run (Toolbox.sleep): the clock moves on, and at each look the user does
    the next thing in `script` (None: nothing yet). What apply_status showed at each look is kept."""

    def __init__(self, box, *script):
        self.box, self.script, self.seen, self.errors = box, list(script), [], []

    def __call__(self, seconds):
        self.seen.append(self.box.apply_status().get("needs_you"))
        if self.script:
            action = self.script.pop(0)
            if action is not None:
                try:
                    action(self.box)
                except InvariantError as error:
                    self.errors.append(str(error))
        self.box.clock.now += seconds


def says(answer, **kwargs):
    return lambda box: box.gate_reply(answer, **kwargs)


@pytest.fixture
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    s = settings()
    s = s.model_copy(update={"apply": s.apply.model_copy(update={"review_first": 0})})
    box = Toolbox(s, Store(tmp_path / "agent.sqlite", s), data_dir=data)
    (data / "intent.yaml").write_text(
        yaml.safe_dump({"intent": {**BASE_INTENT, "location": {"us": {"level": "require"}}}, "vocabulary": BASE_VOCAB}),
        encoding="utf-8",
    )
    box.initialize_intent(str(data / "intent.yaml"))
    box.apply_runner = SeqRunner(box)
    box.form_fill_runner = Browser(box)
    box.letter_renderer = lambda text, pdf_path, name, day: pdf_path.write_bytes(b"%PDF-1.4 letter")
    box.apply_in_background = False
    box.notices = []
    box.notifier = lambda title, body: box.notices.append((title, body))
    box.clock = Clock()
    box.sleep = lambda seconds: setattr(box.clock, "now", box.clock.now + seconds)
    write_applicant(box.data_dir)
    return box


def step(box, plan, **options):
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    box.apply_runner = SeqRunner(box, plan)
    batch = box.prepare_apply_batch(DAY)
    box.start_applying(batch["batch_id"], batch["summary"], **options)
    return batch


def listed(box):
    """An unattended step leaves three jobs on the list: 1 Stripe (careful, CAPTCHA), 2 Acme (CAPTCHA) and 3 Beta
    (login), their tabs closed."""
    step(
        box,
        {
            "j00": [("failed", "captcha", ["jobs.example.test: tick the human check"])],
            "j01": [("held", "captcha", [CAPTCHA_NOTE])],
            "j02": [("held", "login_issue", ["careers.beta.test: sign in to continue"])],
        },
        unattended=True,
    )
    box.notices.clear()


def live(box, job_ids=None, gates=None, *script, plan=None):
    """Re-run the list live, the user doing `script` at the looks."""
    box.apply_runner = SeqRunner(box, plan, prefix="R")
    user = box.sleep = User(box, *script)
    prepared = box.prepare_resume(job_ids, gates, live=True)
    box.resume_applying(prepared["job_ids"], prepared["summary"], live=True)
    return user


def attempts(box, job_id):
    return [
        (r["phase"], r["via"], r["status"], r["reason"]) for r in box.store.apply_attempts() if r["job_id"] == job_id
    ]


# --- L1-L4: an unattended step ------------------------------------------------------------------------------------


def test_an_unattended_step_lists_the_gated_job_closes_its_tab_and_goes_on(box):  # L1
    batch = step(box, {"j01": [("held", "captcha", [CAPTCHA_NOTE])]}, unattended=True)
    hold = box.store.hold("j01")
    assert (hold["reason"], hold["note"], hold["times"], hold["batch_id"]) == (
        "captcha",
        CAPTCHA_NOTE,
        1,
        batch["batch_id"],
    )
    assert hold["tab_url"] == "https://jobs.example.test/j01/apply" and hold["held_at"]
    assert hold["tab_id"] is None and hold["generation"] is None  # never taken over: reopened from its address
    assert box.form_fill_runner.closed == ["A1-j01"]
    assert all(tab["id"] != "A1-j01" for tab in box.form_fill_runner.tabs)
    applications = box.store.applications()
    assert [applications[j]["status"] for j in ("j00", "j01", "j02")] == ["waiting", "held", "applied"]
    (decision,) = [json.loads(d["payload"]) for d in box.store.decisions() if d["kind"] == "apply"]
    assert decision["unattended"] is True
    assert box.notices == [("投递：1 个等你处理，1 个填好等你检查", "1 个验证码：2 Acme")]


def test_a_step_the_user_attends_keeps_the_tab_as_before(box):  # L2
    step(box, {"j01": [("held", "captcha", [CAPTCHA_NOTE])]})
    hold = box.store.hold("j01")
    assert (hold["tab_id"], hold["generation"]) == ("A1-j01", "synthetic")
    assert box.form_fill_runner.closed == []
    (decision,) = [json.loads(d["payload"]) for d in box.store.decisions() if d["kind"] == "apply"]
    assert decision["unattended"] is False


def test_an_unattended_form_fill_job_at_a_gate_closes_its_tab_too(box):  # L3
    step(box, {"j00": [("failed", "captcha", ["jobs.example.test: tick the human check"])]}, unattended=True)
    hold = box.store.hold("j00")
    assert hold["via"] == "form_fill" and hold["tab_id"] is None and hold["tab_url"].endswith("/j00/apply")
    assert box.form_fill_runner.closed == ["N1-j00"]


def test_a_tab_that_cannot_be_closed_still_leaves_the_job_on_the_list(box):  # L3
    box.form_fill_runner.close_fails = True
    step(box, {"j01": [("held", "captcha", [CAPTCHA_NOTE])]}, unattended=True)
    hold = box.store.hold("j01")
    assert hold["reason"] == "captcha" and hold["tab_id"] is None and hold["generation"] is None
    assert box.store.applications()["j02"]["status"] == "applied"  # the step went on


def test_the_list_is_shown_with_its_time_its_tab_and_counts_after_a_restart(box):  # L4
    listed(box)
    fresh = Toolbox(box.settings, Store(box.store.path, box.settings), data_dir=box.data_dir)
    status = fresh.apply_status()
    assert status["status"] == "idle"
    assert [(r["n"], r["gate"], r["tab_open"]) for r in status["held"]] == [
        (1, "验证码", False),
        (2, "验证码", False),
        (3, "登录和注册都不成", False),
    ]
    assert all(row["held_at"] for row in status["held"])
    assert status["held_count"] == {"验证码": 2, "登录和注册都不成": 1}


# --- L5: choosing what to re-run ----------------------------------------------------------------------------------

LIVE = "在场重跑 {n} 个（一个一个来，遇到门槛停在那一页等你，每次最多等 10 分钟）："


def test_the_list_is_chosen_by_gate_kind_or_by_job(box):  # L5
    listed(box)
    captcha = box.prepare_resume(gates=["captcha"], live=True)
    assert captcha["job_ids"] == ["j00", "j01"]
    assert captcha["summary"] == LIVE.format(n=2) + (
        "接着做 2 个：自动提交 1 个：2 Acme（从岗位地址重开）；"
        "停在提交前 1 个（填表模块从岗位地址重填）：1 Stripe, Inc."
    )
    assert box.prepare_resume(live=True)["job_ids"] == ["j00", "j01", "j02"]  # the whole list by default
    assert box.prepare_resume(["j02", "j01"], live=True)["job_ids"] == ["j02", "j01"]
    assert box.prepare_resume(["j01", "j02"], ["login_issue"], live=True)["job_ids"] == ["j02"]
    plain = box.prepare_resume(["j01"])  # not live: as before
    assert plain["summary"] == "接着做 1 个：自动提交 1 个：2 Acme（从岗位地址重开）"
    with pytest.raises(InvariantError):
        box.prepare_resume(["j01"], ["sso_required"], live=True)  # nothing left
    with pytest.raises(InvariantError, match="gate"):
        box.prepare_resume(gates=["arbitration"], live=True)
    with pytest.raises(InvariantError, match="summary"):
        box.resume_applying(captcha["job_ids"], captcha["summary"])  # prepared live, started not live
    with pytest.raises(InvariantError, match="unattended"):
        box.resume_applying(captcha["job_ids"], captcha["summary"], live=True, unattended=True)
    assert all(not hold["resuming"] for hold in box.store.holds())


def test_an_interrupted_hold_is_only_re_run_when_named(box):  # L5
    listed(box)
    box.store.update_hold("j02", reason="interrupted")
    assert box.prepare_resume(live=True)["job_ids"] == ["j00", "j01"]
    assert box.prepare_resume(["j02"], live=True)["job_ids"] == ["j02"]


# --- L6-L11: waiting for the user ---------------------------------------------------------------------------------


def test_the_user_clears_the_gate_and_the_job_goes_on_in_that_tab(box):  # L6
    listed(box)
    user = live(box, ["j01"], None, None, says("done"), plan={"j01": [("held", "captcha", [CAPTCHA_NOTE])]})
    first, again = box.apply_runner.calls
    assert (first.resume, first.resume_tab, first.mode) == (True, None, "submit")  # its tab was gone: reopened
    assert (again.resume, again.resume_tab, again.resume_url) == (True, "R1-j01", "https://jobs.example.test/j01/apply")
    waiting = user.seen[0]
    assert {k: waiting[k] for k in ("n", "job_id", "company", "gate", "site", "todo")} == {
        "n": 2,
        "job_id": "j01",
        "company": "Acme",
        "gate": "验证码",
        "site": "jobs.example.test",
        "todo": CAPTCHA_NOTE,
    }
    assert waiting["seconds_left"] == 600 and set(waiting["replies"]) == {"done", "skip", "decline", "submitted"}
    assert box.notices[0] == ("投递：现在需要你", "验证码：2 Acme。处理完说“好了”")
    assert box.store.applications()["j01"]["status"] == "applied" and box.store.hold("j01") is None
    assert attempts(box, "j01")[-2:] == [
        ("resume", "applypilot", "held", "captcha"),
        ("resume", "applypilot", "applied", None),
    ]
    status = box.apply_status()
    assert status.get("needs_you") is None and [row["job_id"] for row in status["held"]] == ["j00", "j02"]


def test_a_page_that_says_received_is_the_users_own_submission(box):  # L7
    listed(box)

    def submits(box):
        tab = next(t for t in box.form_fill_runner.tabs if t["id"] == "R1-j01")
        tab["url"] = "https://jobs.example.test/j01/thanks"
        box.form_fill_runner.tab_states["R1-j01"] = RECEIVED

    live(box, ["j01"], None, submits, plan={"j01": [("held", "captcha", [CAPTCHA_NOTE])]})
    assert len(box.apply_runner.calls) == 1  # no agent after it
    application = box.store.applications()["j01"]
    assert application["status"] == "applied" and application["note"].startswith("submitted_by_user")
    assert attempts(box, "j01")[-1] == ("resume", "applypilot", "applied", "submitted_by_user")
    audit = [json.loads(d["payload"]) for d in box.store.decisions() if d["kind"] == "observed_submission"]
    assert audit[-1]["actor"] == "unknown" and audit[-1]["job_id"] == "j01"
    assert box.form_fill_runner.closed[-1:] != ["R1-j01"]  # the user's confirmation page stays


def test_a_gate_not_cleared_in_time_goes_back_on_the_list_and_the_third_one_fails(box):  # L8
    listed(box)
    plan = {"j01": [("held", "captcha", [CAPTCHA_NOTE])]}
    live(box, ["j01"], None, plan=plan)  # the user never does anything
    hold = box.store.hold("j01")
    assert (hold["times"], hold["reason"], hold["tab_id"]) == (2, "captcha", None)
    assert box.store.applications()["j01"]["status"] == "held" and "R1-j01" in box.form_fill_runner.closed
    assert box.clock.now >= 1000 + 600
    assert attempts(box, "j01")[-1] == ("resume", "applypilot", "held", "captcha")  # no extra row for the timeout
    live(box, ["j01"], None, plan=plan)
    application = box.store.applications()["j01"]
    assert application["status"] == "failed" and application["note"].startswith(
        "captcha；第三次卡在门槛（在场重跑时没处理完）"
    )
    assert box.store.hold("j01") is None and attempts(box, "j01")[-1] == ("resume", "applypilot", "failed", "captcha")
    assert box.apply_stats()["weeks"][0]["allowed"] == {"captcha": 1}


def test_skipping_keeps_the_job_on_the_list_uncounted(box):  # L9
    listed(box)
    live(box, ["j01"], None, says("skip"), plan={"j01": [("held", "captcha", [CAPTCHA_NOTE])]})
    hold = box.store.hold("j01")
    assert (hold["times"], hold["tab_id"], hold["generation"]) == (1, None, None)
    assert box.form_fill_runner.closed[-1] == "R1-j01" and box.store.applications()["j01"]["status"] == "held"
    assert box.apply_stats()["weeks"][0]["allowed"] == {}


def test_a_tab_the_user_closed_counts_as_skipped(box):  # L9
    listed(box)

    def closes(box):
        box.form_fill_runner.tabs = [t for t in box.form_fill_runner.tabs if t["id"] != "R1-j01"]

    closed = len(box.form_fill_runner.closed)
    live(box, ["j01"], None, closes, plan={"j01": [("held", "captcha", [CAPTCHA_NOTE])]})
    hold = box.store.hold("j01")
    assert (hold["times"], hold["tab_id"]) == (1, None) and len(box.form_fill_runner.closed) == closed


@pytest.mark.parametrize(
    ("answer", "status", "attempt", "closed"),
    [
        ("decline", "skipped", ("failed", "captcha"), True),  # "不处理了": an allowed failure at its gate
        ("submitted", "applied", ("applied", None), False),  # "已投": the user submitted it in the tab
    ],
)
def test_the_user_declines_or_submits_the_waiting_job(box, answer, status, attempt, closed):  # L10
    listed(box)
    live(box, ["j01"], None, says(answer, user_report="the user said so"), plan={"j01": [("held", "captcha", [])]})
    assert box.store.applications()["j01"]["status"] == status and box.store.hold("j01") is None
    row = box.store.apply_attempts()[-1]
    assert (row["job_id"], row["via"], row["status"], row["reason"]) == ("j01", "user", *attempt)
    assert ("R1-j01" in box.form_fill_runner.closed) is closed
    decision = [json.loads(d["payload"]) for d in box.store.decisions() if d["kind"] == "application_status"][-1]
    assert decision["job_ids"] == ["j01"] and decision["approved_via"] == "user" and decision["status"] == status


def test_stopping_ends_the_wait_and_keeps_the_job_and_its_tab(box):  # L11
    listed(box)
    messages = []
    user = live(
        box,
        ["j01", "j02"],
        None,
        lambda box: messages.append(box.stop_applying()["message"]),
        plan={"j01": [("held", "captcha", [CAPTCHA_NOTE])]},
    )
    hold = box.store.hold("j01")
    assert (hold["times"], hold["tab_id"], hold["generation"], hold["resuming"]) == (1, "R1-j01", "synthetic", False)
    assert "R1-j01" not in box.form_fill_runner.closed and box.store.hold("j02")["tab_id"] is None
    assert [spec.job_id for spec in box.apply_runner.calls] == ["j01"]  # the next one never started
    assert "清单" in messages[0] and user.seen[0]["job_id"] == "j01"
    assert box.apply_status()["status"] == "stopped"


# --- L12-L15 ------------------------------------------------------------------------------------------------------


def test_a_careful_job_is_refilled_after_the_gate_and_never_submitted(box):  # L12
    listed(box)
    live(box, ["j00"], None, None, says("done"), plan={"j00": [("failed", "captcha", ["hcaptcha"])]})
    modes = [spec.mode for spec, _ in box.form_fill_runner.fills if spec.job_id == "j00"]
    assert modes == ["stop", "stop", "stop"]  # the step, the re-run, the refill after "好了"
    assert not [spec for spec in box.apply_runner.calls if spec.job_id == "j00" and spec.mode == "submit"]
    assert box.store.applications()["j00"]["status"] == "waiting"


def test_a_job_that_keeps_meeting_gates_waits_at_most_three_times(box):  # L13
    listed(box)
    gate = ("held", "captcha", [CAPTCHA_NOTE])
    user = live(box, ["j01"], None, says("done"), says("done"), says("done"), plan={"j01": [gate] * 4})
    assert len(box.apply_runner.calls) == 4 and len(user.seen) == 3
    assert [title for title, _ in box.notices].count("投递：现在需要你") == 3
    hold = box.store.hold("j01")
    assert (hold["times"], hold["tab_id"]) == (2, None)  # the fourth gate counts as not cleared in time


def test_a_reply_needs_a_job_that_waits_for_the_user(box):  # L14
    listed(box)
    with pytest.raises(InvariantError, match="等你"):
        box.gate_reply("done")
    user = live(box, ["j01"], None, says("done", job_id="j02"), says("skip"), plan={"j01": [("held", "captcha", [])]})
    assert len(user.errors) == 1 and "j01" in user.errors[0]
    with pytest.raises(InvariantError):
        box.gate_reply("maybe")


def test_jobs_are_re_run_one_at_a_time(box):  # L15
    listed(box)
    calls = []

    def skip(box):
        calls.append([spec.job_id for spec in box.apply_runner.calls])
        box.gate_reply("skip")

    gate = [("held", "captcha", [])]
    live(box, ["j01", "j02"], None, skip, skip, plan={"j01": gate, "j02": gate})
    assert calls == [["j01"], ["j01", "j02"]]
    assert box.store.hold("j01")["times"] == 1 and box.store.hold("j02")["times"] == 1


def test_nothing_on_the_list_or_the_live_view_carries_personal_data(box):
    listed(box)
    user = live(
        box, ["j01"], None, says("skip"), plan={"j01": [("held", "captcha", ["sign in as sam.synthetic@example.test"])]}
    )
    shown = json.dumps([user.seen, box.apply_status(), box.store.holds(), box.notices], ensure_ascii=False)
    assert not any(secret in shown for secret in SECRETS)


# --- the parts ----------------------------------------------------------------------------------------------------


def test_what_the_live_notification_says():
    assert notify.now(2, "Acme", "captcha") == ("投递：现在需要你", "验证码：2 Acme。处理完说“好了”")
    assert notify.now(3, "Beta\nHealth", "sso_required") == (
        "投递：现在需要你",
        "要登录：3 Beta Health。处理完说“好了”",
    )


def test_the_wait_is_configured():
    from intent_job_agent.config import Settings

    wait = Settings.load(Path(__file__).parent.parent / "configs/default.yaml").apply.gate_wait
    assert (wait.timeout_seconds, wait.poll_seconds, wait.max_waits) == (600, 5, 3)


def test_the_reply_tool_is_a_preparing_one_and_never_scheduled():
    assert TOOL_TIERS["gate_reply"] == "prepare"
    assert "gate_reply" not in COMMIT_TOOLS | SCHEDULED_TOOLS


def test_updating_a_hold_changes_only_the_named_fields(tmp_path):
    store = Store(tmp_path / "agent.sqlite", settings())
    store.list_applications(["g1"], DAY)
    hold = {
        "batch_id": "b",
        "phase": "rest",
        "n": 1,
        "mode": "submit",
        "via": "applypilot",
        "reason": "captcha",
        "note": None,
        "tab_id": "T",
        "tab_url": "https://x.test/a",
        "times": 1,
        "generation": "g",
    }
    store.record_application_result("g1", "held", None, attempt={"via": "applypilot", "reason": "captcha"}, hold=hold)
    store.update_hold("g1", tab_id=None, generation=None, times=2)
    row = store.hold("g1")
    assert (row["tab_id"], row["generation"], row["times"], row["tab_url"]) == (None, None, 2, "https://x.test/a")
    assert len(store.apply_attempts()) == 1
    with pytest.raises(InvariantError):
        store.update_hold("g1", resuming=True)  # not a field of the list: set_resuming is


class _Session:
    def __init__(self, target_id):
        self.target_id = target_id

    def send(self, method):
        return {"targetInfo": {"targetId": self.target_id}}

    def detach(self):
        pass


class _Page:
    def __init__(self, target_id, url, context):
        self.target_id, self.url, self.context, self.closed = target_id, url, context, False

    def close(self):
        self.closed = True


class _Context:
    def __init__(self, tabs):
        self.pages = [_Page(target_id, url, self) for target_id, url in tabs]

    def new_cdp_session(self, page):
        return _Session(page.target_id)


def test_closing_a_tab_goes_by_its_id_only(monkeypatch):  # spec section 10 item 116
    context = _Context([("X", "https://a.test/one"), ("Y", "https://b.test/two")])
    browser = type("B", (), {"contexts": [context]})()

    @contextmanager
    def fake_playwright(holder):
        yield object()

    monkeypatch.setattr(readback, "_playwright", fake_playwright)
    monkeypatch.setattr(readback.browser_session, "connect", lambda p, binding, expected: browser)
    expected = ("/synthetic", "/synthetic/state.db")
    assert apply_bridge.close_tab(synthetic_binding(), {"id": "Y", "url": "https://b.test/two"}, 5, expected=expected)
    assert [page.closed for page in context.pages] == [False, True]
    # an unknown id is never closed by its address
    target = {"id": "Z", "url": "https://a.test/one"}
    assert not apply_bridge.close_tab(synthetic_binding(), target, 5, expected=expected)
    assert context.pages[0].closed is False


def test_the_runner_closes_a_tab_only_in_the_chrome_it_was_kept_in(tmp_path, monkeypatch):
    from intent_job_agent.form_fill.runner import FormFillRunner

    runner = FormFillRunner(tmp_path, settings(), tmp_path / "profile.json")
    runner.binding = synthetic_binding()
    seen = []
    monkeypatch.setattr(
        apply_bridge, "close_tab", lambda binding, target, seconds, expected: seen.append(target) or True
    )
    assert runner.close_tab({"id": "T", "url": "https://x.test/", "generation": "other"}) is False
    assert runner.close_tab({"id": "T", "url": "https://x.test/", "generation": None}) is False
    assert seen == []
    assert runner.close_tab({"id": "T", "url": "https://x.test/", "generation": "synthetic"}) is True
    assert seen == [{"id": "T", "url": "https://x.test/", "generation": "synthetic"}]
