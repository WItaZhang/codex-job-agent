"""Synthetic regression against existing entrypoints, without importing the new binding module."""

import pytest

from intent_job_agent import apply_bridge
from intent_job_agent.form_fill.runner import FormFillRunner
from intent_job_agent.vendor.applypilot.apply import chrome

from .conftest import settings


def test_existing_foreign_9222_is_not_reused(tmp_path, monkeypatch):
    cfg = settings()
    cfg = cfg.model_copy(
        update={
            "storage": cfg.storage.model_copy(
                update={"data_dir": str(tmp_path), "db_path": str(tmp_path / "synthetic.db")}
            )
        }
    )
    monkeypatch.setattr(apply_bridge, "cdp_alive", lambda port: True)
    # Never launch anything if the old implementation changes its first branch.
    monkeypatch.setattr(chrome, "launch_chrome", lambda *a, **k: pytest.fail("vendor launch forbidden"))
    from intent_job_agent.domain import InvariantError

    try:
        result = apply_bridge.ensure_chrome(cfg)
    except InvariantError:  # No owner: fail closed before starting or attaching.
        return
    assert not isinstance(result, int), "Old entrypoint reused arbitrary service on fixed 9222"


def test_existing_start_never_copies_daily_profile_or_kills_port(tmp_path, monkeypatch):
    cfg = settings()
    cfg = cfg.model_copy(
        update={
            "storage": cfg.storage.model_copy(
                update={"data_dir": str(tmp_path), "db_path": str(tmp_path / "synthetic.db")}
            )
        }
    )
    monkeypatch.setattr(apply_bridge, "cdp_alive", lambda port: False)

    def poison(*args, **kwargs):
        pytest.fail("daily profile copy / vendor port-kill path reached")

    monkeypatch.setattr(chrome, "setup_worker_profile", poison)
    monkeypatch.setattr(chrome, "launch_chrome", poison)
    monkeypatch.setattr(chrome, "_kill_on_port", poison)
    # A missing coordinator may safely refuse, but must not reach vendor side effects.
    from intent_job_agent.domain import InvariantError

    try:
        apply_bridge.ensure_chrome(cfg)
    except InvariantError:
        pass


def test_existing_open_tabs_does_not_read_foreign_9222(tmp_path, monkeypatch):
    cfg = settings()
    cfg = cfg.model_copy(
        update={
            "storage": cfg.storage.model_copy(
                update={"data_dir": str(tmp_path), "db_path": str(tmp_path / "synthetic.db")}
            )
        }
    )
    monkeypatch.setattr(apply_bridge, "cdp_alive", lambda port: True)
    from intent_job_agent.form_fill import readback

    calls = []
    monkeypatch.setattr(readback, "list_targets", lambda port: calls.append(port) or [{"id": "foreign"}])
    runner = FormFillRunner(tmp_path, cfg, tmp_path / "synthetic-profile.json")
    assert runner.open_tabs() is None
    assert calls == []
