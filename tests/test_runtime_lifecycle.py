"""Synthetic X1-X3 lifecycle, deterministic clocks and thread-start failure injection."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from intent_job_agent.runtime.provider import Runtime, Tool
from intent_job_agent.runtime.providers.fake import ScriptedProvider


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str]


def start(rt, request_id="r", workspace="w", run_id="run"):
    return rt.structured(
        request_id=request_id,
        workspace=workspace,
        run_id=run_id,
        revision=1,
        node="tagging",
        data={"job": {"description": "synthetic"}},
        categories=["job"],
        schema=Value,
    )


def make(budgets=None):
    return Runtime.load(
        {"provider": {"name": "fake"}, "budgets": budgets or {}}, ScriptedProvider([{"value": {"tags": []}}] * 5)
    )


def test_zero_tools_only_blocks_tool_turn():
    rt = make({"max_tool_calls": 0})
    h = start(rt)
    assert h.wait(2).status == "completed"
    assert h.drained.wait(2)
    tool = Tool(name="inspect_page", parameters=Value)
    blocked = rt.tool_turn(
        request_id="tool",
        workspace="w",
        run_id="run",
        revision=1,
        node="form_fill",
        data={},
        categories=[],
        tools=(tool,),
    )
    assert blocked.wait(2).error == "budget_exhausted"
    assert len(rt.provider.bodies) == 1


def test_constructor_idle_does_not_spend_total_time(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("intent_job_agent.runtime.provider.time", SimpleNamespace(monotonic=lambda: now[0]))
    rt = make({"total_timeout_seconds": 1.0})
    now[0] = 999.0
    first = start(rt)
    assert first.wait(2).status == "completed"
    assert first.drained.wait(2)
    now[0] += 2.0
    assert start(rt, "second").wait(2).error == "budget_exhausted"
    assert len(rt.provider.bodies) == 1


def test_other_scope_is_stale_and_does_not_consume_budget():
    rt = make({"max_requests": 2})
    first = start(rt)
    assert first.wait(2).status == "completed"
    assert first.drained.wait(2)
    assert start(rt, "foreign", run_id="another").wait(2).error == "stale"
    assert start(rt, "foreign2", workspace="other").wait(2).error == "stale"
    second = start(rt, "second")
    assert second.wait(2).status == "completed"
    assert len(rt.provider.bodies) == 2


@pytest.mark.parametrize("fail_n", [1, 2])
def test_thread_start_failure_rolls_back_unsent_slot(monkeypatch, fail_n):
    import threading

    now = [1.0]
    monkeypatch.setattr("intent_job_agent.runtime.provider.time", SimpleNamespace(monotonic=lambda: now[0]))
    original = threading.Thread.start
    calls = [0]

    def start_or_fail(thread):
        calls[0] += 1
        if calls[0] == fail_n:
            raise RuntimeError("synthetic resource failure /private/path")
        return original(thread)

    monkeypatch.setattr(threading.Thread, "start", start_or_fail)
    rt = make({"max_requests": 1, "total_timeout_seconds": 1.0})
    h = start(rt)
    assert h.wait(2).error == "internal"
    assert h.drained.wait(2)
    assert h.snapshot().attempts == 0
    assert h.snapshot().cancel.transport == "not_sent"
    assert rt.provider.bodies == []
    now[0] = 100.0
    ok = start(rt, "r", workspace="after-launch-failure")
    assert ok.wait(2).status == "completed"
    assert ok.drained.wait(2)
    assert len(rt.provider.bodies) == 1


def test_invalid_input_does_not_bind_scope():
    rt = make()
    rejected = rt.start(
        request_id="bad",
        workspace="other",
        run_id="foreign",
        revision=1,
        node="tagging",
        data={"profile": {"path": "synthetic"}},
        categories=["profile"],
        schema=Value,
    )
    assert rejected.wait().error == "permission_denied"
    assert start(rt).wait(2).status == "completed"


def test_first_admission_atomically_wins_scope():
    from queue import Queue
    from threading import Barrier, Event, Thread

    entered, release = Event(), Event()

    def pending(body, cancel):
        entered.set()
        assert release.wait(3)
        return {"value": {"tags": []}}

    rt = Runtime.load({"provider": {"name": "fake"}, "budgets": {"concurrency": 2}}, ScriptedProvider([pending]))
    gate = Barrier(3)
    results = Queue()

    def attempt(scope):
        gate.wait()
        results.put(start(rt, scope, workspace=scope))

    threads = [Thread(target=attempt, args=(scope,)) for scope in ("a", "b")]
    for t in threads:
        t.start()
    gate.wait()
    try:
        for t in threads:
            t.join(2)
            assert not t.is_alive()
        assert entered.wait(2)
        handles = [results.get_nowait(), results.get_nowait()]
        assert sorted(h.snapshot().status for h in handles) == ["failed", "running"]
        assert next(h for h in handles if h.snapshot().status == "failed").snapshot().error == "stale"
        assert len(rt.provider.bodies) == 1
    finally:
        release.set()
    for h in handles:
        assert h.drained.wait(2)


@pytest.mark.parametrize("action", ["cancel", "invalidate", "revision"])
@pytest.mark.parametrize("reuse", ["scope", "id"])
def test_admitted_unsent_identity_survives_stop(monkeypatch, action, reuse):
    from threading import Event

    rt = make()
    admitted, release = Event(), Event()
    original = rt._run

    def paused(handle, *args):
        admitted.set()
        assert release.wait(3)
        return original(handle, *args)

    monkeypatch.setattr(rt, "_run", paused)
    h = start(rt)
    try:
        assert admitted.wait(2)
        if action == "cancel":
            h.cancel()
        elif action == "invalidate":
            rt.invalidate("w", "run")
        else:
            rt.set_revision("w", "run", 2)
    finally:
        release.set()
    assert h.drained.wait(2)
    assert h.snapshot().attempts == 0
    assert rt.provider.bodies == []
    monkeypatch.setattr(rt, "_run", original)
    if reuse == "scope":
        rejected = start(rt, "foreign", run_id="another")
    else:
        # Current revision avoids confusing revision rejection with retained ID.
        rejected = rt.structured(
            request_id="r",
            workspace="w",
            run_id="run",
            revision=2 if action == "revision" else 1,
            node="tagging",
            data={"job": {"description": "synthetic"}},
            categories=["job"],
            schema=Value,
        )
    assert rejected.wait(2).error == "stale"
    assert rejected.drained.wait(2)
    assert rt.provider.bodies == []
    if action == "cancel":
        ok = start(rt, "new")
        assert ok.wait(2).status == "completed"
        assert ok.drained.wait(2)


def test_admitted_budget_exhaustion_retains_request_id(monkeypatch):
    from threading import Event

    rt = make({"max_requests": 1, "concurrency": 2})
    admitted, release = Event(), Event()
    original = rt._run

    def paused(handle, *args):
        if handle.snapshot().request_id == "delayed":
            admitted.set()
            assert release.wait(3)
        return original(handle, *args)

    monkeypatch.setattr(rt, "_run", paused)
    delayed = start(rt, "delayed")
    try:
        assert admitted.wait(2)
        first = start(rt, "first")
        assert first.wait(2).status == "completed"
        assert first.drained.wait(2)
    finally:
        release.set()
    assert delayed.drained.wait(2)
    assert delayed.snapshot().error == "budget_exhausted"
    assert delayed.snapshot().attempts == 0
    assert start(rt, "delayed").wait(2).error == "stale"
    assert len(rt.provider.bodies) == 1
