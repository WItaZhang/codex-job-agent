"""Observed synthetic CI rows; parser regressions are not a complete Linux fixture run."""

import json
import runpy
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VALIDATE = runpy.run_path(str(ROOT / "scripts/probe_linux_sandbox.py"))["validate"]
REQUIRED = {
    "Layer 1 Sandbox": "Namespace",
    "PID namespaces": "Yes",
    "Network namespaces": "Yes",
    "Seccomp-BPF sandbox": "Yes",
}


def sample():
    return json.loads((ROOT / "tests/fixtures/synthetic_linux_sandbox_ci39929809213.json").read_text())["evidence"]


def test_observed_ci_rows_and_process_evidence_pass():
    VALIDATE(sample())


@pytest.mark.parametrize("field", REQUIRED)
@pytest.mark.parametrize(
    "mutation", ["no", "missing", "conflicting_duplicate", "reversed_duplicate", "identical_duplicate"]
)
def test_each_required_row_is_exact_and_unique(field, mutation):
    data = sample()
    row = field + "\t" + REQUIRED[field]
    replacement = {
        "no": field + "\tNo",
        "missing": "",
        "conflicting_duplicate": row + "\n" + field + "\tNo",
        "reversed_duplicate": field + "\tNo\n" + row,
        "identical_duplicate": row + "\n" + row,
    }[mutation]
    data["chromium_sandbox_status"] = data["chromium_sandbox_status"].replace(row, replacement, 1)
    with pytest.raises(AssertionError):
        VALIDATE(data)


@pytest.mark.parametrize(
    "fake", ["Namespace sandbox Yes", "Layer 1 Sandbox Namespace", "Layer 1 Sandbox\tNamespace extra"]
)
def test_keywords_or_prose_do_not_replace_observed_rows(fake):
    data = sample()
    data["chromium_sandbox_status"] = data["chromium_sandbox_status"].replace("Layer 1 Sandbox\tNamespace", fake)
    with pytest.raises(AssertionError):
        VALIDATE(data)


@pytest.mark.parametrize("field", ["Seccomp", "NoNewPrivs", "foreign", "empty"])
def test_process_evidence_remains_required(field):
    data = deepcopy(sample())
    if field == "foreign":
        data["renderers"][0]["descends_from_owned_browser"] = False
    elif field == "empty":
        data["renderers"] = []
    else:
        data["renderers"][0]["status"][field] = "0"
    with pytest.raises(AssertionError):
        VALIDATE(data)
