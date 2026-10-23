"""Read-only sandbox evidence for the product-owned synthetic Chromium fixture."""

import json
import os
from pathlib import Path


def status(pid):
    fields = {}
    for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in ("Pid", "PPid", "Seccomp", "NoNewPrivs"):
            fields[key] = value.strip()
    return fields


def validate(evidence):
    # Chromium 141 chrome://sandbox body text uses one exact tab-delimited row per field.
    # Headings/prose cannot stand in for a row; duplicate rows (even agreeing ones) are ambiguous.
    rows = {}
    for line in evidence["chromium_sandbox_status"].splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        assert len(parts) == 2, evidence
        name, value = parts
        assert name not in rows, evidence
        rows[name] = value
    for name, expected in {
        "Layer 1 Sandbox": "Namespace",
        "PID namespaces": "Yes",
        "Network namespaces": "Yes",
        "Seccomp-BPF sandbox": "Yes",
    }.items():
        assert rows.get(name) == expected, evidence
    assert evidence["renderers"], evidence
    for renderer in evidence["renderers"]:
        assert renderer["descends_from_owned_browser"], evidence
        assert renderer["status"]["Seccomp"] == "2", evidence
        assert renderer["status"]["NoNewPrivs"] == "1", evidence


def collect(browser, binding):
    page = browser.contexts[0].new_page()
    try:
        page.goto("chrome://sandbox", timeout=10000)
        text = page.locator("body").inner_text(timeout=10000)
        info = browser.new_browser_cdp_session().send("SystemInfo.getProcessInfo")["processInfo"]
        assert [int(p["id"]) for p in info if p["type"] == "browser"] == [binding.pid]
        result = {
            "browser_pid": binding.pid,
            "browser_status": status(binding.pid),
            "chromium_sandbox_status": text[:8192],
            "renderers": [],
        }
        for proc in info:
            if proc["type"] != "renderer":
                continue
            pid = int(proc["id"])
            current, ancestry = pid, []
            for _ in range(32):
                if current in (0, 1, binding.pid) or current in ancestry:
                    break
                ancestry.append(current)
                current = int(status(current)["PPid"])
            namespaces = {}
            for name in ("pid", "user"):
                try:
                    namespaces[name] = {
                        "browser": os.readlink(f"/proc/{binding.pid}/ns/{name}"),
                        "renderer": os.readlink(f"/proc/{pid}/ns/{name}"),
                    }
                except OSError as error:
                    namespaces[name] = {"unavailable": type(error).__name__}
            result["renderers"].append(
                {
                    "pid": pid,
                    "status": status(pid),
                    "ancestry": ancestry,
                    "descends_from_owned_browser": current == binding.pid,
                    "namespaces": namespaces,
                }
            )
        # Emit before assertions so an unsupported observation is diagnosable, not invented or silently waived.
        print("LINUX_SANDBOX_EVIDENCE=" + json.dumps(result), flush=True)
        validate(result)
        return result
    finally:
        page.close()
