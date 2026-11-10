"""Synthetic continuity cases: count starts and tabs without visiting real job sites."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from intent_job_agent.domain import InvariantError
from intent_job_agent.store import Store
from intent_job_agent.tools import Toolbox

from . import test_form_fill_runner as fills
from . import test_gate_handoff as gates
from . import test_gate_list as live_cases
from .apply_fakes import FormFillFake

ff = fills.ff
box = gates.box
live_box = live_cases.box


def gated_fill(ff):
    box, path = ff
    box.form_fill_runner.outcomes = {name: ("failed", "captcha", []) for name in ("acme-ml-01", "beta-ds-02")}
    first = box.start_form_fill(path)
    return box, path, first


def test_incomplete_start_is_not_repeated_after_toolbox_restart(ff):
    box, path, first = gated_fill(ff)
    browser = box.form_fill_runner
    calls = len(browser.calls)
    other = Toolbox(box.settings, Store(box.store.path, box.settings), data_dir=box.data_dir)
    other.form_fill_runner, other.form_fill_in_background = browser, False
    try:
        result = other.start_form_fill(path)
        assert result["status"] == "blocked" and len(result["blocked"]) == 2
        assert all(item["run_id"] == first["run_id"] for item in result["blocked"])
        assert len(browser.calls) == calls and other.store.activity() is None
    finally:
        other.store.db.close()


@pytest.mark.parametrize("known_source", [True, False])
def test_changing_job_id_does_not_revisit_the_same_posting(ff, known_source):
    box, path, _ = gated_fill(ff)
    data = yaml.safe_load(Path(path).read_text())
    if not known_source:
        metadata = fills.recordings(box) / fills.recording_id(data["jobs"][0]["url"]) / "meta.json"
        old = json.loads(metadata.read_text())
        old.pop("last_job_id")  # legacy indexes cannot identify a new local id as the original job
        metadata.write_text(json.dumps(old))
    data["jobs"][0]["id"] = "different-local-id"
    Path(path).write_text(yaml.safe_dump(data))
    before = len(box.form_fill_runner.calls)
    result = box.start_form_fill(path)
    assert len(result["blocked"]) == 2 and len(box.form_fill_runner.calls) == before
    assert result["blocked"][0]["source_job_id"] == ("acme-ml-01" if known_source else None)


def test_explicit_restart_is_bound_to_one_prior_visit(ff):
    box, path, first = gated_fill(ff)
    data = yaml.safe_load(Path(path).read_text())
    for entry in data["jobs"]:
        entry.update(revisit=True, restart_from=first["run_id"], restart_reason="Synthetic user requested restart")
    Path(path).write_text(yaml.safe_dump(data))
    second = box.start_form_fill(path)
    assert second["run_id"] != first["run_id"]
    calls = len(box.form_fill_runner.calls)
    assert box.start_form_fill(path)["status"] == "blocked"
    assert len(box.form_fill_runner.calls) == calls
    events = [json.loads(d["payload"]) for d in box.store.decisions() if d["kind"] == "form_fill_restart"]
    assert len(events) == 2 and all(e["source_run"] == first["run_id"] for e in events)


def test_start_rechecks_recording_after_claiming_activity(ff, monkeypatch):
    box, path = ff
    original = box.store.begin_activity

    def race(*args, **kwargs):
        url = "https://jobs.example.test/acme-ml-01"
        record = fills.RecordingStore(fills.recordings(box), fills.recording_id(url), "prior-won", fills.Redactor([]))
        record.write_meta(url, "Acme Robotics", "ML Engineer", job_id="acme-ml-01")
        return original(*args, **kwargs)

    monkeypatch.setattr(box.store, "begin_activity", race)
    result = box.start_form_fill(path)
    assert [s.job_id for s, _ in box.form_fill_runner.calls] == ["beta-ds-02"]
    assert result["blocked"][0]["run_id"] == "prior-won"


def test_damaged_recording_does_not_look_like_a_first_visit(ff):
    box, path = ff
    for job in yaml.safe_load(Path(path).read_text())["jobs"]:
        folder = fills.recordings(box) / fills.recording_id(job["url"])
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text("{broken synthetic metadata")
    result = box.start_form_fill(path)
    assert result["status"] == "blocked" and not box.form_fill_runner.calls
    assert all(item["reason"] == "record_invalid" for item in result["blocked"])


class ContinuityBrowser(FormFillFake):
    def resume_chrome(self, generation):
        assert generation == "synthetic"
        return self.ensure_chrome()

    def fill(self, spec, **options):
        before = list(self.tabs)
        result = super().fill(spec, **options)
        if spec.resume:
            assert any(t["id"] == spec.resume_tab for t in before)
            self.tabs[:] = before
        return result


def held_job(box):
    (job,) = gates.add_jobs(box, ["Acme"])
    gates.write_letters(box, [job])
    box.form_fill_runner = ContinuityBrowser(box)
    box.apply_runner = gates.GateRunner({job: ("failed", "captcha", [])})
    gates.step(box)
    box.apply_runner.outcomes = {}
    return job


def test_apply_fill_continues_original_tab_and_keeps_each_attempt(box):
    job = held_job(box)
    original = box.store.hold(job)["tab_id"]
    initial_run = box.store.apply_attempts()[-1]["run_id"]
    before = list(box.form_fill_runner.tabs)
    box.apply_runner.outcomes = {job: ("failed", "email_verification", [])}
    gates.resumed(box, [job])
    first_run = box.store.apply_attempts()[-1]["run_id"]
    box.apply_runner.outcomes = {}
    gates.resumed(box, [job])
    second_run = box.store.apply_attempts()[-1]["run_id"]
    assert len({initial_run, first_run, second_run}) == 3
    assert box.form_fill_runner.tabs == before
    spec, _ = box.form_fill_runner.fills[-1]
    assert spec.resume and spec.resume_tab == original and spec.mode == "stop"
    for run_id, source in ((first_run, initial_run), (second_run, first_run)):
        meta = json.loads((box.data_dir / "form-fill/runs" / run_id / job / "meta.json").read_text())
        assert meta["source_run"] == source and meta["tab_id"] == original
    assert box.store.applications()[job]["status"] == "waiting"


def test_live_gate_resume_keeps_distinct_attempts_inside_one_batch(live_box):
    live_cases.listed(live_box)
    live_cases.live(
        live_box,
        ["j00"],
        None,
        None,
        live_cases.says("done"),
        plan={"j00": [("failed", "captcha", ["synthetic gate"])]},
    )
    attempts = [a for a in live_box.store.apply_attempts() if a["job_id"] == "j00" and a["phase"] == "resume"]
    assert len(attempts) == 2 and attempts[0]["run_id"] != attempts[1]["run_id"]
    metas = [
        json.loads((live_box.data_dir / "form-fill/runs" / a["run_id"] / "j00/meta.json").read_text()) for a in attempts
    ]
    assert metas[0]["tab_id"] == metas[1]["tab_id"]
    assert metas[1]["source_run"] == attempts[0]["run_id"]
    from intent_job_agent.form_fill.cli import run_report

    root_run = live_box.apply_status()["run_id"]
    summary = run_report(root_run, live_box.data_dir, live_box.settings).read_text()
    assert all(a["run_id"] in summary for a in attempts)


def test_browser_lost_inside_fill_executor_preserves_the_hold(box, monkeypatch):
    from intent_job_agent.browser_session import BrowserIdentityLost

    job = held_job(box)
    original = box.form_fill_runner.resume_chrome
    called = []

    def lose(generation):
        called.append(generation)
        if len(called) == 2:
            raise BrowserIdentityLost("synthetic browser disappeared after preparation")
        return original(generation)

    monkeypatch.setattr(box.form_fill_runner, "resume_chrome", lose)
    before = box.store.hold(job)
    attempts = len(box.store.apply_attempts())
    gates.resumed(box, [job])
    after = box.store.hold(job)
    assert after and after["tab_id"] == before["tab_id"] and after["times"] == before["times"]
    assert not after["resuming"] and len(box.store.apply_attempts()) == attempts
    assert box.store.applications()[job]["status"] == "held"
    assert len(box.form_fill_runner.fills) == 1 and box.store.activity() is None


@pytest.mark.parametrize("change", ["missing", "replacement", "site", "generation"])
def test_lost_or_changed_tab_does_not_start_another_application(box, change):
    job = held_job(box)
    browser = box.form_fill_runner
    target = next(t for t in browser.tabs if t["id"] == box.store.hold(job)["tab_id"])
    if change == "missing":
        browser.tabs.remove(target)
    elif change == "replacement":
        target["id"] = "same-address-other-tab"
    elif change == "site":
        target["url"] = "https://unrelated.example.test/"
    else:
        target["generation"] = "new-browser"
    before = len(browser.fills)
    plan = box.prepare_resume([job])
    assert plan["job_ids"] == [] and plan["problems"]
    assert len(browser.fills) == before and box.store.hold(job)["times"] == 1


def test_metadata_inspection_failure_does_not_become_a_fresh_start(box, monkeypatch):
    job = held_job(box)

    def fail():
        raise RuntimeError("synthetic-private-path")

    monkeypatch.setattr(box.form_fill_runner, "open_tabs", fail)
    with pytest.raises(InvariantError, match="verify") as error:
        box.prepare_resume([job])
    assert "synthetic-private-path" not in str(error.value)


@pytest.mark.parametrize("raises", [True, False])
def test_failed_unattended_close_keeps_original_identity(box, monkeypatch, raises):
    job = held_job(box)
    hold = box.store.hold(job)

    def fail(_):
        if raises:
            raise RuntimeError("synthetic close failure")
        return False

    monkeypatch.setattr(box.form_fill_runner, "close_tab", fail, raising=False)
    outcome = box._apply_run["jobs"][0]["outcome"]
    box._unattended_close(job, outcome, box.store, box.form_fill_runner)
    after = box.store.hold(job)
    assert after["tab_id"] == hold["tab_id"] and after["generation"] == hold["generation"]
    assert not after["closed_by_runner"]


@pytest.mark.parametrize("change", ["missing_generation", "missing_tab", "changed_url", "duplicate"])
def test_submit_executor_rechecks_identity_without_cold_start(box, monkeypatch, change):
    from intent_job_agent import apply_bridge, browser_session

    from .apply_fakes import synthetic_binding

    job = held_job(box)
    spec = replace(
        box._apply_spec(job, "submit"),
        resume=True,
        resume_tab="original",
        resume_url="https://jobs.example.test/current",
        resume_generation="synthetic",
    )
    target = {"id": "original", "type": "page", "url": spec.resume_url, "generation": "synthetic"}
    tabs = [target]
    if change == "missing_generation":
        spec = replace(spec, resume_generation=None)
    elif change == "missing_tab":
        tabs = []
    elif change == "changed_url":
        target["url"] += "/changed"
    else:
        tabs.append({**target, "id": "duplicate"})
    monkeypatch.setattr(apply_bridge, "configure", lambda *args: None)
    monkeypatch.setattr(browser_session, "ensure", lambda settings, **kw: synthetic_binding())
    monkeypatch.setattr(apply_bridge.readback, "list_targets", lambda *args, **kw: tabs)

    def forbidden(*args, **kwargs):
        pytest.fail("Must not launch an agent or cold-start Chrome")

    monkeypatch.setattr(apply_bridge, "ensure_chrome", forbidden)
    monkeypatch.setattr(apply_bridge, "run_application", forbidden)
    runner = apply_bridge.ChromeRunner(box.data_dir, box.settings, box._applicant().profile)
    assert runner(spec).reason == "browser_identity_lost_before_launch"
