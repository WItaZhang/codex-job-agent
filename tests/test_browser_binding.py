"""Synthetic R1b identity tests: no real browser, applicant or network."""

from dataclasses import replace
from pathlib import Path

import pytest

from intent_job_agent import browser_session as bs
from intent_job_agent import watchdog


def binding(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    profile = root / "browser" / "profile"
    profile.mkdir(parents=True)
    (profile / "DevToolsActivePort").write_text("43123\n/devtools/browser/synthetic-guid\n")
    b = bs.BrowserBinding(
        root=str(root),
        profile=str(profile),
        generation="synthetic",
        pid=123,
        started="synthetic-start",
        endpoint="ws://127.0.0.1:43123/devtools/browser/synthetic-guid",
        owner_pid=456,
        owner_started="owner-start",
        db_path=str(root / "state.db"),
    )
    proc = watchdog.Proc(123, 1, 123, b.started, f"chrome --user-data-dir={profile} --remote-debugging-port=0")
    monkeypatch.setattr(watchdog, "processes", lambda: {123: proc})
    return b


def test_os_identity_and_profile_endpoint_are_required(tmp_path, monkeypatch):
    b = binding(tmp_path, monkeypatch)
    bs.validate_os(b)
    for bad in (
        replace(b, pid=999),
        replace(b, started="reused"),
        replace(b, endpoint=b.endpoint.replace("synthetic-guid", "foreign")),
    ):
        with pytest.raises(bs.BrowserIdentityLost):
            bs.validate_os(bad)


def test_symlink_endpoint_is_refused(tmp_path, monkeypatch):
    b = binding(tmp_path, monkeypatch)
    active = Path(b.profile) / "DevToolsActivePort"
    outside = tmp_path / "foreign"
    active.rename(outside)
    active.symlink_to(outside)
    with pytest.raises(bs.BrowserIdentityLost):
        bs.validate_os(b)


def test_bare_port_cannot_be_used_as_identity():
    with pytest.raises(bs.BrowserIdentityLost):
        bs.validate_os(9222)


def test_metadata_checks_browser_pid_and_never_attaches(tmp_path, monkeypatch):
    b = binding(tmp_path, monkeypatch)
    calls = []

    def rpc(binding, method, **kwargs):
        calls.append(method)
        if method == "SystemInfo.getProcessInfo":
            return {"processInfo": [{"type": "browser", "id": b.pid}]}
        return {"targetInfos": [{"targetId": "t", "type": "page", "url": "about:blank"}]}

    monkeypatch.setattr(bs, "rpc", rpc)
    assert bs.targets(b)[0]["id"] == "t"
    assert calls == ["SystemInfo.getProcessInfo", "Target.getTargets"]
    monkeypatch.setattr(bs, "rpc", lambda *a, **k: {"processInfo": [{"type": "browser", "id": 999}]})
    with pytest.raises(bs.BrowserIdentityLost):
        bs.targets(b)


def test_foreign_process_is_never_killed(tmp_path, monkeypatch):
    b = binding(tmp_path, monkeypatch)
    monkeypatch.setattr(watchdog, "processes", lambda: {})
    monkeypatch.setattr(bs.os, "kill", lambda *a: pytest.fail("foreign kill"))
    with pytest.raises(bs.BrowserIdentityLost):
        bs.close(b)
