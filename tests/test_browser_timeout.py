"""Synthetic socket timeouts must not masquerade as a changed browser identity."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from intent_job_agent import browser_session as bs
from intent_job_agent import watchdog

from .test_browser_binding import binding


def setup(tmp_path, monkeypatch):
    b = binding(tmp_path, monkeypatch)
    (Path(b.root) / "browser/session.json").write_text(json.dumps(b.dump()))
    return b, (b.root, b.db_path)


def timeout(*a, **k):
    raise TimeoutError("synthetic timeout")


def test_single_repeated_and_recovered_timeout(tmp_path, monkeypatch):
    b, expected = setup(tmp_path, monkeypatch)
    watch = watchdog.ChromeWatch(b, [], expected=expected)
    monkeypatch.setattr(bs.socket, "create_connection", timeout)
    watch.check()
    assert watch.misses == 1 and watch.report([]) == []
    watch.check()
    watch.check()
    assert watch.misses == 3
    assert "超时" in watch.report([])[0]
    assert "崩溃" not in watch.report([])[0]
    monkeypatch.setattr(bs, "targets", lambda *a, **k: [])
    watch.check()
    assert watch.misses == 0 and watch.up is not None


@pytest.mark.parametrize("change", ["pid", "generation", "endpoint"])
def test_timeout_rechecks_changed_identity_first(tmp_path, monkeypatch, change):
    b, expected = setup(tmp_path, monkeypatch)

    def slow(*a, **k):
        if change == "pid":
            monkeypatch.setattr(watchdog, "processes", lambda: {})
        elif change == "generation":
            (Path(b.root) / "browser/session.json").write_text(json.dumps(replace(b, generation="other").dump()))
        else:
            (Path(b.profile) / "DevToolsActivePort").write_text("43123\n/devtools/browser/other\n")
        raise TimeoutError("synthetic")

    monkeypatch.setattr(bs.socket, "create_connection", slow)
    with pytest.raises(bs.BrowserIdentityLost):
        watchdog.ChromeWatch(b, [], expected=expected).check()


def test_connection_refusal_is_immediate(tmp_path, monkeypatch):
    b, expected = setup(tmp_path, monkeypatch)

    def refused(*a, **k):
        raise ConnectionRefusedError("synthetic")

    monkeypatch.setattr(bs.socket, "create_connection", refused)
    with pytest.raises(bs.BrowserIdentityLost):
        watchdog.ChromeWatch(b, [], expected=expected).check()


def test_timeout_never_verifies_new_control(tmp_path, monkeypatch):
    b, expected = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(bs.socket, "create_connection", timeout)
    with pytest.raises(bs.BrowserIdentityLost):
        bs.verify(b, expected=expected)


@pytest.mark.parametrize("headless", [False, True])
def test_launch_flags_preserve_behavior_without_foreign_ownership(headless):
    flags = bs.launch_arguments("synthetic-chrome", "/synthetic/own", headless=headless)
    required = {
        "--window-size=1024,768",
        "--profile-directory=Default",
        "--disable-session-crashed-bubble",
        "--disable-features=InfiniteSessionRestore,PasswordManagerOnboarding",
        "--hide-crash-restore-bubble",
        "--noerrdialogs",
        "--password-store=basic",
        "--disable-save-password-bubble",
        "--disable-popup-blocking",
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        "--deny-permission-prompts",
        "--disable-notifications",
        "--remote-debugging-port=0",
        "--user-data-dir=/synthetic/own",
    }
    assert required <= set(flags)
    assert "--enable-automation" not in flags
    assert [x for x in flags if x.startswith("--remote-debugging-port=")] == ["--remote-debugging-port=0"]
    assert ("--headless=new" in flags) == headless


@pytest.mark.parametrize("stage", ["handshake", "pid", "targets"])
def test_read_timeout_is_only_a_probe_miss(tmp_path, monkeypatch, stage):
    import io

    b, expected = setup(tmp_path, monkeypatch)

    class Reader(io.BytesIO):
        def readline(self, *args):
            if stage == "handshake":
                raise TimeoutError("synthetic handshake")
            return super().readline(*args)

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def sendall(self, data):
            pass

        def makefile(self, mode):
            return Reader(b"HTTP/1.1 101 Switching Protocols\r\n\r\n")

    calls = []

    def receive(reader):
        calls.append(1)
        if stage == "pid" or len(calls) > 1:
            raise TimeoutError("synthetic read")
        return json.dumps({"id": 1, "result": {"processInfo": [{"type": "browser", "id": b.pid}]}})

    monkeypatch.setattr(bs.socket, "create_connection", lambda *a, **k: Socket())
    monkeypatch.setattr(watchdog, "_receive", receive)
    watch = watchdog.ChromeWatch(b, [], expected=expected)
    watch.check()
    assert watch.misses == 1
    with pytest.raises(bs.BrowserProbeTimeout):
        bs.targets(b, expected=expected)


@pytest.mark.parametrize("response", [b"HTTP/1.1 404 Not Found\r\n", b""])
def test_wrong_guid_handshake_or_disconnect_is_not_timeout(tmp_path, monkeypatch, response):
    import io

    b, expected = setup(tmp_path, monkeypatch)

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def sendall(self, data):
            pass

        def makefile(self, mode):
            return io.BytesIO(response)

    monkeypatch.setattr(bs.socket, "create_connection", lambda *a, **k: Socket())
    with pytest.raises(bs.BrowserIdentityLost):
        watchdog.ChromeWatch(b, [], expected=expected).check()


def test_control_owner_changed_during_timeout_wins(tmp_path, monkeypatch):
    b, expected = setup(tmp_path, monkeypatch)
    checks = []

    def owner(*a, **k):
        checks.append(1)
        if len(checks) > 1:
            raise bs.BrowserIdentityLost("run changed during probe")

    monkeypatch.setattr(bs, "validate_owner", owner)
    monkeypatch.setattr(bs.socket, "create_connection", timeout)
    with pytest.raises(bs.BrowserIdentityLost, match="run changed"):
        watchdog.ChromeWatch(b, [], expected=expected, control=True).check()


def test_connect_timeout_does_not_return_a_connection(tmp_path, monkeypatch):
    from types import SimpleNamespace

    b, expected = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(bs, "validate_owner", lambda *a, **k: None)
    pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=timeout))
    with pytest.raises(bs.BrowserIdentityLost):
        bs.connect(pw, b, expected=expected)


def test_disconnect_in_extended_frame_is_identity_loss(tmp_path, monkeypatch):
    import io

    b, expected = setup(tmp_path, monkeypatch)

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def sendall(self, data):
            pass

        def makefile(self, mode):
            return io.BytesIO(b"HTTP/1.1 101 Switching Protocols\r\n\r\n\x81\x7e\x00")

    monkeypatch.setattr(bs.socket, "create_connection", lambda *a, **k: Socket())
    with pytest.raises(bs.BrowserIdentityLost):
        watchdog.ChromeWatch(b, [], expected=expected).check()
