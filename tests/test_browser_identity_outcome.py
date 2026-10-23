"""Synthetic four-cell launch boundary and stop priority; no browser or agent process."""

import io
from types import SimpleNamespace

import pytest

from intent_job_agent import apply_bridge, browser_session, watchdog
from intent_job_agent.vendor.applypilot import config

from .apply_fakes import synthetic_binding
from .conftest import settings


@pytest.mark.parametrize("mode", ["submit", "stop"])
@pytest.mark.parametrize("launched", [False, True])
@pytest.mark.parametrize("stopped", [False, True])
def test_identity_loss_launch_cells_and_stop_priority(tmp_path, monkeypatch, mode, launched, stopped):
    s = settings()
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "APPLY_WORKER_DIR", tmp_path)
    monkeypatch.setattr(apply_bridge, "build_prompt", lambda *a: "synthetic")
    monkeypatch.setattr(browser_session, "validate_owner", lambda *a, **k: None)
    monkeypatch.setattr(watchdog, "LIVE", watchdog.Live())
    spawned = []

    class Watch:
        checks = 0

        def __init__(self, *a, **k):
            pass

        def check(self):
            self.checks += 1
            if self.checks == (2 if launched else 1):
                if stopped:
                    watchdog.LIVE.stop("synthetic", modes=("stop", "submit"))
                raise browser_session.BrowserIdentityLost("synthetic identity loss")

        def report(self, *a):
            return []

    monkeypatch.setattr(watchdog, "ChromeWatch", Watch)
    monkeypatch.setattr(watchdog, "Tree", lambda *a: SimpleNamespace(scan=lambda: None, kill=lambda **k: None))
    proc = SimpleNamespace(pid=987654, stdout=io.StringIO(), poll=lambda: None, wait=lambda **k: None)
    monkeypatch.setattr(apply_bridge.subprocess, "Popen", lambda *a, **k: spawned.append(1) or proc)
    monkeypatch.setattr(watchdog, "follow", lambda *a, **k: "stopped" if stopped else None)
    spec = apply_bridge.ApplySpec(
        "synthetic",
        mode,
        "https://example.test/job",
        "test",
        "synthetic",
        tmp_path / "r.pdf",
        tmp_path / "r.md",
        tmp_path / "l.txt",
        tmp_path / "l.pdf",
    )
    result = apply_bridge.run_application(spec, tmp_path, s, tmp_path / "synthetic-profile.json", synthetic_binding())
    assert len(spawned) == int(launched)
    if not launched:
        assert (result.status, result.reason) == ("pending", "browser_identity_lost_before_launch")
    elif mode == "submit":
        assert (result.status, result.reason) == ("held", "interrupted")
    else:
        assert (result.status, result.reason) == ("failed", "browser_identity_lost")


@pytest.mark.parametrize("mode", ["submit", "stop"])
def test_launch_and_stop_use_one_event_barrier(mode):
    import threading

    live = watchdog.Live()
    stop = live.begin("synthetic", mode)
    entered, release, requested, finished = (threading.Event() for _ in range(4))
    result = {}

    def factory():
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(pid=987654)

    def start():
        result["process"] = live.start_process(stop, factory)

    def halt():
        requested.set()
        result["stop"] = live.stop("synthetic")
        finished.set()

    starter = threading.Thread(target=start)
    starter.start()
    assert entered.wait(3)
    stopper = threading.Thread(target=halt)
    stopper.start()
    assert requested.wait(3)
    assert not finished.is_set()  # cannot slip between launch authorization and Popen
    release.set()
    starter.join(3)
    stopper.join(3)
    assert not starter.is_alive() and not stopper.is_alive()
    assert result["process"].pid == 987654
    assert result["stop"] == ("running" if mode == "submit" else "stopped")


@pytest.mark.parametrize("mode", ["submit", "stop"])
def test_prelaunch_stop_barrier_never_calls_factory(mode):
    import threading

    live = watchdog.Live()
    stop = live.begin("synthetic", mode)
    ready, release = threading.Event(), threading.Event()
    result = []

    def launch():
        ready.set()
        assert release.wait(3)
        result.append(live.start_process(stop, lambda: pytest.fail("agent launched after stop")))

    worker = threading.Thread(target=launch)
    worker.start()
    assert ready.wait(3)
    assert live.stop("synthetic") == "armed"
    release.set()
    worker.join(3)
    assert not worker.is_alive() and result == [None]
