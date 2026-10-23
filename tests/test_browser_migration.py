"""Synthetic migration contract: no HTTP rediscovery or bare ports in product CDP consumers."""

import inspect

import pytest

from intent_job_agent import apply_bridge, watchdog
from intent_job_agent.form_fill import checkpoint, readback

from .conftest import settings


@pytest.mark.parametrize(
    "function",
    [
        readback.list_targets,
        readback._read_live,
        readback._glance,
        watchdog.cdp_targets,
        checkpoint.LiveBrowser.__init__,
        apply_bridge.mcp_config,
    ],
)
def test_consumers_do_not_rediscover_browser_by_http_port(function):
    assert "http://127.0.0.1:" not in inspect.getsource(function)
    assert "http://localhost:" not in inspect.getsource(function)


def test_checkpoint_rejects_port_only_execution_config():
    assert "binding" in checkpoint.Config.model_fields
    assert "port" not in checkpoint.Config.model_fields


def test_mcp_refuses_bare_port():
    from intent_job_agent.domain import InvariantError

    with pytest.raises(InvariantError):
        apply_bridge.mcp_config(settings(), 9222)


def test_checkpoint_identity_failure_never_says_carry_on():
    from intent_job_agent.browser_session import BrowserIdentityLost

    def missing():
        raise BrowserIdentityLost("synthetic lost identity")

    worker = checkpoint.Worker(missing, interval=0.01, timeout=0.05)
    worker.start()
    try:
        answer = worker.checkpoint("blank", "http://127.0.0.1/synthetic")
        assert "browser_identity_lost" in answer and "STOP" in answer
        assert "Carry on" not in answer
    finally:
        worker.stop()
