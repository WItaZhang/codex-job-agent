"""No-model validation of the package-external synthetic G10 harness."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["dry-run", "dry-poll"])
def test_g10_fake_sinks_and_materials_before_any_host_model(tmp_path, mode):
    (tmp_path / "SYNTHETIC_G10").write_text("no real business\n")
    result = subprocess.run(
        [sys.executable, "-m", "tests.host_apply_acceptance", mode, str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["passed"]
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert sum(e["kind"] == "network_poison_positive_control" for e in events) == 1
    assert sum(e["kind"] == "sink_refused" for e in events) == 1  # positive control only
    calls = [e for e in events if e["kind"] == "runner"]
    assert calls and all(e["mode"] == "stop" for e in calls if e["company"] == "Stripe, Inc.")
    if mode == "dry-poll":
        assert any(e["kind"] == "await_status_poll" for e in events)
        assert any(e["kind"] == "poll_releases_fake_runner" for e in events)
