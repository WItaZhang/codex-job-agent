"""Synthetic validator tests do not substitute for Linux runtime evidence."""

import runpy
from copy import deepcopy
from pathlib import Path

import pytest

VALIDATE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/probe_linux_sandbox.py"))["validate"]


def sample():
    return {
        "chromium_sandbox_status": "Namespace sandbox Yes\nSeccomp-BPF sandbox Yes",
        "renderers": [{"descends_from_owned_browser": True, "status": {"Seccomp": "2", "NoNewPrivs": "1"}}],
    }


def test_combined_evidence_required():
    VALIDATE(sample())
    for key, value in [("Seccomp", "0"), ("NoNewPrivs", "0")]:
        data = deepcopy(sample())
        data["renderers"][0]["status"][key] = value
        with pytest.raises(AssertionError):
            VALIDATE(data)
    for data in [dict(sample(), chromium_sandbox_status="Seccomp-BPF sandbox Yes"), dict(sample(), renderers=[])]:
        with pytest.raises(AssertionError):
            VALIDATE(data)
    data = sample()
    data["renderers"][0]["descends_from_owned_browser"] = False
    with pytest.raises(AssertionError):
        VALIDATE(data)
