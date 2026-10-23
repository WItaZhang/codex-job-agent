"""Workspace-owned browsers. Exact endpoints are identity evidence, never bare-port discovery.

Supported process/lock implementations: macOS and Linux. This does not authenticate a
malicious process with full access to the same user's files. No daily profiles are adopted.
"""

import base64
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import watchdog
from .domain import InvariantError


class BrowserIdentityLost(InvariantError):
    pass


@dataclass(frozen=True)
class BrowserBinding:
    root: str
    profile: str
    generation: str
    pid: int
    started: str
    endpoint: str
    owner_pid: int
    owner_started: str
    db_path: str

    def dump(self):
        return asdict(self)

    @classmethod
    def load(cls, value):
        try:
            return cls(**value)
        except (TypeError, ValueError):
            raise BrowserIdentityLost("Missing or invalid browser binding; port-only references are refused") from None


def _plain(path: Path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise BrowserIdentityLost("Symlink in browser identity path")


def validate_os(binding: BrowserBinding):
    if not isinstance(binding, BrowserBinding):
        raise BrowserIdentityLost("A complete BrowserBinding is required, not a port")
    if sys.platform not in ("darwin", "linux"):
        raise BrowserIdentityLost("Browser identity is not yet supported on this platform")
    try:
        root, profile = Path(binding.root), Path(binding.profile)
        _plain(root)
        _plain(profile)
        if root.resolve() != root or not profile.is_relative_to(root / "browser"):
            raise BrowserIdentityLost("Browser profile is outside the canonical workspace")
        proc = watchdog.processes().get(binding.pid)
        if proc is None or proc.started != binding.started:
            raise BrowserIdentityLost("Browser PID/start identity lost")
        # ps renders spaces in paths without quotes. Require the whole argument and boundary;
        # ambiguous command lines are refused rather than guessing a different profile.
        argument = re.escape(f"--user-data-dir={profile}")
        if not re.search(r"(?:^|\s)" + argument + r"(?=\s+--|\s+about:blank(?:\s|$)|$)", proc.command):
            raise BrowserIdentityLost("Browser OS profile argument does not match")
        active = profile / "DevToolsActivePort"
        _plain(active)
        port, path = active.read_text().strip().splitlines()
        if not 0 < int(port) < 65536 or not path.startswith("/devtools/browser/"):
            raise BrowserIdentityLost("Invalid browser endpoint record")
        if binding.endpoint != f"ws://127.0.0.1:{port}{path}":
            raise BrowserIdentityLost("Browser endpoint generation changed")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise BrowserIdentityLost(f"Cannot verify browser identity: {error}") from error


def validate_owner(binding):
    validate_os(binding)
    from .store import Store

    if not Path(binding.db_path).is_file():
        raise BrowserIdentityLost("Browser control owner is missing")
    store = Store(binding.db_path)
    try:
        row = store.activity()
        if not row or (row["owner_pid"], row["owner_started"]) != (binding.owner_pid, binding.owner_started):
            raise BrowserIdentityLost("Browser control owner changed")
        if not watchdog.alive(binding.owner_pid, binding.owner_started):
            raise BrowserIdentityLost("Browser control owner is no longer alive")
    finally:
        store.db.close()


def rpc(binding, method, timeout=3.0):
    """Browser-scope CDP only. Never discovers endpoints over HTTP or attaches to pages."""
    validate_os(binding)
    endpoint = urlsplit(binding.endpoint)
    try:
        with socket.create_connection(("127.0.0.1", endpoint.port), timeout=timeout) as sock:
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall(
                (
                    f"GET {endpoint.path} HTTP/1.1\r\nHost: 127.0.0.1:{endpoint.port}\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                ).encode()
            )
            with sock.makefile("rb") as reader:
                if b" 101 " not in reader.readline():
                    raise BrowserIdentityLost("Exact browser WebSocket refused")
                while reader.readline() not in (b"\r\n", b""):
                    pass
                watchdog._send(sock, json.dumps({"id": 1, "method": method}).encode())
                while True:
                    message = json.loads(watchdog._receive(reader))
                    if message.get("id") == 1:
                        if "error" in message:
                            raise BrowserIdentityLost(f"Identity CDP failed: {message['error']}")
                        return message["result"]
    except (OSError, ValueError, KeyError, ConnectionError) as error:
        raise BrowserIdentityLost(f"Exact browser connection lost: {error}") from error


def verify(binding):
    validate_os(binding)
    info = rpc(binding, "SystemInfo.getProcessInfo")
    if [int(p["id"]) for p in info.get("processInfo", []) if p.get("type") == "browser"] != [binding.pid]:
        raise BrowserIdentityLost("CDP browser PID does not match OS identity")
    validate_os(binding)


def targets(binding):
    verify(binding)
    infos = rpc(binding, "Target.getTargets").get("targetInfos", [])
    return [
        {
            "id": i.get("targetId"),
            "type": i.get("type"),
            "url": i.get("url", ""),
            "title": i.get("title", ""),
            "openerId": i.get("openerId"),
        }
        for i in infos
    ]


def connect(playwright, binding):
    """A page-capable connection requires the current control owner, unlike metadata reads."""
    validate_owner(binding)
    browser = playwright.chromium.connect_over_cdp(binding.endpoint, timeout=10000)
    try:
        info = browser.new_browser_cdp_session().send("SystemInfo.getProcessInfo")
        if [int(p["id"]) for p in info["processInfo"] if p["type"] == "browser"] != [binding.pid]:
            raise BrowserIdentityLost("Connected browser PID mismatch")
        validate_owner(binding)
        return browser
    except BaseException:
        # Browser.close over a CDP connection disconnects this client; it must never issue Browser.close CDP.
        browser.close()
        raise


@contextmanager
def management(root):
    if sys.platform not in ("darwin", "linux"):
        raise BrowserIdentityLost("Browser management is not supported on this platform")
    import fcntl

    folder = Path(root) / "browser"
    _plain(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "management.lock"
    _plain(path)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield folder
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def inspect(settings):
    settings.require_external("browser.inspect")
    root = Path(settings.storage.data_dir).resolve()
    path = root / "browser" / "session.json"
    _plain(path)
    if not path.exists():
        return None
    try:
        binding = BrowserBinding.load(json.loads(path.read_text()))
        if binding.root != str(root) or binding.db_path != str(Path(settings.storage.db_path).resolve()):
            raise BrowserIdentityLost("Browser workspace mismatch")
        verify(binding)
        return binding
    except (OSError, ValueError) as error:
        raise BrowserIdentityLost(f"Invalid browser session: {error}") from error


def ensure(settings):
    settings.require_external("browser.ensure")
    from .store import Store
    from .vendor.applypilot.config import get_chrome_path

    root = Path(settings.storage.data_dir).resolve()
    db_path = Path(settings.storage.db_path).resolve()
    if not db_path.is_file():
        raise BrowserIdentityLost("Start a coordinated run before starting its browser")
    store = Store(db_path)
    try:
        row = store.activity()
    finally:
        store.db.close()
    me = watchdog.processes().get(os.getpid())
    if not row or not me or (row["owner_pid"], row["owner_started"]) != (me.pid, me.started):
        raise BrowserIdentityLost("Only the coordinated run owner may start or adopt its browser")
    with management(root) as folder:
        path = folder / "session.json"
        if path.exists():
            old = BrowserBinding.load(json.loads(path.read_text()))
            if watchdog.alive(old.pid, old.started):
                verify(old)
                binding = BrowserBinding(**{**old.dump(), "owner_pid": me.pid, "owner_started": me.started})
                path.write_text(json.dumps(binding.dump()))
                return binding
            # A dead browser is never killed/adopted by port. Its profile is retained, never deleted.
        generation = uuid.uuid4().hex
        profile = folder / f"profile-{generation}"
        profile.mkdir(mode=0o700)
        executable = get_chrome_path()
        args = launch_arguments(executable, profile)
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        identity = None
        try:
            deadline = time.monotonic() + 20
            while not (profile / "DevToolsActivePort").is_file():
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise BrowserIdentityLost("Dedicated Chrome did not publish its endpoint")
                time.sleep(0.05)
            identity = watchdog.processes()[proc.pid]
            port, endpoint = (profile / "DevToolsActivePort").read_text().strip().splitlines()
            binding = BrowserBinding(
                str(root),
                str(profile),
                generation,
                proc.pid,
                identity.started,
                f"ws://127.0.0.1:{port}{endpoint}",
                me.pid,
                me.started,
                str(db_path),
            )
            verify(binding)
            path.write_text(json.dumps(binding.dump()))
            return binding
        except BaseException:
            if proc.poll() is None and (identity is None or watchdog.alive(proc.pid, identity.started)):
                proc.terminate()
                proc.wait(timeout=10)
            raise


def launch_arguments(executable, profile, *, headless=False):
    return [
        executable,
        *(["--headless=new"] if headless else []),
        "--remote-debugging-port=0",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "about:blank",
    ]


def close(binding):
    with management(binding.root):
        validate_owner(binding)
        verify(binding)
        os.kill(binding.pid, signal.SIGTERM)
