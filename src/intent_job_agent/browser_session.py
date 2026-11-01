"""Workspace-owned browsers. Exact endpoints are identity evidence, never bare-port discovery.

Supported process/lock implementations: macOS and Linux. This does not authenticate a
malicious process with full access to the same user's files. No daily profiles are adopted or copied; an
applicant may instead name their own separate Chrome instance in applicant.yaml (decision 33).
"""

import base64
import json
import os
import re
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import watchdog
from .domain import InvariantError


class BrowserIdentityLost(InvariantError):
    pass


class BrowserProbeTimeout(watchdog.ChromeDown):
    """An unanswered metadata probe; never evidence granting control."""


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
    kind: str = ""
    run_id: str = ""

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


def scope(settings):
    """(root, db, profile): the workspace and the one browser profile it may control."""
    return (
        str(Path(settings.storage.data_dir).resolve()),
        str(Path(settings.storage.db_path).resolve()),
        str(profile_dir(settings)),
    )


# Chrome's default user data folders (macOS, Linux). Chrome opens no debugging port on them, and the profiles inside
# belong to the daily browser.
_DAILY = (
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Google/Chrome Beta",
    "Library/Application Support/Google/Chrome Canary",
    "Library/Application Support/Chromium",
    ".config/google-chrome",
    ".config/google-chrome-beta",
    ".config/chromium",
)


def profile_dir(settings) -> Path:
    """The user data folder the applying Chrome runs on: the applicant's own instance when applicant.yaml names one
    (decision 33, spec 4.10 item 6), else this workspace's dedicated profile."""
    from . import applicant

    root = Path(settings.storage.data_dir).resolve()
    dedicated = root / "browser/profile"
    if not (applicant.folder(root, settings.apply.applicant_dir) / applicant.FILE).is_file():
        return dedicated
    try:
        own = applicant.load(root, settings.apply.applicant_dir).chrome_user_data_dir
    except InvariantError as error:
        raise BrowserIdentityLost(f"Cannot read chrome_user_data_dir: {error}") from error
    if own is None:
        return dedicated
    if not own.is_absolute():
        raise BrowserIdentityLost(f"chrome_user_data_dir must be an absolute path, not {own}")
    _plain(own)
    if not own.is_dir() or own.resolve() != own:
        raise BrowserIdentityLost(f"chrome_user_data_dir {own} is not a folder; prepare it first (docs/usage.md)")
    for daily in (Path.home() / d for d in _DAILY):
        if own == daily or daily in own.parents:
            raise BrowserIdentityLost(
                f"chrome_user_data_dir {own} is Chrome's default data folder or inside it; use a separate folder"
            )
    if own == root / "browser" or root / "browser" in own.parents:
        raise BrowserIdentityLost(f"chrome_user_data_dir {own} is inside this workspace's browser folder")
    return own


def _profile(expected):
    """The profile in an expected scope; a bare (root, db) means the workspace's dedicated one."""
    return Path(expected[2]) if len(expected) > 2 else Path(expected[0]) / "browser/profile"


def _own(expected):
    return _profile(expected) != Path(expected[0]) / "browser/profile"


def _root(binding, expected):
    if not isinstance(binding, BrowserBinding):
        raise BrowserIdentityLost("A complete BrowserBinding is required, not a port")
    if expected is None or (binding.root, binding.db_path) != tuple(map(str, expected[:2])):
        raise BrowserIdentityLost("Browser workspace differs from caller's expected root/db")


def _workspace(binding, expected):
    _root(binding, expected)
    if Path(binding.profile) != _profile(expected):
        raise BrowserIdentityLost("Browser profile is not the one this workspace is set to use")


def validate_os(binding: BrowserBinding, *, expected):
    if not isinstance(binding, BrowserBinding):
        raise BrowserIdentityLost("A complete BrowserBinding is required, not a port")
    _workspace(binding, expected)
    if sys.platform not in ("darwin", "linux"):
        raise BrowserIdentityLost("Browser identity is not yet supported on this platform")
    try:
        root, profile = Path(binding.root), Path(binding.profile)
        _plain(root)
        _plain(profile)
        if root.resolve() != root or profile.resolve() != profile:
            raise BrowserIdentityLost("Browser profile is not a canonical path")
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


def validate_owner(binding, *, expected):
    validate_os(binding, expected=expected)
    row = activity(binding.db_path)
    if not row or (row["owner_pid"], row["owner_started"]) != (binding.owner_pid, binding.owner_started):
        raise BrowserIdentityLost("Browser control owner changed")
    if not binding.run_id or (row.get("kind"), row.get("run_id")) != (binding.kind, binding.run_id):
        raise BrowserIdentityLost("Browser execution identity changed")
    validate_generation(binding, expected=expected)
    if not watchdog.alive(binding.owner_pid, binding.owner_started):
        raise BrowserIdentityLost("Browser control owner is no longer alive")


def validate_generation(binding, *, expected):
    _workspace(binding, expected)
    current = _read_record(Path(expected[0]) / "browser/session.json")
    if current is None or (current.generation, current.pid, current.started) != (
        binding.generation,
        binding.pid,
        binding.started,
    ):
        raise BrowserIdentityLost("Browser session generation changed")


def activity(db_path):
    if not Path(db_path).is_file():
        raise BrowserIdentityLost("Browser control owner is missing")
    try:
        with closing(sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM apply_running WHERE id=1").fetchone()
            return dict(row) if row else None
    except sqlite3.Error as error:
        raise BrowserIdentityLost("Cannot read browser coordination record") from error


def _pid(info, binding):
    if [int(p["id"]) for p in info.get("processInfo", []) if p.get("type") == "browser"] != [binding.pid]:
        raise BrowserIdentityLost("CDP browser PID does not match OS identity")


def rpc(binding, method, timeout=3.0, *, expected):
    """Allowlisted metadata. Each connection checks PID before reading targets on that same socket."""
    if method not in ("SystemInfo.getProcessInfo", "Target.getTargets"):
        raise BrowserIdentityLost("Only browser metadata RPC methods are allowed")
    validate_os(binding, expected=expected)
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

                def request(number, name):
                    watchdog._send(sock, json.dumps({"id": number, "method": name}).encode())
                    while True:
                        message = json.loads(watchdog._receive(reader))
                        if message.get("id") == number:
                            if "error" in message:
                                raise BrowserIdentityLost(f"Identity CDP failed: {message['error']}")
                            return message["result"]

                info = request(1, "SystemInfo.getProcessInfo")
                _pid(info, binding)
                result = request(2, method) if method != "SystemInfo.getProcessInfo" else info
                validate_os(binding, expected=expected)
                return result
    except TimeoutError as error:
        validate_os(binding, expected=expected)
        raise BrowserProbeTimeout("Exact browser metadata probe timed out") from error
    except (OSError, ValueError, KeyError, ConnectionError, struct.error) as error:
        raise BrowserIdentityLost(f"Exact browser connection lost: {error}") from error


def verify(binding, *, expected):
    try:
        rpc(binding, "SystemInfo.getProcessInfo", expected=expected)
    except BrowserProbeTimeout as error:
        raise BrowserIdentityLost("Cannot grant browser control: identity verification timed out") from error


def targets(binding, *, expected):
    infos = rpc(binding, "Target.getTargets", expected=expected).get("targetInfos", [])
    return [
        {
            "id": i.get("targetId"),
            "type": i.get("type"),
            "url": i.get("url", ""),
            "title": i.get("title", ""),
            "generation": binding.generation,
            "openerId": i.get("openerId"),
        }
        for i in infos
    ]


def connect(playwright, binding, *, expected):
    """A page-capable connection requires the current control owner, unlike metadata reads."""
    validate_owner(binding, expected=expected)
    try:
        browser = playwright.chromium.connect_over_cdp(binding.endpoint, timeout=10000)
    except Exception as error:
        raise BrowserIdentityLost("Exact browser connection failed") from error
    try:
        info = browser.new_browser_cdp_session().send("SystemInfo.getProcessInfo")
        if [int(p["id"]) for p in info["processInfo"] if p["type"] == "browser"] != [binding.pid]:
            raise BrowserIdentityLost("Connected browser PID mismatch")
        validate_owner(binding, expected=expected)
        return browser
    except BaseException:
        # Browser.close over a CDP connection disconnects this client; it must never issue Browser.close CDP.
        browser.close()
        raise


@contextmanager
def management(root, *, timeout):
    if sys.platform not in ("darwin", "linux"):
        raise BrowserIdentityLost("Browser management is not supported on this platform")
    import fcntl

    folder = Path(root) / "browser"
    _plain(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "management.lock"
    _plain(path)
    with path.open("a") as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise BrowserIdentityLost(
                        "Another session is managing this browser; retry after it finishes"
                    ) from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            yield folder
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read_record(path):
    _plain(path)
    if not path.exists():
        return None
    try:
        return BrowserBinding.load(json.loads(path.read_text()))
    except (OSError, ValueError, BrowserIdentityLost) as error:
        raise BrowserIdentityLost(
            "Browser session record is damaged; preserve it and inspect "
            "this workspace's browser records before retrying"
        ) from error


def _atomic(path, value):
    _plain(path)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x") as out:
            json.dump(value, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def inspect(settings):
    settings.require_external("browser.inspect")
    expected = scope(settings)
    binding = _read_record(Path(expected[0]) / "browser/session.json")
    if binding is None:
        return None
    _root(binding, expected)
    if Path(binding.profile) != _profile(expected) or not watchdog.alive(binding.pid, binding.started):
        return None
    verify(binding, expected=expected)
    return binding


def _own_row(db_path):
    row = activity(db_path)
    me = watchdog.processes().get(os.getpid())
    if (
        not row
        or not me
        or (row["owner_pid"], row["owner_started"]) != (me.pid, me.started)
        or not row.get("run_id")
        or row.get("kind") not in ("step", "resume", "form_fill")
    ):
        raise BrowserIdentityLost("Only the current coordinated run owner may manage its browser")
    return row


def ensure(settings):
    settings.require_external("browser.ensure")
    expected = scope(settings)
    root, db_path, _ = expected
    with management(root, timeout=settings.apply.browser_lock_seconds) as folder:
        # Re-read only after taking the management lock: a previous run may have ended while waiting.
        row = _own_row(db_path)
        owner = dict(
            owner_pid=row["owner_pid"], owner_started=row["owner_started"], kind=row["kind"], run_id=row["run_id"]
        )
        path = folder / "session.json"
        old = _current(path, expected)
        if not _own(expected):
            _dedicated(folder, root, db_path)
        if old is not None:
            if watchdog.alive(old.pid, old.started):
                verify(old, expected=expected)
                binding = BrowserBinding(**{**old.dump(), **owner})
                _atomic(path, binding.dump())
                return binding
        return _launch(settings, expected, path, owner, lambda: _own_row(db_path) == row)


def open_instance(settings):
    """`intent-job-agent chrome`: open the applicant's own Chrome (decision 33), the one /apply uses, for everyday
    browsing. Not running: start it and record its identity with no run owner; a run takes it over in ensure().
    Running as recorded: only ask it for a new window."""
    settings.require_external("browser.open")
    from .vendor.applypilot.config import get_chrome_path

    expected = scope(settings)
    if not _own(expected):
        raise BrowserIdentityLost("applicant.yaml names no chrome_user_data_dir; /apply opens the dedicated profile")
    with management(expected[0], timeout=settings.apply.browser_lock_seconds) as folder:
        path = folder / "session.json"
        old = _current(path, expected)
        if old is not None and watchdog.alive(old.pid, old.started):
            verify(old, expected=expected)
            subprocess.Popen(
                [settings.apply.browser_executable or get_chrome_path(), f"--user-data-dir={_profile(expected)}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return old
        return _launch(settings, expected, path, dict(owner_pid=0, owner_started="", kind="", run_id=""), lambda: True)


def _current(path, expected):
    """This workspace's recorded browser for the configured profile. A record of another profile (the setting
    changed) is set aside: that browser keeps running and is never adopted."""
    old = _read_record(path)
    if old is None:
        return None
    _root(old, expected)
    return old if Path(old.profile) == _profile(expected) else None


def _dedicated(folder, root, db_path):
    profile = folder / "profile"
    marker = folder / "owned-profile.json"
    _plain(profile)
    _plain(marker)
    if profile.exists():
        try:
            owned = json.loads(marker.read_text())
        except (OSError, ValueError) as error:
            raise BrowserIdentityLost("Unrecognized profile; do not adopt or delete it") from error
        if owned != {"root": root, "db_path": db_path, "profile": str(profile)}:
            raise BrowserIdentityLost("Profile ownership record differs from this workspace")
    else:
        if marker.exists():
            raise BrowserIdentityLost("Owned profile is missing; inspect records before retrying")
        profile.mkdir(mode=0o700)
        _atomic(marker, {"root": root, "db_path": db_path, "profile": str(profile)})


def _launch(settings, expected, path, owner, still_owner):
    from .vendor.applypilot.config import get_chrome_path

    root, db_path = expected[:2]
    profile, own = _profile(expected), _own(expected)
    # No live process using this profile may be displaced, including an unknown stale session.
    if any(f"--user-data-dir={profile}" in p.command for p in watchdog.processes().values()):
        if own:
            raise BrowserIdentityLost(
                "Your own Chrome is open without a recorded identity; quit it, then reopen with intent-job-agent chrome"
            )
        raise BrowserIdentityLost("A live process still uses this profile; leave it untouched")
    active = profile / "DevToolsActivePort"
    _plain(active)
    active.unlink(missing_ok=True)  # only a profile with no live user
    proc = subprocess.Popen(
        launch_arguments(
            settings.apply.browser_executable or get_chrome_path(),
            profile,
            headless=settings.apply.browser_headless,
            own=own,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    identity = None
    try:
        deadline = time.monotonic() + settings.apply.browser_start_seconds
        while not active.is_file():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise BrowserIdentityLost("Dedicated Chrome did not publish its endpoint")
            time.sleep(0.05)
        identity = watchdog.processes()[proc.pid]
        port, endpoint = active.read_text().strip().splitlines()
        binding = BrowserBinding(
            root,
            str(profile),
            uuid.uuid4().hex,
            proc.pid,
            identity.started,
            f"ws://127.0.0.1:{port}{endpoint}",
            db_path=db_path,
            **owner,
        )
        verify(binding, expected=expected)
        if not still_owner():
            raise BrowserIdentityLost("Run changed while browser started")
        _atomic(path, binding.dump())
        return binding
    except BaseException:
        if proc.poll() is None and (identity is None or watchdog.alive(proc.pid, identity.started)):
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                if identity is not None and watchdog.alive(proc.pid, identity.started):
                    proc.kill()
                    try:
                        proc.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        pass  # Preserve the original failure; do not touch any other process.
        raise


def launch_arguments(executable, profile, *, headless=False, own=False):
    if own:
        # The applicant's everyday browser: only what the debugging endpoint needs (decision 33).
        return [
            executable,
            *(["--headless=new"] if headless else []),
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
        ]
    return [
        executable,
        *(["--headless=new"] if headless else []),
        "--remote-debugging-port=0",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        "--window-size=1024,768",
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
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "about:blank",
    ]


def close(binding, *, expected, timeout):
    _workspace(binding, expected)
    if _own(expected):
        raise BrowserIdentityLost("Code never closes the applicant's own Chrome")
    with management(expected[0], timeout=timeout):
        validate_owner(binding, expected=expected)
        verify(binding, expected=expected)
        os.kill(binding.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while watchdog.alive(binding.pid, binding.started):
            if time.monotonic() >= deadline:
                # Identity is rechecked; never escalate against a recycled PID.
                if watchdog.alive(binding.pid, binding.started):
                    os.kill(binding.pid, signal.SIGKILL)
                raise BrowserIdentityLost("Owned browser did not exit before the shutdown deadline")
            time.sleep(0.05)
