"""An apply step always ends (spec 4.10 item 11, section 10 items 107-115; decision 24; samples S1-S25 of
specs/stuck-run.samples.md): every wait has a limit, a silent agent and everything it started are ended, a step can be
stopped, a tool server that stopped mid-step leaves nothing running, and changes in the applying Chrome that the
agent did not make are reported.
All data is SYNTHETIC. The real process handling runs against stand-in `claude` scripts and sleeping processes; nothing
here starts Chrome or Claude Code or reaches a website."""

import dataclasses
import json
import os
import subprocess
import sys
import threading
import time

import pytest
import yaml

from intent_job_agent import apply_bridge, watchdog
from intent_job_agent.domain import InvariantError
from intent_job_agent.form_fill import report as form_fill_report
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox

from .apply_fakes import FormFillFake
from .conftest import BASE_INTENT, BASE_VOCAB, settings
from .test_slice6 import FakeRunner, add_jobs, fake_claude, write_applicant, write_letters


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
    box.notifier = lambda title, body: None
    return box


def quick(settings, timeout=30, **watch):
    """The shipped settings with a watchdog fast enough for a test (spec section 10 item 107)."""
    limits = {"stall_seconds": 1.5, "drain_seconds": 0.5, "read_seconds": 1.0, "interval_seconds": 0.1, **watch}
    apply = settings.apply.model_copy(
        update={"job_timeout_seconds": timeout, "watchdog": settings.apply.watchdog.model_copy(update=limits)}
    )
    return settings.model_copy(update={"apply": apply})


def gone(pid: int, within: float = 3.0) -> bool:
    """Whether the process has ended (killed processes are reaped by launchd or by us)."""
    end = time.monotonic() + within
    while time.monotonic() < end:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if not state or state.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def pids(tmp_path) -> dict:
    return json.loads((tmp_path / "pids.json").read_text(encoding="utf-8"))


def one_job(box, tmp_path, monkeypatch, script, mode="submit"):
    """A job, its letter, and a stand-in claude that writes its own and its helper's pids to pids.json."""
    write_applicant(box.data_dir)
    (job,) = add_jobs(box, ["Acme"])
    write_letters(box, [job])
    fake_claude(tmp_path, monkeypatch, script)
    monkeypatch.setenv("FAKE_PIDS", str(tmp_path / "pids.json"))
    return box._apply_spec(job, mode)


def run(box, spec, settings, **options):
    started = time.monotonic()
    outcome = apply_bridge.run_application(spec, box.data_dir, settings, box._applicant().profile, 9, **options)
    return outcome, time.monotonic() - started


# --- S1-S4: an agent run always ends ------------------------------------------------------------------------------

HEAD = """#!/usr/bin/env python3
import json, os, subprocess, sys, time
def say(message):
    print(json.dumps(message), flush=True)
def act():
    say({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__playwright__browser_navigate", "input": {"url": "https://jobs.example.test/j00"}}]}})
def holder():
    # A helper in its own session that keeps the agent's output pipe open, as a leftover server could.
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(40)"], start_new_session=True)
"""

FAKE_LEAVES_A_HOLDER = (
    HEAD
    + """
sys.stdin.read()
helper = holder()
open(os.environ["FAKE_PIDS"], "w").write(json.dumps({"agent": os.getpid(), "helper": helper.pid}))
act()
time.sleep(0.6)
say({"type": "result", "result": "Filled every field.\\nRESULT:APPLIED"})
"""
)

FAKE_GOES_QUIET = (
    HEAD
    + """
sys.stdin.read()
helper = holder()
open(os.environ["FAKE_PIDS"], "w").write(json.dumps({"agent": os.getpid(), "helper": helper.pid}))
if os.environ.get("FAKE_ACT"):
    act()
else:
    say({"type": "system", "subtype": "init"})
time.sleep(40)
"""
)

FAKE_KEEPS_TALKING = (
    HEAD
    + """
sys.stdin.read()
open(os.environ["FAKE_PIDS"], "w").write(json.dumps({"agent": os.getpid()}))
act()
while True:
    say({"type": "assistant", "message": {"content": [{"type": "text", "text": "Still filling the form."}]}})
    time.sleep(0.1)
"""
)

FAKE_NEVER_READS = (
    HEAD
    + """
open(os.environ["FAKE_PIDS"], "w").write(json.dumps({"agent": os.getpid()}))
time.sleep(40)
"""
)


def test_an_agent_that_exits_is_done_even_if_a_helper_keeps_its_output_open(box, tmp_path, monkeypatch):  # S1
    spec = one_job(box, tmp_path, monkeypatch, FAKE_LEAVES_A_HOLDER)
    outcome, took = run(box, spec, quick(box.settings))
    assert took < 8, took  # before: read until the helper let go of the pipe (40 s)
    assert (outcome.status, outcome.reason) == ("applied", None)
    assert gone(pids(tmp_path)["helper"])  # left the agent's process group, still found and ended


@pytest.mark.parametrize(("act", "expected"), [(True, ("failed", "stalled")), (False, ("pending", "system_error"))])
def test_a_silent_agent_is_stalled_and_ended_with_what_it_started(box, tmp_path, monkeypatch, act, expected):  # S2
    spec = one_job(box, tmp_path, monkeypatch, FAKE_GOES_QUIET)
    if act:
        monkeypatch.setenv("FAKE_ACT", "1")
    outcome, took = run(box, spec, quick(box.settings, stall_seconds=1.0))
    assert took < 8, took
    assert (outcome.status, outcome.reason) == expected
    if act:
        assert any("1 秒没有任何输出" in note for note in outcome.notes), outcome.notes
    started = pids(tmp_path)
    assert gone(started["agent"]) and gone(started["helper"])


def test_an_agent_that_talks_past_the_time_limit_times_out(box, tmp_path, monkeypatch):  # S3
    spec = one_job(box, tmp_path, monkeypatch, FAKE_KEEPS_TALKING)
    outcome, took = run(box, spec, quick(box.settings, timeout=1, stall_seconds=5))
    assert took < 8, took
    assert (outcome.status, outcome.reason) == ("failed", "timeout")
    assert gone(pids(tmp_path)["agent"])


def test_an_agent_that_never_reads_its_instructions_does_not_hold_the_step(box, tmp_path, monkeypatch):  # S4
    spec = one_job(box, tmp_path, monkeypatch, FAKE_NEVER_READS)
    big = "x" * 300_000  # far more than a pipe holds: writing it used to wait forever
    outcome, took = run(box, spec, quick(box.settings, stall_seconds=1.0), extra_prompt=big)
    assert took < 8, took
    assert (outcome.status, outcome.reason) == ("pending", "system_error")
    assert gone(pids(tmp_path)["agent"])


FAKE_TALKS_SLOWLY = (
    HEAD
    + """
sys.stdin.read()
open(os.environ["FAKE_PIDS"], "w").write(json.dumps({"agent": os.getpid()}))
act()
for n in range(400):
    say({"type": "assistant", "message": {"content": [{"type": "text", "text": f"step {n}"}]}})
    time.sleep(0.1)
"""
)


def test_a_live_run_shows_its_activity_and_a_stop_ends_a_stop_before_submit_agent(box, tmp_path, monkeypatch):
    spec = one_job(box, tmp_path, monkeypatch, FAKE_TALKS_SLOWLY, mode="stop")  # S6, S11
    log = tmp_path / "runs" / "j00.jsonl"
    result = {}
    thread = threading.Thread(target=lambda: result.update(o=run(box, spec, quick(box.settings), log_path=log)))
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (log.is_file() and "step 3" in log.read_text(encoding="utf-8")):
        time.sleep(0.05)
    assert "step 3" in log.read_text(encoding="utf-8")  # each line is written out as it comes
    live = watchdog.LIVE.view()
    assert live["job_id"] == spec.job_id and live["alive"] and time.time() - live["last"] < 1
    assert watchdog.LIVE.stop(("submit",)) is None  # an auto-submitting agent is never stopped mid-way
    assert watchdog.LIVE.stop(("stop",)) == spec.job_id
    thread.join(5)
    outcome, took = result["o"]
    assert (outcome.status, outcome.reason) == ("pending", "stopped") and took < 8
    assert "你停下了这一步" in outcome.notes[0]
    assert gone(pids(tmp_path)["agent"])
    after = watchdog.LIVE.view()
    assert after["job_id"] == spec.job_id and not after["alive"]  # the time it ended stays for apply_status


def test_stalled_and_stopped_read_as_stuck_and_stopped_in_the_form_fill_report():  # S2, S6
    assert form_fill_report.conclude({"agent_result": "stalled", "status": "filled"}, []) == ("not_filled", "stuck")
    assert form_fill_report.conclude({"agent_result": "stopped", "status": "system_error"}, []) == (
        "not_filled",
        "stopped",
    )
    assert form_fill_report.REASONS["stopped"] == "你停下了"


def test_a_process_reads_the_same_whatever_the_caller_s_locale_and_time_zone(monkeypatch):
    mine = watchdog.processes()[os.getpid()]
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    assert watchdog.processes()[os.getpid()] == mine
    assert watchdog.alive(*watchdog.me())


def test_the_process_table_and_a_tree_kill_reach_a_helper_in_another_session(tmp_path):
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'], start_new_session=True)\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(40)\n",
        encoding="utf-8",
    )
    parent = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True, start_new_session=True)
    child = int(parent.stdout.readline())
    table = watchdog.processes()
    assert table[os.getpid()].command and table[parent.pid].ppid == os.getpid()
    assert child in watchdog.descendants(parent.pid, table)
    tree = watchdog.Tree(parent.pid)
    tree.scan()
    killed = tree.kill(alive=True)
    parent.wait(5)
    assert child in killed and gone(child)


# --- S5: reading a tab has a limit ---------------------------------------------------------------------------------


class Blank:
    def mask(self, text):
        return text

    def mask_json(self, obj):
        return obj


def hangs_until_its_driver_ends(started: dict):
    """A Playwright read on a page that never answers: it starts its driver, then waits until the driver is gone, as
    a Playwright call fails once its driver quits."""

    def read(*_args):
        driver = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(40)", "run-driver"])
        started["driver"] = driver
        while driver.poll() is None:
            time.sleep(0.05)
        raise RuntimeError("Target page, context or browser has been closed")

    return read


def test_a_read_back_that_never_answers_fails_in_time_and_ends_its_driver(tmp_path, monkeypatch):  # S5
    from intent_job_agent.form_fill import readback

    started = {}
    monkeypatch.setattr(readback, "_read_live", hangs_until_its_driver_ends(started))
    took = time.monotonic()
    with pytest.raises(readback.ReadbackError, match="读标签页 1 秒没有回应"):
        readback.read_final_page(9222, {"id": "T"}, tmp_path / "out", Blank(), seconds=1)
    assert time.monotonic() - took < 5
    assert gone(started["driver"].pid)


def test_a_glance_that_never_answers_fails_in_time_and_ends_its_driver(monkeypatch):  # S5, resume
    from intent_job_agent.form_fill import readback

    started = {}
    monkeypatch.setattr(readback, "_glance", hangs_until_its_driver_ends(started))
    took = time.monotonic()
    with pytest.raises(readback.ReadbackError, match="读标签页 1 秒没有回应"):
        readback.tab_state(9222, {"id": "T"}, seconds=1)
    assert time.monotonic() - took < 5
    assert gone(started["driver"].pid)


def test_a_driver_that_was_already_running_is_left_alone(monkeypatch):
    from intent_job_agent.form_fill import readback

    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(40)", "run-driver"])
    try:
        started = {}
        monkeypatch.setattr(readback, "_glance", hangs_until_its_driver_ends(started))
        with pytest.raises(readback.ReadbackError):
            readback.tab_state(9222, {"id": "T"}, seconds=1)
        assert other.poll() is None  # not this read's
    finally:
        other.kill()
        other.wait()


def test_the_form_fill_runner_reads_with_the_configured_limit(box, monkeypatch, tmp_path):
    from intent_job_agent.form_fill import readback
    from intent_job_agent.form_fill.runner import FormFillRunner

    seen = {}
    monkeypatch.setattr(readback, "read_final_page", lambda *a, **k: seen.update(read=k["seconds"]))
    monkeypatch.setattr(readback, "tab_state", lambda *a, **k: seen.update(glance=k["seconds"]))
    runner = FormFillRunner(box.data_dir, box.settings, tmp_path / "profile.json")
    runner.port = 9222
    runner.read({"id": "T"}, tmp_path, Blank())
    runner.tab_state({"id": "T"})
    assert box.settings.apply.watchdog.read_seconds == 120 and seen == {"read": 120, "glance": 120}


# --- S6-S11: stopping a step, and when each job last did anything --------------------------------------------------

DAY = "2026-10-14"
COMPANIES = [f"Co{n}" for n in range(10)] + ["Stripe, Inc.", "Acme", "Beta"]  # 1-5 review; 11 careful; rest submit


class Blocking(FakeRunner):
    """FakeRunner whose run of one job waits like a live agent registered in watchdog.LIVE: a stop-before-submit run
    ends when the step is stopped (as run_application does), an auto-submitting one when the test lets it go."""

    def __init__(self, job, outcomes=None):
        super().__init__(outcomes)
        self.job, self.release, self.started = job, threading.Event(), threading.Event()

    def __call__(self, spec):
        if spec.job_id != self.job:
            return super().__call__(spec)
        self.calls.append(spec)
        stop = watchdog.LIVE.begin(spec.job_id, spec.mode)
        self.started.set()
        try:
            while not (stop.is_set() or self.release.is_set()):
                time.sleep(0.02)
        finally:
            watchdog.LIVE.end(stop)
        if stop.is_set():
            return apply_bridge.ApplyOutcome("pending", "stopped", ["你停下了这一步"])
        return apply_bridge.ApplyOutcome("waiting" if spec.mode == "stop" else "applied", None, [])


def batch_of(box):
    write_applicant(box.data_dir)
    jobs = add_jobs(box, COMPANIES)
    write_letters(box, jobs)
    return box.prepare_apply_batch(DAY)


def in_background(box, runner, batch_id, summary, resume=None):
    box.apply_runner, box.apply_in_background = runner, True
    if resume is None:
        box.start_applying(batch_id, summary)
    else:
        box.resume_applying(resume, summary)
    assert runner.started.wait(10)


def finished(box) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = box.apply_status()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("the step never ended")


def test_stopping_the_first_step_ends_the_form_in_progress_and_leaves_the_rest_pending(box):  # S6, S11
    batch = batch_of(box)
    in_background(box, Blocking("j02"), batch["batch_id"], batch["summary"])
    rows = {row["job_id"]: row for row in box.apply_status()["jobs"]}
    assert rows["j00"]["finished"] and rows["j00"]["last_activity"] == rows["j00"]["finished"]
    running = rows["j02"]
    assert running["state"] == "running" and running["started"] and running["finished"] is None
    assert running["agent_alive"] is True and 0 <= running["quiet_seconds"] < 5
    assert rows["j03"]["started"] is None and "quiet_seconds" not in rows["j03"]
    stopped = box.stop_applying()
    assert stopped["stopped"] is True and stopped["current"]["job_id"] == "j02" and stopped["current"]["ended_now"]
    status = finished(box)
    assert status["status"] == "stopped" and status["stopping"] is False
    states = {row["job_id"]: row["state"] for row in status["jobs"]}
    applications = box.store.applications()
    assert [applications[j]["status"] for j in ("j00", "j01", "j02", "j03", "j04")] == [
        "waiting",
        "waiting",
        "pending",
        "pending",
        "pending",
    ]
    assert [states[j] for j in ("j02", "j03", "j04")] == ["stopped", "stopped", "stopped"]
    assert not [a for a in box.store.apply_attempts() if a["job_id"] in ("j02", "j03", "j04")]  # not an attempt
    assert status["void"] is False and status["next"]["phase"] == "rest"  # 1 and 2 worked: "继续" still works
    box.apply_runner, box.apply_in_background = FakeRunner(), False
    box.start_applying(batch["batch_id"], status["next"]["summary"])
    assert box.store.applications()["j05"]["status"] == "applied"


def test_an_auto_submitting_job_finishes_before_the_step_stops(box):  # S7
    batch = batch_of(box)
    box.start_applying(batch["batch_id"], batch["summary"])
    rest = box.apply_status()["next"]
    runner = Blocking("j06")
    in_background(box, runner, rest["batch_id"], rest["summary"])
    stopped = box.stop_applying()
    assert stopped["current"]["job_id"] == "j06" and stopped["current"]["ended_now"] is False
    assert "等它做完" in stopped["message"]
    assert box.apply_status()["stopping"] is True
    runner.release.set()
    status = finished(box)
    applications = box.store.applications()
    assert applications["j06"]["status"] == "applied" and applications["j07"]["status"] == "pending"
    assert status["status"] == "stopped" and status["next"] is None, status["error"]


def test_a_first_step_stopped_before_anything_worked_is_void(box):  # S8
    batch = batch_of(box)
    in_background(box, Blocking("j00"), batch["batch_id"], batch["summary"])
    box.stop_applying()
    status = finished(box)
    assert status["void"] is True and status["next"] is None
    assert {box.store.applications()[j]["status"] for j in ("j00", "j01", "j02", "j03", "j04")} == {"pending"}


def test_a_stopped_resume_leaves_the_job_held(box):  # S9
    batch = batch_of(box)
    box.apply_runner = FakeRunner({"j01": ("failed", "captcha", ["jobs.example.test: tick the human check"])})
    box.start_applying(batch["batch_id"], batch["summary"])
    attempts = len(box.store.apply_attempts())
    plan = box.prepare_resume(["j01"])
    in_background(box, Blocking("j01"), None, plan["summary"], resume=plan["job_ids"])
    assert box.store.hold("j01")["resuming"] is True
    assert box.stop_applying()["current"]["ended_now"] is True
    status = finished(box)
    hold = box.store.hold("j01")
    assert status["status"] == "stopped" and hold is not None and hold["resuming"] is False and hold["times"] == 1
    assert box.store.applications()["j01"]["status"] == "held" and len(box.store.apply_attempts()) == attempts


def test_stopping_when_nothing_runs_changes_nothing(box):  # S10
    assert box.stop_applying() == {"stopped": False, "message": "现在没有在跑的一步"}


def test_a_stop_asked_just_before_the_agent_starts_ends_it_at_once():
    live = watchdog.Live()
    assert live.stop(("stop",), job_id="j02") is None  # nothing running yet: kept for j02
    first = live.begin("j01", "stop")
    assert not first.is_set()
    live.end(first)
    assert live.begin("j02", "stop").is_set()
    live.disarm()
    assert not live.begin("j02", "stop").is_set()


# --- S12-S17, S25: after a tool server stopped, and other sessions ------------------------------------------------

LONG_AGO = "Mon Jan  1 00:00:00 2024"


def dead_server() -> tuple[int, str]:
    """A tool server that is gone: the pid of a process that has ended."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid, LONG_AGO


def live_server():
    """Another session's tool server that still runs (a sleeping stand-in), with its real start time."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    return proc, (proc.pid, watchdog.processes()[proc.pid].started)


def left_running(box, batch_id, phase, owner, job=None):
    """What a tool server that stopped mid-step leaves in the database: the step running, the activity row naming
    that server, and the job it was on."""
    data = box.store.apply_batch(batch_id)
    data["phases"][phase] = "running"
    box.store.begin_activity(owner, "step", batch=(batch_id, phase, data.pop("day"), data))
    if job:
        box.store.activity_job(owner, *job)


def result(box, job_id, status, batch_id, phase, mode="review", via="form_fill", reason=None):
    attempt = {"batch_id": batch_id, "phase": phase, "mode": mode, "via": via, "reason": reason}
    box.store.record_application_result(job_id, status, reason, attempt=attempt)


def restarted(box) -> Toolbox:
    """A new tool server on the same database and data folder."""
    again = Toolbox(box.settings, Store(box.store.path, box.settings), data_dir=box.data_dir)
    again.apply_runner, again.form_fill_runner = FakeRunner(), FormFillFake(again)
    again.apply_in_background, again.notifier = False, lambda title, body: None
    return again


def test_a_step_keeps_its_activity_row_while_it_runs_and_drops_it_when_closed(box):  # spec section 10 item 111
    batch = batch_of(box)
    runner = Blocking("j02")
    in_background(box, runner, batch["batch_id"], batch["summary"])
    row = box.store.activity()
    assert (row["kind"], row["batch_id"], row["phase"]) == ("step", batch["batch_id"], "review")
    assert (row["job_id"], row["mode"], row["via"]) == ("j02", "review", "form_fill")
    assert (row["owner_pid"], row["owner_started"]) == watchdog.me()
    box.stop_applying()
    finished(box)
    assert box.store.activity() is None


def test_a_first_step_cut_short_by_a_restart_is_closed_and_its_second_step_is_offered(box):  # S12
    batch = batch_of(box)
    left_running(box, batch["batch_id"], "review", dead_server(), ("j02", "review", "form_fill"))
    for job_id in ("j00", "j01"):
        result(box, job_id, "waiting", batch["batch_id"], "review", reason="check_then_submit")
    again = restarted(box)
    status = again.apply_status()
    assert status["status"] == "idle" and box.store.activity() is None
    applications = again.store.applications()
    assert [applications[j]["status"] for j in ("j02", "j03", "j04")] == ["pending", "pending", "pending"]
    j02 = [(a["status"], a["reason"]) for a in again.store.apply_attempts() if a["job_id"] == "j02"]
    assert j02 == [("pending", "interrupted")]  # it stops before submit: nothing was sent
    assert again.store.apply_batch(batch["batch_id"])["phases"] == {"review": "done", "rest": "proposed"}
    assert status["next"]["phase"] == "rest" and status["next"]["batch_id"] == batch["batch_id"]
    assert "第 3 个（Co2）" in status["notice"] and "退回待投" in status["notice"]
    again.start_applying(batch["batch_id"], status["next"]["summary"])  # the second step is not lost
    assert again.store.applications()["j05"]["status"] == "applied"


def test_an_auto_submitted_job_cut_short_by_a_restart_waits_for_the_user(box):  # S13, S25
    batch = batch_of(box)
    box.start_applying(batch["batch_id"], batch["summary"])
    left_running(box, batch["batch_id"], "rest", dead_server(), ("j06", "submit", "applypilot"))
    result(box, "j05", "applied", batch["batch_id"], "rest", mode="submit", via="applypilot")
    again = restarted(box)
    status = again.apply_status()
    hold = again.store.hold("j06")
    assert again.store.applications()["j06"]["status"] == "held" and hold["reason"] == "interrupted"
    assert (hold["phase"], hold["n"], hold["mode"], hold["tab_id"], hold["times"]) == ("rest", 7, "submit", None, 1)
    row = next(row for row in status["held"] if row["job_id"] == "j06")
    assert row["gate"] == "工具服务中途停了，可能已经交过" and "交上了没有" in row["todo"] and row["then"] == "自动提交"
    assert again.store.apply_batch(batch["batch_id"])["phases"]["rest"] == "done"
    assert again.store.applications()["j07"]["status"] == "pending" and status["next"] is None
    assert "第 7 个（Co6）" in status["notice"] and "等你处理" in status["notice"]
    plan = again.prepare_resume(["j06"])
    assert "7 Co6（上次工具服务中途停了，可能已经交过，请先看标签页；从岗位地址重开）" in plan["summary"]
    again.mark_applications(["j06"], "skipped")  # "7 不处理了"
    week = again.apply_stats()["weeks"][0]
    assert week["to_fix"] == {"interrupted": 1} and week["not_counted"]["held"] == 1


def test_a_restart_with_no_job_in_progress_only_closes_the_step(box):  # S14
    batch = batch_of(box)
    left_running(box, batch["batch_id"], "review", dead_server())
    result(box, "j00", "waiting", batch["batch_id"], "review", reason="check_then_submit")
    again = restarted(box)
    status = again.apply_status()
    assert status["next"]["phase"] == "rest" and again.store.holds() == []
    assert [a["job_id"] for a in again.store.apply_attempts()] == ["j00"]


def test_a_step_left_running_before_this_slice_is_closed_too(box):  # S17
    batch = batch_of(box)
    data = box.store.apply_batch(batch["batch_id"])
    data["phases"]["review"] = "running"
    box.store.save_apply_batch(batch["batch_id"], data.pop("day"), data)
    restarted(box).apply_status()
    data = box.store.apply_batch(batch["batch_id"])
    assert data["phases"] == {"review": "done", "rest": "void"} and data["void"] is True  # nothing in it worked


def test_another_session_running_a_step_is_left_alone_and_blocks_a_second_one(box):  # S16
    batch = batch_of(box)
    other, owner = live_server()
    try:
        left_running(box, batch["batch_id"], "review", owner, ("j02", "review", "form_fill"))
        again = restarted(box)
        status = again.apply_status()
        assert status["other_session"]["batch_id"] == batch["batch_id"] and status["other_session"]["phase"] == "review"
        assert again.store.apply_batch(batch["batch_id"])["phases"]["review"] == "running"  # not closed
        assert again.store.activity() is not None and again.store.applications()["j02"]["status"] == "pending"
        with pytest.raises(Exception, match="另一个会话"):
            again.start_applying(batch["batch_id"], batch["summary"])
        with pytest.raises(Exception, match="另一个会话"):
            again.resume_applying(["j02"], "x")
    finally:
        other.kill()
        other.wait()


def orphan(command_args) -> int:
    """A process whose parent has quit (so its parent is the system), as an agent left behind by a stopped server."""
    script = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', *sys.argv[1:]],"
        " start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "print(child.pid)\n"
    )
    out = subprocess.run([sys.executable, "-c", script, *command_args], capture_output=True, text=True, check=True)
    return int(out.stdout)


def test_agents_a_stopped_server_left_behind_are_ended_and_nothing_else(box):  # S15
    batch = batch_of(box)
    stage = box.data_dir / box.settings.discovery.applypilot_dir
    mine = orphan(["--mcp-config", str(stage / ".mcp-apply-0.json")])
    form = orphan(["--mcp-config", str(stage / ".mcp-form-fill-0.json")])
    elsewhere = orphan(["--mcp-config", "/elsewhere/applypilot/.mcp-apply-0.json"])
    child = subprocess.Popen(  # a live server's agent: its parent is that server, not the system
        [sys.executable, "-c", "import time; time.sleep(30)", "--mcp-config", str(stage / ".mcp-apply-0.json")]
    )
    try:
        if watchdog.processes()[mine].ppid != 1:
            pytest.skip("orphans are not handed to pid 1 here")
        left_running(box, batch["batch_id"], "review", dead_server())
        restarted(box).apply_status()
        assert gone(mine) and gone(form)
        assert not gone(elsewhere, within=0.3) and child.poll() is None
    finally:
        for pid in (elsewhere,):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        child.kill()
        child.wait()


def test_stalled_counts_as_a_failure_to_fix(box):  # S25
    batch = batch_of(box)
    box.apply_runner = FakeRunner({"j00": ("failed", "stalled", ["投递 agent 300 秒没有任何输出，算卡住，已结束它"])})
    box.start_applying(batch["batch_id"], batch["summary"])
    assert box.apply_stats()["weeks"][0]["to_fix"] == {"stalled": 1}


def test_a_row_of_this_server_whose_run_never_started_does_not_block_the_next_step(box, monkeypatch):
    batch = batch_of(box)

    def broken(*_args, **_kwargs):
        raise RuntimeError("the decision log is not writable")

    monkeypatch.setattr(box.store, "record_decision", broken)
    with pytest.raises(RuntimeError):
        box.start_applying(batch["batch_id"], batch["summary"])
    monkeypatch.undo()
    assert box.store.activity() is None  # undone at once (review #2): the step never ran
    assert box.store.apply_batch(batch["batch_id"])["phases"]["review"] == "proposed"
    status = box.apply_status()
    assert status["status"] == "idle" and status["next"]["phase"] == "review"
    box.start_applying(batch["batch_id"], status["next"]["summary"])
    assert box.store.applications()["j00"]["status"] == "waiting"


# --- S18-S24: someone using the applying Chrome ---------------------------------------------------------------------

import socketserver  # noqa: E402 - the fake CDP endpoint below is the only user

BLOCKED = ["https://mail.google.com", "https://www.chase.com"]
T1 = {"id": "T1", "type": "page", "url": "https://news.example.test/"}
T2 = {"id": "T2", "type": "page", "url": "https://jobs.omega.test/apply"}


def tab(tab_id, url, opener=None, kind="page"):
    return {"id": tab_id, "type": kind, "url": url, "openerId": opener}


def scripted(*lists):
    """A stand-in for cdp_targets: one list per check (None: Chrome does not answer), the last one repeated."""
    calls = iter(lists)
    last = {"tabs": None}

    def targets(_port):
        try:
            last["tabs"] = next(calls)
        except StopIteration:
            pass
        if last["tabs"] is None:
            raise watchdog.ChromeDown("connection refused")
        return [dict(t) for t in last["tabs"]]

    return targets


def watched(*lists, events=(), pages=(), clock=None, own=()):
    options = {"clock": clock} if clock else {}
    watch = watchdog.ChromeWatch(9222, BLOCKED, targets=scripted(*lists), own=own, **options)
    for _ in lists:
        watch.check()
    return watch.report(list(events), list(pages))


def call(name, **data):
    return {"name": f"mcp__playwright__{name}", "input": data}


NEW, NAV = call("browser_tabs", action="new"), call("browser_navigate", url="https://jobs.acme.test/1")


def test_a_tab_the_agent_did_not_open_is_reported_by_its_site():  # S18
    job = tab("N1", "https://jobs.acme.test/1")
    lines = watched(
        [T1, T2],
        [T1, T2, job, tab("U1", "chrome://newtab/")],
        [T1, T2, job, tab("U1", "https://sports.example.test/scores")],
        events=[NEW, NAV],
    )
    assert lines == ["运行中多了 1 个不是 agent 开的新标签页（sports.example.test）"]


def test_the_job_tab_closed_by_someone_else_is_reported():  # S19
    job = tab("N1", "https://jobs.acme.test/1")
    assert watched([T1, T2], [T1, T2, job], [T1, T2], events=[NEW, NAV]) == [
        "这次运行开的标签页有 1 个被关掉了，不是 agent 关的"
    ]


def test_chrome_going_away_is_reported_with_the_time():  # S20
    times = iter([1000.0, 1001.0, 1002.0, 1003.0])
    job = tab("N1", "https://jobs.acme.test/1")
    lines = watched([T1, T2], [T1, T2, job], None, None, events=[NEW, NAV], clock=lambda: next(times))
    clock = watchdog._clock(1002.0)
    assert lines == [f"投递用的 Chrome 从 {clock} 起连不上了（被关掉、崩溃，或调试端口没有响应）"]


def test_a_chrome_that_never_answered_is_not_reported():  # the agent's own result says so
    assert watched(None, None, events=[NEW]) == []


def test_tabs_open_before_the_run_that_changed_are_reported():  # S21
    lines = watched(
        [T1, T2],
        [tab("T1", "https://sports.example.test/today"), tab("N1", "https://jobs.acme.test/1")],
        events=[NEW, NAV],
    )
    assert lines == ["开始前就开着的标签页里，1 个被关掉、1 个换了地址（sports.example.test）"]


def test_a_site_the_agent_cannot_open_seen_in_a_tab_of_the_run_is_reported():  # S22
    job = tab("N1", "https://jobs.acme.test/1")
    lines = watched([T1, T2], [T1, T2, job], [T1, T2, tab("N1", "https://mail.google.com/mail/u/0/")], events=[NEW])
    assert lines == ["这次运行开的标签页打开过 mail.google.com：agent 打不开这类网站"]


def test_popups_and_the_agent_s_own_tabs_are_not_reported():  # S23
    job = tab("N1", "https://jobs.acme.test/1")
    popup = tab("P1", "https://accounts.linkedin.test/oauth", opener="N1")
    lines = watched(
        [T1, T2],
        [T1, T2, job],
        [T1, T2, job, popup],
        [T1, T2, job],  # the popup closed itself
        [T1, T2],  # the agent closed its own tab at the end
        events=[NEW, NAV, call("browser_tabs", action="close")],
    )
    assert lines == []


def test_a_blank_tab_nobody_used_is_not_reported():  # one Playwright MCP may open by itself
    job = tab("N1", "https://jobs.acme.test/1")
    lines = watched([T1, T2], [T1, T2, tab("B1", "about:blank"), job], [T1, T2, job], events=[NEW, NAV])
    assert lines == []


def test_a_resumed_job_s_own_held_tab_may_change_and_close():  # spec section 10 item 100
    held = tab("H1", "https://careers.acme.test/login")
    lines = watched(
        [T1, held],
        [T1, tab("H1", "https://careers.acme.test/apply/step2")],
        [T1],  # the agent submitted and closed it
        events=[call("browser_tabs", action="close")],
        own=("H1",),
    )
    assert lines == []


class FakeCDP(socketserver.ThreadingTCPServer):
    """A local stand-in for Chrome's DevTools endpoint: /json/version and the browser websocket, answering
    Target.getTargets with the tabs in `tabs_file` (re-read on every request), in two fragments after an event."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, tabs_file):
        self.tabs_file = tabs_file
        super().__init__(("127.0.0.1", 0), FakeCDPHandler)


class FakeCDPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request = self.rfile.readline().decode()
        while self.rfile.readline() not in (b"\r\n", b""):
            pass
        port = self.server.server_address[1]
        if request.startswith("GET /json/version"):
            body = json.dumps({"webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/browser/fake"}).encode()
            self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n")
            self.wfile.write(f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            return
        self.wfile.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        head = self.rfile.read(2)
        length = head[1] & 0x7F
        if length == 126:
            length = int.from_bytes(self.rfile.read(2), "big")
        mask = self.rfile.read(4)
        asked = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(self.rfile.read(length))))
        tabs = json.loads(self.server.tabs_file.read_text(encoding="utf-8"))
        infos = [
            {
                "targetId": t["id"],
                "type": t["type"],
                "url": t["url"],
                **({"openerId": t["openerId"]} if t.get("openerId") else {}),
            }
            for t in tabs
        ]
        self.frame(json.dumps({"method": "Target.targetInfoChanged", "params": {}}).encode(), 0x1, True)
        answer = json.dumps({"id": asked["id"], "result": {"targetInfos": infos}}).encode()
        self.frame(answer[:10], 0x1, False)
        self.frame(answer[10:], 0x0, True)

    def frame(self, payload, opcode, fin):
        size = len(payload)
        head = bytes([(0x80 if fin else 0) | opcode])
        head += bytes([size]) if size < 126 else bytes([126]) + size.to_bytes(2, "big")
        self.wfile.write(head + payload)


@pytest.fixture
def cdp(tmp_path):
    tabs_file = tmp_path / "tabs.json"
    tabs_file.write_text(json.dumps([T1, T2]), encoding="utf-8")
    server = FakeCDP(tabs_file)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_the_cdp_client_lists_the_tabs_with_their_openers(cdp):
    cdp.tabs_file.write_text(json.dumps([T1, tab("P1", "https://x.test/", opener="T1")]), encoding="utf-8")
    port = cdp.server_address[1]
    assert watchdog.cdp_targets(port) == [{**T1, "openerId": None}, tab("P1", "https://x.test/", opener="T1")]


def test_no_chrome_on_the_port_is_chrome_down():
    with pytest.raises(watchdog.ChromeDown):
        watchdog.cdp_targets(9)


FAKE_USER_OPENS_A_TAB = (
    HEAD
    + """
sys.stdin.read()
open(os.environ["FAKE_PIDS"], "w").write(json.dumps({"agent": os.getpid()}))
say({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "mcp__playwright__browser_tabs", "input": {"action": "new"}}]}})
act()
time.sleep(0.4)
tabs = json.load(open(os.environ["FAKE_TABS"]))
tabs += [{"id": "N1", "type": "page", "url": "https://jobs.example.test/j00", "openerId": None},
         {"id": "U1", "type": "page", "url": "https://news2.example.test/", "openerId": None}]
open(os.environ["FAKE_TABS"], "w").write(json.dumps(tabs))
time.sleep(0.6)
say({"type": "result", "result": "RESULT:APPLIED"})
"""
)


def test_an_agent_run_reports_a_tab_someone_else_opened(box, tmp_path, monkeypatch, cdp):  # S18, end to end
    spec = one_job(box, tmp_path, monkeypatch, FAKE_USER_OPENS_A_TAB)
    monkeypatch.setenv("FAKE_TABS", str(cdp.tabs_file))
    outcome = apply_bridge.run_application(
        spec, box.data_dir, quick(box.settings), box._applicant().profile, cdp.server_address[1]
    )
    assert outcome.status == "applied"
    assert outcome.chrome == ["运行中多了 1 个不是 agent 开的新标签页（news2.example.test）"]


def test_the_report_reaches_apply_status_and_the_job_notes(box):  # S18-S22, both paths
    batch = batch_of(box)
    seen = ["运行中多了 1 个不是 agent 开的新标签页（news2.example.test）"]

    class Seen(FakeRunner):
        def __call__(self, spec):
            outcome = super().__call__(spec)
            return dataclasses.replace(outcome, chrome=list(seen)) if spec.job_id in ("j01", "j05") else outcome

    box.apply_runner = Seen()
    box.start_applying(batch["batch_id"], batch["summary"])
    status = box.apply_status()
    rows = {row["job_id"]: row for row in status["jobs"]}
    assert rows["j01"]["chrome_use"] == seen and rows["j00"]["chrome_use"] == []
    assert status["chrome_warning"].startswith("可能有人在用投递用的 Chrome：第 2 个岗位运行时运行中多了 1 个")
    assert "请不要用这个 Chrome" in status["chrome_warning"]
    assert "可能有人在用投递用的 Chrome" in box.store.applications()["j01"]["note"]
    box.start_applying(batch["batch_id"], status["next"]["summary"])
    rows = {row["job_id"]: row for row in box.apply_status()["jobs"]}
    assert rows["j05"]["chrome_use"] == seen
    assert "可能有人在用投递用的 Chrome" in box.store.applications()["j05"]["note"]


def test_starting_a_step_or_a_resume_reminds_the_user_to_leave_the_chrome_alone(box):  # S24
    batch = batch_of(box)
    box.apply_runner = FakeRunner({"j01": ("failed", "captcha", [])})
    started = box.start_applying(batch["batch_id"], batch["summary"])
    assert "请不要用投递用的 Chrome" in started["reminder"] and "停下" in started["reminder"]
    plan = box.prepare_resume(["j01"])
    assert "请不要用投递用的 Chrome" in box.resume_applying(plan["job_ids"], plan["summary"])["reminder"]


def test_stopping_is_a_prepare_tool_that_no_scheduled_run_gets():  # spec 5.1, section 10 item 109
    from intent_job_agent.tools import COMMIT_TOOLS, SCHEDULED_TOOLS, TOOL_TIERS

    assert TOOL_TIERS["stop_applying"] == "prepare"
    assert "stop_applying" not in COMMIT_TOOLS | SCHEDULED_TOOLS


# --- the progress session's pre-merge review (stuck-run-review.md) --------------------------------------------------


def failing_ps(monkeypatch):
    """ps exits with an error and prints nothing (review #1)."""
    real = watchdog.subprocess.run

    def run(args, **kwargs):
        if args and args[0] == "ps":
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"ps: sysctl failed")
        return real(args, **kwargs)

    monkeypatch.setattr(watchdog.subprocess, "run", run)


def test_a_ps_that_fails_never_makes_a_live_session_look_gone(box, monkeypatch):  # review #1
    batch = batch_of(box)
    box.start_applying(batch["batch_id"], batch["summary"])
    other, owner = live_server()
    try:
        left_running(box, batch["batch_id"], "rest", owner, ("j06", "submit", "applypilot"))
        b = restarted(box)
        b._owner()  # B knew who it is before ps failed
        failing_ps(monkeypatch)
        with pytest.raises(subprocess.SubprocessError):
            watchdog.processes()
        assert watchdog.alive(*owner) is True and watchdog.kill_orphans(["--mcp-config /x"]) == []
        status = b.apply_status()
        assert status["other_session"]["batch_id"] == batch["batch_id"]  # still A's
        assert b.store.activity()["owner_pid"] == owner[0]
        assert b.store.applications()["j06"]["status"] == "pending"
        assert b.store.apply_batch(batch["batch_id"])["phases"]["rest"] == "running"
        fresh = restarted(box)  # a server that cannot tell who it is: it leaves everything alone
        assert fresh.apply_status()["status"] == "idle" and fresh.store.activity() is not None
        with pytest.raises(InvariantError, match="ps"):
            fresh.start_applying(batch["batch_id"], "x")
    finally:
        other.kill()
        other.wait()


def test_a_tool_server_is_gone_only_when_a_real_process_list_or_the_system_says_so(monkeypatch):  # review #1
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = watchdog.processes()[proc.pid].started
        assert watchdog.alive(proc.pid, started) is True
        assert watchdog.alive(proc.pid, LONG_AGO) is False  # the pid now belongs to another process
        assert watchdog.alive(dead_server()[0], LONG_AGO) is False
        real = watchdog.processes
        monkeypatch.setattr(watchdog, "processes", lambda: {k: v for k, v in real().items() if k != proc.pid})
        assert watchdog.alive(proc.pid, started) is True  # left out of the list, yet the system has it
    finally:
        proc.kill()
        proc.wait()


def test_two_sessions_recovering_at_once_never_delete_each_other_s_row(box, monkeypatch):  # review #3
    batch = batch_of(box)
    box.store.begin_activity(dead_server(), "resume")  # a resume a stopped server left behind
    other, owner = live_server()
    try:
        b, a = restarted(box), restarted(box)
        a._me = owner  # session A, alive
        inside, errors = threading.Event(), []
        real = watchdog.kill_orphans

        def slow(marks):  # B ends the stopped server's agents while A recovers too and starts its own step
            if threading.current_thread().name == "session-b":
                inside.set()
                time.sleep(1.0)
            return real(marks)

        def session_b():
            try:
                b.apply_status()
            except Exception as error:  # noqa: BLE001 - reported below
                errors.append(error)

        monkeypatch.setattr(watchdog, "kill_orphans", slow)
        thread = threading.Thread(target=session_b, name="session-b")
        thread.start()
        assert inside.wait(5)
        a._recover()  # waits for B: one recovery at a time
        data = a.store.apply_batch(batch["batch_id"])
        data["phases"]["review"] = "running"
        a.store.begin_activity(owner, "step", batch=(batch["batch_id"], "review", data.pop("day"), data))
        a.store.activity_job(owner, "j00", "review", "form_fill")
        thread.join(10)
        assert errors == []
        assert a.store.activity()["owner_pid"] == owner[0]
        data = a.store.apply_batch(batch["batch_id"])
        assert data["phases"]["review"] == "running" and not data.get("void")
        assert b.apply_status()["other_session"]["batch_id"] == batch["batch_id"]  # A's step is left alone
        assert b.store.activity()["owner_pid"] == owner[0]
    finally:
        other.kill()
        other.wait()


def test_a_closing_never_removes_another_server_s_row(box):  # review #3
    other, owner = live_server()
    try:
        box.store.begin_activity(owner, "resume")
        box.store.end_activity(dead_server())
        assert box.store.activity()["owner_pid"] == owner[0]
    finally:
        other.kill()
        other.wait()


def test_a_step_that_ended_in_the_background_frees_the_slot_by_itself(box):  # review #2
    batch = batch_of(box)
    other, owner = live_server()  # session A's tool server, alive
    try:
        box._me = owner
        box.apply_runner, box.apply_in_background = FakeRunner(), True
        box.start_applying(batch["batch_id"], batch["summary"])
        deadline = time.monotonic() + 10
        while box.store.activity() is not None and time.monotonic() < deadline:  # A makes no further call
            time.sleep(0.05)
        assert box.store.activity() is None and box._apply_run["closed"] is True
        assert box.store.apply_batch(batch["batch_id"])["phases"] == {"review": "done", "rest": "proposed"}
        b = restarted(box)  # session B, opened after the notification
        status = b.apply_status()
        assert "other_session" not in status and status["next"]["phase"] == "rest"
        b.start_applying(batch["batch_id"], status["next"]["summary"])
        assert b.store.applications()["j05"]["status"] == "applied"
    finally:
        other.kill()
        other.wait()


def test_stopping_closes_what_a_stopped_server_left_first(box):  # review #2
    box.store.begin_activity(dead_server(), "resume")
    assert box.stop_applying()["stopped"] is False and box.store.activity() is None
