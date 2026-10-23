"""Diagnostic capture tested with controlled synthetic child processes, not a browser substitute."""

import runpy
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/probe_browser_startup.py"


def test_capture_preserves_exit_and_bounded_stderr(tmp_path):
    capture = runpy.run_path(str(SCRIPT))["capture"]
    result = capture(
        [sys.executable, "-c", "import sys;sys.stderr.write('x'*100000);sys.exit(7)"], tmp_path / "absent", 3
    )
    assert result["exit_before_cleanup"] == 7
    assert result["stderr_bytes_seen"] == 100000
    assert len(result["stderr_tail"].encode()) <= 16384
    assert result["stderr_truncated"] and not result["endpoint_published"]


def test_capture_times_out_and_cleans_only_its_child(tmp_path):
    capture = runpy.run_path(str(SCRIPT))["capture"]
    result = capture([sys.executable, "-c", "import time;time.sleep(30)"], tmp_path / "absent", 0.1)
    assert result["timed_out"] and result["exit_before_cleanup"] is None
    assert result["exit_after_cleanup"] is not None
