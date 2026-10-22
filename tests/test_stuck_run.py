"""An apply step always ends (spec 4.10 item 11, section 10 items 107-115; decision 24; samples S1-S25 of
specs/stuck-run.samples.md): every wait has a limit, a silent agent and everything it started are ended, a step can be
stopped, a tool server that stopped mid-step leaves nothing running, and changes in the applying Chrome that the
agent did not make are reported.
All data is SYNTHETIC. The real process handling runs against stand-in `claude` scripts and sleeping processes; nothing
here starts Chrome or Claude Code or reaches a website."""

import json
import os
import subprocess
import sys
import threading
import time

import pytest
import yaml

from intent_job_agent import apply_bridge, watchdog
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
