"""Real, synthetic, empty-profile browser using product launch/identity code; local pages only."""

from intent_job_agent import browser_session, watchdog
from intent_job_agent.store import Store

from .conftest import settings


def start_owned(tmp_path, playwright):
    cfg = settings()
    cfg = cfg.model_copy(
        update={
            "storage": cfg.storage.model_copy(update={"data_dir": str(tmp_path), "db_path": str(tmp_path / "coord.db")})
        }
    )
    store = Store(cfg.storage.db_path, cfg)
    store.begin_activity(watchdog.me(), "form_fill", run_id="synthetic-fixture")
    store.db.close()
    cfg = cfg.model_copy(
        update={
            "apply": cfg.apply.model_copy(
                update={"browser_executable": playwright.chromium.executable_path, "browser_headless": True}
            )
        }
    )
    binding = browser_session.ensure(cfg)
    browser = browser_session.connect(playwright, binding, expected=browser_session.scope(cfg))
    return browser.contexts[0], binding


def finish_owned(binding):
    browser_session.close(binding, expected=(binding.root, binding.db_path), timeout=3)
