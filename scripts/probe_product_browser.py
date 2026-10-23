"""R1b product-path fixture: fresh synthetic workspace/profile, about:blank only; no business tools."""

import argparse
import asyncio
import json
import platform
import tempfile
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from pathlib import Path

from playwright.sync_api import sync_playwright
from probe_browser_binding import TITLE, check_mcp

from intent_job_agent import browser_session as bs
from intent_job_agent import watchdog
from intent_job_agent.config import Settings
from intent_job_agent.form_fill import checkpoint, readback
from intent_job_agent.form_fill.redact import Redactor
from intent_job_agent.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-chrome", action="store_true")
    args = parser.parse_args()
    assert version("playwright") == "1.56.0"
    with tempfile.TemporaryDirectory(prefix="synthetic-r1b-product-") as tmp:
        root = Path(tmp).resolve()
        cfg = Settings.load(Path(__file__).resolve().parents[1] / "configs/default.yaml")
        cfg = cfg.model_copy(
            update={
                "storage": cfg.storage.model_copy(update={"data_dir": str(root), "db_path": str(root / "coord.db")})
            }
        )
        store = Store(root / "coord.db", cfg)
        store.begin_activity(watchdog.me(), "form_fill", run_id="synthetic-product-probe")
        store.db.close()
        with sync_playwright() as pw:
            binary = (
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                if args.system_chrome
                else pw.chromium.executable_path
            )
            cfg = cfg.model_copy(
                update={
                    "apply": cfg.apply.model_copy(
                        update={"browser_executable": binary, "browser_headless": not args.system_chrome}
                    )
                }
            )
            expected = bs.scope(cfg)
            binding = bs.ensure(cfg)
            try:
                browser = bs.connect(pw, binding, expected=expected)
                page = browser.contexts[0].pages[0]
                assert page.url == "about:blank"
                page.set_content(
                    f'<title>{TITLE}</title><label>First Name<input value="Synthetic"></label><button>Next</button>'
                )
                webdriver = page.evaluate("navigator.webdriver")
                browser_version = browser.version
                target = next(t for t in bs.targets(binding, expected=expected) if t["title"] == TITLE)
                watchdog.ChromeWatch(binding, [], expected=expected, control=True).check()
                redactor = Redactor.from_profile({"personal": {"full_name": "Synthetic Applicant"}})
                final = readback.read_final_page(binding, target, root / "readback", redactor, expected=expected)
                assert final.fields

                def checkpoint_read():
                    live = checkpoint.LiveBrowser(binding, redactor, expected=expected)
                    try:
                        assert any(t["id"] == target["id"] for t in live.tabs())
                        assert live.read(target["id"])
                    finally:
                        live.close()

                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(checkpoint_read).result(timeout=30)
                    mcp = pool.submit(
                        lambda: asyncio.run(asyncio.wait_for(check_mcp(binding.endpoint, root), 110))
                    ).result(timeout=120)
                    wrong = binding.endpoint.rsplit("/", 1)[0] + "/synthetic-wrong"
                    mcp_wrong = pool.submit(
                        lambda: asyncio.run(asyncio.wait_for(check_mcp(wrong, root, wrong_guid=True), 110))
                    ).result(timeout=120)
                refused = False
                try:
                    pw.chromium.connect_over_cdp(wrong, timeout=1500)
                except Exception:
                    refused = True
                assert refused
                browser.close()  # Playwright CDP disconnect, not a browser shutdown
                assert watchdog.alive(binding.pid, binding.started)
                assert bs.targets(binding, expected=expected)
                # Restart keeps the owned profile and creates another instance generation.
                cookie = Path(binding.profile) / "synthetic-persistence-proof"
                cookie.write_text("synthetic only")
                first = binding
                bs.close(binding, expected=expected, timeout=5)
                binding = bs.ensure(cfg)
                assert binding.profile == first.profile and binding.generation != first.generation
                assert cookie.read_text() == "synthetic only"
                print(
                    json.dumps(
                        {
                            "synthetic": True,
                            "platform": platform.system(),
                            "os_version": platform.platform(),
                            "binary": binary.replace(str(Path.home()), "<home>"),
                            "headless": cfg.apply.browser_headless,
                            "browser_version": browser_version,
                            "python_playwright": "1.56.0",
                            "navigator_webdriver_observed": webdriver,
                            "launch_args": [
                                x.replace(binding.profile, "<owned-profile>")
                                for x in bs.launch_arguments(
                                    binary, binding.profile, headless=cfg.apply.browser_headless
                                )[1:]
                            ],
                            "product_ensure_connect_metadata_readback_checkpoint_watchdog": True,
                            "cdp_disconnect_keeps_browser_alive": True,
                            "restart_keeps_profile_new_generation": True,
                            "popen_os_cdp_pid_match": True,
                            "wrong_guid_python_refused": True,
                            "mcp": mcp,
                            "mcp_wrong_guid": mcp_wrong,
                            "navigation": "about:blank",
                            "business_tools_called": False,
                            "windows_verified": False,
                            "compatibility_flags_effects_verified": False,
                            "compatibility_flags_note": (
                                "Launch arguments and product identity flow verified; "
                                "per-flag popup/password/permission effects and ignored flags not determined."
                            ),
                        },
                        indent=2,
                    )
                )
            finally:
                bs.close(binding, expected=expected, timeout=5)


if __name__ == "__main__":
    main()
