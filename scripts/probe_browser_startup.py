"""CI-only synthetic startup diagnostic, not a browser validation or product execution entrypoint.

Always reports findings; real pytest and product fixture remain the validation gates.
Never reads an existing profile or changes sandbox/system policy.
"""

import json
import os
import platform
import selectors
import subprocess
import tempfile
import time
from pathlib import Path

LIMIT = 16384


def capture(args, endpoint, timeout):
    tail, seen = bytearray(), 0
    started = time.monotonic()
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    before = None
    published = expired = False
    selector = selectors.DefaultSelector()
    os.set_blocking(proc.stderr.fileno(), False)
    selector.register(proc.stderr, selectors.EVENT_READ)

    def drain(wait):
        nonlocal seen
        for key, _ in selector.select(wait):
            chunk = os.read(key.fd, 4096)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            seen += len(chunk)
            tail.extend(chunk)
            del tail[:-LIMIT]

    try:
        while True:
            drain(0.05)
            before = proc.poll()
            published = endpoint.is_file()
            expired = time.monotonic() - started >= timeout
            if before is not None or published or expired:
                break
    finally:
        # Only the Popen child launched above; no port scanning, process-table kill or existing profile.
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        deadline = time.monotonic() + 1
        while selector.get_map() and time.monotonic() < deadline:
            drain(0.05)
        selector.close()
        proc.stderr.close()
    return {
        "endpoint_published": published,
        "timed_out": expired,
        "exit_before_cleanup": before,
        "exit_after_cleanup": proc.returncode,
        "stderr_tail": bytes(tail).decode("utf-8", errors="replace"),
        "stderr_bytes_seen": seen,
        "stderr_truncated": seen > LIMIT,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main():
    from playwright.sync_api import sync_playwright

    from intent_job_agent.browser_session import launch_arguments

    evidence = {"synthetic": True, "diagnostic_only": True, "platform": platform.platform(), "policy_changed": False}
    # Explicit non-private, read-only kernel status allowlist; never dump environment or user configuration.
    evidence["kernel_status"] = {}
    if platform.system() == "Linux":
        for name in (
            "/proc/sys/kernel/apparmor_restrict_unprivileged_userns",
            "/proc/sys/kernel/unprivileged_userns_clone",
            "/proc/sys/user/max_user_namespaces",
            "/sys/module/apparmor/parameters/enabled",
        ):
            try:
                with open(name) as stream:
                    evidence["kernel_status"][name] = stream.read(512).strip()
            except OSError as error:
                evidence["kernel_status"][name] = type(error).__name__
        evidence["uid"] = os.getuid()
    try:
        with tempfile.TemporaryDirectory(prefix="synthetic-browser-startup-") as directory:
            profile = Path(directory) / "empty-profile"
            profile.mkdir()
            with sync_playwright() as pw:
                binary = pw.chromium.executable_path
            args = launch_arguments(binary, profile, headless=True)
            evidence["binary"] = binary
            evidence["launch_args"] = [a.replace(str(profile), "<synthetic-profile>") for a in args[1:]]
            evidence.update(capture(args, profile / "DevToolsActivePort", 20))
            evidence["stderr_tail"] = evidence["stderr_tail"].replace(str(profile), "<synthetic-profile>")
    except Exception as error:
        evidence["diagnostic_error"] = f"{type(error).__name__}: {error}"
    print(json.dumps(evidence, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
