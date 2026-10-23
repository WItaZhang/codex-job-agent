"""Deterministic synthetic form-fill coordination recovery barriers."""

import threading

import pytest

from intent_job_agent import watchdog

from .test_stuck_run import box as base_box


@pytest.fixture
def box(tmp_path):
    return base_box.__wrapped__(tmp_path)


@pytest.mark.parametrize("status", ["running", "done"])
def test_known_fill_running_or_finishing_is_not_killed(box, monkeypatch, status):
    owner = box._owner()
    box.store.begin_activity(owner, "form_fill", run_id="synthetic-fill")
    box._form_fill_run = {"run_id": "synthetic-fill", "status": status, "closed": False}
    reached, release = threading.Event(), threading.Event()
    original = box._sync_apply_results

    def barrier():
        original()
        reached.set()
        assert release.wait(3)

    monkeypatch.setattr(box, "_sync_apply_results", barrier)
    monkeypatch.setattr(watchdog, "kill_agent", lambda *a: pytest.fail("known fill killed"))
    monkeypatch.setattr(watchdog, "kill_orphans", lambda *a: pytest.fail("known fill orphan kill"))
    monkeypatch.setattr(box, "_close_interrupted", lambda *a: pytest.fail("fill wrote application state"))
    errors = []

    def recover():
        try:
            box.apply_status()
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=recover)
    worker.start()
    assert reached.wait(3)
    box._form_fill_run["status"] = status
    release.set()
    worker.join(3)
    assert not worker.is_alive() and not errors
    assert box.store.activity()["run_id"] == "synthetic-fill"
    assert box.store.apply_attempts() == []


def test_lost_fill_only_ends_its_coordination_record(box, monkeypatch):
    box.store.begin_activity((999999, "dead"), "form_fill", run_id="lost-fill")
    called = []
    monkeypatch.setattr(watchdog, "alive", lambda *a: False)
    monkeypatch.setattr(watchdog, "kill_agent", lambda *a: called.append(a))
    monkeypatch.setattr(watchdog, "kill_orphans", lambda *a: None)
    monkeypatch.setattr(box, "_close_interrupted", lambda *a: pytest.fail("not an application"))
    box._recover()
    assert len(called) == 1
    assert box.store.activity() is None
    assert box.store.apply_attempts() == []


def test_another_toolbox_in_same_process_cannot_take_over_fill(box, monkeypatch):
    from intent_job_agent.domain import InvariantError
    from intent_job_agent.store import Store
    from intent_job_agent.tools import Toolbox

    owner = box._owner()
    box.store.begin_activity(owner, "form_fill", run_id="synthetic-fill")
    box._form_fill_run = {"run_id": "synthetic-fill", "status": "running", "closed": False}
    other = Toolbox(box.settings, Store(box.store.path, box.settings), data_dir=box.data_dir)
    monkeypatch.setattr(watchdog, "kill_agent", lambda *a: pytest.fail("active fill killed"))
    with pytest.raises(InvariantError, match="独立填表"):
        other._refuse_other_session()
    assert box.store.activity()["run_id"] == "synthetic-fill"


def test_identity_lost_before_launch_stops_batch_without_attempt(box):
    from .test_slice6 import FakeRunner
    from .test_stuck_run import batch_of

    box.settings = box.settings.model_copy(update={"apply": box.settings.apply.model_copy(update={"review_first": 0})})
    batch = batch_of(box)
    box.apply_runner = FakeRunner({"j00": ("pending", "browser_identity_lost_before_launch", ["synthetic"])})
    box.start_applying(batch["batch_id"], batch["summary"])
    assert box.apply_status()["status"] == "stopped"
    assert box.store.apply_attempts() == []
    assert all(r["status"] == "pending" for r in box.store.applications().values())
    assert len(box.apply_runner.calls) == 1
