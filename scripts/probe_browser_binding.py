"""R1b explicit local-only capability probe; not a product entrypoint or authorization bypass.

Starts only its own fresh temporary profile and about:blank. Default: Playwright Chromium
headless; --system-chrome: macOS system Google Chrome headed. Requires Python Playwright
1.56.0 and npx for pinned MCP 0.0.83; package download is possible.
No applicant data, login, job site, mail, existing Chrome profile or product Toolbox calls.
"""

import argparse
import asyncio
import json
import os
import platform
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from playwright.sync_api import sync_playwright

from intent_job_agent import watchdog

MCP_PACKAGE = "@playwright/mcp@0.0.83"
TITLE = "Synthetic R1b binding probe"


async def check_mcp(endpoint: str, temp: Path, *, wrong_guid: bool = False) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("NODE_OPTIONS", "NODE_PATH")}
    for name in ("npm-user.conf", "npm-global.conf"):
        (temp / name).write_text("")
    env.update(
        npm_config_cache=str(temp / "npm-cache"),
        npm_config_userconfig=str(temp / "npm-user.conf"),
        npm_config_globalconfig=str(temp / "npm-global.conf"),
    )
    params = StdioServerParameters(
        command="npx",
        args=["-y", MCP_PACKAGE, f"--cdp-endpoint={endpoint}"],
        env=env,
        cwd=str(temp),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            initialized = await client.initialize()
            listed = await client.list_tools()
            assert "browser_tabs" in {t.name for t in listed.tools}
            result = await client.call_tool("browser_tabs", {"action": "list"})
            rendered = "\n".join(c.text for c in result.content if hasattr(c, "text"))
            if wrong_guid:
                assert result.is_error, "Wrong GUID must return a tool error"
                assert TITLE not in rendered, "MCP rediscovered the live browser from a wrong GUID"
                assert "WebSocket" in rendered or "404" in rendered, rendered
            else:
                assert not result.is_error, result
                assert TITLE in rendered and "about:blank" in rendered, rendered
            manifests = list((temp / "npm-cache/_npx").glob("*/node_modules/@playwright/mcp/package.json"))
            assert len(manifests) == 1, "Cannot verify the installed pinned MCP package"
            package = json.loads(manifests[0].read_text())
            assert package["name"] == "@playwright/mcp" and package["version"] == "0.0.83"
            return {
                "package": MCP_PACKAGE,
                "installed_package_version": package["version"],
                "server_version": initialized.server_info.version,
                "exact_ws_tabs": not wrong_guid,
                "wrong_guid_tool_error": bool(result.is_error) if wrong_guid else None,
                "synthetic_title_absent": TITLE not in rendered if wrong_guid else None,
                "resolved_playwright_packages": {
                    name: json.loads((manifests[0].parents[2] / name / "package.json").read_text())["version"]
                    for name in ("playwright", "playwright-core")
                    if (manifests[0].parents[2] / name / "package.json").is_file()
                },
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-chrome", action="store_true", help="Use macOS system Google Chrome, headed")
    args = parser.parse_args()
    assert version("playwright") == "1.56.0"
    with tempfile.TemporaryDirectory(prefix="synthetic-r1b-browser-") as directory:
        temp = Path(directory)
        profile = temp / "empty-profile"
        profile.mkdir(mode=0o700)
        assert not list(profile.iterdir())
        with sync_playwright() as pw:
            executable = (
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                if args.system_chrome
                else pw.chromium.executable_path
            )
            assert Path(executable).is_file(), "Install the pinned Playwright Chromium before this explicit probe"
            launch_args = [
                executable,
                *([] if args.system_chrome else ["--headless=new"]),
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
            proc = subprocess.Popen(
                launch_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            identity = None
            try:
                deadline = time.monotonic() + 15
                active = profile / "DevToolsActivePort"
                while not active.is_file():
                    assert proc.poll() is None, "Probe Chromium exited before creating its endpoint"
                    assert time.monotonic() < deadline, "No endpoint within 15 seconds"
                    time.sleep(0.05)
                identity = watchdog.processes()[proc.pid]
                port, path = active.read_text().strip().splitlines()
                assert 0 < int(port) < 65536 and path.startswith("/devtools/browser/")
                endpoint = f"ws://127.0.0.1:{port}{path}"
                browser = pw.chromium.connect_over_cdp(endpoint, timeout=10000)
                cdp = browser.new_browser_cdp_session()
                processes = cdp.send("SystemInfo.getProcessInfo")["processInfo"]
                browser_pids = [int(p["id"]) for p in processes if p["type"] == "browser"]
                assert browser_pids == [proc.pid], browser_pids
                assert f"--user-data-dir={profile}" in identity.command
                assert watchdog.processes()[proc.pid].started == identity.started
                page = browser.contexts[0].pages[0]
                assert page.url == "about:blank"
                page.evaluate("title => document.title = title", TITLE)
                # Sync Playwright runs its own event loop; isolate the async MCP client.
                with ThreadPoolExecutor(max_workers=1) as pool:
                    mcp = pool.submit(
                        lambda: asyncio.run(asyncio.wait_for(check_mcp(endpoint, temp), timeout=120))
                    ).result(timeout=130)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    mcp_wrong = pool.submit(
                        lambda: asyncio.run(
                            asyncio.wait_for(
                                check_mcp(
                                    f"ws://127.0.0.1:{port}/devtools/browser/synthetic-wrong",
                                    temp,
                                    wrong_guid=True,
                                ),
                                timeout=120,
                            )
                        )
                    ).result(timeout=130)
                assert proc.poll() is None and watchdog.processes()[proc.pid].started == identity.started
                # A different browser GUID must not be silently resolved via the HTTP port.
                refused = False
                try:
                    pw.chromium.connect_over_cdp(
                        f"ws://127.0.0.1:{port}/devtools/browser/synthetic-wrong", timeout=1500
                    )
                except Exception:
                    refused = True
                assert refused
                print(
                    json.dumps(
                        {
                            "synthetic": True,
                            "platform": platform.system(),
                            "os_version": platform.mac_ver()[0]
                            if platform.system() == "Darwin"
                            else platform.release(),
                            "architecture": platform.machine(),
                            "binary": executable,
                            "headless": not args.system_chrome,
                            "launch_args": [
                                a.replace(str(profile), "<temporary-empty-profile>") for a in launch_args[1:]
                            ],
                            "cdp_command_line_required": False,
                            "playwright": version("playwright"),
                            "browser": browser.version,
                            "empty_profile": True,
                            "dynamic_endpoint": True,
                            "exact_ws": True,
                            "cdp_browser_pid_matches_popen": True,
                            "started_stable": True,
                            "os_profile_argument_matches": True,
                            "wrong_guid_refused": True,
                            "mcp": mcp,
                            "mcp_wrong_guid": mcp_wrong,
                            "navigation": "about:blank",
                            "business_tools_called": False,
                            "claim": "capability evidence, not authentication against malicious same-user processes",
                        },
                        indent=2,
                    )
                )
            finally:
                # Only the process launched here; never kill by port or enumerate other Chrome profiles.
                if proc.poll() is None:
                    current = watchdog.processes().get(proc.pid)
                    if identity is None or (current and current.started == identity.started):
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=10)


if __name__ == "__main__":
    main()
