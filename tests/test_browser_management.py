"""E1–E7: synthetic boundary tests, real read-only SQLite and OS file locks."""

import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from intent_job_agent import browser_session as bs
from intent_job_agent import watchdog
from intent_job_agent.vendor.applypilot import config

from .conftest import settings


@pytest.fixture
def managed(tmp_path, monkeypatch):
    cfg = settings()
    db = tmp_path / "coord.db"
    cfg = cfg.model_copy(
        update={"storage": cfg.storage.model_copy(update={"data_dir": str(tmp_path), "db_path": str(db)})}
    )
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE apply_running(id,owner_pid,owner_started,kind,run_id,batch_id,phase)")
        conn.execute("INSERT INTO apply_running VALUES(1,?,'owner','form_fill','run-1',NULL,NULL)", (os.getpid(),))
    procs = {os.getpid(): watchdog.Proc(os.getpid(), 1, 1, "owner", "synthetic-server")}
    monkeypatch.setattr(watchdog, "processes", lambda: procs)
    monkeypatch.setattr(config, "get_chrome_path", lambda: "synthetic-chrome")
    monkeypatch.setattr(bs, "verify", lambda *a, **k: None)
    launched = []

    def popen(args, **kwargs):
        from pathlib import Path

        profile = Path(next(a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir=")))
        assert not (profile / "DevToolsActivePort").exists(), "stale endpoint survived before Popen"
        pid = 10000 + len(launched)
        (profile / "DevToolsActivePort").write_text(f"43123\n/devtools/browser/guid-{pid}\n")
        procs[pid] = watchdog.Proc(pid, 1, pid, f"start-{pid}", " ".join(args))
        launched.append(profile)
        return SimpleNamespace(pid=pid, poll=lambda: None, terminate=lambda: None, wait=lambda **k: None)

    monkeypatch.setattr(bs.subprocess, "Popen", popen)
    return cfg, db, procs, launched


def test_restart_keeps_owned_profile_and_changes_instance(managed):
    cfg, db, procs, launched = managed
    first = bs.ensure(cfg)
    cookie = launched[0] / "synthetic-cookie"
    cookie.write_text("synthetic session")
    procs.pop(first.pid)
    second = bs.ensure(cfg)
    assert first.profile == second.profile
    assert first.generation != second.generation
    assert cookie.read_text() == "synthetic session"
    assert len(launched) == 2


def test_owner_query_is_readonly_and_next_run_invalidates_old_binding(managed, monkeypatch):
    cfg, db, procs, _ = managed
    b = bs.ensure(cfg)
    from intent_job_agent import store

    monkeypatch.setattr(store, "Store", lambda *a, **k: pytest.fail("validation must not construct Store"))
    expected = (cfg.storage.data_dir, str(db))
    bs.validate_owner(b, expected=expected)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE apply_running SET run_id='run-2'")
    with pytest.raises(bs.BrowserIdentityLost):
        bs.validate_owner(b, expected=expected)


def test_copied_session_cannot_adopt_other_workspace(managed, tmp_path):
    cfg, db, _, _ = managed
    b = bs.ensure(cfg)
    other = tmp_path / "other"
    (other / "browser").mkdir(parents=True)
    (other / "browser/session.json").write_text(json.dumps(b.dump()))
    cfg2 = cfg.model_copy(update={"storage": cfg.storage.model_copy(update={"data_dir": str(other)})})
    with pytest.raises(bs.BrowserIdentityLost, match="workspace"):
        bs.ensure(cfg2)


def test_corrupt_session_is_not_overwritten(managed):
    cfg, _, _, _ = managed
    bs.ensure(cfg)
    from pathlib import Path

    path = Path(cfg.storage.data_dir) / "browser/session.json"
    path.write_text("{")
    with pytest.raises(bs.BrowserIdentityLost, match="record"):
        bs.ensure(cfg)
    assert path.read_text() == "{"


def test_management_lock_has_a_deadline(tmp_path):
    with bs.management(tmp_path, timeout=0.05):
        with pytest.raises(bs.BrowserIdentityLost, match="manag"):
            with bs.management(tmp_path, timeout=0.05):
                pytest.fail("second manager acquired lock")


def test_metadata_rpc_refuses_control_methods_without_connecting(monkeypatch):
    monkeypatch.setattr(bs.socket, "create_connection", lambda *a, **k: pytest.fail("network"))
    for method in ("Target.attachToTarget", "Runtime.evaluate", "Browser.close"):
        with pytest.raises(bs.BrowserIdentityLost):
            bs.rpc(None, method, expected=("/synthetic", "/synthetic/db"))


def test_connect_uses_expected_workspace_and_run_without_store(managed, monkeypatch):
    cfg, db, _, _ = managed
    b = bs.ensure(cfg)
    expected = bs.scope(cfg)
    calls = []

    class Browser:
        def new_browser_cdp_session(self):
            return SimpleNamespace(send=lambda method: {"processInfo": [{"type": "browser", "id": b.pid}]})

        def close(self):
            calls.append("disconnect")

    pw = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=lambda endpoint, **k: calls.append(endpoint) or Browser())
    )
    assert bs.connect(pw, b, expected=expected)
    assert calls == [b.endpoint]
    with pytest.raises(bs.BrowserIdentityLost):
        bs.connect(pw, b, expected=(expected[0] + "-foreign", expected[1]))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE apply_running SET run_id='next-run'")
    with pytest.raises(bs.BrowserIdentityLost):
        bs.connect(pw, b, expected=expected)
    assert calls == [b.endpoint]


def test_owner_is_rechecked_after_management_lock(managed, monkeypatch):
    from contextlib import contextmanager

    cfg, db, _, launched = managed
    original = bs.management

    @contextmanager
    def changed(root, **kwargs):
        with original(root, **kwargs) as folder:
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE apply_running SET owner_pid=999999")
            yield folder

    monkeypatch.setattr(bs, "management", changed)
    with pytest.raises(bs.BrowserIdentityLost):
        bs.ensure(cfg)
    assert launched == []


def test_session_write_uses_atomic_replace(managed, monkeypatch):
    cfg, _, _, _ = managed
    original = bs.os.replace
    seen = []

    def replace(source, target):
        # Complete JSON is available before the atomic change, never a half-written record.
        value = json.loads(source.read_text())
        seen.append((target.name, value))
        original(source, target)

    monkeypatch.setattr(bs.os, "replace", replace)
    b = bs.ensure(cfg)
    assert seen[-1] == ("session.json", b.dump())


def test_live_unknown_process_on_owned_profile_preserves_active_port(managed):
    cfg, _, procs, launched = managed
    b = bs.ensure(cfg)
    from pathlib import Path

    active = Path(b.profile) / "DevToolsActivePort"
    before = active.read_bytes()
    old = procs.pop(b.pid)
    procs[55555] = watchdog.Proc(55555, 1, 55555, "another", old.command)
    with pytest.raises(bs.BrowserIdentityLost, match="live process"):
        bs.ensure(cfg)
    assert active.read_bytes() == before and len(launched) == 1


@pytest.mark.parametrize("damage", ["marker", "missing-profile"])
def test_ownership_damage_does_not_modify_profile(managed, damage):
    from pathlib import Path

    cfg, _, procs, launched = managed
    b = bs.ensure(cfg)
    procs.pop(b.pid)
    marker = Path(b.root) / "browser/owned-profile.json"
    profile = Path(b.profile)
    if damage == "marker":
        marker.write_text('{"root":"foreign"}')
    else:
        profile.rename(profile.with_name("preserved-profile"))
    before = {str(p): p.read_bytes() for p in Path(b.root).rglob("*") if p.is_file()}
    with pytest.raises(bs.BrowserIdentityLost):
        bs.ensure(cfg)
    after = {str(p): p.read_bytes() for p in Path(b.root).rglob("*") if p.is_file()}
    assert after == before and len(launched) == 1


@pytest.mark.parametrize("reused", [False, True])
def test_close_deadline_never_escalates_reused_pid(managed, monkeypatch, reused):
    import signal

    cfg, _, procs, _ = managed
    b = bs.ensure(cfg)
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        if reused:
            old = procs[pid]
            procs[pid] = watchdog.Proc(pid, old.ppid, old.pgid, "reused", old.command)

    monkeypatch.setattr(bs.os, "kill", kill)
    if reused:
        bs.close(b, expected=bs.scope(cfg), timeout=0.02)
        assert calls == [(b.pid, signal.SIGTERM)]
    else:
        with pytest.raises(bs.BrowserIdentityLost, match="shutdown deadline"):
            bs.close(b, expected=bs.scope(cfg), timeout=0.02)
        assert calls == [(b.pid, signal.SIGTERM), (b.pid, signal.SIGKILL)]
