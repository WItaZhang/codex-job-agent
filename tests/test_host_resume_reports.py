"""Synthetic real resume threads and independent MCP-style Store connections; no external IO."""

import threading

import pytest

from intent_job_agent.domain import InvariantError
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox

from . import test_gate_handoff as gates
from .test_slice5b import job as duplicate_job

box = gates.box
held_three = gates.held_three


@pytest.mark.parametrize("job_id", ["j06", "j07"], ids=["active", "queued"])
def test_independent_host_cannot_report_jobs_owned_by_resume(box, monkeypatch, job_id):
    held_three(box)
    started, release, ended = threading.Event(), threading.Event(), threading.Event()
    original_runner = box.apply_runner
    original_ended = box._ended

    def runner(spec, *args, **kwargs):
        if spec.job_id == "j06":
            started.set()
            assert release.wait(10), "synthetic runner barrier timed out"
        return original_runner(spec, *args, **kwargs)

    def end(run, writer):
        try:
            original_ended(run, writer)
        finally:
            ended.set()

    monkeypatch.setattr(box, "_ended", end)
    box.apply_runner = runner
    box.apply_in_background = True
    plan = box.prepare_resume(["j06", "j07"])
    other_store = Store(box.store.path, box.settings)
    other = Toolbox(box.settings, other_store, data_dir=box.data_dir)
    try:
        box.resume_applying(plan["job_ids"], plan["summary"])
        assert started.wait(10)
        assert other_store.activity()["kind"] == "resume"
        assert other_store.hold("j06")["resuming"]
        assert not other_store.hold("j07")["resuming"]
        other.mark_applications(["j01"], "skipped")  # held but not selected for this resume
        assert other_store.hold("j01") is None
        before = other_store.hold(job_id)
        with pytest.raises(InvariantError, match="resum"):
            other.mark_applications([job_id], "skipped")
        assert other_store.hold(job_id) == before
    finally:
        release.set()
        assert ended.wait(10), "real resume thread did not finish"
        other_store.db.close()
    assert box._apply_run["status"] == "done"
    assert box.store.activity() is None
    assert not box.store.resume_jobs()
    box.mark_applications(["j07"], "applied")


def test_persistent_started_guard_survives_activity_close(box):
    held_three(box)
    box.store.set_resuming(["j06"], True)
    other_store = Store(box.store.path, box.settings)
    try:
        other = Toolbox(box.settings, other_store, data_dir=box.data_dir)
        with pytest.raises(InvariantError, match="resum"):
            other.mark_applications(["j06"], "pending")
        assert other_store.hold("j06")["resuming"]
    finally:
        other_store.db.close()


def test_legacy_activity_without_selection_is_not_treated_as_idle(box):
    held_three(box)
    box.store.begin_activity((12345, "synthetic"), "resume")
    with pytest.raises(InvariantError, match="resum"):
        box.mark_applications(["j07"], "applied")
    box.store.end_activity((12345, "synthetic"))
    box.mark_applications(["j07"], "applied")


def test_start_rechecks_missing_hold_atomically(box):
    held_three(box)
    box.mark_applications(["j07"], "skipped")
    with pytest.raises(InvariantError, match="selection changed"):
        box.store.begin_activity((12345, "synthetic"), "resume", resume_jobs=["j06", "j07"])
    assert box.store.activity() is None
    assert not box.store.hold("j06")["resuming"]


def test_old_coordination_schema_migrates_without_inventing_selection(box):
    box.store.begin_activity((12345, "synthetic"), "resume")
    box.store.db.execute("ALTER TABLE apply_running DROP COLUMN resume_jobs")
    box.store.db.commit()
    again = Store(box.store.path, box.settings)
    try:
        assert again.activity()["resume_jobs"] is None
        assert again.activity()["owner_pid"] == 12345
    finally:
        again.db.close()


def test_reporting_other_listing_cannot_bypass_persistent_resume_guard(box):
    held_three(box)
    box.store.add_job(duplicate_job("acme-sf").model_copy(update={"id": "j06"}))
    box.store.add_job(duplicate_job("acme-sea").model_copy(update={"id": "synthetic-alias"}))
    box.store.list_applications(["synthetic-alias"], "2026-10-14")
    box.store.begin_activity((12345, "synthetic"), "resume", resume_jobs=["j06"])
    before = box.store.hold("j06")
    with pytest.raises(InvariantError, match="resum"):
        box.mark_applications(["synthetic-alias"], "applied")
    assert box.store.hold("j06") == before
    assert box.store.applications()["synthetic-alias"]["status"] == "pending"
